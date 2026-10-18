"""PyCharm entry point: one Run starts HTTP + optional HTTPS tunnel + Telegram worker."""
import argparse,json,os,signal,subprocess,sys,time,urllib.request,urllib.error,uuid
from pathlib import Path
from tunnel import QuickTunnel,TunnelError
from network import ssl_context,NetworkSetupError
ROOT=Path(__file__).resolve().parent

class StartupError(RuntimeError):pass

def read_settings(path):
 if not path.exists():raise StartupError(f'Создайте {path.name} по образцу telegram.env.example и заполните BOT_TOKEN локально.')
 result={}
 for line in path.read_text(encoding='utf-8-sig').splitlines():
  line=line.strip()
  if not line or line.startswith('#'):continue
  if '=' not in line:raise StartupError(f'Некорректная строка в {path.name}. Формат: ИМЯ=значение.')
  key,value=line.split('=',1);key=key.strip()
  if not key or not key.replace('_','').isalnum():raise StartupError('Некорректное имя настройки.')
  result[key]=value.strip().strip('"').strip("'")
 return result

def build_environment(settings,demo=False,port=None):
 env=dict(os.environ)
 for k,v in settings.items():env.setdefault(k,v)
 env['APP_MODE']='demo' if demo else 'live'
 env['HOST']='127.0.0.1'
 env['PYTHONUNBUFFERED']='1'
 env['DB_PATH']=str(ROOT/('data/preview.sqlite3' if demo else env.get('DB_PATH','data/telegram.sqlite3')))
 env['PORT']=str(port if port is not None else env.get('PORT','8875'))
 env['INSTANCE_ID']=uuid.uuid4().hex
 try:
  if not 1<=int(env['PORT'])<=65535:raise ValueError()
  for v in env.get('MANAGER_IDS','').split(','):
   if v.strip() and int(v)<=0:raise ValueError()
 except ValueError:raise StartupError('Проверьте PORT и MANAGER_IDS: нужны положительные целые числа.')
 if not demo:
  token=env.get('BOT_TOKEN','')
  if not token or token in ('PASTE_BOT_TOKEN_HERE','локальный_токен_тестового_бота'):raise StartupError('Заполните BOT_TOKEN в telegram.env. Токен не отправляйте в чат.')
  if env.get('HTTPS_MODE','cloudflare') not in ('cloudflare','external'):raise StartupError('HTTPS_MODE: cloudflare или external.')
  if env.get('HTTPS_MODE','cloudflare')=='external':
   from domain import https_url,Problem
   from urllib.parse import urlparse
   try:
    url=https_url(env.get('MINI_APP_URL',''))
    parts=urlparse(url)
    if parts.hostname in ('localhost','127.0.0.1','::1') or parts.path not in ('','/') or parts.query or parts.fragment:raise ValueError()
   except (Problem,ValueError):raise StartupError('Для external задайте публичный HTTPS-адрес корня приложения (без пути и параметров).')
 return env

def stop_process(process):
 if process and process.poll() is None:
  process.terminate()
  try:process.wait(timeout=8)
  except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

def await_server(process,port,instance_id,timeout=15):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  if process.poll() is not None:raise StartupError('Сервер Mini App не запустился. Проверьте настройки и свободен ли порт.')
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
    data=json.load(response)
    if data.get('ok') and data.get('instance_id')==instance_id:return
    raise StartupError('Порт занят другим приложением. Задайте другой PORT в telegram.env.')
  except (OSError,ValueError):pass
  time.sleep(.1)
 raise StartupError('Сервер Mini App не ответил. Проверьте свободен ли порт.')

def await_public(url,instance_id,timeout=60):
 # Verify that the public root routes to this exact server before setting bot buttons.
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  try:
   with urllib.request.urlopen(url.rstrip('/')+'/health',timeout=5,context=ssl_context()) as response:
    data=json.load(response)
    if data.get('mode')=='live' and data.get('instance_id')==instance_id:return
    raise StartupError('HTTPS-адрес ведёт на другой сервер. MINI_APP_URL должен вести на текущий сервер этого проекта.')
  except urllib.error.URLError as exc:
   import ssl
   if isinstance(exc.reason,ssl.SSLCertVerificationError):raise StartupError('Системное хранилище не доверяет сертификату HTTPS-адреса. Проверьте сертификаты и сеть; проверка TLS включена.') from None
   time.sleep(.5)
  except (OSError,ValueError):time.sleep(.5)
 raise StartupError('Публичный HTTPS-адрес не ответил. Проверьте сеть, туннель и MINI_APP_URL.')


def run(argv=None):
 parser=argparse.ArgumentParser(description='MAJLIS: Telegram-бот и Mini App одной кнопкой Run.')
 parser.add_argument('--demo',action='store_true',help='Только локальный просмотр, без Telegram и HTTPS.')
 parser.add_argument('--port',type=int);parser.add_argument('--config',default='telegram.env');parser.add_argument('--check',action='store_true',help='Проверить настройки без запуска и без обращения к Telegram.')
 args=parser.parse_args(argv)
 server=worker=None;tunnel=None
 try:
  settings={} if args.demo else read_settings(ROOT/args.config)
  env=build_environment(settings,args.demo,args.port)
  if not args.demo:ssl_context()
  if args.check:
   if not args.demo and env.get('HTTPS_MODE','cloudflare')=='cloudflare':QuickTunnel(int(env['PORT']),env.get('CLOUDFLARED_PATH')).executable()
   print('Настройки корректны. Токен скрыт. Доступность Telegram и HTTPS ещё не проверялась.');return 0
  if not args.demo:
   # Prevent duplicate launches for the same database on this machine.
   lock=RunLock(Path(env['DB_PATH']).with_suffix('.run.lock'));lock.acquire()
  else:lock=None
  try:
   server=subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT,env=env)
   await_server(server,int(env['PORT']),env['INSTANCE_ID'])
   print(f'Локальный сервер: http://127.0.0.1:{env["PORT"]}',flush=True)
   if args.demo:
    print('Локальный просмотр. Для Telegram запустите main.py без --demo.',flush=True)
   else:
    if env.get('HTTPS_MODE','cloudflare')=='cloudflare':
     tunnel=QuickTunnel(int(env['PORT']),env.get('CLOUDFLARED_PATH'),doh_edges=env.get('CLOUDFLARE_DOH_EDGE','0')=='1',protocol=env.get('CLOUDFLARE_PROTOCOL','http2'),edge_hostnames=env.get('CLOUDFLARE_EDGE_HOSTNAMES','0')=='1')
     print('Получаем временный HTTPS-адрес…',flush=True)
     env['MINI_APP_URL']=tunnel.start()
    await_public(env['MINI_APP_URL'],env['INSTANCE_ID'])
    print('Mini App: '+env['MINI_APP_URL'],flush=True)
    if not env.get('MANAGER_IDS'):print('Менеджеры пока не назначены. Отправьте /id боту, внесите ID в telegram.env и перезапустите.',flush=True)
    worker=subprocess.Popen([sys.executable,str(ROOT/'bot.py')],cwd=ROOT,env=env)
   while True:
    if server.poll() is not None:raise StartupError('Сервер остановился. Все связанные процессы завершаются.')
    if worker and worker.poll() is not None:
     if worker.returncode!=0:raise StartupError('Telegram-бот остановился. Причина указана выше; токен в вывод не включается.')
     break
    if tunnel and tunnel.process.poll() is not None:raise StartupError('HTTPS-туннель остановился. Перезапустите main.py для получения нового адреса.')
    time.sleep(.3)
  finally:
   stop_process(worker)
   if tunnel:tunnel.close()
   stop_process(server)
   if lock:lock.release()
 except KeyboardInterrupt:
  print('\nMAJLIS остановлен.');return 0
 except (StartupError,TunnelError,NetworkSetupError,OSError) as exc:
  print('Не удалось запустить MAJLIS: '+str(exc),file=sys.stderr);return 1
 return 0

class RunLock:
 """OS-managed lock; automatically released after a crash; works on Mac/Windows/Linux."""
 def __init__(self,path):self.path=path;self.file=None
 def acquire(self):
  self.path.parent.mkdir(parents=True,exist_ok=True);self.file=self.path.open('a+b');self.file.seek(0)
  if self.file.read(1)==b'':self.file.write(b'0');self.file.flush()
  self.file.seek(0)
  try:
   if os.name=='nt':
    import msvcrt;msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl;fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError:
   self.file.close();self.file=None;raise StartupError('Этот проект уже запущен. Остановите предыдущий main.py.')
 def release(self):
  if self.file:
   self.file.seek(0)
   if os.name=='nt':
    import msvcrt;msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
   else:
    import fcntl;fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
   self.file.close();self.file=None

if __name__=='__main__':
 # PyCharm Stop on Unix sends SIGTERM. Convert it to cleanup of all child processes.
 if hasattr(signal,'SIGTERM'):signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
 raise SystemExit(run())

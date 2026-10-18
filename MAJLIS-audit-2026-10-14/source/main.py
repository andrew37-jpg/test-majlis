"""PyCharm Run: PostgreSQL + migrations + API + temporary HTTPS + Telegram worker."""
import argparse,json,os,signal,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def stop_process(process):
    if process and process.poll() is None:
        process.terminate()
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

def wait_http(url,instance,process=None,timeout=50):
    import urllib.request
    from majlis.network import ssl_context
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process and process.poll() is not None:raise RuntimeError('API остановился до завершения запуска.')
        try:
            with urllib.request.urlopen(url.rstrip('/')+'/health',timeout=5,context=ssl_context()) as r:data=json.load(r)
            if data.get('instance_id')==instance:return
            raise RuntimeError('Адрес ведёт на другую копию приложения.')
        except RuntimeError:raise
        except Exception:time.sleep(.5)
    raise RuntimeError('Mini App не ответил по адресу '+url)

def run():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    if not (ROOT/'.env').exists():raise RuntimeError('Создайте .env по образцу .env.example и заполните ключ локально.')
    from majlis.config import Settings
    cfg=Settings()
    if args.check:print('Настройки прочитаны. Ключ скрыт. Сетевые соединения не проверялись.');return
    env=dict(os.environ);env['PYTHONUNBUFFERED']='1';env['INSTANCE_ID']=uuid.uuid4().hex
    api=worker=tunnel=None;lock=None
    try:
        # Local OS lock prevents competing launchers before they replace bot buttons.
        data=ROOT/'data';data.mkdir(exist_ok=True);lock=(data/'launcher.lock').open('a+b')
        lock.write(b'0');lock.flush();lock.seek(0)
        try:
            if os.name=='nt':
                import msvcrt;msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl;fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise RuntimeError('Эта копия уже запущена. Остановите предыдущий main.py.') from None
        if cfg.auto_start_database:
            print('Запускаем PostgreSQL в Docker…',flush=True)
            subprocess.run(['docker','compose','--env-file',str(ROOT/'.env'),'-f',str(ROOT/'compose.local.yaml'),'up','-d','--build','--wait','db'],cwd=ROOT,check=True,env=env)
        from majlis.database import Database
        db=Database();db.open()
        try:db.migrate()
        finally:db.close()
        print('PostgreSQL готов. Миграции применены.',flush=True)
        api=subprocess.Popen([sys.executable,'-m','uvicorn','majlis.api:app','--host','127.0.0.1','--port',str(cfg.port),'--no-access-log','--log-level','warning','--no-proxy-headers'],cwd=ROOT,env=env)
        local=f'http://127.0.0.1:{cfg.port}';wait_http(local,env['INSTANCE_ID'],api)
        if cfg.https_mode=='localhost_run':
            from majlis.tunnel import LocalhostTunnel
            tunnel=LocalhostTunnel(cfg.port);url=tunnel.start()
        else:
            from majlis.domain import https_url
            url=https_url(cfg.mini_app_url)
        wait_http(url,env['INSTANCE_ID'],api)
        env['MINI_APP_URL']=url
        # Persist only the public address for tools; credentials stay in .env.
        (data/'runtime.json').write_text(json.dumps({'mini_app_url':url,'local_url':local,'instance_id':env['INSTANCE_ID']}))
        print('Mini App: '+url,flush=True)
        worker=subprocess.Popen([sys.executable,'-m','majlis.cli','bot'],cwd=ROOT,env=env)
        next_probe=time.monotonic()+15;route_failures=0
        while True:
            if api.poll() is not None:raise RuntimeError('API остановился. Перезапустите main.py.')
            if worker.poll() is not None:raise RuntimeError('Telegram worker остановился. Проверьте сообщение выше.')
            if tunnel:
                broken=tunnel.process.poll() is not None
                changed=tunnel.url and tunnel.url!=url
                if time.monotonic()>=next_probe:
                    try:wait_http(url,env['INSTANCE_ID'],api,timeout=6);route_failures=0
                    except RuntimeError:route_failures+=1
                    next_probe=time.monotonic()+15
                if broken or changed or route_failures>=2:
                    print('Проверка HTTPS не прошла. Восстанавливаем адрес и кнопку Mini App…',flush=True)
                    stop_process(worker);worker=None
                    recovered=False
                    for attempt in range(3):
                        try:
                            if changed and not broken:
                                url=tunnel.url;changed=False
                            else:
                                tunnel.close();tunnel=LocalhostTunnel(cfg.port);url=tunnel.start()
                            wait_http(url,env['INSTANCE_ID'],api,timeout=20)
                            recovered=True;break
                        except RuntimeError:
                            print('Повторяем соединение HTTPS…',flush=True);time.sleep(3)
                    if not recovered:raise RuntimeError('HTTPS не восстановлен. Проверьте сеть и перезапустите main.py.')
                    env['MINI_APP_URL']=url
                    (data/'runtime.json').write_text(json.dumps({'mini_app_url':url,'local_url':local,'instance_id':env['INSTANCE_ID']}))
                    worker=subprocess.Popen([sys.executable,'-m','majlis.cli','bot'],cwd=ROOT,env=env)
                    route_failures=0;next_probe=time.monotonic()+15
                    print('HTTPS восстановлен: '+url+'. Откройте обновлённое меню бота или отправьте /start.',flush=True)
            time.sleep(.5)
    finally:
        stop_process(worker)
        if tunnel:tunnel.close()
        stop_process(api)
        if lock:lock.close()
        # PostgreSQL remains running; data survives Stop, restart, and container recreation.

if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    try:run()
    except KeyboardInterrupt:print('MAJLIS остановлен. PostgreSQL и сохранённые заказы остаются на диске.')
    except Exception as exc:
        if isinstance(exc,RuntimeError):print('Не удалось запустить MAJLIS: '+str(exc),file=sys.stderr)
        else:print('Не удалось запустить MAJLIS: '+type(exc).__name__+'. Проверьте .env, Docker и сеть.',file=sys.stderr)
        raise SystemExit(1)

"""Telegram Bot API worker. Normally started by main.py alongside the Mini App."""
import json,os,time,ssl,threading,urllib.request,urllib.error
from server import TOKEN,DEMO,DB,MANAGERS
from domain import https_url,Problem,STAGE_MAP
from network import ssl_context,NetworkSetupError
from storage import connect,initialize

class TelegramFailure(Exception):
 def __init__(self,code,retry_after=5):
  self.code=code;self.retry_after=max(1,min(3600,retry_after));super().__init__(f'Telegram API code {code}')

def call(method,payload):
 """Never print exception text: it may contain a token-bearing request URL."""
 try:
  request=urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/{method}',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(request,timeout=40,context=ssl_context()) as response: result=json.load(response)
  if not result.get('ok'):raise TelegramFailure(result.get('error_code',500),result.get('parameters',{}).get('retry_after',5))
  return result['result']
 except urllib.error.HTTPError as exc:
  try:data=json.load(exc);retry=data.get('parameters',{}).get('retry_after',5)
  except (ValueError,TypeError):retry=5
  raise TelegramFailure(exc.code,retry) from None
 except urllib.error.URLError as exc:
  raise TelegramFailure(-1 if isinstance(exc.reason,ssl.SSLCertVerificationError) else 0) from None
 except (TimeoutError,OSError,ValueError,KeyError):raise TelegramFailure(0) from None

def keyboard(url):return {'inline_keyboard':[[{'text':'Открыть кабинет','web_app':{'url':url}}]]}

def send(chat_id,text,url=None):
 payload={'chat_id':chat_id,'text':text[:4000]}
 if url:payload['reply_markup']=keyboard(url)
 return call('sendMessage',payload)

def respond(message,url):
 user=message.get('from') or {};chat=message.get('chat') or {}
 if chat.get('type')!='private' or not user.get('id'):return
 text=message.get('text') or ''
 if not isinstance(text,str):return
 command=text.split()[0].split('@')[0].lower() if text.strip() else ''
 if command=='/start':
  with connect(DB) as db:count=db.execute('SELECT COUNT(*) FROM orders WHERE customer_id=?',(user['id'],)).fetchone()[0]
  welcome='Добро пожаловать в MAJLIS. Здесь можно следить за заказами и этапами доставки.'
  welcome+='\n\nВаши заказы уже доступны в кабинете.' if count else f'\n\nПока заказов нет. Если заказ уже оформлен, передайте менеджеру ваш Telegram ID: {user["id"]}. Менеджер привяжет заказ к кабинету.'
  if user['id'] in MANAGERS:welcome+='\n\nВ вашем кабинете доступна панель менеджера.'
  send(chat['id'],welcome,url)
 elif command=='/status':
  with connect(DB) as db:rows=db.execute('SELECT number,status FROM orders WHERE customer_id=? ORDER BY id DESC',(user['id'],)).fetchall()
  answer='\n'.join(f'{r["number"]}: {STAGE_MAP[r["status"]]["title"]}' for r in rows) or 'Пока нет заказов. Если вы уже оформили заказ, попросите менеджера привязать его к вашему кабинету.'
  send(chat['id'],answer,url)
 elif command=='/id':send(chat['id'],f'Ваш Telegram ID: {user["id"]}\nЭтот номер нужен для привязки заказа или настройки доступа менеджера.')
 elif command=='/manager':
  if user['id'] in MANAGERS:send(chat['id'],'Откройте кабинет — вам доступна панель менеджера.',url)
  else:send(chat['id'],'Для вашего аккаунта доступ менеджера не назначен.',url)
 elif command=='/help':send(chat['id'],'/start — открыть кабинет\n/status — мои заказы\n/id — мой Telegram ID\n/manager — панель менеджера\n/help — помощь',url)
 else:send(chat['id'],'Откройте кабинет для просмотра заказов или отправьте /help.',url)

def deliver():
 with connect(DB) as db:rows=db.execute("SELECT * FROM outbox WHERE state='pending' AND next_attempt<=? ORDER BY id LIMIT 10",(time.time(),)).fetchall()
 for row in rows:
  try:
   call('sendMessage',{'chat_id':row['customer_id'],'text':row['text']})
   with connect(DB) as db:db.execute("UPDATE outbox SET state='sent',last_error='' WHERE id=?",(row['id'],))
  except TelegramFailure as exc:
   if exc.code in (401,409):raise
   attempts=row['attempts']+1
   state='failed' if exc.code in (400,403) or attempts>=8 else 'pending'
   delay=max(exc.retry_after,min(3600,30*2**attempts))
   with connect(DB) as db:db.execute('UPDATE outbox SET state=?,attempts=?,next_attempt=?,last_error=? WHERE id=?',(state,attempts,time.time()+delay,f'Telegram error {exc.code}',row['id']))
   if exc.code==429:break

def configure(url):
 """Checks before writing bot settings. Existing webhook is not deleted automatically."""
 me=call('getMe',{})
 webhook=call('getWebhookInfo',{})
 if webhook.get('url'):raise RuntimeError('У этого бота настроен webhook. Используйте нового тестового бота или отключите прежний webhook осознанно. Ничего автоматически не удалено.')
 call('setMyCommands',{'commands':[{'command':'start','description':'Открыть кабинет'},{'command':'status','description':'Мои заказы'},{'command':'id','description':'Мой Telegram ID'},{'command':'manager','description':'Панель менеджера'},{'command':'help','description':'Помощь'}]})
 call('setChatMenuButton',{'menu_button':{'type':'web_app','text':'Кабинет','web_app':{'url':url}}})
 return me

def failure_message(code):
 return {-1:'Система не доверяет сертификату Telegram. Проверьте системные сертификаты и настройки сети; проверка TLS включена.',0:'Нет соединения с Telegram. Проверьте Интернет, VPN и доверенные сертификаты Python.',401:'Telegram отклонил токен. Проверьте BOT_TOKEN в telegram.env.',409:'Другой процесс уже получает обновления этого бота. Остановите его.',429:'Telegram ограничил частоту запросов.'}.get(code,f'Telegram вернул ошибку {code}.')

def main(stop_event=None):
 if DEMO:raise SystemExit('Для Telegram используйте main.py без --demo.')
 stop_event=stop_event or threading.Event()
 try:url=https_url(os.getenv('MINI_APP_URL',''))
 except Problem as exc:raise SystemExit(exc.message)
 initialize(DB,False)
 try:
  me=configure(url)
 except TelegramFailure as exc:raise SystemExit(failure_message(exc.code)) from None
 except (RuntimeError,NetworkSetupError) as exc:raise SystemExit(str(exc)) from None
 print(f'Бот @{me.get("username","unknown")} запущен. Откройте его и отправьте /start.',flush=True)
 while not stop_event.is_set():
  try:
   deliver()
   with connect(DB) as db:
    saved=db.execute("SELECT value FROM bot_state WHERE key='offset'").fetchone();offset=int(saved['value']) if saved else 0
   updates=call('getUpdates',{'offset':offset,'timeout':10,'allowed_updates':['message']})
   for update in updates:
    if stop_event.is_set():break
    try:
     if 'message' in update:respond(update['message'],url)
    except TelegramFailure as exc:
     # A blocked recipient must not poison the update stream forever.
     if exc.code not in (400,403):raise
    with connect(DB) as db:db.execute("INSERT INTO bot_state VALUES ('offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(update['update_id']+1),))
  except TelegramFailure as exc:
   if exc.code in (-1,401,409):raise SystemExit(failure_message(exc.code)) from None
   print(failure_message(exc.code)+' Повторяем подключение.',flush=True);stop_event.wait(exc.retry_after)

if __name__=='__main__':
 try:main()
 except KeyboardInterrupt:print('Telegram-бот остановлен.')

import json,time,threading
import httpx,psycopg
from .config import settings
from .database import Database
from .network import ssl_context
from .domain import https_url,STAGE_MAP
from .services import ensure_user

class TelegramFailure(Exception):
    def __init__(self,code,retry_after=5):
        self.code=code;self.retry_after=max(1,min(3600,int(retry_after)))
        super().__init__(f'Telegram error {code}')

class Telegram:
    def __init__(self):
        self.token=settings().bot_token.get_secret_value()
        self.client=httpx.Client(verify=ssl_context(),timeout=25)
    def close(self):self.client.close()
    def call(self,method,payload):
        try:
            response=self.client.post(f'https://api.telegram.org/bot{self.token}/{method}',json=payload)
            data=response.json()
            if not data.get('ok'):raise TelegramFailure(data.get('error_code',response.status_code),data.get('parameters',{}).get('retry_after',5))
            return data['result']
        except TelegramFailure:raise
        except (httpx.HTTPError,ValueError,KeyError):raise TelegramFailure(0) from None
    def send(self,chat,text,url=None):
        data={'chat_id':chat,'text':text[:4000]}
        if url:data['reply_markup']={'inline_keyboard':[[{'text':'Открыть кабинет MAJLIS','web_app':{'url':url}}]]}
        return self.call('sendMessage',data)
    def configure(self,url):
        https_url(url)
        me=self.call('getMe',{})
        if self.call('getWebhookInfo',{}).get('url'):
            raise RuntimeError('У бота настроен webhook. Прежний webhook не удалён; используйте отдельного тестового бота.')
        self.call('setMyCommands',{'commands':[{'command':'start','description':'Открыть кабинет'},{'command':'status','description':'Мои заказы'},{'command':'id','description':'Мой Telegram ID'},{'command':'help','description':'Помощь'}]})
        self.call('setChatMenuButton',{'menu_button':{'type':'web_app','text':'Кабинет MAJLIS','web_app':{'url':url}}})
        return me

def respond(db,api,message,url):
    user=message.get('from') or {};chat=message.get('chat') or {}
    if chat.get('type')!='private' or not user.get('id') or user.get('is_bot'):return
    text=message.get('text') or ''
    command=text.strip().split()[0].split('@')[0].lower() if text.strip() else ''
    with db.connection() as conn:ensure_user(conn,user,started=True)
    if command=='/start':
        api.send(chat['id'],'Добро пожаловать в MAJLIS.\nОткройте кабинет, чтобы следить за заказами и доставкой.\n\nЕсли заказ ещё не привязан, передайте менеджеру ваш Telegram ID: '+str(user['id']),url)
    elif command=='/id':api.send(chat['id'],'Ваш Telegram ID: '+str(user['id']))
    elif command=='/status':
        with db.connection() as conn:
            rows=conn.execute('SELECT number,status FROM orders WHERE customer_id=%s ORDER BY id DESC LIMIT 30',(user['id'],)).fetchall()
        api.send(chat['id'],'\n'.join(f"{r['number']}: {STAGE_MAP[r['status']]['title']}" for r in rows) or 'Пока нет заказов. Попросите менеджера привязать ваш заказ.',url)
    else:api.send(chat['id'],'/start — кабинет\n/status — мои заказы\n/id — мой Telegram ID\n/help — помощь',url)

def claim_notification(conn):
    return conn.execute('''UPDATE outbox SET state='sending',attempts=attempts+1,lease_until=now()+interval '2 minutes'
        WHERE id=(SELECT id FROM outbox WHERE (state='pending' AND available_at<=now()) OR
        (state='sending' AND lease_until<now()) ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *''').fetchone()

def deliver(db,api):
    for _ in range(10):
        with db.connection() as conn:row=claim_notification(conn)
        if not row:return
        with db.connection() as conn:
            # Lock the order before outbox, same order as reassignment transactions.
            order=conn.execute('SELECT o.customer_id FROM orders o JOIN events e ON e.order_id=o.id WHERE e.id=%s FOR UPDATE OF o',(row['event_id'],)).fetchone()
            current=conn.execute('SELECT * FROM outbox WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
            if current['state']!='sending':continue
            if order['customer_id']!=row['customer_id']:
                conn.execute("UPDATE outbox SET state='failed',last_error='owner_changed',lease_until=NULL WHERE id=%s",(row['id'],));continue
            try:
                api.send(row['customer_id'],row['text'])
                conn.execute("UPDATE outbox SET state='sent',sent_at=now(),lease_until=NULL,last_error='' WHERE id=%s",(row['id'],))
            except TelegramFailure as exc:
                if exc.code in (401,409):raise
                state='failed' if exc.code in (400,403) or row['attempts']>=8 else 'pending'
                delay=max(exc.retry_after,min(3600,30*2**row['attempts']))
                conn.execute("UPDATE outbox SET state=%s,available_at=now()+(%s*interval '1 second'),lease_until=NULL,last_error=%s WHERE id=%s",(state,delay,f'Telegram error {exc.code}',row['id']))
                if exc.code==429:return

def run(stop=None):
    stop=stop or threading.Event();cfg=settings();url=https_url(cfg.mini_app_url)
    db=Database();db.open();api=Telegram();leader=None
    try:
        # Dedicated session lock prevents duplicate polling even across different computers.
        leader=psycopg.connect(cfg.database_url.get_secret_value(),autocommit=True)
        if not leader.execute('SELECT pg_try_advisory_lock(742019144)').fetchone()[0]:
            raise RuntimeError('Бот уже запущен с этой базой. Второй процесс остановлен.')
        me=api.configure(url)
        print(f"Бот @{me['username']} запущен. Отправьте /start и нажмите «Открыть кабинет MAJLIS».",flush=True)
        while not stop.is_set():
            try:
                deliver(db,api)
                with db.connection() as conn:
                    saved=conn.execute("SELECT value FROM bot_state WHERE key='offset'").fetchone()
                updates=api.call('getUpdates',{'offset':int(saved['value']) if saved else 0,'timeout':10,'allowed_updates':['message']})
                for update in updates:
                    if stop.is_set():break
                    try:
                        if 'message' in update:respond(db,api,update['message'],url)
                    except TelegramFailure as exc:
                        if exc.code not in (400,403):raise
                    with db.connection() as conn:
                        conn.execute("INSERT INTO bot_state(key,value) VALUES ('offset',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(update['update_id']+1),))
            except TelegramFailure as exc:
                if exc.code in (401,409):raise
                print(f'Соединение с Telegram: ошибка {exc.code}; повторяем.',flush=True);stop.wait(exc.retry_after)
    finally:
        api.close();db.close()
        if leader:leader.close()

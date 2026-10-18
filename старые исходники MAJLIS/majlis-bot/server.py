"""Local preview and production HTTP app. Place behind HTTPS reverse proxy in live mode."""
import os,json,time,hmac,hashlib,sqlite3,threading
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit
from domain import Problem,validate_init_data,STAGES,https_url,positive_int,required_text
from storage import connect,initialize,order_json,get_order,create_order,change_status,integration_event,now
ROOT=Path(__file__).resolve().parent

def load_env():
 path=ROOT/'.env'
 if path.exists():
  for line in path.read_text().splitlines():
   if line.strip() and not line.lstrip().startswith('#') and '=' in line:
    k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('\"').strip("'"))
load_env()
DEMO=os.getenv('APP_MODE','demo')=='demo'
if os.getenv('APP_MODE','demo') not in ('demo','live'): raise RuntimeError('APP_MODE: demo или live')
DB=str(ROOT/os.getenv('DB_PATH','data/demo.sqlite3' if DEMO else 'data/live.sqlite3'))
TOKEN=os.getenv('BOT_TOKEN','')
MANAGERS={int(v.strip()) for v in os.getenv('MANAGER_IDS','').split(',') if v.strip()}
PAYMENT_HOSTS={v.strip().lower() for v in os.getenv('PAYMENT_HOSTS','').split(',') if v.strip()}
INTEGRATION_SECRET=os.getenv('INTEGRATION_SECRET','')
HOST=os.getenv('HOST','127.0.0.1')
if DEMO and HOST not in ('127.0.0.1','localhost','::1'): raise RuntimeError('Demo доступна только на localhost.')
if not DEMO and not TOKEN: raise RuntimeError('Для live задайте BOT_TOKEN. Без MANAGER_IDS панель менеджера недоступна.')
LIMIT_LOCK=threading.Lock();LIMITS={}

def identity(headers):
 if DEMO:
  key=headers.get('X-Demo-User','buyer')
  users={'buyer':{'id':1001,'first_name':'Андрей','role':'buyer'},'empty':{'id':1002,'first_name':'Новый покупатель','role':'buyer'},'manager':{'id':9001,'first_name':'Менеджер','role':'manager'}}
  if key not in users: raise Problem(401,'Неизвестный демонстрационный профиль.')
  return users[key]
 try: user=validate_init_data(headers.get('X-Telegram-Init-Data',''),TOKEN)
 except (ValueError,TypeError): raise Problem(401,'Некорректные данные Telegram.')
 user['role']='manager' if user['id'] in MANAGERS else 'buyer'
 return user

class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):
  # Do not log URLs/query strings, initData, Telegram IDs or tokens.
  print(f'HTTP {self.command} {args[1] if len(args)>1 else ""}',flush=True)
 def send(self,code,body,kind='application/json; charset=utf-8'):
  raw=json.dumps(body,ensure_ascii=False).encode() if isinstance(body,(dict,list)) else body
  self.send_response(code)
  self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)))
  self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
  self.send_header('Referrer-Policy','no-referrer')
  self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'")
  self.end_headers();self.wfile.write(raw)
 def do_GET(self): self.handle_request()
 def do_POST(self): self.handle_request()
 def handle_request(self):
  try:
   with LIMIT_LOCK:
    t=time.monotonic(); key=self.client_address[0]
    bucket=[v for v in LIMITS.get(key,[]) if t-v<60]
    if len(bucket)>=240: raise Problem(429,'Слишком много запросов. Попробуйте через минуту.')
    LIMITS[key]=bucket+[t]
    if len(LIMITS)>2000:
     for k in list(LIMITS):
      if not LIMITS[k] or t-LIMITS[k][-1]>60: LIMITS.pop(k,None)
   path=urlsplit(self.path).path
   if self.command=='GET' and path=='/health': return self.send(200,{'ok':True,'mode':'demo' if DEMO else 'live','instance_id':os.getenv('INSTANCE_ID','')})
   files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
   if self.command=='GET' and path in files:
    name,kind=files[path];return self.send(200,(ROOT/'web'/name).read_bytes(),kind)
   if not path.startswith('/api/'): raise Problem(404,'Страница не найдена.')
   data={};raw=b''
   if self.command=='POST':
    length=int(self.headers.get('Content-Length','0'))
    if length<=0 or length>65536: raise Problem(413,'Некорректный размер запроса.')
    if not self.headers.get('Content-Type','').startswith('application/json'): raise Problem(415,'Требуется JSON.')
    raw=self.rfile.read(length)
    try: data=json.loads(raw)
    except (ValueError,UnicodeDecodeError): raise Problem(400,'Некорректный JSON.')
    if not isinstance(data,dict): raise Problem(400,'Ожидается объект JSON.')
   if self.command=='POST' and path=='/api/integrations/events':
    if DEMO or len(INTEGRATION_SECRET)<32: raise Problem(503,'Внешние интеграции пока не подключены.')
    stamp=self.headers.get('X-Event-Timestamp',''); signature=self.headers.get('X-Event-Signature','')
    try:
     if abs(time.time()-int(stamp))>300: raise ValueError()
    except ValueError: raise Problem(401,'Подпись события истекла.')
    expected=hmac.new(INTEGRATION_SECRET.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature): raise Problem(401,'Неверная подпись события.')
    with connect(DB) as db: result=integration_event(db,data)
    return self.send(200,result)
   user=identity(self.headers)
   with connect(DB) as db:
    if self.command=='GET' and path=='/api/bootstrap':
     if user['role']=='manager': rows=db.execute('SELECT * FROM orders ORDER BY id DESC').fetchall()
     else: rows=db.execute('SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC',(user['id'],)).fetchall()
     result={'user':user,'demo':DEMO,'stages':[{'code':x[0],'title':x[1],'description':x[2]} for x in STAGES], 'orders':[order_json(db,r) for r in rows], 'updated_at':now(), 'links':{k:os.getenv(v,'') for k,v in [('catalog','CATALOG_URL'),('support','SUPPORT_URL')]},'integrations':{'cargo':'not_connected','delivery':'not_connected','website':'not_connected','payments':'manual_links' if PAYMENT_HOSTS else 'not_connected'}}
     if user['role']=='manager': result['notifications']=[dict(r) for r in db.execute('SELECT id,state,attempts,last_error FROM outbox ORDER BY id DESC LIMIT 30')]
     db.commit();return self.send(200,result)
    if self.command!='POST': raise Problem(404,'Метод не найден.')
    if user['role']!='manager': raise Problem(403,'Это действие доступно только менеджеру.')
    if path=='/api/orders':
     result=create_order(db,data);db.commit();return self.send(201,result)
    if path=='/api/orders/status':
     oid=positive_int(data.get('order_id'),'order_id')
     note=data.get('note','')
     if not isinstance(note,str) or len(note)>500: raise Problem(400,'Комментарий слишком длинный.')
     result=change_status(db,oid,required_text(data,'status',32),note,'manager')
     db.commit();return self.send(200,result)
    if path=='/api/orders/details':
     import datetime
     oid=positive_int(data.get('order_id'),'order_id');get_order(db,oid)
     customer=positive_int(data.get('customer_id'),'customer_id')
     shipment=data.get('shipment','');eta=data.get('eta','');confirmed=data.get('eta_confirmed',False)
     if not isinstance(shipment,str) or len(shipment)>100 or not isinstance(eta,str) or not isinstance(confirmed,bool): raise Problem(400,'Проверьте данные поставки.')
     if eta:
      try: datetime.date.fromisoformat(eta)
      except ValueError: raise Problem(400,'Проверьте дату поставки.')
     db.execute('UPDATE orders SET customer_id=?,shipment=?,eta=?,eta_confirmed=?,updated_at=? WHERE id=?',(customer,shipment,eta,int(confirmed and bool(eta)),now(),oid))
     result=get_order(db,oid);db.commit();return self.send(200,result)
    if path=='/api/orders/payment':
     oid=positive_int(data.get('order_id'),'order_id');get_order(db,oid)
     if not PAYMENT_HOSTS: raise Problem(503,'Платёжный сервис не настроен. Реальные ссылки недоступны.')
     link=https_url(required_text(data,'url',2000),PAYMENT_HOSTS)
     db.execute('UPDATE orders SET payment_url=?,updated_at=? WHERE id=?',(link,now(),oid))
     result=get_order(db,oid);db.commit();return self.send(200,result)
    raise Problem(404,'Метод не найден.')
  except Problem as exc: self.send(exc.status,{'error':exc.message})
  except (ValueError,TypeError): self.send(400,{'error':'Проверьте значения полей.'})
  except Exception:
   # Avoid leaking database and environment details to clients.
   self.send(500,{'error':'Не удалось выполнить запрос. Повторите позже.'})

if __name__=='__main__':
 initialize(DB,DEMO)
 print(f'MAJLIS {"DEMO" if DEMO else "LIVE"}: http://{HOST}:{os.getenv("PORT","8765")}',flush=True)
 ThreadingHTTPServer((HOST,int(os.getenv('PORT','8765'))),Handler).serve_forever()

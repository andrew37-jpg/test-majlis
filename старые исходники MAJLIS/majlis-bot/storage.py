import json, sqlite3, datetime
from pathlib import Path
from domain import STAGE_MAP, Problem, positive_int, required_text

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')

def connect(path):
 db=sqlite3.connect(path,timeout=20)
 db.row_factory=sqlite3.Row
 db.execute('PRAGMA foreign_keys=ON')
 db.execute('PRAGMA journal_mode=WAL')
 return db

def initialize(path,demo=False):
 Path(path).parent.mkdir(parents=True,exist_ok=True)
 with connect(path) as db:
  db.executescript('''
  CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,number TEXT NOT NULL UNIQUE,customer_id INTEGER NOT NULL,customer_name TEXT NOT NULL,item TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),shipment TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,eta TEXT NOT NULL DEFAULT '',eta_confirmed INTEGER NOT NULL DEFAULT 0,payment_url TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES orders(id),status TEXT NOT NULL,note TEXT NOT NULL,source TEXT NOT NULL,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL UNIQUE REFERENCES events(id),customer_id INTEGER NOT NULL,text TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '');
  CREATE TABLE IF NOT EXISTS integration_events(external_id TEXT PRIMARY KEY,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS bot_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
  ''')
  mode='demo' if demo else 'live'
  old=db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
  if old and old['value']!=mode: raise RuntimeError('Demo и live требуют разных баз данных.')
  db.execute("INSERT OR IGNORE INTO metadata VALUES ('mode',?)",(mode,))
  if demo and not db.execute('SELECT 1 FROM orders').fetchone():
   for n,item,amount,st,eta in [('MJ-DEMO-1042','MacBook Air 13″ · 16 / 512 ГБ',12990000,'in_transit','2026-10-28'),('MJ-DEMO-1043','PlayStation 5 · Slim',6290000,'confirmed','')]:
    order=create_order(db,{'number':n,'customer_id':1001,'customer_name':'Андрей · демо','item':item,'amount':amount,'shipment':'DEMO-PALLET-01' if st=='in_transit' else '', 'eta':eta})
    if st!='confirmed': change_status(db,order['id'],st,'Тестовое событие доставки','demo')

def order_json(db,row):
 obj=dict(row)
 obj['stage']=STAGE_MAP[obj['status']]
 obj['events']=[dict(r) for r in db.execute('SELECT * FROM events WHERE order_id=? ORDER BY id',(obj['id'],))]
 return obj

def get_order(db,order_id):
 row=db.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
 if not row: raise Problem(404,'Заказ не найден.')
 return order_json(db,row)

def create_order(db,data):
 number=required_text(data,'number',64)
 customer=positive_int(data.get('customer_id'),'customer_id')
 amount=positive_int(data.get('amount'),'amount')
 name=required_text(data,'customer_name',100)
 item=required_text(data,'item',300)
 shipment=data.get('shipment','')
 eta=data.get('eta','')
 if not isinstance(eta,str): raise Problem(400,'Проверьте дату поставки.')
 if not isinstance(shipment,str) or len(shipment)>100: raise Problem(400,'Проверьте номер партии.')
 if eta:
  try: datetime.date.fromisoformat(eta)
  except (ValueError,TypeError): raise Problem(400,'Проверьте дату поставки.')
 stamp=now()
 try:
  cur=db.execute('INSERT INTO orders(number,customer_id,customer_name,item,amount,shipment,status,eta,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',(number,customer,name,item,amount,shipment,'confirmed',eta,stamp,stamp))
 except sqlite3.IntegrityError: raise Problem(409,'Такой номер заказа уже существует.')
 db.execute('INSERT INTO events(order_id,status,note,source,created_at) VALUES (?,?,?,?,?)',(cur.lastrowid,'confirmed','Заказ зарегистрирован','manager',stamp))
 return get_order(db,cur.lastrowid)

def change_status(db,order_id,status,note,source,notify=True):
 if status not in STAGE_MAP: raise Problem(400,'Неизвестный этап доставки.')
 old=get_order(db,order_id)
 if old['status']==status: return old
 stamp=now()
 db.execute('UPDATE orders SET status=?,updated_at=? WHERE id=?',(status,stamp,order_id))
 cur=db.execute('INSERT INTO events(order_id,status,note,source,created_at) VALUES (?,?,?,?,?)',(order_id,status,note,source,stamp))
 stage=STAGE_MAP[status]
 message=f"Заказ {old['number']}\n{stage['title']}\n{stage['description']}"
 if notify: db.execute('INSERT INTO outbox(event_id,customer_id,text) VALUES (?,?,?)',(cur.lastrowid,old['customer_id'],message))
 return get_order(db,order_id)

def integration_event(db,data):
 event_id=required_text(data,'event_id',128)
 shipment=required_text(data,'shipment',100)
 status=required_text(data,'status',32)
 if status not in STAGE_MAP: raise Problem(400,'Неизвестный этап доставки.')
 if db.execute('SELECT 1 FROM integration_events WHERE external_id=?',(event_id,)).fetchone(): return {'duplicate':True,'updated':0}
 orders=db.execute('SELECT id FROM orders WHERE shipment=?',(shipment,)).fetchall()
 if not orders: raise Problem(404,'Партия не найдена.')
 db.execute('INSERT INTO integration_events VALUES (?,?)',(event_id,now()))
 for row in orders: change_status(db,row['id'],status,'Обновление от внешней системы','integration')
 return {'duplicate':False,'updated':len(orders)}

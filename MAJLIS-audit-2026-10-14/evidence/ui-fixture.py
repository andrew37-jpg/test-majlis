"""Temporary localhost-only visual fixture. Does not connect to Telegram or real DB."""
import json,sys,datetime
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
sys.path.insert(0,str(Path('work/majlis-audit/source').resolve()))
from majlis.domain import STAGES,STAGE_MAP
ROOT=Path('work/majlis-audit/source/web').resolve()
orders=[]
def event(oid,status,note='Тестовое событие'):
 return {'id':oid,'order_id':oid,'status':status,'note':note,'source':'manager','created_at':'2026-10-14T15:00:00+00:00'}
for oid,number,item,amount,status,eta,confirmed in [(1,'MJ-1042','MacBook Air 13″ · 16 / 512 ГБ',12990000,'in_transit','2026-10-28',False),(2,'MJ-1043','PlayStation 5 · Slim',6290000,'confirmed','',False),(3,'MJ-1030','iPhone 16 · 256 ГБ',8990000,'delivered','2026-10-10',True)]:
 orders.append({'id':oid,'number':number,'customer_id':3001,'customer_name':'Тестовый покупатель','item':item,'amount':amount,'status':status,'stage':STAGE_MAP[status],'eta':eta,'eta_confirmed':confirmed,'shipment':'TEST-PALLET','payment_url':'','version':1,'created_at':'2026-10-10T12:00:00+00:00','updated_at':'2026-10-14T15:00:00+00:00','events':[event(oid,'confirmed')]+([event(oid+10,status)] if status!='confirmed' else [])})
template=orders[0]
orders=[{**template,'id':i,'number':f'AUDIT-{i:03}','item':('<img src=x onerror="alert(1)">' if i==105 else 'Тестовый товар'),'events':[]} for i in range(105,0,-1)]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,data,code=200):
  raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');
  if getattr(self,'clear_conflict',False):self.send_header('Set-Cookie','qa_conflict=; Path=/; Max-Age=0')
  self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  parts=urlsplit(self.path);q=parse_qs(parts.query)
  if parts.path=='/api/bootstrap':
   role='manager' if 'qa_role=manager' in self.headers.get('Cookie','') else 'buyer'
   all_orders=[] if 'qa_role=empty' in self.headers.get('Cookie','') else orders
   rows=[o for o in all_orders if (q.get('bucket',['all'])[0]=='all' or (q['bucket'][0]=='delivered')==(o['status']=='delivered')) and q.get('q',[''])[0].lower() in ' '.join(str(o.get(f,'')) for f in ['number','item','shipment','customer_name']).lower()]
   matching=len(rows)
   if 'before' in q: rows=[o for o in rows if o['id']<int(q['before'][0])]
   more=len(rows)>100;rows=rows[:100]
   return self.send({'user':{'id':9001 if role=='manager' else 3001,'first_name':'Тестовый менеджер' if role=='manager' else 'Андрей · тест','role':role},'demo':True,'stages':[{'code':s[0],'title':s[1],'description':s[2]} for s in STAGES],'orders':rows,'summary':{'total':len(all_orders),'active':sum(o['status']!='delivered' for o in all_orders),'delivered':sum(o['status']=='delivered' for o in all_orders),'matching':matching},'next_cursor':rows[-1]['id'] if more else None,'updated_at':'2026-10-14T15:00:00+00:00','links':{'catalog':'','support':''},'integrations':{'cargo':'not_connected','delivery':'not_connected','website':'not_connected','payments':'not_connected'},'notifications':[]})
  if parts.path=='/qa-sdk.js':
   theme='dark' if 'qa_theme=dark' in self.headers.get('Cookie','') else 'light'
   raw=("window.Telegram={WebApp:{colorScheme:"+json.dumps(theme)+",initData:'',ready(){},expand(){},onEvent(){},setHeaderColor(){},setBackgroundColor(){},enableClosingConfirmation(){},disableClosingConfirmation(){}}};").encode()
   self.send_response(200);self.send_header('Content-Type','text/javascript');self.end_headers();self.wfile.write(raw);return
  if parts.path.startswith('/api/orders/'):
   oid=int(parts.path.rsplit('/',1)[1]);return self.send(next(o for o in orders if o['id']==oid))
  names={'/':'index.html'  ,'/app.js':'app.js','/ui.js':'ui.js','/style.css':'style.css'}
  if parts.path not in names:return self.send({'error':'Not found'},404)
  name=names[parts.path];self.send_response(200);self.send_header('Content-Type','text/html' if name.endswith('.html') else 'text/javascript' if name.endswith('.js') else 'text/css')
  if parts.path=='/' and 'role' in q:self.send_header('Set-Cookie','qa_role='+q['role'][0]+'; Path=/; SameSite=Strict')
  if parts.path=='/' and 'theme' in q:self.send_header('Set-Cookie','qa_theme='+q['theme'][0]+'; Path=/; SameSite=Strict')
  if parts.path=='/' and 'conflict' in q:self.send_header('Set-Cookie','qa_conflict=once; Path=/; SameSite=Strict')
  self.end_headers();raw=(ROOT/name).read_bytes()
  if name=='index.html':raw=raw.replace(b'https://telegram.org/js/telegram-web-app.js',b'/qa-sdk.js')
  self.wfile.write(raw)
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  if self.path=='/api/orders':
   oid=max(o['id'] for o in orders)+1;order={**data,'id':oid,'status':'confirmed','stage':STAGE_MAP['confirmed'],'version':1,'eta_confirmed':False,'payment_url':'','events':[event(oid,'confirmed')]};orders.append(order);return self.send(order,201)
  order=next(o for o in orders if o['id']==data['order_id'])
  if 'qa_conflict=once' in self.headers.get('Cookie',''):
   order['version']+=1;order['status']='warehouse';order['stage']=STAGE_MAP['warehouse'];self.clear_conflict=True
  if data['version']!=order['version']:return self.send({'error':'Версия устарела'},409)
  for key,value in data.items():
   if key not in ('order_id','version','note'):order[key]=value
  if 'status' in data:order['stage']=STAGE_MAP[data['status']];order['events'].append(event(100+len(order['events']),data['status'],data.get('note','')))
  order['version']+=1
  print('QA mutation:',self.path,'version',data['version'],flush=True);self.send(order)
print('Visual QA localhost:8892 (fictional data)',flush=True)
ThreadingHTTPServer(('127.0.0.1',8892),Handler).serve_forever()

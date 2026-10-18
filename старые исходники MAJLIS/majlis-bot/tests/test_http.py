"""Real HTTP checks, each server uses a separate disposable database."""
import unittest,tempfile,subprocess,sys,os,time,json,hmac,hashlib,socket,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[1]
TOKEN='fixture-token-not-a-real-bot'

def signed(uid):
 d={'auth_date':str(int(time.time())),'user':json.dumps({'id':uid,'first_name':'Fixture'})}
 key=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
 d['hash']=hmac.new(key,'\n'.join(f'{k}={v}' for k,v in sorted(d.items())).encode(),hashlib.sha256).hexdigest()
 return urlencode(d)

class HTTPTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory()
  with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
  cls.url=f'http://127.0.0.1:{port}'
  env=dict(os.environ,APP_MODE='live',BOT_TOKEN=TOKEN,MANAGER_IDS='9001',DB_PATH=str(Path(cls.tmp.name)/'live.sqlite3'),HOST='127.0.0.1',PORT=str(port),INTEGRATION_SECRET='fixture-secret-32-characters-long!',PAYMENT_HOSTS='payments.example')
  cls.process=subprocess.Popen([sys.executable,str(ROOT/'server.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  for i in range(100):
   try:urllib.request.urlopen(cls.url+'/health',timeout=1);break
   except (urllib.error.URLError,TimeoutError):time.sleep(.05)
  else:raise RuntimeError('Test HTTP server failed to start')
 @classmethod
 def tearDownClass(cls):cls.process.terminate();cls.process.wait(timeout=5);cls.tmp.cleanup()
 def req(self,path,data=None,uid=9001,extra=None):
  headers={'Content-Type':'application/json','X-Telegram-Init-Data':signed(uid)}
  headers.update(extra or {})
  r=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
  try:
   with urllib.request.urlopen(r) as response:return response.status,json.load(response)
  except urllib.error.HTTPError as exc:return exc.code,json.load(exc)
 def test_full_workflow_and_isolation(self):
  status,o=self.req('/api/orders',{'number':'HTTP-1','customer_id':1001,'customer_name':'Fixture','item':'Fixture product','amount':7990000,'shipment':'HTTP-PALLET'})
  self.assertEqual(status,201);oid=o['id']
  self.assertEqual(self.req('/api/bootstrap',uid=1001)[1]['orders'][0]['number'],'HTTP-1')
  self.assertEqual(self.req('/api/bootstrap',uid=1002)[1]['orders'],[])
  self.assertEqual(self.req('/api/orders/status',{'order_id':oid,'status':'warehouse'},uid=1001)[0],403)
  self.assertEqual(self.req('/api/orders/status',{'order_id':oid,'status':'warehouse'})[0],200)
  buyer=self.req('/api/bootstrap',uid=1001)[1]
  self.assertEqual(buyer['orders'][0]['status'],'warehouse');self.assertNotIn('notifications',buyer)
  self.assertEqual(self.req('/api/orders/details',{'order_id':oid,'customer_id':1001,'shipment':'HTTP-PALLET','eta':'2026-11-01','eta_confirmed':True})[0],200)
  self.assertTrue(self.req('/api/bootstrap',uid=1001)[1]['orders'][0]['eta_confirmed'])
  self.assertEqual(self.req('/api/orders/payment',{'order_id':oid,'url':'https://evil.example/payment'})[0],400)
  self.assertEqual(self.req('/api/orders/payment',{'order_id':oid,'url':'https://payments.example/payment'})[0],200)
  # Signed cargo update and repeated event: only one new notification.
  event={'event_id':'http-event-1','shipment':'HTTP-PALLET','status':'last_mile'}
  raw=json.dumps(event).encode();stamp=str(int(time.time()))
  signature=hmac.new(b'fixture-secret-32-characters-long!',stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
  headers={'X-Event-Timestamp':stamp,'X-Event-Signature':signature}
  self.assertEqual(self.req('/api/integrations/events',event,extra=headers)[1]['updated'],1)
  self.assertTrue(self.req('/api/integrations/events',event,extra=headers)[1]['duplicate'])
  self.assertEqual(len(self.req('/api/bootstrap')[1]['notifications']),2)
 def test_unsigned_login_and_demo_spoof_rejected(self):
  status,_=self.req('/api/bootstrap',extra={'X-Telegram-Init-Data':'','X-Demo-User':'manager'})
  self.assertEqual(status,401)
 def test_integration_bad_signature(self):
  status,_=self.req('/api/integrations/events',{'event_id':'bad','shipment':'test','status':'warehouse'},extra={'X-Event-Timestamp':str(int(time.time())),'X-Event-Signature':'fake'})
  self.assertEqual(status,401)
 def test_nonmanager_creation_denied(self):
  self.assertEqual(self.req('/api/orders',{},uid=1002)[0],403)

if __name__=='__main__':unittest.main()

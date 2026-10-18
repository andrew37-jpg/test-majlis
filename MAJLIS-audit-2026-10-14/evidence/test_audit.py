"""Audit reproduction cases on the isolated majlis_audit_*_test database only."""
import json,hashlib,hmac,time,threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from psycopg.errors import LockNotAvailable
from test_platform import signed,payload,created,queued
from majlis.bot import deliver

def test_audit_reassignment_retains_previous_personal_fields(client,cfg):
 order=created(client,cfg)
 assert client.post('/api/orders/payment',headers=signed(cfg),json={'order_id':order['id'],'version':1,'url':'https://payments.example/invoice/old-buyer'}).status_code==200
 assert client.post('/api/orders/status',headers=signed(cfg),json={'order_id':order['id'],'version':2,'status':'purchased','note':'Private note for previous customer (fictional)'}).status_code==200
 assert client.post('/api/orders/details',headers=signed(cfg),json={'order_id':order['id'],'version':3,'customer_id':4001,'shipment':'NEW-PARTY','eta':'','eta_confirmed':False}).status_code==200
 new=client.get('/api/orders/'+str(order['id']),headers=signed(cfg,4001))
 assert new.status_code==200
 data=new.json()
 assert data['customer_name']==payload()['customer_name']
 assert data['payment_url']=='https://payments.example/invoice/old-buyer'
 assert any('previous customer' in e['note'] for e in data['events'])
 assert client.get('/api/orders/'+str(order['id']),headers=signed(cfg,3001)).status_code==404
 print('AUDIT_PRIVACY: new owner receives old customer name/payment link/history; previous owner is denied')

def test_audit_late_integration_can_regress_delivered_order(client,cfg):
 order=created(client,cfg)
 def send(event,status):
  raw=json.dumps({'event_id':event,'shipment':'TEST-CARGO','status':status}).encode();stamp=str(int(time.time()))
  signature=hmac.new(cfg.integration_secret.get_secret_value().encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
  return client.post('/api/integrations/events',content=raw,headers={'Content-Type':'application/json','X-Event-Timestamp':stamp,'X-Event-Signature':signature})
 assert send('NEW-DELIVERED','delivered').status_code==200
 assert send('LATE-CUSTOMS','customs').status_code==200
 assert client.get('/api/orders/'+str(order['id']),headers=signed(cfg,3001)).json()['status']=='customs'
 print('AUDIT_STATUS: valid later request can regress delivered -> customs')

def test_audit_ready_has_no_application_rate_limit(client):
 results=[client.get('/ready').status_code for _ in range(121)]
 assert set(results)=={200}
 print('AUDIT_RATE: 121 unauthenticated readiness queries all returned 200; no stress test performed')

def test_audit_signed_identity_is_replayable(client,cfg):
 headers=signed(cfg,3001)
 assert client.get('/api/bootstrap',headers=headers).status_code==200
 assert client.get('/api/bootstrap',headers=headers).status_code==200
 print('AUDIT_REPLAY: identical signed initData accepted twice within validity window')

def test_audit_sql_injection_and_mass_assignment_denied(client,cfg):
 order=created(client,cfg)
 assert client.get('/api/bootstrap',headers=signed(cfg,3002),params={'q':"' OR 1=1 --"}).json()['orders']==[]
 data=payload();data['number']='MJ-ATTEMPT';data['role']='manager'
 assert client.post('/api/orders',headers=signed(cfg),json=data).status_code==422
 assert client.post('/api/orders/details',headers=signed(cfg,3002),json={'order_id':order['id'],'version':1,'customer_id':3002,'shipment':'','eta':'','eta_confirmed':False}).status_code==403


def test_audit_outbox_network_send_holds_order_lock(db):
 order=queued(db);started=threading.Event();release=threading.Event()
 class SlowAPI:
  def send(self,*args):started.set();assert release.wait(3)
 with ThreadPoolExecutor(max_workers=1) as executor:
  task=executor.submit(deliver,db,SlowAPI())
  try:
   assert started.wait(2)
   with pytest.raises(LockNotAvailable):
    with db.connection() as conn:conn.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE NOWAIT',(order['id'],))
   print('AUDIT_LOCK: order row locked throughout simulated external network send')
  finally:release.set()
  task.result(timeout=4)


def test_audit_mutations_exist_without_csrf_cookie(client,cfg):
 assert client.post('/api/orders',json=payload()).status_code==401
 assert client.get('/api/bootstrap',headers={'X-Telegram-Init-Data':'user=forged&auth_date=1&hash=bad'}).status_code==401
 assert client.post('/api/orders',headers=signed(cfg,3001),json=payload()).status_code==403


def test_audit_error_headers_not_consistent(client):
 response=client.post('/api/orders',json={'data':'a'*70000})
 assert response.status_code==413
 assert 'content-security-policy' not in response.headers
 assert 'cache-control' not in response.headers
 print('AUDIT_HEADERS: early 413 response omits common security/cache headers')

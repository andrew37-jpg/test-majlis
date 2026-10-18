import unittest,tempfile,json,time,hmac,hashlib,csv
from pathlib import Path
from urllib.parse import urlencode
from domain import validate_init_data,Problem,https_url
from storage import initialize,connect,create_order,change_status,integration_event
from migrate import import_csv

class SecurityTests(unittest.TestCase):
 def signed(self,age=0):
  d={'auth_date':str(int(time.time())-age),'user':json.dumps({'id':111,'first_name':'Test'}),'query_id':'abc'}
  key=hmac.new(b'WebAppData',b'test-token',hashlib.sha256).digest()
  d['hash']=hmac.new(key,'\n'.join(f'{k}={v}' for k,v in sorted(d.items())).encode(),hashlib.sha256).hexdigest()
  return urlencode(d)
 def test_auth_valid(self):self.assertEqual(validate_init_data(self.signed(),'test-token')['id'],111)
 def test_auth_tampering(self):
  with self.assertRaises(Problem):validate_init_data(self.signed().replace('abc','forged'),'test-token')
 def test_expired(self):
  with self.assertRaises(Problem):validate_init_data(self.signed(4000),'test-token')
 def test_future(self):
  with self.assertRaises(Problem):validate_init_data(self.signed(-500),'test-token')
 def test_duplicate_field(self):
  with self.assertRaises(Problem):validate_init_data(self.signed()+'&user=other','test-token')
 def test_payment_host(self):
  self.assertEqual(https_url('https://payments.example/pay/1',{'payments.example'}),'https://payments.example/pay/1')
  for url in ['http://payments.example/pay','https://payments.example.evil/pay','https://payments.example@evil.test/pay','https://payments.example:8000/pay','javascript:alert(1)']:
   with self.subTest(url=url),self.assertRaises(Problem):https_url(url,{'payments.example'})

class StorageTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'test.sqlite3');initialize(self.path,False)
 def tearDown(self):self.tmp.cleanup()
 def data(self,n='TEST-1',uid=111):return {'number':n,'customer_id':uid,'customer_name':'Test','item':'Device','amount':10000,'shipment':'PALLET-A'}
 def test_duplicate_order(self):
  with connect(self.path) as db:
   create_order(db,self.data())
   with self.assertRaises(Problem):create_order(db,self.data())
 def test_status_no_duplicate_notification(self):
  with connect(self.path) as db:
   o=create_order(db,self.data());change_status(db,o['id'],'in_transit','','manager');change_status(db,o['id'],'in_transit','','manager')
   self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
 def test_cargo_batch_and_replay(self):
  with connect(self.path) as db:
   create_order(db,self.data());create_order(db,self.data('TEST-2',222))
   event={'event_id':'evt-1','shipment':'PALLET-A','status':'warehouse'}
   self.assertEqual(integration_event(db,event)['updated'],2)
   self.assertTrue(integration_event(db,event)['duplicate'])
   self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],2)
 def test_unknown_shipment_does_not_consume_event(self):
  with connect(self.path) as db:
   with self.assertRaises(Problem):integration_event(db,{'event_id':'evt-1','shipment':'missing','status':'warehouse'})
   self.assertEqual(db.execute('SELECT COUNT(*) FROM integration_events').fetchone()[0],0)
 def test_customer_isolation_query(self):
  with connect(self.path) as db:
   create_order(db,self.data());create_order(db,self.data('TEST-2',222))
   rows=db.execute('SELECT * FROM orders WHERE customer_id=?',(111,)).fetchall()
   self.assertEqual([r['number'] for r in rows],['TEST-1'])
 def test_demo_database_cannot_be_live(self):
  with self.assertRaises(RuntimeError):initialize(self.path,True)
 def test_invalid_amount(self):
  with connect(self.path) as db:
   for amount in [0,-1,True,1.5,'nan']:
    d=self.data();d['amount']=amount
    with self.subTest(amount=amount),self.assertRaises(Problem):create_order(db,d)
 def write_csv(self,rows):
  file=Path(self.tmp.name)/'import.csv'
  with file.open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=['number','customer_id','customer_name','item','amount','status','shipment','eta']);w.writeheader();w.writerows(rows)
  return str(file)
 def test_import_dry_run_then_apply_and_repeat(self):
  row=self.data();row.update(status='in_transit',eta='');f=self.write_csv([row])
  self.assertEqual(import_csv(f,self.path)['new_orders'],1)
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],0)
  import_csv(f,self.path,apply=True)
  self.assertEqual(import_csv(f,self.path,apply=True)['unchanged_orders'],1)
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
 def test_import_atomic_rollback(self):
  a=self.data();a.update(status='confirmed',eta='');b=self.data('TEST-2');b.update(status='invalid',eta='');f=self.write_csv([a,b])
  with self.assertRaises(Problem):import_csv(f,self.path,apply=True)
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],0)

if __name__=='__main__':unittest.main()

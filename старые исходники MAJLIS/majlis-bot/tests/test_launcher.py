import unittest,tempfile,os,subprocess,json
from pathlib import Path
from unittest.mock import patch,MagicMock
import main
from tunnel import QuickTunnel,TunnelError

class LauncherTests(unittest.TestCase):
 def test_missing_token_stops_before_processes(self):
  with patch.dict(os.environ,{},clear=True),self.assertRaises(main.StartupError):main.build_environment({})
 def test_live_environment_uses_separate_database(self):
  with patch.dict(os.environ,{},clear=True):env=main.build_environment({'BOT_TOKEN':'fixture-token'})
  self.assertEqual(env['APP_MODE'],'live');self.assertTrue(env['DB_PATH'].endswith('telegram.sqlite3'));self.assertEqual(env['HOST'],'127.0.0.1')
 def test_bad_port_and_manager(self):
  for settings in [{'PORT':'0'},{'PORT':'65536'},{'MANAGER_IDS':'abc'},{'MANAGER_IDS':'-1'}]:
   with self.subTest(settings=settings),patch.dict(os.environ,{},clear=True),self.assertRaises(main.StartupError):main.build_environment(dict(settings,BOT_TOKEN='fixture'))
 def test_external_url_must_be_public_https(self):
  for url in ['http://example.com','https://localhost','']:
   with self.subTest(url=url),patch.dict(os.environ,{},clear=True),self.assertRaises(main.StartupError):main.build_environment({'BOT_TOKEN':'fixture','HTTPS_MODE':'external','MINI_APP_URL':url})
 def test_os_environment_overrides_file(self):
  with patch.dict(os.environ,{'PORT':'8899'},clear=True):env=main.build_environment({'BOT_TOKEN':'fixture','PORT':'8875'})
  self.assertEqual(env['PORT'],'8899')
 def test_lock_is_released(self):
  with tempfile.TemporaryDirectory() as tmp:
   a=main.RunLock(Path(tmp)/'run.lock');b=main.RunLock(Path(tmp)/'run.lock')
   a.acquire()
   with self.assertRaises(main.StartupError):b.acquire()
   a.release();b.acquire();b.release()
 def test_config_from_any_working_directory(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'settings.env';path.write_text('# comment\nBOT_TOKEN="fixture"\nPORT=8875\n')
   self.assertEqual(main.read_settings(path)['BOT_TOKEN'],'fixture')
 def test_shutdown_cleans_children_and_tunnel(self):
  class FakeProcess:
   def __init__(self):self.returncode=None;self.stopped=False
   def poll(self):return self.returncode
   def terminate(self):self.stopped=True;self.returncode=0
   def wait(self,timeout=0):return self.returncode
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True):
   config=Path(tmp)/'settings.env';config.write_text(f'BOT_TOKEN=fixture\nDB_PATH={tmp}/db.sqlite3\n')
   server=FakeProcess();worker=FakeProcess();tunnel_process=FakeProcess()
   tunnel=MagicMock();tunnel.start.return_value='https://fixture.trycloudflare.com';tunnel.process=tunnel_process
   with patch.object(main.subprocess,'Popen',side_effect=[server,worker]),patch.object(main,'await_server'),patch.object(main,'await_public'),patch.object(main,'QuickTunnel',return_value=tunnel),patch.object(main.time,'sleep',side_effect=KeyboardInterrupt):
    self.assertEqual(main.run(['--config',str(config)]),0)
   self.assertTrue(server.stopped);self.assertTrue(worker.stopped);tunnel.close.assert_called_once()
 def test_tunnel_failure_cleans_server(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True):
   config=Path(tmp)/'settings.env';config.write_text(f'BOT_TOKEN=fixture\nDB_PATH={tmp}/db.sqlite3\n')
   server=MagicMock();server.poll.return_value=None
   tunnel=MagicMock();tunnel.start.side_effect=TunnelError('fixture failure')
   with patch.object(main.subprocess,'Popen',return_value=server),patch.object(main,'await_server'),patch.object(main,'QuickTunnel',return_value=tunnel):self.assertEqual(main.run(['--config',str(config)]),1)
   server.terminate.assert_called_once();tunnel.close.assert_called_once()
 def test_public_routes_to_same_live_instance(self):
  response=MagicMock();response.__enter__.return_value=response;response.read.return_value=json.dumps({'mode':'demo','instance_id':'other'}).encode()
  with patch.object(main.urllib.request,'urlopen',return_value=response),self.assertRaises(main.StartupError):main.await_public('https://fixture.example','expected')


class TLSChecks(unittest.TestCase):
 def test_certificate_and_hostname_checks_are_enabled(self):
  import ssl
  from network import ssl_context
  context=ssl_context()
  self.assertTrue(context.check_hostname)
  self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
  self.assertGreaterEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)

if __name__=='__main__':unittest.main()

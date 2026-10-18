import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import bot
from storage import initialize,connect,create_order,change_status

class BotTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=str(Path(self.tmp.name)/'bot.sqlite3');initialize(self.db,False)
  self.db_patch=patch.object(bot,'DB',self.db);self.db_patch.start()
  with connect(self.db) as db:self.order=create_order(db,{'number':'BOT-1','customer_id':111,'customer_name':'Fixture','item':'Device','amount':10000})
 def tearDown(self):self.db_patch.stop();self.tmp.cleanup()
 def test_status_only_own_orders_and_ignore_groups(self):
  with patch.object(bot,'call') as api:
   bot.respond({'from':{'id':222},'chat':{'type':'private','id':222},'text':'/status'},'https://app.example')
   self.assertNotIn('BOT-1',api.call_args.args[1]['text'])
   api.reset_mock();bot.respond({'from':{'id':111},'chat':{'type':'group','id':555},'text':'/status'},'https://app.example');api.assert_not_called()
 def test_notification_sent_and_not_repeated(self):
  with connect(self.db) as db:change_status(db,self.order['id'],'purchased','','manager')
  with patch.object(bot,'call',return_value={} ) as api:
   bot.deliver();bot.deliver();self.assertEqual(api.call_count,1)
  with connect(self.db) as db:self.assertEqual(db.execute('SELECT state FROM outbox').fetchone()[0],'sent')
 def test_notification_transient_failure_retry(self):
  with connect(self.db) as db:change_status(db,self.order['id'],'purchased','','manager')
  with patch.object(bot,'call',side_effect=bot.TelegramFailure(0)):bot.deliver()
  with connect(self.db) as db:
   row=db.execute('SELECT * FROM outbox').fetchone();self.assertEqual(row['state'],'pending');self.assertEqual(row['attempts'],1);self.assertGreater(row['next_attempt'],0)
 def test_blocked_bot_marks_notification_failed(self):
  with connect(self.db) as db:change_status(db,self.order['id'],'purchased','','manager')
  with patch.object(bot,'call',side_effect=bot.TelegramFailure(403)):bot.deliver()
  with connect(self.db) as db:self.assertEqual(db.execute('SELECT state FROM outbox').fetchone()[0],'failed')


class BotSetupTests(unittest.TestCase):
 def test_configure_checks_bot_and_adds_menu(self):
  with patch.object(bot,'call',side_effect=[{'username':'fixture_bot'},{'url':''},True,True]) as api:
   result=bot.configure('https://app.example')
  self.assertEqual(result['username'],'fixture_bot')
  self.assertEqual([x.args[0] for x in api.call_args_list],['getMe','getWebhookInfo','setMyCommands','setChatMenuButton'])
  self.assertEqual(api.call_args_list[-1].args[1]['menu_button']['web_app']['url'],'https://app.example')
 def test_configure_does_not_replace_existing_webhook(self):
  with patch.object(bot,'call',side_effect=[{'username':'fixture_bot'},{'url':'https://existing.example/webhook'}]) as api,self.assertRaises(RuntimeError):bot.configure('https://app.example')
  self.assertEqual(api.call_count,2)
 def test_id_command_works_before_manager_configuration(self):
  with patch.object(bot,'call') as api:bot.respond({'from':{'id':123},'chat':{'type':'private','id':123},'text':'/id'},'https://app.example')
  self.assertIn('123',api.call_args.args[1]['text'])
 def test_manager_command_denies_unassigned_user(self):
  with patch.object(bot,'MANAGERS',{999}),patch.object(bot,'call') as api:bot.respond({'from':{'id':123},'chat':{'type':'private','id':123},'text':'/manager'},'https://app.example')
  self.assertIn('не назначен',api.call_args.args[1]['text'])
 def test_non_text_message_does_not_crash(self):
  with patch.object(bot,'call') as api:bot.respond({'from':{'id':123},'chat':{'type':'private','id':123},'photo':[{}]},'https://app.example')
  self.assertEqual(api.call_count,1)

if __name__=='__main__':unittest.main()

import os,pytest
from pydantic import SecretStr
from majlis.config import Settings
from majlis.database import Database
@pytest.fixture(scope='session')
def test_database_url():
 from urllib.parse import urlsplit,urlunsplit
 parts=urlsplit(Settings().database_url.get_secret_value())
 value=os.getenv('TEST_DATABASE_URL') or urlunsplit(parts._replace(path='/majlis_test'))
 if not urlsplit(value).path.endswith('_test'):raise RuntimeError('Нужна отдельная база с суффиксом _test.')
 return value
@pytest.fixture
def cfg(monkeypatch,test_database_url):
 value=Settings();value.database_url=SecretStr(test_database_url);value.bot_token=SecretStr('123456789:'+('a'*40));value.manager_ids='9001';value.payment_hosts='payments.example';value.integration_secret=SecretStr('b'*40);value.instance_id='test-instance'
 for module in ('database','api','bot'):monkeypatch.setattr('majlis.'+module+'.settings',lambda:value)
 return value
@pytest.fixture
def db(cfg):
 database=Database();database.open();database.migrate()
 with database.connection() as conn:conn.execute('TRUNCATE audit_log,outbox,events,orders,integration_events,bot_state,rate_limits,users RESTART IDENTITY CASCADE')
 yield database
 database.close()
@pytest.fixture
def client(db,cfg):
 from fastapi.testclient import TestClient
 from majlis.api import app
 with TestClient(app) as client:yield client

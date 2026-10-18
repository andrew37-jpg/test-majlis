import hashlib
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from .config import settings, ROOT

class Database:
    def __init__(self, url=None):
        self.pool = ConnectionPool(
            conninfo=url or settings().database_url.get_secret_value(),
            min_size=1, max_size=settings().db_pool_size, timeout=10,
            kwargs={'row_factory': dict_row, 'connect_timeout': 5}, open=False,
        )
    def open(self):
        self.pool.open()
        self.pool.wait(timeout=20)
    def close(self):
        self.pool.close()
    @contextmanager
    def connection(self):
        with self.pool.connection() as connection:
            yield connection
    def migrate(self):
        with self.connection() as db:
            db.execute('SELECT pg_advisory_xact_lock(742019143)')
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            for path in sorted((ROOT / 'migrations').glob('*.sql')):
                sql = path.read_text(encoding='utf-8')
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                old = db.execute('SELECT checksum FROM schema_migrations WHERE name=%s', (path.name,)).fetchone()
                if old:
                    if old['checksum'] != checksum:
                        raise RuntimeError('Изменена применённая миграция. Создайте новую миграцию.')
                    continue
                db.execute(sql)
                db.execute('INSERT INTO schema_migrations(name,checksum) VALUES (%s,%s)', (path.name, checksum))

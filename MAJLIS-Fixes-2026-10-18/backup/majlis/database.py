import hashlib
from contextlib import contextmanager

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import ROOT, settings


class Database:
    def __init__(self, url=None):
        self.url = url or settings().database_url.get_secret_value()
        self.pool = ConnectionPool(
            conninfo=self.url,
            min_size=1,
            max_size=settings().db_pool_size,
            timeout=10,
            kwargs={"row_factory": dict_row, "connect_timeout": 5},
            open=False,
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
        import psycopg

        migration_url = settings().migration_database_url.get_secret_value()
        if not migration_url:
            raise RuntimeError("Задайте MIGRATION_DATABASE_URL с отдельной ролью мигратора.")
        with psycopg.connect(migration_url, row_factory=dict_row) as db:
            db.execute("SELECT pg_advisory_xact_lock(742019143)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            for path in sorted((ROOT / "migrations").glob("*.sql")):
                migration_sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(migration_sql.encode()).hexdigest()
                old = db.execute(
                    "SELECT checksum FROM schema_migrations WHERE name=%s", (path.name,)
                ).fetchone()
                if old:
                    if old["checksum"] != checksum:
                        raise RuntimeError(
                            "Изменена применённая миграция. Создайте новую миграцию."
                        )
                    continue
                db.execute(migration_sql)
                db.execute(
                    "INSERT INTO schema_migrations(name,checksum) VALUES (%s,%s)",
                    (path.name, checksum),
                )
            runtime = conninfo_to_dict(self.url).get("user")
            owner = db.execute("SELECT current_user AS name").fetchone()["name"]
            if not runtime or runtime == owner:
                raise RuntimeError("Runtime и мигратор должны быть разными ролями.")
            role = sql.Identifier(runtime)
            db.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role))
            db.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(role))
            db.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            db.execute(
                sql.SQL(
                    "GRANT SELECT ON users,orders,events,outbox,rate_limits,bot_state,integration_events,schema_migrations,manager_access,order_managers,customer_messages TO {}"
                ).format(role)
            )
            db.execute(
                sql.SQL(
                    "GRANT INSERT ON users,orders,events,outbox,audit_log,rate_limits,bot_state,integration_events TO {}"
                ).format(role)
            )
            db.execute(
                sql.SQL("GRANT UPDATE ON users,orders,outbox,rate_limits,bot_state TO {}").format(
                    role
                )
            )
            db.execute(
                sql.SQL("GRANT INSERT ON order_managers,customer_messages TO {}").format(role)
            )
            db.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION public.manager_order_access(BIGINT,BIGINT),public.manager_create_permission(BIGINT) TO {}"
                ).format(role)
            )
            db.execute(
                sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role)
            )

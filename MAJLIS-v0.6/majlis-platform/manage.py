"""Backup and safe restore rehearsal. Never overwrite an existing database."""

import argparse
import datetime
import json
import os
import re
import shutil

# fixed Docker argv, never shell execution
import subprocess  # nosec B404
from pathlib import Path
from urllib.parse import urlsplit

from psycopg import sql

from majlis.config import ROOT, settings

TABLES = (
    "users",
    "orders",
    "events",
    "outbox",
    "audit_log",
    "integration_events",
    "rate_limits",
    "bot_state",
    "schema_migrations",
    "legacy_imports",
)


def database_name(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise ValueError("Используйте имя БД из латинских букв, цифр и подчёркиваний.")
    return value


def docker_prefix(args):
    binary = shutil.which("docker")
    if not binary:
        raise RuntimeError("Docker не найден.")
    if args.container:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", args.container):
            raise ValueError("Некорректное имя контейнера.")
        return [binary, "exec", "-i", args.container]
    compose = "compose.server.yaml" if args.server else "compose.local.yaml"
    return [
        binary,
        "compose",
        "--env-file",
        str(ROOT / ".env"),
        "-f",
        str(ROOT / compose),
        "exec",
        "-T",
        "db",
    ]


def run_db(prefix, command, **kwargs):
    # Fixed executable and argv; data supplied through stdin, no command shell.
    return subprocess.run(prefix + command, check=False, stderr=subprocess.PIPE, **kwargs)  # nosec B603


def fingerprint(prefix, name):
    result = {}
    for table in TABLES:
        query = sql.SQL(
            "SELECT json_build_object('count',count(*),'hash',md5(coalesce(string_agg(row_to_json(t)::text,'|' ORDER BY row_to_json(t)::text),''))) FROM {} t"
        ).format(sql.Identifier(table))
        res = run_db(
            prefix,
            [
                "psql",
                "-U",
                "majlis",
                "-d",
                name,
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                query.as_string(),
            ],
            stdout=subprocess.PIPE,
        )
        if res.returncode:
            raise RuntimeError("Не удалось проверить таблицы восстановленной БД.")
        result[table] = json.loads(res.stdout)
    query = """SELECT coalesce(json_agg(json_build_object(
        'table',conrelid::regclass::text,'name',conname,'type',contype,
        'columns',conkey,'reference',CASE WHEN confrelid=0 THEN NULL ELSE confrelid::regclass::text END,
        'reference_columns',confkey,'update_action',confupdtype,'delete_action',confdeltype,
        'deferrable',condeferrable,'deferred',condeferred,'validated',convalidated
        ) ORDER BY conrelid::regclass::text,conname),'[]')
        FROM pg_constraint WHERE connamespace='public'::regnamespace"""
    res = run_db(
        prefix, ["psql", "-U", "majlis", "-d", name, "-At", "-c", query], stdout=subprocess.PIPE
    )
    if res.returncode:
        raise RuntimeError("Не удалось проверить ограничения БД.")
    result["constraints"] = json.loads(res.stdout)
    return result


def verify_constraints(prefix, name):
    # pg_dump may reparse equivalent CHECK expressions with different implicit
    # cast placement. Compare metadata AND probe actual constraints in rollback.
    setup = """BEGIN;
    INSERT INTO users(telegram_id) VALUES (9223372036854775000) ON CONFLICT DO NOTHING;
    INSERT INTO orders(number,customer_id,customer_name,item,amount,status)
      VALUES ('__restore_probe__',9223372036854775000,'Fictional','Probe',1,'confirmed');
    """
    probes = [
        (
            "INSERT INTO orders(number,customer_id,customer_name,item,amount,status) VALUES ('__restore_probe__',9223372036854775000,'Fictional','Probe',1,'confirmed')",
            "23505",
        ),
        ("UPDATE orders SET amount=0 WHERE number='__restore_probe__'", "23514"),
        ("UPDATE orders SET amount=1000000000001 WHERE number='__restore_probe__'", "23514"),
        ("UPDATE orders SET status='invalid' WHERE number='__restore_probe__'", "23514"),
        ("UPDATE orders SET customer_id=-1 WHERE number='__restore_probe__'", "23503"),
        ("UPDATE orders SET eta_confirmed=true,eta=NULL WHERE number='__restore_probe__'", "23514"),
        ("UPDATE orders SET customer_generation=0 WHERE number='__restore_probe__'", "23514"),
        ("UPDATE orders SET integration_sequence=-1 WHERE number='__restore_probe__'", "23514"),
        ("UPDATE orders SET version=0 WHERE number='__restore_probe__'", "23514"),
        ("INSERT INTO users(telegram_id) VALUES (-1)", "23514"),
        (
            "INSERT INTO events(order_id,status,note,source) VALUES (-1,'confirmed','','manager')",
            "23503",
        ),
        (
            "INSERT INTO outbox(event_id,customer_id,text) VALUES (-1,9223372036854775000,'Probe')",
            "23503",
        ),
    ]
    probes.extend(
        [
            (
                "INSERT INTO events(order_id,status,note,source,kind) SELECT id,'confirmed','','migration','invalid' FROM orders WHERE number='__restore_probe__'",
                "23514",
            ),
            (
                "INSERT INTO legacy_imports(source,legacy_id,payload_hash,order_id) VALUES ('synthetic','x','hash',-1)",
                "23503",
            ),
            (
                "INSERT INTO legacy_imports(source,legacy_id,payload_hash,order_id) SELECT 'synthetic','x','hash',id FROM orders WHERE number='__restore_probe__'; INSERT INTO legacy_imports(source,legacy_id,payload_hash,order_id) SELECT 'synthetic','x','hash',id FROM orders WHERE number='__restore_probe__'",
                "23505",
            ),
        ]
    )
    for statement, expected in probes:
        result = run_db(
            prefix,
            [
                "psql",
                "-U",
                "majlis",
                "-d",
                name,
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                "VERBOSITY=sqlstate",
            ],
            input=(setup + statement + ";ROLLBACK;").encode(),
            stdout=subprocess.PIPE,
        )
        if not result.returncode or expected.encode() not in result.stderr:
            raise RuntimeError("Ограничение восстановленной БД не прошло проверку.")
    return len(probes)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["backup", "inspect-backup", "restore-test", "privacy-plan"]
    )
    parser.add_argument("--server", action="store_true")
    parser.add_argument(
        "--container", help="Existing database container; optional for a local rehearsal"
    )
    parser.add_argument("--file", type=Path)
    parser.add_argument(
        "--target", help="NEW database ending in _test; existing databases are never replaced"
    )
    parser.add_argument("--customer", type=int)
    args = parser.parse_args(argv)
    cfg = settings()
    if args.command == "privacy-plan":
        if not args.customer or args.customer <= 0:
            raise ValueError("Укажите --customer с положительным Telegram ID.")
        from majlis.database import Database

        db = Database()
        db.open()
        try:
            with db.connection() as conn:
                counts = conn.execute(
                    "SELECT count(*) AS current_orders FROM orders WHERE customer_id=%s",
                    (args.customer,),
                ).fetchone()
                counts["historical_events"] = conn.execute(
                    "SELECT count(*) AS n FROM events WHERE customer_id=%s", (args.customer,)
                ).fetchone()["n"]
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "counts": counts,
                        "action": "Удаление не выполнено. Нужны основания, сроки и правила для истории/backup.",
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            db.close()
        return 0
    prefix = docker_prefix(args)
    source = database_name(urlsplit(cfg.database_url.get_secret_value()).path.lstrip("/"))
    if args.command == "backup":
        folder = ROOT / "data/backups"
        folder.mkdir(parents=True, exist_ok=True)
        target = args.file or folder / (
            "majlis-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".dump"
        )
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            result = run_db(prefix, ["pg_dump", "-U", "majlis", "-d", source, "-Fc"], stdout=output)
        if result.returncode:
            raise RuntimeError("Резервная копия не завершена. Файл может быть неполным.")
        print("Резервная копия: " + str(target.resolve()))
        return 0
    if not args.file or not args.file.is_file():
        raise ValueError("Укажите существующий --file.")
    if args.command == "inspect-backup":
        with args.file.open("rb") as archive:
            result = run_db(prefix, ["pg_restore", "--list"], stdin=archive, stdout=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError("Архив PostgreSQL не прочитан.")
        print("Архив читается; это не проверка восстановления.")
        return 0
    target = database_name(args.target or "")
    if not target.endswith("_test") or target == source:
        raise ValueError("Восстановление разрешено только в НОВУЮ отдельную *_test БД.")
    create = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)).as_string()
    result = run_db(
        prefix,
        ["psql", "-U", "majlis", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", create],
        stdout=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError("Тестовая БД не создана. Существующую БД нельзя перезаписывать.")
    with args.file.open("rb") as archive:
        result = run_db(
            prefix,
            [
                "pg_restore",
                "-U",
                "majlis",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
                "-d",
                target,
            ],
            stdin=archive,
            stdout=subprocess.PIPE,
        )
    if result.returncode:
        raise RuntimeError(
            "Restore не завершён; рабочая база не изменена, тестовая сохранена для диагностики."
        )
    before = fingerprint(prefix, source)
    after = fingerprint(prefix, target)
    if before != after:
        raise RuntimeError(
            "Снимки не совпадают. При активной записи в источник повторите проверку на согласованном snapshot."
        )
    tested_constraints = verify_constraints(prefix, target)
    print(
        json.dumps(
            {
                "tested_constraint_rejections": tested_constraints,
                "restored_database": target,
                "tables": len(TABLES),
                "constraints": len(after["constraints"]),
                "row_counts": {t: after[t]["count"] for t in TABLES},
                "data_and_constraints_match": True,
                "ownership": "Test restore deliberately excludes ownership/ACL; production privileges must be applied separately.",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        # Messages above contain no database URL or credential.
        print("Операция не завершена: " + str(exc))
        raise SystemExit(1) from None

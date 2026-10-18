import copy
import json

import psycopg
import pytest
from psycopg.rows import dict_row
from pydantic import ValidationError
from test_platform import payload

from majlis.domain import Problem
from majlis.legacy_import import ImportPackage, apply_test_package, inspect_package, main
from majlis.legacy_import import test_target_urls as check_target_urls
from majlis.services import bootstrap


def package_data():
    order = payload()
    order["number"] = "SYNTHETIC-IMPORT-1"
    return {
        "schema_version": 1,
        "source": "synthetic_fixture",
        "orders": [
            {
                "source_order_id": "fake-1",
                "telegram_id_verified": True,
                "currency": "RUB",
                "order": order,
                "status": "customs",
                "history": [
                    {
                        "status": "confirmed",
                        "note": "Visible fictional checkpoint",
                        "occurred_at": "2026-10-01T10:00:00+03:00",
                        "owner_telegram_id": 3001,
                        "owner_verified": True,
                        "visible_to_customer": True,
                    },
                    {
                        "status": "purchased",
                        "note": "Unknown fictional owner",
                        "occurred_at": "2026-10-02T10:00:00+03:00",
                    },
                ],
            }
        ],
    }


def test_import_atomic_idempotent_and_history_ownership(db, cfg):
    package = ImportPackage(**package_data())
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(), row_factory=dict_row
    ) as conn:
        before = inspect_package(package, conn, cfg.payment_domains)
        assert before["valid"] and before["would_insert"] == 1
        assert apply_test_package(conn, package, cfg.payment_domains) == {
            "test_rehearsal": True,
            "inserted": 1,
            "skipped": 0,
            "notifications_created": 0,
        }
        assert apply_test_package(conn, package, cfg.payment_domains)["skipped"] == 1
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
        result = bootstrap(conn, {"id": 3001, "role": "buyer"}, cfg)
        events = result["orders"][0]["events"]
        assert any(e["note"] == "Visible fictional checkpoint" for e in events)
        assert not any(e["note"] == "Unknown fictional owner" for e in events)
        assert result["orders"][0]["status"] == "customs"
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(), row_factory=dict_row
    ) as conn:
        assert conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"] == 3


def test_conflict_does_not_overwrite_or_partially_insert(db, cfg):
    package = ImportPackage(**package_data())
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(), row_factory=dict_row
    ) as conn:
        apply_test_package(conn, package, cfg.payment_domains)
    data = package_data()
    data["orders"][0]["order"]["amount"] = 999
    second = copy.deepcopy(data["orders"][0])
    second["source_order_id"] = "fake-2"
    second["order"]["number"] = "SYNTHETIC-IMPORT-2"
    data["orders"].append(second)
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(), row_factory=dict_row
    ) as conn:
        plan = inspect_package(ImportPackage(**data), conn, cfg.payment_domains)
        assert not plan["valid"] and plan["errors"][0]["code"] == "source_id_payload_conflict"
        with pytest.raises(Problem):
            apply_test_package(conn, ImportPackage(**data), cfg.payment_domains)
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 1
        assert conn.execute("SELECT amount FROM orders").fetchone()["amount"] == 12001


@pytest.mark.parametrize(
    "change",
    [
        "unverified_id",
        "internal_id",
        "unknown_status",
        "currency",
        "timezone",
        "history_owner",
        "payment",
    ],
)
def test_rejects_ambiguous_mapping(change):
    data = package_data()
    row = data["orders"][0]
    if change == "unverified_id":
        row["telegram_id_verified"] = False
    elif change == "internal_id":
        row["telegram_id_verified"] = 1
    elif change == "unknown_status":
        row["status"] = "legacy_unknown_stage"
    elif change == "currency":
        row["currency"] = "USD"
    elif change == "timezone":
        row["history"][0]["occurred_at"] = "2026-10-01T10:00:00"
    elif change == "history_owner":
        row["history"][0]["owner_telegram_id"] = 4001
    elif change == "payment":
        row["payment_url"] = "https://payments.example/invoice"
        row["payment_link_verified"] = False
    with pytest.raises(ValidationError):
        ImportPackage(**data)


def test_duplicate_ids_and_target_numbers_are_reported(db, cfg):
    data = package_data()
    data["orders"].append(copy.deepcopy(data["orders"][0]))
    report = inspect_package(ImportPackage(**data))
    assert {x["code"] for x in report["errors"]} == {
        "duplicate_source_id",
        "duplicate_number_in_file",
    }
    package = ImportPackage(**package_data())
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(), row_factory=dict_row
    ) as conn:
        apply_test_package(conn, package, cfg.payment_domains)
        other = package_data()
        other["source"] = "another_synthetic_source"
        assert (
            inspect_package(ImportPackage(**other), conn, cfg.payment_domains)["errors"][0]["code"]
            == "target_number_exists"
        )


def test_runtime_cannot_access_import_mapping(db, cfg):
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.connection() as conn:
        conn.execute("SELECT * FROM legacy_imports")


def test_dry_run_cli_no_database_and_no_personal_output(tmp_path, capsys):
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(package_data()))
    assert main(["inspect", "--file", str(path)]) == 0
    output = capsys.readouterr().out
    assert "synthetic" not in output and "3001" not in output and "Покупатель" not in output
    report = json.loads(output)
    assert report["dry_run"] and not report["target_checked"]


def test_rehearsal_guard_rejects_real_target(cfg):
    from pydantic import SecretStr

    cfg.database_url = SecretStr("postgresql://app@localhost/majlis")
    cfg.migration_database_url = SecretStr("postgresql://owner@localhost/majlis")
    with pytest.raises(ValueError):
        check_target_urls(cfg)


def test_database_failure_rolls_back_entire_import(db, cfg):
    data = package_data()
    second = copy.deepcopy(data["orders"][0])
    second["source_order_id"] = "fake-2"
    second["order"]["number"] = "FORCE-FAIL"
    data["orders"].append(second)
    with (
        psycopg.connect(
            cfg.migration_database_url.get_secret_value(), row_factory=dict_row
        ) as conn,
        conn.transaction(force_rollback=True),
    ):
        conn.execute(
            "CREATE FUNCTION pg_temp.reject_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.number='FORCE-FAIL' THEN RAISE EXCEPTION 'fixture database failure'; END IF; RETURN NEW; END $$"
        )
        conn.execute(
            "CREATE TRIGGER reject_fixture BEFORE INSERT ON orders FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_fixture()"
        )
        with pytest.raises(psycopg.errors.RaiseException):
            apply_test_package(conn, ImportPackage(**data), cfg.payment_domains)
        for table in ["orders", "events", "legacy_imports", "audit_log"]:
            from psycopg import sql

            assert (
                conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
                ).fetchone()["n"]
                == 0
            )


def test_concurrent_duplicate_import_only_inserts_once(db, cfg):
    from concurrent.futures import ThreadPoolExecutor

    package = ImportPackage(**package_data())

    def run():
        with psycopg.connect(
            cfg.migration_database_url.get_secret_value(), row_factory=dict_row
        ) as conn:
            return apply_test_package(conn, package, cfg.payment_domains)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))
    assert sorted(r["inserted"] for r in results) == [0, 1]
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 1


def test_invalid_export_reports_locations_without_personal_values(tmp_path, capsys):
    data = package_data()
    data["orders"][0]["telegram_id_verified"] = False
    data["orders"][0]["order"]["customer_name"] = "PRIVATE_NOT_FOR_REPORT"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    assert main(["inspect", "--file", str(path)]) == 1
    raw = capsys.readouterr().out
    assert "PRIVATE_NOT_FOR_REPORT" not in raw
    assert json.loads(raw)["locations"][0]["location"] == ["orders", 0, "telegram_id_verified"]


def test_import_preserves_explicit_original_creation_time(db, cfg):
    from datetime import datetime

    data = package_data()
    data["orders"][0]["source_created_at"] = "2026-09-25T10:00:00+03:00"
    package = ImportPackage(**data)
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(), row_factory=dict_row
    ) as conn:
        assert inspect_package(package)["warnings"] == []
        apply_test_package(conn, package, cfg.payment_domains)
        row = conn.execute("SELECT created_at FROM orders").fetchone()
        assert row["created_at"] == datetime.fromisoformat("2026-09-25T10:00:00+03:00")
    assert (
        inspect_package(ImportPackage(**package_data()))["warnings"][0]["code"]
        == "source_creation_time_not_provided"
    )

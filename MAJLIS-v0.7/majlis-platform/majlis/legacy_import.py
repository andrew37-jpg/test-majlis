"""Target-side import contract. No inference of the old DB; writes only in test CLI."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from pydantic import Field, ValidationError, field_validator, model_validator

from .config import settings
from .domain import STAGE_MAP, Problem, https_url
from .schemas import Id, Input, NewOrder
from .services import add_event, audit, ensure_user

MAX_BYTES = 8 * 1024 * 1024


class HistoryRecord(Input):
    status: str = Field(max_length=32)
    note: str = Field(default="", max_length=500)
    occurred_at: datetime
    owner_telegram_id: Id | None = None
    owner_verified: bool = Field(default=False, strict=True)
    visible_to_customer: bool = Field(default=False, strict=True)

    @field_validator("status")
    @classmethod
    def known_status(cls, value):
        if value not in STAGE_MAP:
            raise ValueError("Map source status explicitly")
        return value

    @field_validator("occurred_at")
    @classmethod
    def explicit_timezone(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value

    @model_validator(mode="after")
    def ownership(self):
        if self.owner_telegram_id is not None and not self.owner_verified:
            raise ValueError("History owner requires explicit verification")
        if self.visible_to_customer and not self.owner_verified:
            raise ValueError("Visibility requires verified ownership")
        return self


class ImportRecord(Input):
    source_order_id: str = Field(min_length=1, max_length=128)
    telegram_id_verified: Literal[True]
    currency: Literal["RUB"]
    order: NewOrder
    status: str = Field(max_length=32)
    source_created_at: datetime | None = None
    eta_confirmed: bool = Field(default=False, strict=True)
    payment_url: str = Field(default="", max_length=2000)
    payment_link_verified: bool = Field(default=False, strict=True)
    history: list[HistoryRecord] = Field(default_factory=list, max_length=500)

    @field_validator("source_created_at")
    @classmethod
    def creation_timezone(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Explicit source timezone required")
        return value

    @field_validator("telegram_id_verified", mode="before")
    @classmethod
    def explicit_verification(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("Explicit true required")
        return value

    @field_validator("status")
    @classmethod
    def known_status(cls, value):
        if value not in STAGE_MAP:
            raise ValueError("Map source status explicitly")
        return value

    @model_validator(mode="after")
    def consistency(self):
        if self.eta_confirmed and not self.order.eta:
            raise ValueError("Confirmed ETA requires a date")
        if self.payment_url and not self.payment_link_verified:
            raise ValueError("Payment URL must be verified or omitted")
        for event in self.history:
            if event.visible_to_customer and event.owner_telegram_id != self.order.customer_id:
                raise ValueError("History visibility must belong to current verified owner")
        return self


class ImportPackage(Input):
    schema_version: Annotated[int, Field(strict=True)]
    source: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    orders: list[ImportRecord] = Field(min_length=1, max_length=1000)

    @field_validator("schema_version")
    @classmethod
    def supported(cls, value):
        if value != 1:
            raise ValueError("Unsupported normalized contract")
        return value


def load_package(path: Path) -> ImportPackage:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Normalized file exceeds 8 MiB")
    return ImportPackage.model_validate_json(raw)


def digest(record: ImportRecord) -> str:
    return hashlib.sha256(record.model_dump_json().encode()).hexdigest()


def inspect_package(package: ImportPackage, conn=None, payment_domains=None) -> dict:
    """No writes. Report row indices/codes, never raw customer or invoice values."""
    errors = []
    warnings = []
    ids = set()
    numbers = set()
    skipped = 0
    mappings = {}
    existing = set()
    if conn:
        mappings = {
            row["legacy_id"]: row["payload_hash"]
            for row in conn.execute(
                "SELECT legacy_id,payload_hash FROM legacy_imports WHERE source=%s AND legacy_id=ANY(%s)",
                (package.source, [r.source_order_id for r in package.orders]),
            ).fetchall()
        }
        existing = {
            row["number"]
            for row in conn.execute(
                "SELECT number FROM orders WHERE number=ANY(%s)",
                ([r.order.number for r in package.orders],),
            ).fetchall()
        }
    for index, record in enumerate(package.orders, 1):
        if record.source_created_at is None:
            warnings.append({"row": index, "code": "source_creation_time_not_provided"})
        if record.source_order_id in ids:
            errors.append({"row": index, "code": "duplicate_source_id"})
        if record.order.number in numbers:
            errors.append({"row": index, "code": "duplicate_number_in_file"})
        ids.add(record.source_order_id)
        numbers.add(record.order.number)
        if record.payment_url:
            try:
                https_url(record.payment_url, payment_domains)
            except Problem:
                errors.append({"row": index, "code": "unsafe_payment_url"})
            if payment_domains is None:
                errors.append({"row": index, "code": "payment_allowlist_required"})
        previous = mappings.get(record.source_order_id)
        if previous:
            if previous == digest(record):
                skipped += 1
            else:
                errors.append({"row": index, "code": "source_id_payload_conflict"})
        elif record.order.number in existing:
            errors.append({"row": index, "code": "target_number_exists"})
    return {
        "dry_run": True,
        "target_checked": conn is not None,
        "valid": not errors,
        "records": len(package.orders),
        "already_imported": skipped,
        "would_insert": len(package.orders) - skipped if not errors else 0,
        "errors": errors,
        "warnings": warnings,
    }


def apply_test_package(conn, package: ImportPackage, payment_domains: set[str]) -> dict:
    """Atomic, append-only, idempotent. Caller/CLI restrict this to an isolated DB."""
    if not conn.execute("SELECT current_database() AS name").fetchone()["name"].endswith("_test"):
        raise Problem(400, "Только отдельная тестовая БД.")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(742019145,hashtext(%s))", (package.source,))
        plan = inspect_package(package, conn, payment_domains)
        if not plan["valid"]:
            raise Problem(409, "Импорт остановлен: исправьте конфликты по dry-run.")
        inserted = 0
        for record in package.orders:
            mapped = conn.execute(
                "SELECT order_id FROM legacy_imports WHERE source=%s AND legacy_id=%s",
                (package.source, record.source_order_id),
            ).fetchone()
            if mapped:
                continue
            data = record.order
            ensure_user(conn, {"id": data.customer_id})
            row = conn.execute(
                """INSERT INTO orders(number,customer_id,customer_name,item,amount,shipment,status,eta,eta_confirmed,payment_url,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now())) RETURNING *""",
                (
                    data.number,
                    data.customer_id,
                    data.customer_name,
                    data.item,
                    data.amount,
                    data.shipment,
                    record.status,
                    data.eta,
                    record.eta_confirmed,
                    record.payment_url,
                    record.source_created_at,
                ),
            ).fetchone()
            for event in sorted(record.history, key=lambda event: event.occurred_at):
                if event.owner_telegram_id is not None:
                    ensure_user(conn, {"id": event.owner_telegram_id})
                generation = 1 if event.visible_to_customer else 0
                conn.execute(
                    """INSERT INTO events(order_id,status,note,source,created_at,customer_id,customer_generation,kind)
                    VALUES (%s,%s,%s,'migration',%s,%s,%s,'status')""",
                    (
                        row["id"],
                        event.status,
                        event.note,
                        event.occurred_at,
                        event.owner_telegram_id,
                        generation,
                    ),
                )
            add_event(
                conn,
                row,
                "Заказ добавлен из проверенного экспорта. Старые документы и оплаты требуют отдельной сверки.",
                "migration",
                kind="import",
            )
            audit(conn, None, "import", {}, row)
            conn.execute(
                "INSERT INTO legacy_imports(source,legacy_id,payload_hash,order_id) VALUES (%s,%s,%s,%s)",
                (package.source, record.source_order_id, digest(record), row["id"]),
            )
            inserted += 1
        return {
            "test_rehearsal": True,
            "inserted": inserted,
            "skipped": len(package.orders) - inserted,
            "notifications_created": 0,
        }


def test_target_urls(cfg):
    a = urlsplit(cfg.database_url.get_secret_value())
    b = urlsplit(cfg.migration_database_url.get_secret_value())
    if (
        not a.path.endswith("_test")
        or (a.hostname, a.port, a.path) != (b.hostname, b.port, b.path)
        or a.username == b.username
    ):
        raise ValueError("Репетиция разрешена только в одной отдельной *_test БД с двумя ролями.")


def safe_validation_locations(exc: ValidationError) -> list[dict]:
    known = {
        "source_created_at",
        "schema_version",
        "source",
        "orders",
        "order",
        "source_order_id",
        "telegram_id_verified",
        "currency",
        "status",
        "eta_confirmed",
        "payment_url",
        "payment_link_verified",
        "history",
        "customer_id",
        "customer_name",
        "item",
        "number",
        "amount",
        "shipment",
        "eta",
        "note",
        "occurred_at",
        "owner_telegram_id",
        "owner_verified",
        "visible_to_customer",
    }
    return [
        {
            "location": [
                part if isinstance(part, int) or part in known else "unknown_field"
                for part in e["loc"]
            ],
            "code": e["type"],
        }
        for e in exc.errors(include_input=False, include_context=False, include_url=False)[:50]
    ]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect", "rehearse"])
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--check-target", action="store_true")
    parser.add_argument("--apply-test", action="store_true")
    args = parser.parse_args(argv)
    if args.apply_test and args.command != "rehearse":
        raise ValueError("--apply-test допускается только для rehearse")
    try:
        package = load_package(args.file)
    except ValidationError as exc:
        print(
            json.dumps(
                {
                    "valid": False,
                    "dry_run": True,
                    "error": "invalid_normalized_input",
                    "error_count": exc.error_count(),
                    "locations": safe_validation_locations(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1
    if not args.check_target and args.command == "inspect":
        report = inspect_package(package)
    else:
        cfg = settings()
        if args.command == "rehearse":
            test_target_urls(cfg)
        with psycopg.connect(
            cfg.migration_database_url.get_secret_value(), row_factory=dict_row
        ) as conn:
            if args.apply_test:
                report = apply_test_package(conn, package, cfg.payment_domains)
            else:
                conn.execute("SET TRANSACTION READ ONLY")
                report = inspect_package(package, conn, cfg.payment_domains)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("valid", True) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, Problem, psycopg.Error):
        print(
            "Проверка переноса не завершена. Проверьте локальные настройки, файл и dry-run; данные/реквизиты не выводятся."
        )
        raise SystemExit(1) from None

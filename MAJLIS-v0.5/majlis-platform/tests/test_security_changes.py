import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import dict_row
from test_platform import created, payload, queued, signed

from majlis.bot import deliver
from majlis.domain import Problem
from majlis.http_boundary import PublicLimiter
from majlis.schemas import Details
from majlis.services import update_details


def event(client, cfg, event_id, status, sequence=None):
    data = {"event_id": event_id, "shipment": "TEST-CARGO", "status": status}
    if sequence is not None:
        data["sequence"] = sequence
    raw = json.dumps(data).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(
        cfg.integration_secret.get_secret_value().encode(),
        stamp.encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return client.post(
        "/api/integrations/events",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Event-Timestamp": stamp,
            "X-Event-Signature": sig,
        },
    )


def test_transfer_requires_explicit_name_and_confirmation(client, cfg):
    order = created(client, cfg)
    data = {"order_id": order["id"], "version": 1, "customer_id": 4001}
    for extras in ({}, {"customer_name": "New"}, {"transfer_confirmed": True}):
        assert (
            client.post(
                "/api/orders/details", headers=signed(cfg), json={**data, **extras}
            ).status_code
            == 422
        )
    assert (
        client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 3001)).status_code == 200
    )


def test_transfer_back_does_not_mix_generations(client, cfg, db):
    order = created(client, cfg)
    for owner, name, version in [(4001, "Next", 1), (3001, "Returned", 2)]:
        assert (
            client.post(
                "/api/orders/details",
                headers=signed(cfg),
                json={
                    "order_id": order["id"],
                    "version": version,
                    "customer_id": owner,
                    "customer_name": name,
                    "transfer_confirmed": True,
                },
            ).status_code
            == 200
        )
    data = client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 3001)).json()
    assert data["customer_generation"] == 3
    assert all(e["customer_id"] == 3001 and e["customer_generation"] == 3 for e in data["events"])
    assert (
        client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 4001)).status_code == 404
    )
    with db.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM events WHERE order_id=%s", (order["id"],)
            ).fetchone()["n"]
            == 3
        )
    manager = client.get("/api/orders/" + str(order["id"]), headers=signed(cfg)).json()
    assert len(manager["events"]) == 3


def test_export_scoped_and_audit_minimized(client, cfg, db):
    order = created(client, cfg)
    data = client.get("/api/me/export", headers=signed(cfg, 3002)).json()
    assert data["orders"] == []
    assert (
        client.get("/api/me/export", headers=signed(cfg, 3001)).json()["orders"][0]["id"]
        == order["id"]
    )
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(), row_factory=dict_row
    ) as conn:
        row = conn.execute("SELECT before_data,after_data FROM audit_log").fetchone()
        assert "customer_name" not in row["after_data"]
        assert "payment_url" not in row["after_data"]
        assert "item" not in row["after_data"]


def test_sequence_duplicate_payload_conflict_and_manual_correction(client, cfg):
    order = created(client, cfg)
    assert event(client, cfg, "HIGH", "customs", 20).json()["updated"] == 1
    assert event(client, cfg, "OLD", "delivered", 10).json()["updated"] == 0
    assert event(client, cfg, "HIGH", "customs", 20).json()["duplicate"]
    assert event(client, cfg, "HIGH", "delivered", 21).status_code == 409
    assert event(client, cfg, "FINISH", "delivered", 30).json()["updated"] == 1
    latest = client.get("/api/orders/" + str(order["id"]), headers=signed(cfg)).json()
    result = client.post(
        "/api/orders/status",
        headers=signed(cfg),
        json={
            "order_id": order["id"],
            "version": latest["version"],
            "status": "customs",
            "note": "Manual correction",
        },
    )
    assert result.status_code == 200 and result.json()["status"] == "customs"
    assert event(client, cfg, "REPLAY", "delivered", 29).json()["updated"] == 0


def test_concurrent_external_events_cannot_regress(client, cfg):
    order = created(client, cfg)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(
            executor.map(
                lambda args: event(client, cfg, *args),
                [("FIN", "delivered", 100), ("LATE", "customs", 50)],
            )
        )
    assert all(response.status_code == 200 for response in replies)
    assert (
        client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 3001)).json()["status"]
        == "delivered"
    )


def test_send_does_not_hold_row_lock_and_transfer_fails_fast(db):
    order = queued(db)
    started = threading.Event()
    release = threading.Event()

    class API:
        def send(self, *args):
            started.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(deliver, db, API())
        try:
            assert started.wait(3)
            with db.connection() as conn:
                assert conn.execute(
                    "SELECT id FROM orders WHERE id=%s FOR UPDATE NOWAIT", (order["id"],)
                ).fetchone()
            before = time.monotonic()
            with pytest.raises(Problem) as problem, db.connection() as conn:
                update_details(
                    conn,
                    Details(
                        order_id=order["id"],
                        version=2,
                        customer_id=4001,
                        customer_name="New",
                        transfer_confirmed=True,
                    ),
                    9001,
                )
            assert problem.value.status == 409 and time.monotonic() - before < 1
            with db.connection() as conn:
                assert (
                    conn.execute(
                        "SELECT customer_id FROM orders WHERE id=%s", (order["id"],)
                    ).fetchone()["customer_id"]
                    == 3001
                )
        finally:
            release.set()
        task.result(timeout=5)
    with db.connection() as conn:
        update_details(
            conn,
            Details(
                order_id=order["id"],
                version=2,
                customer_id=4001,
                customer_name="New",
                transfer_confirmed=True,
            ),
            9001,
        )


def test_runtime_cannot_ddl_rewrite_audit_or_truncate(db):
    queued(db)
    for statement in [
        "CREATE TABLE forbidden(id int)",
        "ALTER TABLE orders ADD COLUMN forbidden int",
        "DROP TABLE rate_limits",
        "TRUNCATE rate_limits",
        "UPDATE audit_log SET action='rewritten'",
        "DELETE FROM audit_log",
        "SELECT * FROM audit_log",
        "INSERT INTO schema_migrations(name,checksum) VALUES ('forbidden','bad')",
    ]:
        with (
            db.connection() as conn,
            conn.transaction(force_rollback=True),
            pytest.raises(InsufficientPrivilege),
        ):
            conn.execute(statement)
    with db.connection() as conn:
        assert not conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()["rolsuper"]
        assert not conn.execute(
            "SELECT pg_get_userbyid(relowner)=current_user AS owned FROM pg_class WHERE oid='orders'::regclass"
        ).fetchone()["owned"]


def test_public_limit_before_auth_without_database(client, monkeypatch):
    client.app.state.public_limiter = PublicLimiter(limit=2)

    def blocked():
        raise AssertionError("Must not contact DB before rejecting invalid auth")

    monkeypatch.setattr(client.app.state.db, "connection", blocked)
    assert client.get("/api/bootstrap", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 401
    assert client.get("/api/bootstrap", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 401
    assert client.get("/api/bootstrap", headers={"X-Forwarded-For": "3.3.3.3"}).status_code == 429
    assert client.get("/ready").status_code == 403


def test_limiter_recovery_and_memory_capacity():
    now = [0.0]
    limiter = PublicLimiter(limit=2, capacity=2, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b") and not limiter.allow("c")
    now[0] = 61
    assert limiter.allow("c") and limiter.allow("a")


@pytest.mark.parametrize("status", [200, 401, 403, 413, 415, 422, 500])
def test_headers_all_response_paths(client, cfg, monkeypatch, status):
    if status == 200:
        response = client.get("/health")
    elif status == 401:
        response = client.get("/api/bootstrap")
    elif status == 403:
        response = client.post("/api/orders", headers=signed(cfg, 3001), json=payload())
    elif status == 413:
        response = client.post("/api/orders", json={"huge": "x" * 70000})
    elif status == 415:
        response = client.post("/api/orders", content="abc", headers={"Content-Type": "text/plain"})
    elif status == 422:
        response = client.post("/api/orders", json={}, headers=signed(cfg))
    else:

        def fail(*args, **kwargs):
            raise RuntimeError("SECRET_MUST_NOT_LEAK")

        monkeypatch.setattr("majlis.api.services.bootstrap", fail)
        response = client.get("/api/bootstrap", headers=signed(cfg))
        assert "SECRET_MUST_NOT_LEAK" not in response.text
    assert response.status_code == status
    for header in [
        "content-security-policy",
        "cache-control",
        "x-content-type-options",
        "referrer-policy",
    ]:
        assert header in response.headers
    assert response.headers["cache-control"] == "no-store"


def test_legacy_transfer_histories_are_quarantined(cfg):
    with (
        psycopg.connect(
            cfg.migration_database_url.get_secret_value(), row_factory=dict_row
        ) as conn,
        conn.transaction(force_rollback=True),
    ):
        conn.execute("CREATE SCHEMA legacy_rehearsal")
        conn.execute("SET LOCAL search_path=legacy_rehearsal")
        conn.execute(Path("migrations/001_initial.sql").read_text())
        conn.execute("INSERT INTO users(telegram_id) VALUES (1),(2)")
        conn.execute(
            "INSERT INTO orders(number,customer_id,customer_name,item,amount,status) VALUES ('LEGACY',2,'New','Test',1,'confirmed')"
        )
        conn.execute(
            "INSERT INTO events(order_id,status,note,source) VALUES (1,'confirmed','Old owner comment','manager')"
        )
        conn.execute(
            "INSERT INTO audit_log(action,order_id,before_data,after_data) VALUES ('details',1,'{\"customer_id\":1}','{\"customer_id\":2}')"
        )
        conn.execute(Path("migrations/002_ownership_and_events.sql").read_text())
        event_row = conn.execute("SELECT * FROM events").fetchone()
        assert event_row["customer_id"] is None and event_row["customer_generation"] == 0
        assert event_row["note"] == "Old owner comment"


def test_history_pages_remain_scoped_after_transfer(client, cfg, db):
    order = created(client, cfg)
    from majlis.services import change_status

    with db.connection() as conn:
        for index in range(105):
            change_status(
                conn,
                order["id"],
                "purchased" if index % 2 == 0 else "customs",
                f"History {index}",
                "manager",
                9001,
            )
    data = client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 3001)).json()
    assert len(data["events"]) == 100
    older = client.get(
        "/api/orders/" + str(order["id"]) + "/history",
        params={"before": data["history_cursor"]},
        headers=signed(cfg, 3001),
    )
    assert older.status_code == 200 and len(older.json()["events"]) == 6
    assert not {e["id"] for e in data["events"]} & {e["id"] for e in older.json()["events"]}
    assert (
        client.get(
            "/api/orders/" + str(order["id"]) + "/history", headers=signed(cfg, 3002)
        ).status_code
        == 404
    )
    data = client.get("/api/orders/" + str(order["id"]), headers=signed(cfg)).json()
    assert (
        client.post(
            "/api/orders/details",
            headers=signed(cfg),
            json={
                "order_id": order["id"],
                "version": data["version"],
                "customer_id": 4001,
                "customer_name": "Next",
                "transfer_confirmed": True,
            },
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/orders/" + str(order["id"]) + "/history", headers=signed(cfg, 3001)
        ).status_code
        == 404
    )
    result = client.get(
        "/api/orders/" + str(order["id"]) + "/history", headers=signed(cfg, 4001)
    ).json()
    assert len(result["events"]) == 1 and result["events"][0]["customer_id"] == 4001


def test_production_integration_requires_sequence_and_rejects_huge_timestamp(client, cfg):
    created(client, cfg)
    cfg.environment = "production"
    assert event(client, cfg, "NO-SEQUENCE", "customs").status_code == 422
    assert event(client, cfg, "WITH-SEQUENCE", "customs", 1).status_code == 200
    assert (
        client.post(
            "/api/integrations/events",
            json={"event_id": "HUGE", "shipment": "TEST-CARGO", "status": "customs"},
            headers={"X-Event-Timestamp": "9" * 300, "X-Event-Signature": "bad"},
        ).status_code
        == 401
    )


def test_transfer_refuses_unresolved_send_even_after_lease_expiry(db):
    order = queued(db)
    with db.connection() as conn:
        conn.execute("UPDATE outbox SET state='sending',lease_until=now()-interval '1 second'")
    with pytest.raises(Problem) as exc, db.connection() as conn:
        update_details(
            conn,
            Details(
                order_id=order["id"],
                version=2,
                customer_id=4001,
                customer_name="New",
                transfer_confirmed=True,
            ),
            9001,
        )
    assert exc.value.status == 409

import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import psycopg
import pytest

from majlis.bot import TelegramFailure, claim_notification, deliver
from majlis.domain import Problem, validate_init_data
from majlis.schemas import Details, NewOrder
from majlis.services import change_status, create_order, ensure_user, update_details


def signed(cfg, uid=9001, age=0):
    values = {
        "auth_date": str(int(time.time()) - age),
        "user": json.dumps({"id": uid, "first_name": "Test"}),
    }
    secret = hmac.new(
        b"WebAppData", cfg.bot_token.get_secret_value().encode(), hashlib.sha256
    ).digest()
    values["hash"] = hmac.new(
        secret, "\n".join(f"{k}={v}" for k, v in sorted(values.items())).encode(), hashlib.sha256
    ).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(values)}


def payload():
    return {
        "number": "MJ-TEST-1",
        "customer_id": 3001,
        "customer_name": "Покупатель",
        "item": "Тестовый товар",
        "amount": 12001,
        "shipment": "TEST-CARGO",
        "eta": "",
    }


def created(client, cfg):
    result = client.post("/api/orders", headers=signed(cfg), json=payload())
    assert result.status_code == 201, result.text
    return result.json()


def test_requires_verified_telegram(client, cfg):
    assert client.get("/api/bootstrap").status_code == 401
    assert client.get("/api/bootstrap", headers={"X-Demo-User": "manager"}).status_code == 401
    assert client.get("/api/bootstrap", headers=signed(cfg, age=4000)).status_code == 401
    good = signed(cfg)
    good["X-Telegram-Init-Data"] += "&user=%7B%7D"
    assert client.get("/api/bootstrap", headers=good).status_code == 401


def test_buyer_cannot_mutate_or_read_another_customer(client, cfg):
    created(client, cfg)
    assert len(client.get("/api/bootstrap", headers=signed(cfg, 3001)).json()["orders"]) == 1
    assert client.get("/api/bootstrap", headers=signed(cfg, 3002)).json()["orders"] == []
    assert client.post("/api/orders", headers=signed(cfg, 3002), json=payload()).status_code == 403


def test_duplicate_and_strict_money(client, cfg):
    created(client, cfg)
    assert client.post("/api/orders", headers=signed(cfg), json=payload()).status_code == 409
    for bad in (True, 1.2, 0, -1, 1000000000001):
        data = payload()
        data["amount"] = bad
        assert client.post("/api/orders", headers=signed(cfg), json=data).status_code == 422


def test_status_atomic_outbox_and_revision(client, cfg, db):
    order = created(client, cfg)
    data = {"order_id": order["id"], "version": 1, "status": "in_transit", "note": "В пути"}
    result = client.post("/api/orders/status", headers=signed(cfg), json=data)
    assert result.status_code == 200
    assert result.json()["version"] == 2
    assert client.post("/api/orders/status", headers=signed(cfg), json=data).status_code == 409
    data["version"] = 2
    assert client.post("/api/orders/status", headers=signed(cfg), json=data).status_code == 200
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(),
        row_factory=__import__("psycopg.rows", fromlist=["dict_row"]).dict_row,
    ) as conn:
        for table, count in [("outbox", 1), ("events", 2), ("audit_log", 2)]:
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == count


def test_transaction_rolls_back_all_side_effects(db, cfg):
    with pytest.raises(RuntimeError), db.connection() as conn:
        ensure_user(conn, {"id": 9001})
        order = create_order(conn, NewOrder(**payload()), 9001)
        change_status(conn, order["id"], "purchased", "", "manager", 9001, 1)
        raise RuntimeError("abort")
    with psycopg.connect(
        cfg.migration_database_url.get_secret_value(),
        row_factory=__import__("psycopg.rows", fromlist=["dict_row"]).dict_row,
    ) as conn:
        for table in ("orders", "events", "outbox", "audit_log"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0


def test_integration_signature_idempotency(client, cfg):
    created(client, cfg)
    raw = json.dumps(
        {"event_id": "EVENT-1", "shipment": "TEST-CARGO", "status": "customs"}
    ).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(
        cfg.integration_secret.get_secret_value().encode(),
        stamp.encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Event-Timestamp": stamp,
        "X-Event-Signature": signature,
    }
    assert client.post("/api/integrations/events", content=raw, headers=headers).json() == {
        "duplicate": False,
        "updated": 1,
    }
    assert client.post("/api/integrations/events", content=raw, headers=headers).json() == {
        "duplicate": True,
        "updated": 0,
    }
    headers["X-Event-Signature"] = "wrong"
    assert client.post("/api/integrations/events", content=raw, headers=headers).status_code == 401


def test_payment_exact_host(client, cfg):
    order = created(client, cfg)
    for bad in (
        "https://payments.example.attacker.invalid",
        "http://payments.example",
        "https://attacker@payments.example",
    ):
        assert (
            client.post(
                "/api/orders/payment",
                headers=signed(cfg),
                json={"order_id": order["id"], "version": 1, "url": bad},
            ).status_code
            == 400
        )
    assert (
        client.post(
            "/api/orders/payment",
            headers=signed(cfg),
            json={
                "order_id": order["id"],
                "version": 1,
                "url": "https://payments.example/pay/test",
            },
        ).status_code
        == 200
    )


def queued(db):
    with db.connection() as conn:
        ensure_user(conn, {"id": 9001})
        order = create_order(conn, NewOrder(**payload()), 9001)
        change_status(conn, order["id"], "purchased", "", "manager", 9001, 1)
    return order


def test_concurrent_claim_and_lease_recovery(db):
    queued(db)

    def claim():
        with db.connection() as conn:
            return claim_notification(conn)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: claim(), range(2)))
    assert sum(row is not None for row in claims) == 1
    with db.connection() as conn:
        conn.execute("UPDATE outbox SET lease_until=now()-interval '1 second'")
    assert claim()["attempts"] == 2


def test_reassignment_cancels_old_customer_queue(db):
    order = queued(db)
    with db.connection() as conn:
        update_details(
            conn,
            Details(
                order_id=order["id"],
                version=2,
                customer_id=3002,
                customer_name="New buyer",
                transfer_confirmed=True,
            ),
            9001,
        )

    class API:
        def send(self, *args):
            raise AssertionError("Old customer")

    deliver(db, API())
    with db.connection() as conn:
        assert conn.execute("SELECT state,last_error FROM outbox").fetchone() == {
            "state": "failed",
            "last_error": "owner_changed",
        }


def test_retry_and_blocked_recipient(db):
    queued(db)

    class API:
        def send(self, *args):
            raise TelegramFailure(429, 60)

    deliver(db, API())
    with db.connection() as conn:
        assert conn.execute("SELECT state,attempts,last_error FROM outbox").fetchone() == {
            "state": "pending",
            "attempts": 1,
            "last_error": "Telegram error 429",
        }
        conn.execute("UPDATE outbox SET available_at=now()-interval '1 second'")

    class Blocked:
        def send(self, *args):
            raise TelegramFailure(403)

    deliver(db, Blocked())
    with db.connection() as conn:
        assert conn.execute("SELECT state FROM outbox").fetchone()["state"] == "failed"


def test_health_and_database_ready(client):
    assert client.get("/health").json()["instance_id"] == "test-instance"
    assert client.get("/ready", headers={"X-Health-Token": "h" * 40}).json() == {
        "ok": True,
        "database": "postgresql",
    }
    assert "Content-Security-Policy" in client.get("/").headers


def test_rejects_oversized_request(client, cfg):
    assert (
        client.post("/api/orders", headers=signed(cfg), json={"note": "a" * 70000}).status_code
        == 413
    )


def test_signed_bool_id_rejected(cfg):
    with pytest.raises(Problem):
        validate_init_data(
            signed(cfg, True)["X-Telegram-Init-Data"], cfg.bot_token.get_secret_value()
        )


def test_two_editors_one_revision_only(client, cfg):
    order = created(client, cfg)

    def edit(stage):
        return client.post(
            "/api/orders/status",
            headers=signed(cfg),
            json={"order_id": order["id"], "version": 1, "status": stage, "note": ""},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        codes = list(executor.map(edit, ["customs", "warehouse"]))
    assert sorted(codes) == [200, 409]


def test_rate_limit_and_recovery(client, cfg, db):
    assert client.get("/api/bootstrap", headers=signed(cfg, 3001)).status_code == 200
    with db.connection() as conn:
        conn.execute("UPDATE rate_limits SET requests=120 WHERE telegram_id=3001")
    assert client.get("/api/bootstrap", headers=signed(cfg, 3001)).status_code == 429
    with db.connection() as conn:
        conn.execute(
            "UPDATE rate_limits SET window_start=now()-interval '2 minutes' WHERE telegram_id=3001"
        )
    assert client.get("/api/bootstrap", headers=signed(cfg, 3001)).status_code == 200


def test_missing_integration_shipment_rolls_back_receipt(db):
    from majlis.schemas import Integration
    from majlis.services import integration_event

    with pytest.raises(Problem), db.connection() as conn:
        integration_event(
            conn, Integration(event_id="MISSING", shipment="ABSENT", status="customs")
        )
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM integration_events").fetchone()["n"] == 0


def test_migration_checksum_is_enforced(db):
    import psycopg

    with psycopg.connect(
        __import__("majlis.config", fromlist=["settings"])
        .settings()
        .migration_database_url.get_secret_value()
    ) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum='modified' WHERE name='001_initial.sql'"
        )
    try:
        with pytest.raises(RuntimeError):
            db.migrate()
    finally:
        from pathlib import Path

        checksum = hashlib.sha256(Path("migrations/001_initial.sql").read_bytes()).hexdigest()
        with psycopg.connect(
            __import__("majlis.config", fromlist=["settings"])
            .settings()
            .migration_database_url.get_secret_value()
        ) as conn:
            conn.execute(
                "UPDATE schema_migrations SET checksum=%s WHERE name=%s",
                (checksum, "001_initial.sql"),
            )


def test_server_search_respects_customer_scope_and_literal_wildcards(client, cfg):
    first = payload()
    first["number"] = "MJ-100%_SPECIAL"
    first["customer_name"] = "PRIVATE-NAME"
    assert client.post("/api/orders", headers=signed(cfg), json=first).status_code == 201
    second = payload()
    second["number"] = "MJ-OTHER"
    second["customer_id"] = 3002
    assert client.post("/api/orders", headers=signed(cfg), json=second).status_code == 201
    result = client.get("/api/bootstrap", headers=signed(cfg, 3001), params={"q": "%_"}).json()
    assert [o["number"] for o in result["orders"]] == ["MJ-100%_SPECIAL"]
    assert (
        client.get("/api/bootstrap", headers=signed(cfg, 3002), params={"q": "SPECIAL"}).json()[
            "orders"
        ]
        == []
    )
    assert (
        client.get(
            "/api/bootstrap", headers=signed(cfg, 3001), params={"q": "PRIVATE-NAME"}
        ).json()["orders"]
        == []
    )
    assert (
        client.get("/api/bootstrap", headers=signed(cfg), params={"q": "PRIVATE-NAME"}).json()[
            "summary"
        ]["matching"]
        == 1
    )


def test_filters_counts_and_search_beyond_first_page(client, cfg, db):
    from majlis.schemas import NewOrder

    with db.connection() as conn:
        ensure_user(conn, {"id": 9001})
        for i in range(105):
            data = payload()
            data["number"] = f"MJ-{i:03}"
            create_order(conn, NewOrder(**data), 9001)
        conn.execute("UPDATE orders SET status='delivered' WHERE number='MJ-000'")
    first = client.get("/api/bootstrap", headers=signed(cfg)).json()
    assert len(first["orders"]) == 100 and first["summary"] == {
        "total": 105,
        "active": 104,
        "delivered": 1,
        "matching": 105,
    }
    result = client.get(
        "/api/bootstrap", headers=signed(cfg), params={"q": "MJ-000", "bucket": "delivered"}
    ).json()
    assert [o["number"] for o in result["orders"]] == ["MJ-000"]
    assert result["summary"]["matching"] == 1
    assert (
        client.get(
            "/api/bootstrap", headers=signed(cfg), params={"bucket": "active", "q": "MJ-000"}
        ).json()["orders"]
        == []
    )
    page = client.get(
        "/api/bootstrap", headers=signed(cfg), params={"before": first["next_cursor"]}
    ).json()
    assert len(page["orders"]) == 5 and page["summary"]["total"] == 105
    assert (
        client.get("/api/bootstrap", headers=signed(cfg), params={"q": "x" * 101}).status_code
        == 422
    )


def test_single_order_reload_respects_ownership(client, cfg):
    order = created(client, cfg)
    assert (
        client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 3001)).json()["number"]
        == "MJ-TEST-1"
    )
    assert (
        client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 3002)).status_code == 404
    )
    assert client.get("/api/orders/" + str(order["id"]), headers=signed(cfg)).status_code == 200
    assert client.get("/api/orders/-1", headers=signed(cfg)).status_code == 404

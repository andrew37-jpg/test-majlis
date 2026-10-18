import hashlib
import hmac
import json
import time

import psycopg
from test_platform import created, payload, signed


def grant(cfg, uid, oid=None, edit=False, create=False, enabled=True):
    with psycopg.connect(cfg.migration_database_url.get_secret_value()) as c:
        c.execute("INSERT INTO users(telegram_id) VALUES (%s) ON CONFLICT DO NOTHING", (uid,))
        c.execute(
            "INSERT INTO manager_access(telegram_id,enabled,can_create) VALUES (%s,%s,%s) ON CONFLICT(telegram_id) DO UPDATE SET enabled=excluded.enabled,can_create=excluded.can_create",
            (uid, enabled, create),
        )
        if oid is not None:
            c.execute(
                "INSERT INTO order_managers(order_id,manager_id,can_edit) VALUES (%s,%s,%s) ON CONFLICT(order_id,manager_id) DO UPDATE SET can_edit=excluded.can_edit",
                (oid, uid, edit),
            )


def cabinet(cfg, uid, role):
    return {**signed(cfg, uid), "X-MAJLIS-Cabinet": role}


def test_cabinet_switch_scopes_global_manager_and_prevents_buyer_elevation(client, cfg):
    first = created(client, cfg)
    own = payload()
    own.update(number="MANAGER-OWN", customer_id=9001)
    second = client.post("/api/orders", headers=signed(cfg), json=own).json()
    buyer = cabinet(cfg, 9001, "buyer")
    result = client.get("/api/bootstrap", headers=buyer).json()
    assert [o["id"] for o in result["orders"]] == [second["id"]]
    assert result["user"]["available_cabinets"] == ["buyer", "manager"]
    assert result["user"]["can_create"] is False
    assert result["orders"][0]["permissions"] == {"edit": False}
    assert client.get("/api/orders/" + str(first["id"]), headers=buyer).status_code == 404
    assert client.post("/api/orders", headers=buyer, json=payload()).status_code == 403
    assert client.get("/api/bootstrap", headers=cabinet(cfg, 3001, "manager")).status_code == 403
    assert client.get("/api/bootstrap", headers=cabinet(cfg, 3001, "root")).status_code == 400
    result = client.get("/api/bootstrap", headers=cabinet(cfg, 9001, "manager")).json()
    assert len(result["orders"]) == 2 and result["user"]["can_create"]


def test_scoped_manager_reads_only_assigned_orders_and_queue(client, cfg):
    first = created(client, cfg)
    own = payload()
    own.update(number="OTHER", customer_id=3002)
    other = client.post("/api/orders", headers=signed(cfg), json=own).json()
    client.post(
        "/api/orders/status",
        headers=signed(cfg),
        json={"order_id": first["id"], "version": 1, "status": "purchased"},
    )
    client.post(
        "/api/orders/status",
        headers=signed(cfg),
        json={"order_id": other["id"], "version": 1, "status": "purchased"},
    )
    grant(cfg, 8001, first["id"])
    headers = cabinet(cfg, 8001, "manager")
    result = client.get("/api/bootstrap", headers=headers).json()
    assert result["summary"]["total"] == 1 and len(result["notifications"]) == 1
    assert (
        result["orders"][0]["id"] == first["id"] and not result["orders"][0]["permissions"]["edit"]
    )
    assert client.get("/api/orders/" + str(first["id"]), headers=headers).status_code == 200
    for suffix in ["", "/history"]:
        assert (
            client.get("/api/orders/" + str(other["id"]) + suffix, headers=headers).status_code
            == 404
        )
    assert client.post("/api/orders", headers=headers, json=payload()).status_code == 403
    assert (
        client.post(
            "/api/orders/status",
            headers=headers,
            json={"order_id": first["id"], "version": 2, "status": "customs"},
        ).status_code
        == 403
    )
    assert (
        client.get("/api/bootstrap", headers={**headers}, params={"q": "OTHER"}).json()["orders"]
        == []
    )


def test_each_mutation_enforces_assignment_and_readonly_permissions(client, cfg):
    order = created(client, cfg)
    grant(cfg, 8002, order["id"])
    mutation = {
        "status": {"status": "purchased"},
        "commercial": {"item": "Changed", "amount": 100},
        "details": {"customer_id": 3001, "customer_name": "Name"},
        "payment": {"url": "https://payments.example/invoice"},
        "tracking": {"tracking_number": "T1", "carrier": "Carrier"},
    }
    for path, extra in mutation.items():
        data = {"order_id": order["id"], "version": 1, **extra}
        assert (
            client.post(
                "/api/orders/" + path, headers=cabinet(cfg, 8002, "manager"), json=data
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/orders/" + path, headers=cabinet(cfg, 3001, "buyer"), json=data
            ).status_code
            == 403
        )
    grant(cfg, 8002, order["id"], edit=True)
    data = {"order_id": order["id"], "version": 1, "tracking_number": "T1", "carrier": "Carrier"}
    assert (
        client.post(
            "/api/orders/tracking", headers=cabinet(cfg, 8002, "manager"), json=data
        ).status_code
        == 200
    )
    grant(cfg, 8002, enabled=False)
    assert (
        client.get(
            "/api/orders/" + str(order["id"]), headers=cabinet(cfg, 8002, "manager")
        ).status_code
        == 403
    )


def test_scoped_create_assigns_only_creator_and_manager_can_also_be_buyer(client, cfg):
    grant(cfg, 8003, create=True)
    data = payload()
    data["customer_id"] = 8003
    result = client.post("/api/orders", headers=cabinet(cfg, 8003, "manager"), json=data)
    assert result.status_code == 201
    for role in ["buyer", "manager"]:
        rows = client.get("/api/bootstrap", headers=cabinet(cfg, 8003, role)).json()["orders"]
        assert len(rows) == 1 and rows[0]["permissions"]["edit"] == (role == "manager")
    grant(cfg, 8004)
    assert (
        client.get("/api/bootstrap", headers=cabinet(cfg, 8004, "manager")).json()["orders"] == []
    )


def test_tracking_revision_idempotence_and_no_payment_claim(client, cfg, db):
    order = created(client, cfg)
    oid = order["id"]
    data = {
        "order_id": oid,
        "version": 1,
        "tracking_number": "<TRACK&1>",
        "carrier": "Confirmed carrier",
    }
    r = client.post("/api/orders/tracking", headers=signed(cfg), json=data)
    assert r.status_code == 200
    order = r.json()
    assert order["version"] == 2 and order["tracking"]["number"] == "<TRACK&1>"
    assert order["tracking"]["external_state"] == "not_received" and order["tracking"]["updated_at"]
    assert (
        order["tracking"]["payment_state"] == "not_confirmed"
        and order["tracking"]["order_state"] == "not_provided"
    )
    data["version"] = 2
    assert (
        client.post("/api/orders/tracking", headers=signed(cfg), json=data).json()["version"] == 2
    )
    data["version"] = 1
    assert client.post("/api/orders/tracking", headers=signed(cfg), json=data).status_code == 409
    buyer = client.get("/api/orders/" + str(oid), headers=signed(cfg, 3001)).json()
    assert buyer["tracking"]["carrier"] == "Confirmed carrier" and not buyer["permissions"]["edit"]
    with db.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 1
    assert client.get("/api/orders/" + str(oid), headers=signed(cfg, 3002)).status_code == 404


def event(client, cfg, value):
    stamp = str(int(time.time()))
    body = json.dumps(value).encode()
    sig = hmac.new(
        cfg.integration_secret.get_secret_value().encode(),
        stamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return client.post(
        "/api/integrations/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Event-Timestamp": stamp,
            "X-Event-Signature": sig,
        },
    )


def test_external_tracking_replay_out_of_order_and_staleness(client, cfg):
    order = created(client, cfg)
    oid = order["id"]
    data = {
        "event_id": "tracking-1",
        "shipment": "TEST-CARGO",
        "status": "delivered",
        "sequence": 2,
    }
    assert event(client, cfg, data).json()["updated"] == 1
    assert event(client, cfg, data).json()["duplicate"]
    late = {**data, "event_id": "late", "status": "purchased", "sequence": 1}
    assert event(client, cfg, late).json()["updated"] == 0
    result = client.get("/api/orders/" + str(oid), headers=signed(cfg, 3001)).json()
    assert result["status"] == "delivered" and result["tracking"]["external_state"] == "received"
    assert result["tracking"]["payment_state"] == "not_confirmed"
    bad = {**data, "event_id": "invalid", "status": "not-a-known-status"}
    assert event(client, cfg, bad).status_code == 400
    with psycopg.connect(cfg.migration_database_url.get_secret_value()) as c:
        c.execute(
            "UPDATE orders SET external_updated_at=now()-interval '25 hours' WHERE id=%s", (oid,)
        )
    result = client.get("/api/orders/" + str(oid), headers=signed(cfg, 3001)).json()
    assert result["tracking"]["external_state"] == "stale" and result["status"] == "delivered"
    assert event(client, cfg, data).json()["duplicate"]
    assert (
        client.get("/api/orders/" + str(oid), headers=signed(cfg, 3001)).json()["tracking"][
            "external_state"
        ]
        == "stale"
    )


def test_runtime_cannot_edit_acl_and_permission_check_locks_revocation(client, cfg):
    import pytest

    order = created(client, cfg)
    grant(cfg, 8005, order["id"], edit=True)
    with psycopg.connect(cfg.database_url.get_secret_value()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE manager_access SET can_create=true WHERE telegram_id=8005")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE order_managers SET can_edit=true")
        conn.rollback()
        assert (
            conn.execute(
                "SELECT public.manager_order_access(%s,%s)", (8005, order["id"])
            ).fetchone()[0]
            is True
        )
        with psycopg.connect(cfg.migration_database_url.get_secret_value()) as operator:
            operator.execute("SET LOCAL lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                operator.execute("UPDATE manager_access SET enabled=false WHERE telegram_id=8005")
    grant(cfg, 8005, enabled=False)
    assert client.get("/api/bootstrap", headers=cabinet(cfg, 8005, "manager")).status_code == 403

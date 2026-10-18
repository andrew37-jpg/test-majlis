"""Audit reproduction cases on the isolated majlis_audit_*_test database only."""

import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from test_platform import created, payload, queued, signed

from majlis.bot import deliver


def test_transfer_isolates_previous_personal_fields(client, cfg):
    order = created(client, cfg)
    assert (
        client.post(
            "/api/orders/payment",
            headers=signed(cfg),
            json={
                "order_id": order["id"],
                "version": 1,
                "url": "https://payments.example/invoice/old-buyer",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/orders/status",
            headers=signed(cfg),
            json={
                "order_id": order["id"],
                "version": 2,
                "status": "purchased",
                "note": "Private note for previous customer (fictional)",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/orders/details",
            headers=signed(cfg),
            json={
                "order_id": order["id"],
                "version": 3,
                "customer_id": 4001,
                "customer_name": "Новый покупатель",
                "transfer_confirmed": True,
                "shipment": "NEW-PARTY",
                "eta": "",
                "eta_confirmed": False,
            },
        ).status_code
        == 200
    )
    new = client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 4001))
    assert new.status_code == 200
    data = new.json()
    assert data["customer_name"] == "Новый покупатель"
    assert data["payment_url"] == ""
    assert not any("previous customer" in e["note"] for e in data["events"])
    assert (
        client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 3001)).status_code == 404
    )


def test_late_integration_does_not_regress_delivered_order(client, cfg):
    order = created(client, cfg)

    def send(event, status):
        raw = json.dumps({"event_id": event, "shipment": "TEST-CARGO", "status": status}).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(
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
                "X-Event-Signature": signature,
            },
        )

    assert send("NEW-DELIVERED", "delivered").status_code == 200
    assert send("LATE-CUSTOMS", "customs").status_code == 200
    assert (
        client.get("/api/orders/" + str(order["id"]), headers=signed(cfg, 3001)).json()["status"]
        == "delivered"
    )


def test_public_ready_is_rejected(client):
    results = [client.get("/ready").status_code for _ in range(121)]
    assert set(results) <= {403, 429} and 403 in results


def test_signed_identity_valid_within_window(client, cfg):
    headers = signed(cfg, 3001)
    assert client.get("/api/bootstrap", headers=headers).status_code == 200
    assert client.get("/api/bootstrap", headers=headers).status_code == 200


def test_sql_injection_and_mass_assignment_denied(client, cfg):
    order = created(client, cfg)
    assert (
        client.get("/api/bootstrap", headers=signed(cfg, 3002), params={"q": "' OR 1=1 --"}).json()[
            "orders"
        ]
        == []
    )
    data = payload()
    data["number"] = "MJ-ATTEMPT"
    data["role"] = "manager"
    assert client.post("/api/orders", headers=signed(cfg), json=data).status_code == 422
    assert (
        client.post(
            "/api/orders/details",
            headers=signed(cfg, 3002),
            json={
                "order_id": order["id"],
                "version": 1,
                "customer_id": 3002,
                "shipment": "",
                "eta": "",
                "eta_confirmed": False,
            },
        ).status_code
        == 403
    )


def test_outbox_network_does_not_hold_order_row_lock(db):
    order = queued(db)
    started = threading.Event()
    release = threading.Event()

    class SlowAPI:
        def send(self, *args):
            started.set()
            assert release.wait(3)

    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(deliver, db, SlowAPI())
        try:
            assert started.wait(2)
            with db.connection() as conn:
                conn.execute("SELECT id FROM orders WHERE id=%s FOR UPDATE NOWAIT", (order["id"],))
        finally:
            release.set()
        task.result(timeout=4)


def test_unsigned_and_buyer_mutations_denied(client, cfg):
    assert client.post("/api/orders", json=payload()).status_code == 401
    assert (
        client.get(
            "/api/bootstrap", headers={"X-Telegram-Init-Data": "user=forged&auth_date=1&hash=bad"}
        ).status_code
        == 401
    )
    assert client.post("/api/orders", headers=signed(cfg, 3001), json=payload()).status_code == 403


def test_early_errors_include_security_headers(client):
    response = client.post("/api/orders", json={"data": "a" * 70000})
    assert response.status_code == 413
    assert "content-security-policy" in response.headers
    assert response.headers["cache-control"] == "no-store"

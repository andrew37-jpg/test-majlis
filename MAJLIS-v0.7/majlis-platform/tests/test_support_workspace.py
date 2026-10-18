from types import SimpleNamespace

import psycopg
from test_cabinets_tracking import cabinet, grant
from test_platform import created, signed


def allow_inbox(cfg, uid, all_orders=False):
    grant(cfg, uid)
    with psycopg.connect(cfg.migration_database_url.get_secret_value()) as c:
        c.execute(
            "UPDATE manager_access SET can_read_inbox=true,all_orders=%s WHERE telegram_id=%s",
            (all_orders, uid),
        )


def test_buyer_request_is_idempotent_private_and_cannot_spoof_customer(client, cfg, db):
    data = {"text": "Вопрос о моём заказе <script>", "idempotency_key": "request-key-00001"}
    a = client.post("/api/support", headers=signed(cfg, 3001), json=data)
    b = client.post("/api/support", headers=signed(cfg, 3001), json=data)
    assert a.status_code == 201 and b.json() == {"id": a.json()["id"], "duplicate": True}
    assert (
        client.post(
            "/api/support", headers=signed(cfg, 3001), json={**data, "text": "Different"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/support", headers=signed(cfg, 3001), json={**data, "customer_id": 3002}
        ).status_code
        == 422
    )
    assert client.get("/api/manager/inbox", headers=signed(cfg, 3001)).status_code == 403
    assert client.get("/api/manager/activity", headers=signed(cfg, 3001)).status_code == 403
    mine = client.get("/api/me/export", headers=signed(cfg, 3001)).json()["messages"]
    assert len(mine) == 1 and mine[0]["text"] == data["text"]
    assert client.get("/api/me/export", headers=signed(cfg, 3002)).json()["messages"] == []
    with db.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM customer_messages").fetchone()["n"] == 1


def test_inbox_requires_separate_explicit_permission_even_for_global_manager(client, cfg):
    client.post(
        "/api/support",
        headers=signed(cfg, 3001),
        json={"text": "Request", "idempotency_key": "request-0001"},
    )
    assert client.get("/api/manager/inbox", headers=signed(cfg)).status_code == 403
    allow_inbox(cfg, 8006, all_orders=True)
    result = client.get("/api/manager/inbox", headers=cabinet(cfg, 8006, "manager"))
    assert result.status_code == 200 and result.json()["items"][0]["customer_id"] == 3001
    assert client.get("/api/manager/inbox", headers=cabinet(cfg, 8006, "buyer")).status_code == 403
    with psycopg.connect(cfg.migration_database_url.get_secret_value()) as c:
        c.execute("UPDATE manager_access SET can_read_inbox=false WHERE telegram_id=8006")
    assert (
        client.get("/api/manager/inbox", headers=cabinet(cfg, 8006, "manager")).status_code == 403
    )


def test_manager_activity_obeys_order_assignments(client, cfg):
    order = created(client, cfg)
    grant(cfg, 8007)
    assert (
        client.get("/api/manager/activity", headers=cabinet(cfg, 8007, "manager")).json()["items"]
        == []
    )
    grant(cfg, 8007, order["id"])
    assert (
        client.get("/api/manager/activity", headers=cabinet(cfg, 8007, "manager")).json()["items"][
            0
        ]["order_id"]
        == order["id"]
    )
    allow_inbox(cfg, 8008, all_orders=True)
    result = client.get("/api/bootstrap", headers=cabinet(cfg, 8008, "manager")).json()
    assert result["summary"]["total"] == 1 and result["user"]["manager_all"]


def test_support_limit_keeps_retry_safe(client, cfg):
    for n in range(5):
        assert (
            client.post(
                "/api/support",
                headers=signed(cfg, 3001),
                json={"text": str(n), "idempotency_key": "request-key-" + str(n)},
            ).status_code
            == 201
        )
    assert (
        client.post(
            "/api/support",
            headers=signed(cfg, 3001),
            json={"text": "overflow", "idempotency_key": "request-key-6"},
        ).status_code
        == 429
    )
    assert client.post(
        "/api/support",
        headers=signed(cfg, 3001),
        json={"text": "0", "idempotency_key": "request-key-0"},
    ).json()["duplicate"]


def test_bot_records_only_direct_messages_once_and_no_external_send(db, cfg):
    from majlis.bot import respond

    calls = []
    api = SimpleNamespace(send=lambda *args: calls.append(args))
    msg = {
        "from": {"id": 3001, "first_name": "Fictional"},
        "chat": {"id": 3001, "type": "private"},
        "message_id": 17,
        "text": "Question",
    }
    respond(db, api, msg, "https://example.com")
    respond(db, api, msg, "https://example.com")
    respond(
        db,
        api,
        {**msg, "message_id": 18, "chat": {"id": -1, "type": "group"}},
        "https://example.com",
    )
    assert calls == []
    with db.connection() as c:
        rows = c.execute("SELECT customer_id,text FROM customer_messages").fetchall()
        assert rows == [{"customer_id": 3001, "text": "Question"}]


def test_manager_entry_command_checks_actual_sender_and_is_not_publicly_listed(db, cfg):
    from majlis.bot import respond

    calls = []
    api = SimpleNamespace(send=lambda *args: calls.append(args))
    msg = {
        "from": {"id": 3001},
        "chat": {"id": 3001, "type": "private"},
        "message_id": 1,
        "text": "/manager",
    }
    respond(db, api, msg, "https://example.com/")
    assert calls[-1] == (3001, "Раздел недоступен.")
    allow_inbox(cfg, 8009, all_orders=True)
    respond(
        db,
        api,
        {**msg, "from": {"id": 8009}, "chat": {"id": 8009, "type": "private"}},
        "https://example.com/",
    )
    assert calls[-1][0] == 8009 and calls[-1][2] == "https://example.com/manager/"

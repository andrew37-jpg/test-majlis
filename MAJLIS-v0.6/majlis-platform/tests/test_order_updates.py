from test_platform import created, signed


def test_commercial_edit_clears_stale_invoice_and_is_scoped(client, cfg, db):
    order = created(client, cfg)
    assert (
        client.post(
            "/api/orders/payment",
            headers=signed(cfg),
            json={"order_id": order["id"], "version": 1, "url": "https://payments.example/old"},
        ).status_code
        == 200
    )
    data = {"order_id": order["id"], "version": 2, "item": "Updated item", "amount": 50001}
    assert (
        client.post("/api/orders/commercial", headers=signed(cfg, 3001), json=data).status_code
        == 403
    )
    updated = client.post("/api/orders/commercial", headers=signed(cfg), json=data)
    assert updated.status_code == 200
    assert updated.json()["payment_url"] == "" and updated.json()["amount"] == 50001
    assert updated.json()["events"][-1]["kind"] == "commercial"
    assert client.post("/api/orders/commercial", headers=signed(cfg), json=data).status_code == 409
    data["version"] = 3
    assert (
        client.post("/api/orders/commercial", headers=signed(cfg), json=data).json()["version"] == 3
    )
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 2


def test_delivery_change_is_recorded_and_notified_without_checkpoint_change(client, cfg, db):
    order = created(client, cfg)
    data = {
        "order_id": order["id"],
        "version": 1,
        "customer_id": 3001,
        "shipment": "NEW-PARTY",
        "eta": "2026-11-10",
        "eta_confirmed": True,
    }
    response = client.post("/api/orders/details", headers=signed(cfg), json=data)
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert response.json()["events"][-1]["kind"] == "details"
    with db.connection() as conn:
        assert conn.execute("SELECT customer_id,text FROM outbox").fetchone()["customer_id"] == 3001
        assert (
            conn.execute("SELECT count(*) AS n FROM events WHERE kind='status'").fetchone()["n"]
            == 1
        )


def test_payment_revoke_allowed_if_provider_disabled_and_noop_no_duplicate(client, cfg, db):
    order = created(client, cfg)
    assert (
        client.post(
            "/api/orders/payment",
            headers=signed(cfg),
            json={
                "order_id": order["id"],
                "version": 1,
                "url": "https://payments.example/invoice-secret-test",
            },
        ).status_code
        == 200
    )
    cfg.payment_hosts = ""
    result = client.post(
        "/api/orders/payment",
        headers=signed(cfg),
        json={"order_id": order["id"], "version": 2, "url": ""},
    )
    assert result.status_code == 200 and result.json()["payment_url"] == ""
    assert result.json()["events"][-1]["kind"] == "payment"
    assert (
        client.post(
            "/api/orders/payment",
            headers=signed(cfg),
            json={"order_id": order["id"], "version": 3, "url": ""},
        ).json()["version"]
        == 3
    )
    with db.connection() as conn:
        rows = conn.execute("SELECT text FROM outbox").fetchall()
        assert len(rows) == 2 and all("invoice-secret-test" not in r["text"] for r in rows)


def test_invalid_commercial_data_is_rejected(client, cfg):
    order = created(client, cfg)
    for amount in [True, 0, 1.5, -1]:
        assert (
            client.post(
                "/api/orders/commercial",
                headers=signed(cfg),
                json={"order_id": order["id"], "version": 1, "item": "Valid", "amount": amount},
            ).status_code
            == 422
        )


def test_migration_classifies_only_audit_proven_v05_transfers(cfg):
    from pathlib import Path

    import psycopg
    from psycopg.rows import dict_row

    with (
        psycopg.connect(
            cfg.migration_database_url.get_secret_value(), row_factory=dict_row
        ) as conn,
        conn.transaction(force_rollback=True),
    ):
        conn.execute("CREATE SCHEMA v05_transfer_rehearsal")
        conn.execute("SET LOCAL search_path=v05_transfer_rehearsal")
        for name in [
            "001_initial.sql",
            "002_ownership_and_events.sql",
            "003_event_kinds_and_import_map.sql",
        ]:
            conn.execute((Path("migrations") / name).read_text())
        conn.execute("INSERT INTO users(telegram_id) VALUES (3001),(9001)")
        conn.execute(
            "INSERT INTO orders(number,customer_id,customer_name,item,amount,status,customer_generation) VALUES ('V05',3001,'Synthetic','Synthetic',1,'confirmed',2)"
        )
        conn.execute(
            "INSERT INTO events(order_id,status,note,source,actor_id,customer_id,customer_generation) VALUES (1,'confirmed','Заказ привязан к вашему кабинету','manager',9001,3001,2)"
        )
        conn.execute(
            "INSERT INTO events(order_id,status,note,source,actor_id,customer_id,customer_generation) VALUES (1,'confirmed','Other actual checkpoint','manager',9001,3001,2)"
        )
        conn.execute(
            "INSERT INTO audit_log(actor_id,action,order_id,after_data) VALUES (9001,'transfer',1,'{\"customer_generation\":2}')"
        )
        conn.execute(Path("migrations/004_classify_verified_transfers.sql").read_text())
        kinds = conn.execute("SELECT kind FROM events ORDER BY id").fetchall()
        assert [row["kind"] for row in kinds] == ["transfer", "status"]

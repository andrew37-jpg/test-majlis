import hashlib
from datetime import date, datetime

from psycopg import sql
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .domain import STAGE_MAP, STAGES, Problem, https_url


def serial(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serial(v) for v in value]
    return value


def ensure_user(db, user, started=False):
    db.execute(
        """INSERT INTO users(telegram_id,first_name,bot_started) VALUES (%s,%s,%s)
        ON CONFLICT(telegram_id) DO UPDATE SET first_name=CASE WHEN excluded.first_name<>'' THEN excluded.first_name ELSE users.first_name END,
        bot_started=users.bot_started OR excluded.bot_started,updated_at=now()""",
        (user["id"], str(user.get("first_name", ""))[:100], started),
    )


def get_order(db, oid, lock=False):
    row = db.execute(
        sql.SQL("SELECT * FROM orders WHERE id=%s") + sql.SQL(" FOR UPDATE" if lock else ""), (oid,)
    ).fetchone()
    if not row:
        raise Problem(404, "Заказ не найден.")
    return row


def public_order(row, events):
    data = serial(row)
    data["eta"] = data["eta"] or ""
    data["stage"] = STAGE_MAP[row["status"]]
    data["events"] = serial(events)
    data["history_cursor"] = events[0]["id"] if len(events) == 100 else None
    return data


def order_json(db, oid, user=None):
    row = get_order(db, oid)
    events = order_events(db, row, user)
    return public_order(row, events)


def order_events(db, row, user=None, before=None):
    # A buyer receives only events from the CURRENT ownership generation.
    manager = user is None or user["role"] == "manager"
    query = sql.SQL(
        "SELECT id,order_id,status,note,source,created_at,customer_id,customer_generation,kind FROM events WHERE order_id=%s"
    )
    params = [row["id"]]
    if not manager:
        query += sql.SQL(" AND customer_id=%s AND customer_generation=%s")
        params += [user["id"], row["customer_generation"]]
    if before:
        query += sql.SQL(" AND id<%s")
        params.append(before)
    return db.execute(query + sql.SQL(" ORDER BY id DESC LIMIT 100"), params).fetchall()[::-1]


def add_event(db, row, note, source, actor=None, kind="status"):
    return db.execute(
        "INSERT INTO events(order_id,status,note,source,actor_id,customer_id,customer_generation,kind) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            row["id"],
            row["status"],
            note,
            source,
            actor,
            row["customer_id"],
            row["customer_generation"],
            kind,
        ),
    ).fetchone()


def audit(db, actor, action, before, after):
    # No names, notes, product description, or secret invoice URL in new audit records.
    fields = (
        "id",
        "customer_id",
        "customer_generation",
        "status",
        "version",
        "eta",
        "eta_confirmed",
    )

    def minimal(value):
        return {key: serial(value[key]) for key in fields if key in value}

    db.execute(
        "INSERT INTO audit_log(actor_id,action,order_id,before_data,after_data) VALUES (%s,%s,%s,%s,%s)",
        (actor, action, after["id"], Jsonb(minimal(before)), Jsonb(minimal(after))),
    )


def bootstrap(db, user, cfg, before=None, q="", bucket="all"):
    if bucket not in ("all", "active", "delivered"):
        raise Problem(400, "Неизвестный фильтр заказов.")
    params = []
    clauses = []
    if user["role"] != "manager":
        clauses.append("customer_id=%s")
        params.append(user["id"])
    scope = " WHERE " + " AND ".join(clauses) if clauses else ""
    summary = db.execute(
        sql.SQL(
            "SELECT count(*) AS total,count(*) FILTER (WHERE status<>'delivered') AS active,count(*) FILTER (WHERE status='delivered') AS delivered FROM orders"
        )
        + sql.SQL(scope),
        params,
    ).fetchone()
    if bucket == "active":
        clauses.append("status<>'delivered'")
    elif bucket == "delivered":
        clauses.append("status='delivered'")
    q = q.strip()
    if q:
        needle = (
            "%"
            + q.replace(chr(92), chr(92) * 2)
            .replace("%", chr(92) + "%")
            .replace("_", chr(92) + "_")
            + "%"
        )
        fields = ["number", "item", "shipment"] + (
            ["customer_name", "customer_id::text"] if user["role"] == "manager" else []
        )
        clauses.append("(" + " OR ".join(field + " ILIKE %s" for field in fields) + ")")
        params.extend([needle] * len(fields))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    summary["matching"] = db.execute(
        sql.SQL("SELECT count(*) AS n FROM orders") + sql.SQL(where), params
    ).fetchone()["n"]
    if before:
        clauses.append("id<%s")
        params.append(before)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = db.execute(
        sql.SQL("SELECT * FROM orders") + sql.SQL(where) + sql.SQL(" ORDER BY id DESC LIMIT 101"),
        params,
    ).fetchall()
    more = len(rows) > 100
    rows = rows[:100]
    grouped = {row["id"]: [] for row in rows}
    if rows:
        # One bounded lateral query rather than one query per returned order.
        scope = (
            sql.SQL("TRUE")
            if user["role"] == "manager"
            else sql.SQL("e.customer_id=%s AND e.customer_generation=o.customer_generation")
        )
        event_params = [] if user["role"] == "manager" else [user["id"]]
        event_params.append([row["id"] for row in rows])
        query = sql.SQL(
            "SELECT e.* FROM orders o CROSS JOIN LATERAL (SELECT id,order_id,status,note,source,created_at,customer_id,customer_generation,kind FROM events e WHERE e.order_id=o.id AND {} ORDER BY id DESC LIMIT 100) e WHERE o.id=ANY(%s) ORDER BY e.id"
        ).format(scope)
        for event in db.execute(query, event_params).fetchall():
            grouped[event["order_id"]].append(event)
    result = {
        "user": user,
        "demo": False,
        "stages": [{"code": s[0], "title": s[1], "description": s[2]} for s in STAGES],
        "orders": [public_order(row, grouped[row["id"]]) for row in rows],
        "summary": summary,
        "updated_at": datetime.now().astimezone().isoformat(),
        "next_cursor": rows[-1]["id"] if more else None,
        "links": {"catalog": cfg.catalog_url, "support": cfg.support_url},
        "integrations": {
            "cargo": "not_connected",
            "delivery": "not_connected",
            "website": "not_connected",
            "payments": "manual_links" if cfg.payment_domains else "not_connected",
        },
    }
    if user["role"] == "manager":
        result["notifications"] = serial(
            db.execute(
                "SELECT id,state,attempts,last_error FROM outbox ORDER BY id DESC LIMIT 30"
            ).fetchall()
        )
    return result


def create_order(db, data, actor):
    ensure_user(db, {"id": data.customer_id})
    try:
        with db.transaction():
            row = db.execute(
                """INSERT INTO orders(number,customer_id,customer_name,item,amount,shipment,status,eta)
                VALUES (%s,%s,%s,%s,%s,%s,'confirmed',%s) RETURNING *""",
                (
                    data.number,
                    data.customer_id,
                    data.customer_name,
                    data.item,
                    data.amount,
                    data.shipment,
                    data.eta,
                ),
            ).fetchone()
    except UniqueViolation:
        raise Problem(409, "Такой номер заказа уже существует.") from None
    add_event(db, row, "Заказ зарегистрирован", "manager", actor)
    audit(db, actor, "create", {}, row)
    return order_json(db, row["id"])


def check_revision(row, version):
    if row["version"] != version:
        raise Problem(409, "Заказ уже изменён. Обновите кабинет и повторите действие.")


def change_status(db, oid, status, note, source, actor=None, version=None):
    if status not in STAGE_MAP:
        raise Problem(400, "Неизвестный этап доставки.")
    old = get_order(db, oid, lock=True)
    if version is not None:
        check_revision(old, version)
    if old["status"] == status:
        return order_json(db, oid)
    row = db.execute(
        "UPDATE orders SET status=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *",
        (status, oid),
    ).fetchone()
    event = add_event(db, row, note, source, actor)
    stage = STAGE_MAP[status]
    text = f"Заказ {old['number']}\n{stage['title']}\n{stage['description']}"
    db.execute(
        "INSERT INTO outbox(event_id,customer_id,text) VALUES (%s,%s,%s)",
        (event["id"], old["customer_id"], text),
    )
    audit(db, actor, "status", old, row)
    return order_json(db, oid)


def update_details(db, data, actor):
    old = get_order(db, data.order_id, lock=True)
    check_revision(old, data.version)
    changed = old["customer_id"] != data.customer_id
    if changed:
        if not data.customer_name or not data.transfer_confirmed:
            raise Problem(422, "При передаче подтвердите смену покупателя и укажите новое имя.")
        in_flight = db.execute(
            "SELECT EXISTS(SELECT 1 FROM outbox n JOIN events e ON e.id=n.event_id WHERE e.order_id=%s AND n.state='sending') AS busy",
            (data.order_id,),
        ).fetchone()["busy"]
        if in_flight:
            raise Problem(
                409,
                "Уведомление ещё обрабатывается. Дождитесь завершения или восстановления worker.",
            )
        if not db.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS locked", (-data.order_id,)
        ).fetchone()["locked"]:
            raise Problem(409, "Сейчас отправляется уведомление. Повторите передачу заказа позже.")
    ensure_user(db, {"id": data.customer_id})
    if changed:
        db.execute(
            "UPDATE outbox SET state='failed',last_error='owner_changed',lease_until=NULL WHERE event_id IN (SELECT id FROM events WHERE order_id=%s) AND state IN ('pending','sending')",
            (data.order_id,),
        )
    row = db.execute(
        """UPDATE orders SET customer_id=%s,customer_name=%s,shipment=%s,eta=%s,eta_confirmed=%s,
        customer_generation=customer_generation+%s,payment_url=CASE WHEN %s THEN '' ELSE payment_url END,
        version=version+1,updated_at=now() WHERE id=%s RETURNING *""",
        (
            data.customer_id,
            data.customer_name or old["customer_name"],
            data.shipment,
            data.eta,
            data.eta_confirmed and bool(data.eta),
            int(changed),
            changed,
            data.order_id,
        ),
    ).fetchone()
    if changed:
        add_event(db, row, "Заказ привязан к вашему кабинету", "manager", actor, kind="transfer")
    if not changed and any(old[key] != row[key] for key in ("eta", "eta_confirmed", "shipment")):
        note = "Данные поставки обновлены. " + (
            "Дата: "
            + str(row["eta"])
            + (" (подтверждена)" if row["eta_confirmed"] else " (ориентировочно)")
            if row["eta"]
            else "Дата уточняется."
        )
        event = add_event(db, row, note, "manager", actor, kind="details")
        queue_event(db, row, event, "Поставка уточнена. " + note)
    audit(db, actor, "transfer" if changed else "details", old, row)
    return order_json(db, data.order_id)


def queue_event(db, row, event, text):
    db.execute(
        "INSERT INTO outbox(event_id,customer_id,text) VALUES (%s,%s,%s)",
        (event["id"], row["customer_id"], f"Заказ {row['number']}\n{text}"),
    )


def update_payment(db, data, actor, cfg):
    if data.url and not cfg.payment_domains:
        raise Problem(503, "Платёжный сервис не настроен.")
    url = https_url(data.url, cfg.payment_domains) if data.url else ""
    old = get_order(db, data.order_id, lock=True)
    check_revision(old, data.version)
    if old["payment_url"] == url:
        return order_json(db, data.order_id)
    row = db.execute(
        "UPDATE orders SET payment_url=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *",
        (url, data.order_id),
    ).fetchone()
    note = (
        "Добавлена ссылка на оплату. Ссылка не подтверждает оплату."
        if url
        else "Ссылка на оплату удалена. Уточните оплату у магазина."
    )
    event = add_event(db, row, note, "manager", actor, kind="payment")
    queue_event(db, row, event, note)
    audit(db, actor, "payment_link" if url else "payment_revoke", old, row)
    return order_json(db, data.order_id)


def update_commercial(db, data, actor):
    old = get_order(db, data.order_id, lock=True)
    check_revision(old, data.version)
    if old["item"] == data.item and old["amount"] == data.amount:
        return order_json(db, data.order_id)
    row = db.execute(
        "UPDATE orders SET item=%s,amount=%s,payment_url='',version=version+1,updated_at=now() WHERE id=%s RETURNING *",
        (data.item, data.amount, data.order_id),
    ).fetchone()
    note = "Товар или сумма заказа уточнены. Прежняя ссылка оплаты в кабинете удалена; уточните актуальную оплату у магазина."
    event = add_event(db, row, note, "manager", actor, kind="commercial")
    queue_event(db, row, event, note)
    audit(db, actor, "commercial", old, row)
    return order_json(db, data.order_id)


def integration_event(db, data):
    if data.status not in STAGE_MAP:
        raise Problem(400, "Неизвестный этап доставки.")
    digest = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    receipt = db.execute(
        "INSERT INTO integration_events(external_id,payload_hash) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING external_id",
        (data.event_id, digest),
    ).fetchone()
    if not receipt:
        previous = db.execute(
            "SELECT payload_hash FROM integration_events WHERE external_id=%s", (data.event_id,)
        ).fetchone()
        if previous["payload_hash"] and previous["payload_hash"] != digest:
            raise Problem(409, "Идентификатор события уже использован с другими данными.")
        return {"duplicate": True, "updated": 0}
    orders = db.execute(
        "SELECT * FROM orders WHERE shipment=%s ORDER BY id FOR UPDATE", (data.shipment,)
    ).fetchall()
    if not orders:
        raise Problem(404, "Партия не найдена.")
    rank = {stage[0]: i for i, stage in enumerate(STAGES)}
    updated = 0
    for row in orders:
        if data.sequence is not None:
            if data.sequence <= row["integration_sequence"]:
                continue
            db.execute(
                "UPDATE orders SET integration_sequence=%s WHERE id=%s", (data.sequence, row["id"])
            )
        # The existing delivery vocabulary has no cancel/return states. Only
        # automated progression is monotonic; manual manager corrections remain.
        if rank[data.status] <= rank[row["status"]]:
            continue
        change_status(db, row["id"], data.status, "Обновление от внешней системы", "integration")
        updated += 1
    return {"duplicate": False, "updated": updated}


def rate_limit(db, user_id):
    row = db.execute(
        """INSERT INTO rate_limits(telegram_id,window_start,requests) VALUES (%s,now(),1)
        ON CONFLICT(telegram_id) DO UPDATE SET
        requests=CASE WHEN rate_limits.window_start<now()-interval '1 minute' THEN 1 ELSE rate_limits.requests+1 END,
        window_start=CASE WHEN rate_limits.window_start<now()-interval '1 minute' THEN now() ELSE rate_limits.window_start END
        RETURNING requests""",
        (user_id,),
    ).fetchone()
    if row["requests"] > 120:
        raise Problem(429, "Слишком много запросов. Повторите через минуту.")

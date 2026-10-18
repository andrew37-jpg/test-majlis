"""First-party messages only. No access to users' private Telegram conversations."""

import hashlib

from psycopg import sql

from . import access
from .domain import Problem
from .services import serial


def create(db, customer_id, text, key):
    digest = hashlib.sha256(text.encode()).hexdigest()
    db.execute("SELECT telegram_id FROM users WHERE telegram_id=%s FOR UPDATE", (customer_id,))
    existing = db.execute(
        "SELECT id,payload_hash FROM customer_messages WHERE customer_id=%s AND idempotency_key=%s",
        (customer_id, key),
    ).fetchone()
    if existing:
        if existing["payload_hash"] != digest:
            raise Problem(
                409, "Повторное обращение содержит другой текст. Создайте новое обращение."
            )
        return {"id": existing["id"], "duplicate": True}
    recent = db.execute(
        "SELECT count(*) AS n FROM customer_messages WHERE customer_id=%s AND source='miniapp' AND created_at>now()-interval '10 minutes'",
        (customer_id,),
    ).fetchone()["n"]
    if recent >= 5:
        raise Problem(429, "Слишком много обращений. Повторите через 10 минут.")
    row = db.execute(
        "INSERT INTO customer_messages(customer_id,source,text,idempotency_key,payload_hash) VALUES (%s,'miniapp',%s,%s,%s) RETURNING id",
        (customer_id, text, key, digest),
    ).fetchone()
    return {"id": row["id"], "duplicate": False}


def from_telegram(db, message):
    customer = (message.get("from") or {}).get("id")
    chat = message.get("chat") or {}
    mid = message.get("message_id")
    if (
        chat.get("type") != "private"
        or chat.get("id") != customer
        or type(mid) is not int
        or mid <= 0
    ):
        return
    kind = next(
        (key for key in ["photo", "document", "voice", "video"] if key in message),
        "text" if message.get("text") else "other",
    )
    labels = {
        "photo": "Фотография",
        "document": "Документ",
        "voice": "Голосовое сообщение",
        "video": "Видео",
        "other": "Сообщение другого типа",
        "text": "Сообщение",
    }
    text = str(message.get("text") or message.get("caption") or labels[kind])[:4096]
    digest = hashlib.sha256(text.encode()).hexdigest()
    db.execute(
        "INSERT INTO customer_messages(customer_id,source,text,kind,telegram_chat_id,telegram_message_id,payload_hash) VALUES (%s,'telegram',%s,%s,%s,%s,%s) ON CONFLICT(telegram_chat_id,telegram_message_id) DO NOTHING",
        (customer, text, kind, chat["id"], mid, digest),
    )


def inbox(db, user, before=None):
    if user["role"] != "manager" or not user.get("can_read_inbox"):
        raise Problem(403, "Доступ к обращениям не назначен.")
    # Recheck explicit inbox capability; inherited global-order access is insufficient.
    profile = db.execute(
        "SELECT telegram_id FROM manager_access WHERE telegram_id=%s AND enabled AND can_read_inbox",
        (user["id"],),
    ).fetchone()
    if not profile:
        raise Problem(403, "Доступ к обращениям не назначен.")
    query = sql.SQL(
        "SELECT m.id,m.customer_id,u.first_name,m.source,m.kind,m.text,m.created_at FROM customer_messages m JOIN users u ON u.telegram_id=m.customer_id"
    )
    params = []
    if before:
        query += sql.SQL(" WHERE m.id<%s")
        params.append(before)
    rows = db.execute(query + sql.SQL(" ORDER BY m.id DESC LIMIT 51"), params).fetchall()
    return {"items": serial(rows[:50]), "next_cursor": rows[49]["id"] if len(rows) > 50 else None}


def activity(db, user, before=None):
    constraint, params = access.scope(user)
    query = sql.SQL(
        "SELECT e.id,orders.id AS order_id,orders.number,e.kind,e.status,e.note,e.source,e.created_at FROM events e JOIN orders ON orders.id=e.order_id"
    )
    clauses = [constraint] if constraint else []
    if before:
        clauses.append("e.id<%s")
        params.append(before)
    if clauses:
        query += sql.SQL(" WHERE ") + sql.SQL(" AND ".join(clauses))
    rows = db.execute(query + sql.SQL(" ORDER BY e.id DESC LIMIT 51"), params).fetchall()
    return {"items": serial(rows[:50]), "next_cursor": rows[49]["id"] if len(rows) > 50 else None}


def own_messages(db, uid):
    return serial(
        db.execute(
            "SELECT id,source,kind,text,created_at FROM customer_messages WHERE customer_id=%s ORDER BY id DESC LIMIT 1000",
            (uid,),
        ).fetchall()
    )

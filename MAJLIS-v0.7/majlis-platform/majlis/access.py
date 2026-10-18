"""Server-owned cabinet context. Operators manage grants using the migrator role."""

from .domain import Problem


def context(db, user, cfg, cabinet=None):
    global_access = user["id"] in cfg.managers
    profile = db.execute(
        "SELECT can_create,all_orders,can_read_inbox FROM manager_access WHERE telegram_id=%s AND enabled",
        (user["id"],),
    ).fetchone()
    global_access = global_access or bool(profile and profile["all_orders"])
    allowed = global_access or profile is not None
    if cabinet not in (None, "buyer", "manager"):
        raise Problem(400, "Неизвестный кабинет.")
    if cabinet == "manager" and not allowed:
        raise Problem(403, "Доступ менеджера не назначен.")
    role = cabinet or ("manager" if allowed else "buyer")
    return {
        **user,
        "role": role,
        "cabinet": role,
        "available_cabinets": ["buyer", "manager"] if allowed else ["buyer"],
        "manager_all": global_access,
        "can_read_inbox": role == "manager" and bool(profile and profile["can_read_inbox"]),
        "can_create": role == "manager"
        and (global_access or bool(profile and profile["can_create"])),
    }


def scope(user):
    if user["role"] != "manager":
        return "customer_id=%s", [user["id"]]
    if user.get("manager_all", True):
        return "", []
    return (
        "EXISTS (SELECT 1 FROM order_managers a JOIN manager_access m ON m.telegram_id=a.manager_id WHERE a.order_id=orders.id AND a.manager_id=%s AND m.enabled)",
        [user["id"]],
    )


def check_order(db, row, user, edit=False):
    if user["role"] != "manager":
        if edit or row["customer_id"] != user["id"]:
            raise Problem(404, "Заказ не найден.")
        return
    if user.get("manager_all", True):
        return
    grant = db.execute(
        "SELECT public.manager_order_access(%s,%s) AS can_edit", (user["id"], row["id"])
    ).fetchone()
    if grant["can_edit"] is None:
        raise Problem(404, "Заказ не найден.")
    if edit and not grant["can_edit"]:
        raise Problem(403, "Заказ доступен только для просмотра.")


def permissions(db, row, user):
    if user["role"] != "manager":
        return {"edit": False}
    if user.get("manager_all", True):
        return {"edit": True}
    grant = db.execute(
        "SELECT a.can_edit FROM order_managers a JOIN manager_access m ON m.telegram_id=a.manager_id WHERE a.order_id=%s AND a.manager_id=%s AND m.enabled",
        (row["id"], user["id"]),
    ).fetchone()
    return {"edit": bool(grant and grant["can_edit"])}


def list_permissions(db, rows, user):
    if user["role"] != "manager":
        return {row["id"]: {"edit": False} for row in rows}
    if user.get("manager_all", True):
        return {row["id"]: {"edit": True} for row in rows}
    grants = db.execute(
        "SELECT order_id,can_edit FROM order_managers WHERE manager_id=%s AND order_id=ANY(%s)",
        (user["id"], [row["id"] for row in rows]),
    ).fetchall()
    return {row["order_id"]: {"edit": row["can_edit"]} for row in grants}

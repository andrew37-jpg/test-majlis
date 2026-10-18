"""Only known local facts; no guessed provider location, payment, or order lifecycle."""

from datetime import UTC, datetime, timedelta


def snapshot(row):
    external = row.get("external_updated_at")
    return {
        "scope": "order_and_cargo_batch",
        "shipment": row["shipment"],
        "number": row.get("tracking_number", ""),
        "carrier": row.get("carrier", ""),
        "updated_at": row.get("tracking_updated_at"),
        "external_updated_at": external,
        "external_state": "not_received"
        if not external
        else ("stale" if datetime.now(UTC) - external > timedelta(hours=24) else "received"),
        "stale_after_hours": 24,
        "payment_state": "not_confirmed",
        "order_state": "not_provided",
    }

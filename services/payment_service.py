"""Validation shared by payment handlers and tests."""

import hashlib

LEGACY_AMOUNTS = {10000: 110, 50000: 570, 100000: 1200, 500000: 6300}


def receipt_key(charge_id):
    if not charge_id:
        raise ValueError("Missing Telegram charge ID")
    return hashlib.sha256(charge_id.encode()).hexdigest()


def validate_order(order, *, user_id, currency, total_amount, payload):
    return bool(
        order
        and order.get("user_id") == user_id
        and currency == order.get("currency") == "XTR"
        and total_amount == order.get("amount")
        and payload == order.get("payload")
        and isinstance(order.get("tokens"), int)
        and order["tokens"] > 0
    )


def legacy_tokens(currency, total_amount, payload):
    if currency == "RUB" and payload == "real-invoice-live":
        return LEGACY_AMOUNTS.get(total_amount)
    return None

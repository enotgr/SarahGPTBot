import pytest

from services.payment_service import legacy_tokens, receipt_key, validate_order


@pytest.fixture
def order():
    return {"user_id": 42, "currency": "XTR", "amount": 50, "tokens": 110, "payload": "sarah2_order"}


def test_payment_must_match_user_amount_currency_and_payload(order):
    args = dict(user_id=42, currency="XTR", total_amount=50, payload="sarah2_order")
    assert validate_order(order, **args)
    for key, value in [("user_id", 43), ("currency", "RUB"), ("total_amount", 1), ("payload", "forged")]:
        assert not validate_order(order, **{**args, key: value})
    assert not validate_order(None, **args)


def test_pending_legacy_receipts_can_still_be_credited():
    assert legacy_tokens("RUB", 10000, "real-invoice-live") == 110
    assert legacy_tokens("RUB", 50000, "real-invoice-live") == 570
    assert legacy_tokens("XTR", 10000, "real-invoice-live") is None
    assert legacy_tokens("RUB", 999, "real-invoice-live") is None
    assert legacy_tokens("RUB", 10000, "forged") is None


def test_receipt_ids_are_safe_for_firebase_and_distinct():
    assert len(receipt_key("charge/with.#$[]")) == 64
    assert receipt_key("one") != receipt_key("two")

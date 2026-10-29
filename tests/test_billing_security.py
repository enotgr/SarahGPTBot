import pytest

from services.wallet import (
    InsufficientBalance,
    PromoRestricted,
    apply_payment,
    credit_once,
    finish_request,
    reconcile_referral,
    reserve_request,
    start_request,
)

RECORD = {"user_id": 42, "tokens": 110, "currency": "XTR", "total_amount": 99}


def test_refund_removes_credit_once_and_prevents_replayed_success():
    user = apply_payment({"tokens": 25}, "charge", 110, RECORD)
    assert user["tokens"] == 135
    user = apply_payment(user, "charge", 110, RECORD, refund=True)
    assert user["tokens"] == 25
    assert apply_payment(user, "charge", 110, RECORD, refund=True) == user
    assert apply_payment(user, "charge", 110, RECORD) == user


def test_refund_before_success_leaves_tombstone_without_stealing_existing_credits():
    user = apply_payment({"tokens": 25}, "charge", 110, RECORD, refund=True)
    assert apply_payment(user, "charge", 110, RECORD)["tokens"] == 25


def test_spent_refunded_payment_leaves_debt_and_blocks_new_generation():
    user = apply_payment({"tokens": 0}, "charge", 110, RECORD)
    user["tokens"] = 5
    user = apply_payment(user, "charge", 110, RECORD, refund=True)
    assert user["tokens"] == -105
    with pytest.raises(InsufficientBalance):
        reserve_request(user, "r", 1)


def test_refund_during_reserved_request_prevents_its_dispatch():
    user = reserve_request(apply_payment({"tokens": 0}, "charge", 110, RECORD), "r", 4)
    user = apply_payment(user, "charge", 110, RECORD, refund=True)
    with pytest.raises(InsufficientBalance):
        start_request(user, "r")
    assert finish_request(user, "r", False)["tokens"] == 0


def test_promotions_cannot_fund_premium_but_legacy_and_paid_balances_can():
    user = credit_once({"tokens": 0}, "_promotions", "welcome", 25)
    with pytest.raises(PromoRestricted):
        reserve_request(user, "premium", 4, allow_promo=False)
    user = apply_payment(user, "charge", 110, RECORD)
    user = reserve_request(user, "paid-premium", 4, allow_promo=False)
    assert user["tokens"] == 131 and user["promo_tokens"] == 25
    assert reserve_request({"tokens": 999000}, "family", 4, allow_promo=False)["tokens"] == 998996


def test_unused_promo_reservation_refund_restores_its_bucket():
    user = credit_once({"tokens": 0}, "_promotions", "welcome", 25)
    user = reserve_request(user, "r", 1)
    assert user["promo_tokens"] == 24
    user = finish_request(user, "r", False)
    assert user["promo_tokens"] == 25


def test_referral_requires_purchase_and_refund_revokes_unused_bonus():
    users = {"42": {"tokens": 25, "referred_by": "1"}, "1": {"tokens": 10}}
    assert reconcile_referral(users, 42, 15, True) == users
    users["42"] = apply_payment(users["42"], "charge", 110, RECORD)
    users = reconcile_referral(users, 42, 15, True)
    assert users["1"]["tokens"] == 25
    users["42"] = apply_payment(users["42"], "charge", 110, RECORD, refund=True)
    users = reconcile_referral(users, 42, 15, True)
    assert users["1"]["tokens"] == 10
    assert reconcile_referral(users, 42, 15, True) == users


def test_refund_wins_race_before_referral_transaction():
    users = {
        "42": apply_payment({"tokens": 0, "referred_by": "1"}, "charge", 110, RECORD, refund=True),
        "1": {"tokens": 10},
    }
    # Even if the grant was reserved against an earlier purchase snapshot, recheck at commit.
    assert reconcile_referral(users, 42, 15, True)["1"]["tokens"] == 10

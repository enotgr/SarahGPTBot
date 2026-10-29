import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest

from services.wallet import (
    DuplicateRequest,
    InsufficientBalance,
    credit_once,
    ensure_user_record,
    finish_request,
    reserve_request,
)


def test_existing_balance_and_unknown_fields_survive_lazy_migration():
    original = {
        "tokens": 329,
        "model": "gpt4",
        "created_at": 123,
        "username": "existing",
        "other": {"keep": True},
    }
    saved = copy.deepcopy(original)
    result = ensure_user_record(original, "new-name", "999")
    assert result == {**original, "model": "smart"}
    assert original == saved
    assert "referred_by" not in result


def test_missing_model_does_not_reset_zero_balance():
    assert ensure_user_record({"tokens": 0, "username": "x"}, "x")["tokens"] == 0
    assert ensure_user_record({"tokens": 0}, "x")["model"] == "fast"
    assert ensure_user_record(None, "new")["tokens"] == 25


def test_request_failure_refunds_exactly_once():
    user = reserve_request({"tokens": 12}, "request1", 12)
    assert user["tokens"] == 0
    user = credit_once(user, "_payments", "paid", 110)
    user = finish_request(user, "request1", False)
    assert user["tokens"] == 122
    assert finish_request(user, "request1", False) == user
    assert finish_request(user, "request1", True) == user


def test_success_cannot_be_refunded_or_processed_twice():
    user = finish_request(reserve_request({"tokens": 5}, "request1", 1), "request1", True)
    assert finish_request(user, "request1", False)["tokens"] == 4
    with pytest.raises(DuplicateRequest):
        reserve_request(user, "request1", 1)


def test_insufficient_balance_has_no_side_effects():
    user = {"tokens": 1, "custom": True}
    with pytest.raises(InsufficientBalance):
        reserve_request(user, "r", 12)
    assert user == {"tokens": 1, "custom": True}


def test_repeated_payment_and_referral_are_idempotent():
    user = {"tokens": 7, "username": "keep"}
    for _ in range(10):
        user = credit_once(user, "_payments", "receipt", 110)
        user = credit_once(user, "_referrals", "invitee", 15)
    assert user["tokens"] == 132
    assert user["username"] == "keep"


def test_atomic_concurrent_credits_and_debits_preserve_balance():
    # Emulate Firebase's serialized, retry-safe transaction commit boundary.
    state, lock = {"user": {"tokens": 100}}, Lock()

    def transaction(index):
        with lock:
            state["user"] = credit_once(state["user"], "_payments", str(index % 10), 5)
            state["user"] = reserve_request(state["user"], f"r{index}", 1)
            state["user"] = finish_request(state["user"], f"r{index}", index % 2 == 0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(transaction, range(100)))
    assert state["user"]["tokens"] == 100


@pytest.mark.parametrize("value,target", [("gpt3", "fast"), ("gpt4", "smart"), ("unknown", "fast")])
def test_legacy_model_mapping(value, target):
    assert ensure_user_record({"tokens": 5, "model": value}, "x")["model"] == target

"""Pure, retry-safe wallet transactions; preserve all existing user fields."""

import copy
import time

from consts.common import model_key


class InsufficientBalance(Exception):
    pass


class DuplicateRequest(Exception):
    pass


def ensure_user_record(old, username, referral=None):
    user = (
        copy.deepcopy(old)
        if old is not None
        else {
            "username": username or "Человек",
            "tokens": 25,
            "created_at": time.time() * 1000,
        }
    )
    user.setdefault("tokens", 0)
    user.setdefault("username", username or "Человек")
    user["model"] = model_key(user.get("model"))
    if old is None and referral:
        user["referred_by"] = str(referral)
    return user


def reserve_request(old, request_id, cost):
    if cost <= 0:
        raise ValueError("Request cost must be positive")
    user = copy.deepcopy(old)
    if not user:
        raise ValueError("Unknown user")
    requests = user.setdefault("_requests", {})
    if request_id in requests:
        raise DuplicateRequest()
    if user["tokens"] < cost:
        raise InsufficientBalance()
    user["tokens"] -= cost
    requests[request_id] = {"cost": cost, "status": "reserved", "created_at": time.time()}
    return user


def finish_request(old, request_id, success):
    user = copy.deepcopy(old)
    request = (user.get("_requests") or {}).get(request_id)
    if request and request["status"] == "reserved":
        request["status"] = "completed" if success else "refunded"
        if not success:
            user["tokens"] += request["cost"]
    return user


def credit_once(old, kind, receipt, amount, details=None):
    if amount <= 0 or kind not in ("_payments", "_referrals"):
        raise ValueError("Invalid credit operation")
    if not old:
        raise ValueError("Unknown recipient")
    user = copy.deepcopy(old)
    receipts = user.setdefault(kind, {})
    if receipt not in receipts:
        user["tokens"] += amount
        receipts[receipt] = details or {"amount": amount, "created_at": time.time()}
    return user

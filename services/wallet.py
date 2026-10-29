"""Pure, retry-safe wallet transactions; preserve all existing user fields."""

import copy
import time

from consts.common import model_key


class InsufficientBalance(Exception):
    pass


class DuplicateRequest(Exception):
    pass


class PromoRestricted(Exception):
    pass


def ensure_user_record(old, username, referral=None, welcome_tokens=25):
    user = (
        copy.deepcopy(old)
        if old is not None
        else {
            "username": username or "Человек",
            "tokens": welcome_tokens,
            "promo_tokens": welcome_tokens,
            "_welcome_bonus_pending": welcome_tokens == 0,
            "created_at": time.time() * 1000,
        }
    )
    user.setdefault("tokens", 0)
    user.setdefault("username", username or "Человек")
    user["model"] = model_key(user.get("model"))
    if old is None and referral:
        user["referred_by"] = str(referral)
    return user


def reserve_request(old, request_id, cost, allow_promo=True):
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
    promo = min(max(0, user.get("promo_tokens", 0)), max(0, user["tokens"]))
    promo_spent = max(0, cost - (user["tokens"] - promo))
    if promo_spent and not allow_promo:
        raise PromoRestricted("Бонусные токены действуют для Luna. /luna или /buy — пополнить баланс.")
    user["tokens"] -= cost
    if promo_spent:
        user["promo_tokens"] = promo - promo_spent
    requests[request_id] = {
        "cost": cost,
        "status": "reserved",
        "created_at": time.time(),
        "promo_spent": promo_spent,
    }
    return user


def start_request(old, request_id):
    user = copy.deepcopy(old)
    request = (user.get("_requests") or {}).get(request_id)
    if not request or request["status"] != "reserved":
        raise ValueError("Missing wallet reservation")
    if user["tokens"] < 0:
        raise InsufficientBalance()
    request["api_started"] = True
    return user


def finish_request(old, request_id, success):
    user = copy.deepcopy(old)
    request = (user.get("_requests") or {}).get(request_id)
    if request and request["status"] == "reserved":
        request["status"] = "completed" if success else "refunded"
        if not success:
            user["tokens"] += request["cost"]
            if request.get("promo_spent"):
                user["promo_tokens"] = user.get("promo_tokens", 0) + request["promo_spent"]
    return user


def credit_once(old, kind, receipt, amount, details=None):
    if amount <= 0 or kind not in ("_payments", "_referrals", "_promotions"):
        raise ValueError("Invalid credit operation")
    if not old:
        raise ValueError("Unknown recipient")
    user = copy.deepcopy(old)
    receipts = user.setdefault(kind, {})
    if receipt not in receipts:
        user["tokens"] += amount
        receipts[receipt] = details or {"amount": amount, "created_at": time.time()}
        if kind == "_promotions":
            user["promo_tokens"] = user.get("promo_tokens", 0) + amount
    return user


def apply_payment(old, receipt, amount, record, refund=False):
    """A refund tombstone wins even when updates arrive out of order."""
    user = copy.deepcopy(old)
    entries = user.setdefault("_payments", {})
    existing = entries.get(receipt)
    if existing and (
        existing.get("tokens", amount) != amount
        or existing.get("user_id", record["user_id"]) != record["user_id"]
    ):
        raise ValueError("Payment receipt mismatch")
    if refund:
        if existing and existing.get("status") != "refunded":
            user["tokens"] -= amount  # Retain debt if a refunded purchase was already spent.
        entries[receipt] = {**(existing or record), "status": "refunded"}
    elif existing is None:
        user["tokens"] += amount
        entries[receipt] = {**record, "status": "credited"}
    return user


def revoke_promotion(old, receipt):
    user = copy.deepcopy(old)
    entry = (user.get("_promotions") or {}).get(receipt)
    if entry and entry.get("status") != "revoked":
        unused = min(user.get("promo_tokens", 0), entry["amount"], max(0, user["tokens"]))
        user["tokens"] -= unused
        user["promo_tokens"] -= unused
        entry["status"] = "revoked"
    return user


def reconcile_referral(users, identifier, amount, allowed):
    """Check the purchase and update its referrer in one Firebase transaction."""
    users = copy.deepcopy(users or {})
    user = users.get(str(identifier))
    if not user or not user.get("referred_by"):
        return users
    referrer = str(user["referred_by"])
    owner = users.get(referrer)
    if not owner or referrer == str(identifier) or str(identifier) in (owner.get("_referrals") or {}):
        return users
    receipt = f"referral_{identifier}"
    active = any(p.get("status", "credited") != "refunded" for p in (user.get("_payments") or {}).values())
    if active and allowed:
        users[referrer] = credit_once(
            owner, "_promotions", receipt, amount, {"amount": amount, "status": "credited"}
        )
    elif not active:
        users[referrer] = revoke_promotion(owner, receipt)
    return users

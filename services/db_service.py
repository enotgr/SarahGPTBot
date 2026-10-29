"""Async Firebase adapter. Existing users, contexts and payments keep their paths."""

import asyncio
from functools import cached_property

import firebase_admin
from firebase_admin import credentials, db

import config
from services.finance import finance
from services.wallet import (
    apply_payment,
    credit_once,
    ensure_user_record,
    finish_request,
    reconcile_referral,
    reserve_request,
    start_request,
)


class DBService:
    @cached_property
    def app(self):
        return firebase_admin.initialize_app(
            credentials.Certificate(config.FIREBASE_CREDENTIALS),
            {"databaseURL": config.FIREBASE_URL, "httpTimeout": 20},
            name="sarah-v2",
        )

    def reference(self, name, identifier=None):
        path = f"/{name}" if identifier is None else f"/{name}/{identifier}"
        return db.reference(path, app=self.app)

    async def get_db(self, name):
        return await asyncio.to_thread(self.reference(name).get)

    async def get_obj_by_id(self, name, identifier):
        return await asyncio.to_thread(self.reference(name, identifier).get)

    async def set_obj_by_id(self, name, identifier, value):
        await asyncio.to_thread(self.reference(name, identifier).set, value)

    async def update_user(self, identifier, changes):
        await asyncio.to_thread(self.reference("users", identifier).update, changes)

    async def transact_user(self, identifier, transform):
        return await asyncio.to_thread(self.reference("users", identifier).transaction, transform)

    async def ensure_user(self, identifier, username, referral=None):
        user = await self.transact_user(
            identifier, lambda old: ensure_user_record(old, username, referral, 0)
        )
        if user.get("_welcome_bonus_pending"):
            if await finance.claim_grant("welcome", identifier, config.NEW_USER_TOKENS):
                user = await self.credit(identifier, "_promotions", "welcome", config.NEW_USER_TOKENS)
                await self.update_user(identifier, {"_welcome_bonus_pending": False})
        return user

    async def reserve(self, identifier, request_id, cost, allow_promo=True):
        return await self.transact_user(
            identifier, lambda old: reserve_request(old, request_id, cost, allow_promo)
        )

    async def start_request(self, identifier, request_id):
        return await self.transact_user(identifier, lambda old: start_request(old, request_id))

    async def finish(self, identifier, request_id, success):
        return await self.transact_user(identifier, lambda old: finish_request(old, request_id, success))

    async def credit(self, identifier, kind, receipt, amount, details=None):
        return await self.transact_user(
            identifier, lambda old: credit_once(old, kind, receipt, amount, details)
        )

    async def payment(self, identifier, receipt, amount, record, refund=False):
        return await self.transact_user(
            identifier, lambda old: apply_payment(old, receipt, amount, record, refund)
        )

    async def award_referral(self, identifier):
        user = await self.get_obj_by_id("users", identifier)
        if not user or not user.get("referred_by"):
            return
        referrer = user["referred_by"]
        if str(referrer) == str(identifier):
            return
        owner = await self.get_obj_by_id("users", referrer)
        if not owner or str(identifier) in (owner.get("_referrals") or {}):
            return
        receipt = f"referral_{identifier}"
        active = any(
            p.get("status", "credited") != "refunded" for p in (user.get("_payments") or {}).values()
        )
        previous = (owner.get("_promotions") or {}).get(receipt)
        if active and previous:
            return
        if not active and (not previous or previous.get("status") == "revoked"):
            return
        allowed = active and await finance.claim_grant("referral", identifier, config.REFERRAL_TOKENS)
        await asyncio.to_thread(
            self.reference("users").transaction,
            lambda all_users: reconcile_referral(all_users, identifier, config.REFERRAL_TOKENS, allowed),
        )

    async def recover_requests(self):
        users = await self.get_db("users") or {}
        recovered = 0
        for user_id, user in users.items():
            for request_id, request in (user.get("_requests") or {}).items():
                if request.get("status") == "reserved":
                    charged = await finance.wallet_charge(user_id, request_id)
                    await self.finish(
                        user_id, request_id, bool(request.get("api_started")) if charged is None else charged
                    )
                    recovered += 1
        return recovered


db_service = DBService()

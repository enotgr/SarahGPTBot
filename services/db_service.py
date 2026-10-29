"""Async Firebase adapter. Existing users, contexts and payments keep their paths."""

import asyncio
from functools import cached_property

import firebase_admin
from firebase_admin import credentials, db

import config
from services.wallet import credit_once, ensure_user_record, finish_request, reserve_request


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
        return await self.transact_user(identifier, lambda old: ensure_user_record(old, username, referral))

    async def reserve(self, identifier, request_id, cost):
        return await self.transact_user(identifier, lambda old: reserve_request(old, request_id, cost))

    async def finish(self, identifier, request_id, success):
        return await self.transact_user(identifier, lambda old: finish_request(old, request_id, success))

    async def credit(self, identifier, kind, receipt, amount, details=None):
        return await self.transact_user(
            identifier, lambda old: credit_once(old, kind, receipt, amount, details)
        )

    async def recover_requests(self):
        users = await self.get_db("users") or {}
        recovered = 0
        for user_id, user in users.items():
            for request_id, request in (user.get("_requests") or {}).items():
                if request.get("status") == "reserved":
                    await self.finish(user_id, request_id, False)
                    recovered += 1
        return recovered


db_service = DBService()

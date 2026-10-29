import copy

import pytest

import config
import services.db_service as module
from services.db_service import DBService
from services.finance import Finance
from services.wallet import reserve_request, start_request


class MemoryDB(DBService):
    def __init__(self):
        self.users = {}

    async def transact_user(self, user_id, transform):
        self.users[user_id] = transform(self.users.get(user_id))
        return copy.deepcopy(self.users[user_id])

    async def update_user(self, user_id, changes):
        self.users[user_id].update(changes)

    async def get_db(self, name):
        return copy.deepcopy(self.users)


@pytest.mark.asyncio
async def test_new_welcome_bonus_is_capped_and_does_not_reset_existing_balances(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "finance", Finance(tmp_path / "db.sqlite3"))
    monkeypatch.setattr(config, "PROMO_DAILY_CREDITS", 25)
    db = MemoryDB()
    assert (await db.ensure_user(1, "one"))["tokens"] == 25
    assert (await db.ensure_user(1, "one"))["tokens"] == 25
    assert (await db.ensure_user(2, "two"))["tokens"] == 0
    db.users[3] = {"tokens": 999000, "username": "family"}
    assert (await db.ensure_user(3, "family"))["tokens"] == 999000
    assert "promo_tokens" not in db.users[3]


@pytest.mark.asyncio
async def test_crash_between_cost_settlement_and_wallet_refund_uses_durable_decision(tmp_path, monkeypatch):
    ledger = Finance(tmp_path / "db.sqlite3")
    monkeypatch.setattr(module, "finance", ledger)
    db = MemoryDB()
    db.users["1"] = start_request(reserve_request({"tokens": 20}, "r", 1), "r")
    await ledger.payments_ready(True)
    key = await ledger.reserve(1, "r", "text", "gpt-6-luna", "paid_or_legacy", 1, 1792)
    await ledger.start(key)
    await ledger.settle(key, 0, "rejected", charge=False)
    await ledger.recover()
    assert await db.recover_requests() == 1
    assert db.users["1"]["tokens"] == 20
    assert await db.recover_requests() == 0

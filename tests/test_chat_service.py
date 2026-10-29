import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.chat_service as module
from services.chat_service import ChatService
from services.wallet import finish_request, reserve_request


class FakeDB:
    def __init__(self):
        self.users = {1: {"tokens": 20, "model": "fast"}, 2: {"tokens": 20, "model": "fast"}}
        self.contexts = {}

    async def get_obj_by_id(self, name, user_id):
        return self.contexts.get(user_id)

    async def set_obj_by_id(self, name, user_id, value):
        self.contexts[user_id] = value

    async def reserve(self, user_id, request_id, cost):
        self.users[user_id] = reserve_request(self.users[user_id], request_id, cost)

    async def finish(self, user_id, request_id, success):
        self.users[user_id] = finish_request(self.users[user_id], request_id, success)


def message(user_id=1, message_id=5):
    preview = SimpleNamespace(edit_text=AsyncMock())
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=user_id),
        message_id=message_id,
        text="hello <world>",
        caption=None,
        photo=None,
        answer=AsyncMock(return_value=preview),
        answer_photo=AsyncMock(),
    )


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(module, "db_service", fake)
    return fake


@pytest.mark.asyncio
async def test_success_charges_once_and_records_context(db):
    async def stream(*args):
        yield "answer <valid text>"

    service = ChatService()
    service.api = SimpleNamespace(stream_answer=stream)
    msg = message()
    assert service.begin(msg, db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 19
    assert db.contexts[1][-1]["content"] == "answer <valid text>"
    assert service.begin(msg, db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 19


@pytest.mark.asyncio
async def test_api_failure_refunds_and_keeps_context_unchanged(db):
    async def stream(*args):
        yield "partial"
        raise TimeoutError()

    service = ChatService()
    service.api = SimpleNamespace(stream_answer=stream)
    service.begin(message(), db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 20
    assert 1 not in db.contexts


@pytest.mark.asyncio
async def test_cancellation_and_other_users_remain_responsive(db):
    entered = asyncio.Event()

    async def stream(*args):
        entered.set()
        await asyncio.Event().wait()
        yield "unreachable"

    service = ChatService()
    service.api = SimpleNamespace(stream_answer=stream)
    assert service.begin(message(), db.users[1])
    await entered.wait()
    assert not service.begin(message(message_id=6), db.users[1])
    assert service.begin(message(user_id=2), db.users[2])
    assert await service.stop(1)
    assert db.users[1]["tokens"] == 20
    assert 2 in service.active
    await service.stop(2)
    assert db.users[2]["tokens"] == 20


@pytest.mark.asyncio
async def test_uncertain_database_reservation_is_refunded(db):
    original_reserve = db.reserve

    async def uncertain(*args):
        await original_reserve(*args)
        raise TimeoutError()

    db.reserve = uncertain
    service = ChatService()
    service.begin(message(), db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 20


@pytest.mark.asyncio
async def test_delivery_failure_refunds(db):
    async def stream(*args):
        yield "answer"

    service = ChatService()
    service.api = SimpleNamespace(stream_answer=stream)
    msg = message()
    msg.answer.return_value.edit_text.side_effect = [None, RuntimeError("send failed"), None]
    service.begin(msg, db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 20
    assert 1 not in db.contexts


@pytest.mark.asyncio
async def test_stop_waits_for_pending_wallet_commit_before_refund(db):
    entered, release = asyncio.Event(), asyncio.Event()
    original = db.reserve

    async def delayed_reserve(*args):
        entered.set()
        await release.wait()
        await original(*args)

    db.reserve = delayed_reserve
    service = ChatService()
    service.begin(message(), db.users[1])
    await entered.wait()
    stopping = asyncio.create_task(service.stop(1))
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await stopping
    assert db.users[1]["tokens"] == 20
    assert next(iter(db.users[1]["_requests"].values()))["status"] == "refunded"


@pytest.mark.asyncio
async def test_reset_prevents_new_request_until_context_is_cleared(db):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_set(*args):
        entered.set()
        await release.wait()

    db.set_obj_by_id = delayed_set
    service = ChatService()
    resetting = asyncio.create_task(service.reset(1))
    await entered.wait()
    assert not service.begin(message(), db.users[1])
    assert not await service.reset(1)
    release.set()
    assert await resetting
    assert not service.is_busy(1)


@pytest.mark.asyncio
async def test_retry_after_photo_does_not_charge_for_missing_image(db):
    db.contexts[1] = [
        {"role": "user", "content": "[Фото] describe"},
        {"role": "assistant", "content": "description"},
    ]
    service = ChatService()
    service.begin(message(), db.users[1], retry=True)
    await service.active[1]
    assert db.users[1]["tokens"] == 20
    assert "_requests" not in db.users[1]

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.chat_service as module
from services.chat_service import ChatService
from services.finance import Finance
from services.wallet import finish_request, reserve_request, start_request


class FakeDB:
    def __init__(self):
        self.users = {1: {"tokens": 20, "model": "fast"}, 2: {"tokens": 20, "model": "fast"}}
        self.contexts = {}

    async def get_obj_by_id(self, name, user_id):
        return self.contexts.get(user_id)

    async def set_obj_by_id(self, name, user_id, value):
        self.contexts[user_id] = value

    async def reserve(self, user_id, request_id, cost, allow_promo=True):
        self.users[user_id] = reserve_request(self.users[user_id], request_id, cost, allow_promo)
        return self.users[user_id]

    async def start_request(self, user_id, request_id):
        self.users[user_id] = start_request(self.users[user_id], request_id)

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
def db(monkeypatch, tmp_path):
    fake = FakeDB()
    monkeypatch.setattr(module, "db_service", fake)
    ledger = Finance(tmp_path / "finance.sqlite3")
    ledger._payments_ready(True)
    monkeypatch.setattr(module, "finance", ledger)
    monkeypatch.setattr(module.config, "REQUEST_COOLDOWN_SECONDS", 0)
    return fake


class FakeAPI:
    def __init__(self, stream):
        self.stream = stream

    async def prepare(self, context, *args):
        return {"context": context, "input_tokens": 100}

    async def stream_answer(self, *args, audit, prepared):
        audit["dispatched"] = True
        async for delta in self.stream(*args):
            yield delta
        audit["usage"] = {"input_tokens": 100, "output_tokens": 10}


@pytest.mark.asyncio
async def test_success_charges_once_and_records_context(db):
    async def stream(*args):
        yield "answer <valid text>"

    service = ChatService()
    service.api = FakeAPI(stream)
    msg = message()
    assert service.begin(msg, db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 19
    assert db.contexts[1][-1]["content"] == "answer <valid text>"
    assert service.begin(msg, db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 19


@pytest.mark.asyncio
async def test_dispatched_unknown_failure_consumes_quote_and_keeps_context_unchanged(db):
    async def stream(*args):
        yield "partial"
        raise TimeoutError()

    service = ChatService()
    service.api = FakeAPI(stream)
    service.begin(message(), db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 19
    assert 1 not in db.contexts


@pytest.mark.asyncio
async def test_cancellation_and_other_users_remain_responsive(db):
    entered = asyncio.Event()

    async def stream(*args):
        entered.set()
        await asyncio.Event().wait()
        yield "unreachable"

    service = ChatService()
    service.api = FakeAPI(stream)
    assert service.begin(message(), db.users[1])
    await entered.wait()
    assert not service.begin(message(message_id=6), db.users[1])
    assert service.begin(message(user_id=2), db.users[2])
    assert await service.stop(1)
    assert db.users[1]["tokens"] == 19
    assert 2 in service.active
    await service.stop(2)
    assert db.users[2]["tokens"] in (19, 20)  # The second request may still be queued.


@pytest.mark.asyncio
async def test_uncertain_database_reservation_is_refunded(db):
    original_reserve = db.reserve

    async def uncertain(*args, **kwargs):
        await original_reserve(*args, **kwargs)
        raise TimeoutError()

    db.reserve = uncertain
    service = ChatService()
    service.begin(message(), db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 20


@pytest.mark.asyncio
async def test_delivery_failure_does_not_refund_generated_answer_and_keeps_export(db):
    async def stream(*args):
        yield "answer"

    service = ChatService()
    service.api = FakeAPI(stream)
    msg = message()
    msg.answer.return_value.edit_text.side_effect = [None, RuntimeError("send failed"), None]
    service.begin(msg, db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 19
    assert db.contexts[1][-1]["content"] == "answer"


@pytest.mark.asyncio
async def test_stop_waits_for_pending_wallet_commit_before_refund(db):
    entered, release = asyncio.Event(), asyncio.Event()
    original = db.reserve

    async def delayed_reserve(*args, **kwargs):
        entered.set()
        await release.wait()
        await original(*args, **kwargs)

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


@pytest.mark.asyncio
async def test_stop_while_queued_is_free_and_releases_cost_budget(db):
    entered = asyncio.Event()
    service = ChatService()
    service.semaphore = asyncio.Semaphore(0)
    msg = message()
    original = msg.answer

    async def answer(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        return result

    msg.answer = answer
    service.begin(msg, db.users[1])
    await asyncio.wait_for(entered.wait(), 2)
    await service.stop(1)
    assert db.users[1]["tokens"] == 20
    assert (await module.finance.report())["cost_micro"] == 0


@pytest.mark.asyncio
async def test_started_stop_is_charged_and_keeps_conservative_api_cost(db):
    entered = asyncio.Event()

    async def stream(*args):
        entered.set()
        await asyncio.Event().wait()
        yield "unreachable"

    service = ChatService()
    service.api = FakeAPI(stream)
    service.begin(message(), db.users[1])
    await asyncio.wait_for(entered.wait(), 2)
    await service.stop(1)
    assert db.users[1]["tokens"] == 19
    assert (await module.finance.report())["cost_micro"] == 1792


@pytest.mark.asyncio
async def test_token_validation_failure_refunds_without_paid_dispatch(db):
    from services.openai_request import InputTooLarge

    service = ChatService()
    service.api = SimpleNamespace(prepare=AsyncMock(side_effect=InputTooLarge("too big")))
    service.begin(message(), db.users[1])
    await service.active[1]
    assert db.users[1]["tokens"] == 20
    assert (await module.finance.report())["cost_micro"] == 0


@pytest.mark.asyncio
async def test_rejected_image_refunds_user_but_retains_potential_output_moderation_cost(db):
    import httpx
    from openai import BadRequestError

    error = BadRequestError(
        "moderated",
        response=httpx.Response(400, request=httpx.Request("POST", "https://test.invalid")),
        body={"code": "moderation_blocked"},
    )

    async def generate(prompt, user_id, *, audit):
        audit["dispatched"] = True
        raise error

    service = ChatService()
    service.api = SimpleNamespace(generate_image=generate)
    service.begin(message(), db.users[1], kind="image")
    await service.active[1]
    assert db.users[1]["tokens"] == 20
    assert (await module.finance.report())["cost_micro"] == 80000

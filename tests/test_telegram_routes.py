"""Exercise real aiogram routing without network requests or production data."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update

import config
import services.payment_reconciliation as reconciliation
from handlers import main_handlers, payments
from services.wallet import apply_payment, credit_once, ensure_user_record


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.methods = []

    async def close(self):
        return None

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.methods.append(method)
        if method.__api_method__ in ("answerCallbackQuery", "answerPreCheckoutQuery"):
            return True
        return Message(
            message_id=900,
            date=1,
            chat={"id": 42, "type": "private"},
            from_user={"id": bot.id, "is_bot": True, "first_name": "Bot"},
            text=getattr(method, "text", None),
        ).as_(bot)


class MemoryDB:
    def __init__(self):
        self.data = {"users": {}, "contexts": {}, "payments": {}, "payment_orders": {}}

    async def ensure_user(self, user_id, username, referral=None):
        user = ensure_user_record(self.data["users"].get(user_id), username, referral)
        self.data["users"][user_id] = user
        return user

    async def get_obj_by_id(self, name, user_id):
        return self.data[name].get(user_id)

    async def set_obj_by_id(self, name, user_id, value):
        self.data[name][user_id] = value

    async def update_user(self, user_id, changes):
        self.data["users"][user_id].update(changes)

    async def credit(self, user_id, kind, receipt, amount, details=None):
        self.data["users"][user_id] = credit_once(self.data["users"][user_id], kind, receipt, amount, details)

    async def payment(self, user_id, receipt, amount, record, refund=False):
        self.data["users"][user_id] = apply_payment(
            self.data["users"][user_id], receipt, amount, record, refund
        )
        return self.data["users"][user_id]

    async def award_referral(self, user_id):
        return None


dispatcher = Dispatcher()
dispatcher.include_router(payments.router)
dispatcher.include_router(main_handlers.router)


@pytest.fixture
def app(monkeypatch):
    database = MemoryDB()
    session = FakeSession()
    bot = Bot(token=config.BOT_TOKEN, session=session)
    monkeypatch.setattr(main_handlers, "db_service", database)
    monkeypatch.setattr(payments, "db_service", database)
    monkeypatch.setattr(reconciliation, "db_service", database)
    monkeypatch.setattr(config, "STARS_PACKAGES", {110: 99})
    main_handlers.public_waiters.clear()
    main_handlers.public_drafts.clear()
    begin = Mock(return_value=True)
    monkeypatch.setattr(main_handlers.chat_service, "begin", begin)
    return SimpleNamespace(db=database, session=session, bot=bot, begin=begin)


async def feed_message(app, text=None, **fields):
    payload = {
        "message_id": 5,
        "date": 1,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "<test>"},
        **fields,
    }
    if text is not None:
        payload["text"] = text
        if text.startswith("/"):
            payload["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    await dispatcher.feed_update(app.bot, Update.model_validate({"update_id": 10, "message": payload}))


async def feed_callback(app, data):
    await dispatcher.feed_update(
        app.bot,
        Update.model_validate(
            {
                "update_id": 11,
                "callback_query": {
                    "id": "callback_123",
                    "chat_instance": "test",
                    "data": data,
                    "from": {"id": 42, "is_bot": False, "first_name": "Person"},
                    "message": {
                        "message_id": 900,
                        "date": 1,
                        "chat": {"id": 42, "type": "private"},
                        "from": {"id": app.bot.id, "is_bot": True, "first_name": "Bot"},
                        "text": "preview",
                    },
                },
            }
        ),
    )


@pytest.mark.asyncio
async def test_start_and_model_commands_keep_balance(app):
    await feed_message(app, "/start")
    assert app.db.data["users"][42]["tokens"] == 25
    await feed_message(app, "/terra")
    assert app.db.data["users"][42]["model"] == "smart"
    assert app.db.data["users"][42]["tokens"] == 25
    await feed_message(app, "/luna")
    assert app.db.data["users"][42]["model"] == "fast"
    assert app.db.data["users"][42]["tokens"] == 25
    app.begin.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/gpt3", "/gpt4", "/fast", "/balanced", "/smart"])
async def test_removed_model_commands_are_not_active_or_sent_to_openai(app, command):
    await feed_message(app, command)
    app.begin.assert_not_called()
    assert 42 not in app.db.data["users"]


@pytest.mark.asyncio
async def test_unknown_command_and_unsupported_content_do_not_spend_tokens(app):
    await feed_message(app, "/not_a_command")
    await feed_message(
        app,
        sticker={
            "file_id": "x",
            "file_unique_id": "y",
            "type": "regular",
            "width": 100,
            "height": 100,
            "is_animated": False,
            "is_video": False,
        },
    )
    app.begin.assert_not_called()


@pytest.mark.asyncio
async def test_retry_callback_uses_clicking_user_and_new_request_id(app):
    await feed_callback(app, "chat:retry")
    args, kwargs = app.begin.call_args
    assert args[0].from_user.id == 42
    assert args[0].bot is app.bot
    assert kwargs["retry"] is True
    assert kwargs["request_id"] == "cb_callback_123"


@pytest.mark.asyncio
async def test_buy_creates_user_bound_stars_invoice(app):
    await feed_callback(app, "buy:110")
    invoice = next(m for m in app.session.methods if m.__api_method__ == "sendInvoice")
    assert invoice.currency == "XTR"
    assert invoice.provider_token == ""
    assert invoice.prices[0].amount == 99
    order = next(iter(app.db.data["payment_orders"].values()))
    assert order["user_id"] == 42
    assert invoice.payload == order["payload"]


@pytest.mark.asyncio
async def test_successful_payment_is_routed_and_duplicate_is_not_double_credited(app):
    await feed_callback(app, "buy:110")
    order = next(iter(app.db.data["payment_orders"].values()))
    receipt = {
        "currency": "XTR",
        "total_amount": 99,
        "invoice_payload": order["payload"],
        "telegram_payment_charge_id": "test_charge",
        "provider_payment_charge_id": "",
    }
    await feed_message(app, successful_payment=receipt)
    await feed_message(app, successful_payment=receipt)
    assert app.db.data["users"][42]["tokens"] == 135
    assert len(app.db.data["payments"]) == 1
    app.begin.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("refund_first", [False, True])
async def test_refund_update_wins_regardless_of_delivery_order(app, refund_first):
    await feed_callback(app, "buy:110")
    order = next(iter(app.db.data["payment_orders"].values()))
    receipt = {
        "currency": "XTR",
        "total_amount": 99,
        "invoice_payload": order["payload"],
        "telegram_payment_charge_id": "refund-charge",
        "provider_payment_charge_id": "",
    }
    refund = {k: v for k, v in receipt.items() if k != "provider_payment_charge_id"}
    if refund_first:
        await feed_message(app, refunded_payment=refund)
    await feed_message(app, successful_payment=receipt)
    await feed_message(app, refunded_payment=refund)
    await feed_message(app, refunded_payment=refund)
    await feed_message(app, successful_payment=receipt)
    assert app.db.data["users"][42]["tokens"] == 25
    assert len(app.db.data["payments"]) == 1
    assert next(iter(app.db.data["payments"].values()))["status"] == "refunded"


@pytest.mark.asyncio
async def test_public_message_requires_admin_confirmation_and_does_not_start_sending(app, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [42])
    await feed_message(app, "/public")
    assert 42 in main_handlers.public_waiters
    await feed_message(app, "Announcement preview")
    assert main_handlers.public_drafts[42]["text"] == "Announcement preview"
    assert not main_handlers.public_tasks
    assert all(getattr(m, "chat_id", 42) == 42 for m in app.session.methods)
    app.begin.assert_not_called()
    await feed_callback(app, "public:cancel")
    assert 42 not in main_handlers.public_drafts

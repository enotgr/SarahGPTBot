import asyncio
import logging
import time
import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

import config
from handlers.main_handlers import ensure_user, keyboard
from services.db_service import db_service
from services.payment_service import legacy_tokens, receipt_key, validate_order

logger = logging.getLogger(__name__)
router = Router(name="payments")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


@router.message(Command("buy"))
async def buy(message: Message):
    await ensure_user(message)
    if not config.STARS_PACKAGES:
        await message.answer(
            "Пополнение переводится на Telegram Stars. "
            "Имеющиеся токены действуют без изменений.\n"
            "/ref — получить 15 токенов за друга\n/support — помощь с покупкой"
        )
        return
    rows = [
        [(f"{tokens} токенов · {stars} ⭐", f"buy:{tokens}")]
        for tokens, stars in sorted(config.STARS_PACKAGES.items())
    ]
    await message.answer(
        "Покупка токенов за Telegram Stars. Выберите пакет.\n"
        "Перед оплатой: /terms — условия, /paysupport — помощь.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.startswith("buy:"))
async def buy_callback(callback: CallbackQuery):
    await callback.answer()
    try:
        tokens = int(callback.data.split(":", 1)[1])
        amount = config.STARS_PACKAGES[tokens]
    except (ValueError, KeyError):
        await callback.message.answer("Этот пакет недоступен. /buy — актуальные пакеты.")
        return
    order_id = uuid.uuid4().hex
    payload = "sarah2_" + order_id
    order = {
        "user_id": callback.from_user.id,
        "tokens": tokens,
        "amount": amount,
        "currency": "XTR",
        "payload": payload,
        "created_at": time.time() * 1000,
    }
    await db_service.set_obj_by_id("payment_orders", order_id, order)
    await callback.message.answer_invoice(
        title=f"{tokens} токенов Sarah",
        description=f"{tokens} токенов бота. Ответы по тарифу выбранной модели. "
        "После начала генерации остановленный запрос оплачивается. /paysupport — помощь.",
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{tokens} токенов", amount=amount)],
        start_parameter="tokens",
    )


@router.callback_query(F.data.in_({"110tokens", "570tokens", "1200tokens", "6300tokens"}))
async def old_buy_callback(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("Старое меню оплаты обновлено. /buy — актуальные пакеты.")


async def load_order(payload):
    if not payload.startswith("sarah2_"):
        return None
    order_id = payload.removeprefix("sarah2_")
    if len(order_id) != 32 or any(c not in "0123456789abcdef" for c in order_id):
        return None
    return await db_service.get_obj_by_id("payment_orders", order_id)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    valid = False
    try:
        async with asyncio.timeout(6):
            order = await load_order(query.invoice_payload)
            valid = validate_order(
                order,
                user_id=query.from_user.id,
                currency=query.currency,
                total_amount=query.total_amount,
                payload=query.invoice_payload,
            )
    except Exception as exc:
        logger.warning("Pre-checkout validation failed: %s", type(exc).__name__)
    await query.answer(
        ok=valid,
        error_message=None if valid else "Счёт устарел или временно недоступен. Создайте новый через /buy.",
    )


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment
    user = await ensure_user(message)
    order = await load_order(payment.invoice_payload)
    if validate_order(
        order,
        user_id=message.from_user.id,
        currency=payment.currency,
        total_amount=payment.total_amount,
        payload=payment.invoice_payload,
    ):
        amount = order["tokens"]
    else:
        amount = legacy_tokens(payment.currency, payment.total_amount, payment.invoice_payload)
    if not amount:
        logger.error(
            "Unrecognized successful payment receipt=%s", receipt_key(payment.telegram_payment_charge_id)
        )
        await message.answer(
            "Оплата получена, но параметры покупки требуют проверки. "
            "Свяжитесь с /paysupport и сохраните квитанцию Telegram."
        )
        return
    key = receipt_key(payment.telegram_payment_charge_id)
    # Previous application stored receipts under provider_payment_charge_id.
    provider_id = payment.provider_payment_charge_id
    if provider_id and not any(c in provider_id for c in ".#$[]/"):
        old = await db_service.get_obj_by_id("payments", provider_id)
        if old:
            await message.answer("Эта покупка уже была зачислена. /tokens — баланс.")
            return
    record = {
        "tokens": amount,
        "user_id": message.from_user.id,
        "username": user["username"],
        "currency": payment.currency,
        "total_amount": payment.total_amount,
        "invoice_payload": payment.invoice_payload,
        "telegram_payment_charge_id": payment.telegram_payment_charge_id,
        "provider_payment_charge_id": provider_id,
        "created_at": time.time() * 1000,
    }
    # The receipt and balance change share one Firebase transaction.
    result = await db_service.payment(message.from_user.id, key, amount, record)
    saved = result["_payments"][key]
    await db_service.set_obj_by_id("payments", key, saved)
    await db_service.award_referral(message.from_user.id)
    if saved.get("status") == "refunded":
        await message.answer("Эта покупка возвращена. Токены за неё не начисляются.")
    else:
        await message.answer(f"Покупка зачислена: {amount} токенов. /tokens — баланс.")


@router.message(F.refunded_payment)
async def refunded_payment(message: Message):
    payment = message.refunded_payment
    from services.payment_reconciliation import apply_refund

    try:
        result = await apply_refund(
            message.from_user.id,
            payment.invoice_payload,
            payment.telegram_payment_charge_id,
            payment.total_amount,
            payment.currency,
        )
    except ValueError:
        logger.error("Unrecognized refund receipt=%s", receipt_key(payment.telegram_payment_charge_id))
        await message.answer("Возврат оплаты требует проверки. /paysupport — помощь.")
        return
    await message.answer(
        "Оплата возвращена, начисленные за неё токены отменены. "
        f"Текущий баланс: {result['tokens']}. /paysupport — помощь."
    )

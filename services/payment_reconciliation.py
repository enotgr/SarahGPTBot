"""Reconcile missed Telegram refunds before permitting paid API work."""

import asyncio
import logging
import time

from services.db_service import db_service
from services.finance import finance
from services.payment_service import receipt_key, validate_order

logger = logging.getLogger(__name__)


async def apply_refund(user_id, payload, charge_id, total_amount, currency="XTR"):
    key = receipt_key(charge_id)
    saved = await db_service.get_obj_by_id("payments", key) or {}
    payload = payload or saved.get("invoice_payload", "")
    order_id = payload.removeprefix("sarah2_")
    if (
        not payload.startswith("sarah2_")
        or len(order_id) != 32
        or any(c not in "0123456789abcdef" for c in order_id)
    ):
        raise ValueError("Unknown refund order")
    order = await db_service.get_obj_by_id("payment_orders", order_id)
    if not validate_order(
        order, user_id=user_id, currency=currency, total_amount=total_amount, payload=payload
    ):
        raise ValueError("Refund does not match order")
    record = {
        "user_id": user_id,
        "tokens": order["tokens"],
        "currency": currency,
        "total_amount": total_amount,
        "invoice_payload": payload,
        "telegram_payment_charge_id": charge_id,
        "created_at": time.time() * 1000,
    }
    if not await db_service.get_obj_by_id("users", user_id):
        await db_service.ensure_user(user_id, "")
    # Receipt tombstones also handle refund-before-purchase update ordering.
    result = await db_service.payment(user_id, key, order["tokens"], record, refund=True)
    await db_service.set_obj_by_id("payments", key, result["_payments"][key])
    await db_service.award_referral(user_id)
    return result


async def reconcile(bot):
    offset = 0
    while True:
        page = await bot.get_star_transactions(offset=offset, limit=100)
        for transaction in page.transactions:
            receiver = transaction.receiver
            if receiver and receiver.type == "user" and receiver.transaction_type == "invoice_payment":
                await apply_refund(
                    receiver.user.id, receiver.invoice_payload, transaction.id, abs(transaction.amount)
                )
        if len(page.transactions) < 100:
            return
        offset += len(page.transactions)


async def check_payments(bot):
    try:
        async with asyncio.timeout(120):
            await reconcile(bot)
        await finance.payments_ready(True)
        logger.info("Telegram refund reconciliation complete")
        return True
    except Exception as exc:
        await finance.payments_ready(False)
        logger.error("Refund reconciliation failed; API paused: %s", type(exc).__name__)
        return False


async def watch_payments(bot, initial_ok):
    ok = initial_ok
    while True:
        await asyncio.sleep(1800 if ok else 60)
        ok = await check_payments(bot)

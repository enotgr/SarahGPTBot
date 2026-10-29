import asyncio
import logging
from contextlib import suppress

from aiogram import F
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import ErrorEvent

import config
from handlers import main_handlers, payments
from misc import bot, dp, set_menu
from services.chat_service import chat_service
from services.db_service import db_service
from services.finance import finance
from services.payment_reconciliation import check_payments, watch_payments

logger = logging.getLogger(__name__)


@dp.error(F.exception.as_("exception"))
async def on_error(event: ErrorEvent, exception: Exception):
    if isinstance(exception, TelegramForbiddenError):
        return True
    # Do not log complete updates, prompts, payment details or exception strings.
    logger.error("Update failed: %s update_id=%s", type(exception).__name__, event.update.update_id)
    if event.update.message:
        try:
            await event.update.message.answer(
                "Сервис временно недоступен. Попробуйте позже. /support — помощь."
            )
        except Exception as exc:
            logger.warning("Error notification failed: %s", type(exc).__name__)
    return True


async def main():
    config.validate()
    dp.include_router(payments.router)
    dp.include_router(main_handlers.router)
    await finance.recover()
    recovered = await db_service.recover_requests()
    logger.info("Startup: recovered %s unfinished reservations", recovered)
    await finance.payments_ready(False)
    synced = await check_payments(bot)
    payments_task = asyncio.create_task(watch_payments(bot, synced))
    try:
        await set_menu()
        # Keep queued messages and payment notifications; never drop pending updates.
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            tasks_concurrency_limit=32,
            close_bot_session=False,
        )
    finally:
        payments_task.cancel()
        with suppress(asyncio.CancelledError):
            await payments_task
        await main_handlers.close_background_tasks()
        await chat_service.close()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    asyncio.run(main())

"""One request per user, bounded global concurrency, durable reservations and refunds."""

import asyncio
import base64
import logging
import time

from aiogram.types import BufferedInputFile
from openai import APIStatusError

import config
from consts.common import get_model, model_key
from services.context import trim_context
from services.db_service import db_service
from services.finance import BudgetExceeded, RateLimited, finance
from services.openai_request import OpenAIRequest, error_message
from services.pricing import image_cost, reservation_cost, text_cost
from services.wallet import DuplicateRequest, InsufficientBalance, PromoRestricted
from utils.handler_utils import StreamingMessage

logger = logging.getLogger(__name__)


async def durable(operation):
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class ChatService:
    def __init__(self):
        self.active = {}
        self.resetting = set()
        self.image_waiters = {}
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)
        self.api = None

    def is_busy(self, user_id):
        return user_id in self.active or user_id in self.resetting

    async def reset(self, user_id):
        if user_id in self.resetting:
            return False
        self.resetting.add(user_id)
        try:
            await self.stop(user_id)
            await db_service.set_obj_by_id("contexts", user_id, [])
        finally:
            self.resetting.discard(user_id)
        return True

    async def stop(self, user_id):
        self.image_waiters.pop(user_id, None)
        task = self.active.get(user_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return True
        return False

    async def close(self):
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.api:
            await self.api.close()

    def begin(self, message, user, *, kind="text", retry=False, request_id=None, markup=None):
        user_id = message.from_user.id
        if self.is_busy(user_id):
            return False
        task = asyncio.create_task(
            self._run(
                message,
                user,
                kind=kind,
                retry=retry,
                request_id=request_id or f"m_{message.chat.id}_{message.message_id}",
                markup=markup,
            )
        )
        self.active[user_id] = task

        def done(finished):
            if self.active.get(user_id) is finished:
                self.active.pop(user_id, None)
            if not finished.cancelled() and finished.exception():
                logger.error("Request task failed: %s", type(finished.exception()).__name__)

        task.add_done_callback(done)
        return True

    async def _run(self, message, user, *, kind, retry, request_id, markup):
        user_id = message.from_user.id
        delivery = StreamingMessage(message)
        reserved = False
        audit = {}
        budget_key = None
        outcome = "rejected"
        rejected_by_api = False
        unbilled_rejection = False
        mode = model_key(user.get("model"))
        try:
            text = (message.text or message.caption or "").strip()
            context = trim_context(await db_service.get_obj_by_id("contexts", user_id))
            if retry:
                if not context:
                    await message.answer("Пока нет ответа для повтора. Сначала напишите вопрос.")
                    return
                text = context[-2]["content"]
                if text.startswith("[Фото] "):
                    await message.answer("Для повторного анализа пришлите фото ещё раз. Токены не списаны.")
                    return
                context = context[:-2]
            if message.photo and not retry:
                text = text or "Опиши изображение и помоги разобраться в нём."
            if not text:
                await message.answer("Напишите текст запроса.")
                return
            if len(text) > (3000 if kind == "image" else config.MAX_INPUT_CHARS):
                await message.answer(
                    "Запрос слишком длинный. Сократите его до "
                    f"{3000 if kind == 'image' else config.MAX_INPUT_CHARS} символов."
                )
                return
            cost = config.IMAGE_COST if kind == "image" else get_model(mode).cost
            # Mark reservation possible before awaiting: a timed-out DB write may have committed.
            reserved = True
            wallet = await durable(
                db_service.reserve(user_id, request_id, cost, allow_promo=kind == "text" and mode == "fast")
            )
            category = "promo" if wallet["_requests"][request_id].get("promo_spent") else "paid_or_legacy"
            if category != "promo" and user_id in set(config.ADMIN_IDS) | config.PERSONAL_USER_IDS:
                category = "personal"
            # The stable key allows cleanup even if cancellation interrupts a committed reservation.
            from services.finance import request_key

            budget_key = request_key(user_id, request_id)
            await durable(
                finance.reserve(
                    user_id,
                    request_id,
                    kind,
                    get_model(mode).id,
                    category,
                    cost,
                    reservation_cost(kind, mode),
                )
            )
            await delivery.start()
            started = time.monotonic()
            async with asyncio.timeout(config.REQUEST_TIMEOUT + 60):
                async with self.semaphore:
                    if self.api is None:
                        self.api = OpenAIRequest()
                    if kind == "image":
                        if len(text.encode("utf-8")) > 6000:
                            from services.openai_request import InputTooLarge

                            raise InputTooLarge(
                                "Сократите описание изображения: допустимо до 6000 байт UTF-8."
                            )
                        await durable(finance.start(budget_key))
                        await durable(db_service.start_request(user_id, request_id))
                        await delivery.preview.edit_text("Рисую изображение…  /stop — остановить")
                        image = await self.api.generate_image(text, user_id, audit=audit)
                        await message.answer_photo(BufferedInputFile(image, filename="sarah.png"))
                        await delivery.preview.edit_text("Изображение готово. Списано 10 токенов.")
                    else:
                        image = None
                        if message.photo and not retry:
                            # Low-detail input bounds image cost; avoid retaining private photo bytes.
                            photo = next(
                                (p for p in reversed(message.photo) if p.width <= 1024), message.photo[0]
                            )
                            data = await message.bot.download(photo)
                            image = "data:image/jpeg;base64," + base64.b64encode(data.getvalue()).decode()
                        prepared = await self.api.prepare(
                            context, text, mode, user.get("style", "normal"), image
                        )
                        await durable(finance.start(budget_key))
                        await durable(db_service.start_request(user_id, request_id))
                        async for delta in self.api.stream_answer(
                            context,
                            text,
                            mode,
                            user.get("style", "normal"),
                            image,
                            audit=audit,
                            prepared=prepared,
                        ):
                            await delivery.append(delta)
                        # Photo contents are not stored. Subsequent turns retain its description.
                        saved_text = ("[Фото] " if image else "") + text
                        updated = trim_context(
                            prepared["context"]
                            + [
                                {"role": "user", "content": saved_text},
                                {"role": "assistant", "content": delivery.text},
                            ]
                        )
                        await db_service.set_obj_by_id("contexts", user_id, updated)
                        await delivery.finish(markup)
            outcome = "completed"
            logger.info(
                "Request complete kind=%s model=%s elapsed=%.1f",
                kind,
                user.get("model", "fast"),
                time.monotonic() - started,
            )
        except DuplicateRequest:
            reserved = False
            await message.answer("Этот запрос уже обработан. Для нового ответа используйте /retry.")
        except InsufficientBalance:
            await message.answer(
                "Недостаточно токенов для выбранного режима. "
                "/tokens — баланс, /models — модель дешевле, /ref — бонус за друга."
            )
        except (PromoRestricted, BudgetExceeded, RateLimited) as exc:
            await delivery.fail(str(exc))
        except asyncio.CancelledError:
            outcome = "cancelled"
            await delivery.fail(
                "Генерация остановлена. "
                + (
                    f"Запрос уже отправлен OpenAI; списано {cost} токенов."
                    if audit.get("dispatched")
                    else "До генерации токены не списываются."
                )
            )
            raise
        except Exception as exc:
            outcome = "failed"
            rejected_by_api = isinstance(exc, APIStatusError) and exc.status_code in (
                400,
                401,
                403,
                404,
                422,
                429,
            )
            unbilled_rejection = isinstance(exc, APIStatusError) and exc.status_code in (401, 403, 404, 429)
            charged = audit.get("dispatched") and not rejected_by_api
            await delivery.fail(
                error_message(exc)
                + (
                    "\nЗапрос отправлен OpenAI, его стоимость списана. При проблеме обратитесь в /support."
                    if charged
                    else "\nТокены за этот запрос возвращаются."
                )
            )
        finally:
            charge = bool(audit.get("dispatched") and not rejected_by_api)
            if budget_key:
                try:
                    usage = audit.get("usage")
                    actual = image_cost(usage) if kind == "image" else text_cost(usage, get_model(mode).id)
                    if not audit.get("dispatched") or unbilled_rejection:
                        actual = 0
                    await durable(finance.settle(budget_key, actual, outcome, usage, charge))
                except Exception as exc:
                    logger.error("Cost finalization pending: %s", type(exc).__name__)
            if reserved:
                try:
                    await durable(db_service.finish(user_id, request_id, charge))
                except Exception as exc:
                    # Pending reservations are reconciled at the next startup.
                    logger.error("Wallet finalization pending: %s", type(exc).__name__)


chat_service = ChatService()

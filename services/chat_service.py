"""One request per user, bounded global concurrency, durable reservations and refunds."""

import asyncio
import base64
import logging
import time

from aiogram.types import BufferedInputFile

import config
from consts.common import get_model
from services.context import trim_context
from services.db_service import db_service
from services.openai_request import OpenAIRequest, error_message
from services.wallet import DuplicateRequest, InsufficientBalance
from utils.handler_utils import StreamingMessage

logger = logging.getLogger(__name__)


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
        success = False
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
            cost = config.IMAGE_COST if kind == "image" else get_model(user.get("model")).cost
            # Mark reservation possible before awaiting: a timed-out DB write may have committed.
            reserved = True
            reservation = asyncio.create_task(db_service.reserve(user_id, request_id, cost))
            try:
                await asyncio.shield(reservation)
            except asyncio.CancelledError:
                # A Firebase worker thread cannot be cancelled. Wait for its commit before refunding.
                await reservation
                raise
            await delivery.start()
            started = time.monotonic()
            async with asyncio.timeout(config.REQUEST_TIMEOUT + 60):
                async with self.semaphore:
                    if self.api is None:
                        self.api = OpenAIRequest()
                    if kind == "image":
                        await delivery.preview.edit_text("Рисую изображение…  /stop — остановить")
                        image = await self.api.generate_image(text, user_id)
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
                        async for delta in self.api.stream_answer(
                            context,
                            text,
                            user.get("model"),
                            user.get("style", "normal"),
                            image,
                        ):
                            await delivery.append(delta)
                        await delivery.finish(markup)
                        # Photo contents are not stored. Subsequent turns retain its description.
                        saved_text = ("[Фото] " if image else "") + text
                        updated = trim_context(
                            context
                            + [
                                {"role": "user", "content": saved_text},
                                {"role": "assistant", "content": delivery.text},
                            ]
                        )
                        await db_service.set_obj_by_id("contexts", user_id, updated)
            success = True
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
            reserved = False
            await message.answer(
                "Недостаточно токенов для выбранного режима. "
                "/tokens — баланс, /models — модель дешевле, /ref — бонус за друга."
            )
        except asyncio.CancelledError:
            await delivery.fail("Генерация остановлена. Токены будут возвращены на баланс.")
            raise
        except Exception as exc:
            await delivery.fail(error_message(exc) + "\nТокены за неудачный запрос будут возвращены.")
        finally:
            if reserved:
                try:
                    await asyncio.shield(db_service.finish(user_id, request_id, success))
                except Exception as exc:
                    # Pending reservations are reconciled at the next startup.
                    logger.error("Wallet finalization pending: %s", type(exc).__name__)


chat_service = ChatService()

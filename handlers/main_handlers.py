import asyncio
import io
import logging
import time
import uuid

import qrcode
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from consts.common import HELP_TEXT, MODELS, get_model, model_key
from services.chat_service import chat_service
from services.context import clean_context
from services.db_service import db_service

router = Router(name="main")
logger = logging.getLogger(__name__)
# Context and balances are keyed by Telegram user IDs in the existing database.
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
public_waiters = set()
public_drafts = {}
public_tasks = {}


def keyboard(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows
        ]
    )


def answer_keyboard():
    return keyboard([[("↻ Другой ответ", "chat:retry"), ("＋ Новый диалог", "chat:new")]])


async def ensure_user(message, referral=None):
    person = message.from_user
    user = await db_service.ensure_user(person.id, person.username or person.first_name, referral)
    # Idempotent referral credit can recover if the process stopped after account creation.
    referrer = user.get("referred_by")
    if referrer and str(person.id) != str(referrer):
        target = await db_service.get_obj_by_id("users", referrer)
        if target:
            await db_service.credit(referrer, "_referrals", str(person.id), config.REFERRAL_TOKENS)
    return user


@router.message(CommandStart())
async def send_welcome(message: Message):
    args = message.text.split(maxsplit=1)
    referral = args[1] if len(args) > 1 and args[1].isdigit() else None
    if referral == str(message.from_user.id):
        referral = None
    user = await ensure_user(message, referral)
    await message.answer(
        f"Привет, {message.from_user.first_name or 'друг'}! Я Sarah.\n"
        f"Напишите вопрос или пришлите фото с подписью.\n\n"
        f"Модель: {get_model(user.get('model')).title}\n"
        f"Баланс: {user['tokens']} токенов\n\n/help — все возможности",
        reply_markup=keyboard([[("Выбрать модель", "menu:models"), ("Баланс", "menu:tokens")]]),
    )


@router.message(Command("help"))
async def help_message(message: Message):
    await message.answer(HELP_TEXT)


async def show_balance(message, user):
    model = get_model(user.get("model"))
    await message.answer(
        f"Баланс: {user['tokens']} токенов\n"
        f"{model.title}\nСтоимость ответа: {model.cost} токенов\n"
        f"Примерно {max(0, user['tokens']) // model.cost} ответов в этом режиме.\n\n"
        "/ref — пригласить друга (+15)\n/buy — пополнить баланс"
    )


@router.message(Command("tokens", "balance"))
async def tokens(message: Message):
    await show_balance(message, await ensure_user(message))


async def show_models(message, user):
    rows = [
        [
            (
                f"{'✓ ' if model_key(user.get('model')) == key else ''}{model.title} · {model.cost} ток.",
                f"model:{key}",
            )
        ]
        for key, model in MODELS.items()
    ]
    await message.answer(
        "Выберите режим. Цена указана за один ответ; история диалога сохраняется.\n"
        "Текущий: " + get_model(user.get("model")).title,
        reply_markup=keyboard(rows),
    )


@router.message(Command("models", "model"))
async def models(message: Message):
    await show_models(message, await ensure_user(message))


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(callback: CallbackQuery):
    await callback.answer()
    user = await db_service.ensure_user(callback.from_user.id, callback.from_user.username)
    if callback.data == "menu:models":
        await show_models(callback.message, user)
    else:
        await show_balance(callback.message, user)


async def select_model(user_id, key):
    await db_service.ensure_user(user_id, None)
    await db_service.update_user(user_id, {"model": key})


@router.callback_query(F.data.startswith("model:"))
async def model_callback(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]
    if key not in MODELS:
        await callback.answer("Модель недоступна", show_alert=True)
        return
    await callback.answer()
    await select_model(callback.from_user.id, key)
    model = MODELS[key]
    await callback.message.answer(f"Выбрано: {model.title}\nСтоимость ответа: {model.cost} токенов.")


@router.message(Command("luna", "terra"))
async def choose_model(message: Message):
    command = message.text.split()[0][1:].split("@")[0]
    key = {"luna": "fast", "terra": "smart"}[command]
    await select_model(message.from_user.id, key)
    await message.answer(f"Выбрано: {MODELS[key].title}\nСтоимость ответа: {MODELS[key].cost} токенов.")


@router.message(Command("new", "reset"))
async def reset_context(message: Message):
    if await chat_service.reset(message.from_user.id):
        await message.answer("Новый диалог начат. Баланс и выбранная модель сохранены.")
    else:
        await message.answer("Диалог уже очищается. Подождите немного.")


@router.message(Command("stop", "cancel"))
async def stop(message: Message):
    public_waiters.discard(message.from_user.id)
    public_drafts.pop(message.from_user.id, None)
    broadcasting = public_tasks.get(message.from_user.id)
    if broadcasting:
        broadcasting.cancel()
        await asyncio.gather(broadcasting, return_exceptions=True)
    stopped = await chat_service.stop(message.from_user.id)
    if not stopped:
        await message.answer("Готово. Активной генерации нет. Напишите новый вопрос.")


@router.message(Command("retry"))
async def retry(message: Message):
    user = await ensure_user(message)
    if not chat_service.begin(message, user, retry=True, markup=answer_keyboard()):
        await message.answer("Уже готовлю ответ. Дождитесь его или нажмите /stop.")


@router.callback_query(F.data.startswith("chat:"))
async def chat_callback(callback: CallbackQuery):
    await callback.answer()
    # Callback.message belongs to the bot. Substitute only the verified clicking user.
    message = callback.message.model_copy(update={"from_user": callback.from_user})
    if callback.data == "chat:new":
        await reset_context(message)
    elif callback.data == "chat:retry":
        user = await ensure_user(message)
        if not chat_service.begin(
            message, user, retry=True, request_id="cb_" + callback.id, markup=answer_keyboard()
        ):
            await message.answer("Уже готовлю ответ. /stop — остановить.")


@router.message(Command("style"))
async def style(message: Message):
    await ensure_user(message)
    await message.answer(
        "Как отвечать?",
        reply_markup=keyboard(
            [
                [("Кратко", "style:brief"), ("Обычно", "style:normal"), ("Подробно", "style:detailed")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("style:"))
async def style_callback(callback: CallbackQuery):
    value = callback.data.split(":", 1)[1]
    if value not in ("brief", "normal", "detailed"):
        await callback.answer()
        return
    await callback.answer()
    await db_service.ensure_user(callback.from_user.id, callback.from_user.username)
    await db_service.update_user(callback.from_user.id, {"style": value})
    await callback.message.answer("Стиль ответов сохранён.")


@router.message(Command("image"))
async def request_image(message: Message):
    user = await ensure_user(message)
    if chat_service.is_busy(message.from_user.id):
        await message.answer("Сначала дождитесь ответа или нажмите /stop.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        prompt_message = message.model_copy(update={"text": args[1]})
        chat_service.begin(prompt_message, user, kind="image")
        return
    chat_service.image_waiters[message.from_user.id] = time.monotonic() + 600
    await message.answer(
        "Опишите изображение следующим сообщением.\n"
        f"Стоимость: {config.IMAGE_COST} токенов. /cancel — отменить.",
        reply_markup=keyboard([[("Отменить", "cancel_generate_image")]]),
    )


@router.callback_query(F.data == "cancel_generate_image")
async def cancel_image(callback: CallbackQuery):
    await callback.answer()
    chat_service.image_waiters.pop(callback.from_user.id, None)
    await callback.message.answer("Режим создания изображения отменён.")


@router.message(Command("ref", "qrcode", "qr", "ref_link"))
async def ref(message: Message):
    await ensure_user(message)
    me = await message.bot.get_me()
    link = f"https://t.me/{me.username}?start={message.from_user.id}"
    buffer = io.BytesIO()
    await asyncio.to_thread(qrcode.make(link).save, buffer, "PNG")
    await message.answer_photo(
        BufferedInputFile(buffer.getvalue(), filename="invite.png"),
        caption=f"Пригласите друга: {link}\nЗа нового пользователя по вашей ссылке — 15 токенов.",
    )


@router.message(Command("export"))
async def export(message: Message):
    context = clean_context(await db_service.get_obj_by_id("contexts", message.from_user.id))
    if not context:
        await message.answer("Текущий диалог пуст.")
        return
    text = "\n\n".join(("Вы: " if m["role"] == "user" else "Sarah: ") + m["content"] for m in context)
    await message.answer_document(BufferedInputFile(text.encode(), filename="dialog.txt"))


@router.message(Command("support", "paysupport", "terms"))
async def support(message: Message):
    contact = (
        "@" + config.SUPPORT_USERNAME
        if config.SUPPORT_USERNAME
        else (f"tg://user?id={config.ADMIN_IDS[0]}" if config.ADMIN_IDS else "владельцем бота")
    )
    markup = None
    if config.ADMIN_IDS or config.SUPPORT_USERNAME:
        url = (
            f"https://t.me/{config.SUPPORT_USERNAME}"
            if config.SUPPORT_USERNAME
            else f"tg://user?id={config.ADMIN_IDS[0]}"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Написать в поддержку", url=url),
                ]
            ]
        )
    await message.answer(
        "Для помощи с оплатой и работой бота свяжитесь с " + contact + ".\n"
        f"Ваш ID: {message.from_user.id}\n"
        "Токены бота расходуются по тарифу выбранной модели. "
        "При ошибке или отмене запроса списание возвращается. "
        "Ответы ИИ могут содержать неточности. "
        "Условия покупки и возврата уточняйте у владельца перед оплатой.",
        reply_markup=markup,
    )


@router.message(Command("users"))
async def users(message: Message):
    if message.from_user.id in config.ADMIN_IDS:
        data = await db_service.get_db("users") or {}
        await message.answer(f"Пользователей: {len(data)}")


@router.message(Command("public"))
async def public(message: Message):
    if message.from_user.id in config.ADMIN_IDS:
        args = message.text.split(maxsplit=1)
        if len(args) > 1:
            await prepare_public(message, args[1])
        else:
            public_waiters.add(message.from_user.id)
            await message.answer(
                "Отправьте текст рассылки следующим сообщением. "
                "Сначала покажу предпросмотр; /cancel — отменить."
            )


async def prepare_public(message, text):
    if len(text) > 3000:
        await message.answer("Сократите текст рассылки до 3000 символов. /public — попробовать снова.")
        return
    nonce = uuid.uuid4().hex[:16]
    public_drafts[message.from_user.id] = {
        "text": text,
        "nonce": nonce,
        "expires": time.monotonic() + 600,
    }
    await message.answer(
        "Предпросмотр рассылки:\n\n" + text,
        reply_markup=keyboard([[("Отправить всем", "public:" + nonce), ("Отменить", "public:cancel")]]),
    )


@router.callback_query(F.data.startswith("public:"))
async def public_callback(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer()
        return
    if callback.data == "public:cancel":
        public_waiters.discard(callback.from_user.id)
        public_drafts.pop(callback.from_user.id, None)
        await callback.answer("Рассылка отменена")
        return
    draft = public_drafts.get(callback.from_user.id)
    if not draft or draft["expires"] < time.monotonic() or callback.data != "public:" + draft["nonce"]:
        await callback.answer("Предпросмотр устарел. Начните заново: /public.", show_alert=True)
        return
    if public_tasks:
        await callback.answer("Уже выполняется рассылка. Дождитесь её завершения.", show_alert=True)
        return
    public_drafts.pop(callback.from_user.id, None)
    public_tasks[callback.from_user.id] = asyncio.create_task(
        send_public(callback.message, callback.from_user.id, draft["text"])
    )
    await callback.answer("Рассылка запущена. /cancel — остановить.")


async def send_public(message, admin_id, text):
    sent, unavailable = 0, 0
    try:
        users = await db_service.get_db("users") or {}
        for user_id in users:
            try:
                try:
                    await message.bot.send_message(user_id, text)
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after)
                    await message.bot.send_message(user_id, text)
                sent += 1
            except (TelegramForbiddenError, TelegramAPIError):
                unavailable += 1
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Broadcast stopped: %s", type(exc).__name__)
    finally:
        public_tasks.pop(admin_id, None)
        try:
            await message.answer(
                f"Рассылка завершена или остановлена. Доставлено: {sent}, недоступно: {unavailable}."
            )
        except TelegramAPIError as exc:
            logger.warning("Broadcast summary failed: %s", type(exc).__name__)


async def close_background_tasks():
    tasks = list(public_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message):
    await message.answer("Неизвестная команда. /help — список возможностей.")


@router.message(F.text | F.photo)
async def user_messages(message: Message):
    if message.from_user.id in config.ADMIN_IDS and message.from_user.id in public_waiters:
        public_waiters.discard(message.from_user.id)
        if message.text:
            await prepare_public(message, message.text)
        else:
            await message.answer("Рассылка поддерживает текст. /public — начать заново.")
        return
    user = await ensure_user(message)
    expires = chat_service.image_waiters.pop(message.from_user.id, 0)
    if expires > time.monotonic():
        kind = "image"
    else:
        kind = "text"
    if not chat_service.begin(message, user, kind=kind, markup=answer_keyboard()):
        if kind == "image":
            chat_service.image_waiters[message.from_user.id] = expires
        await message.answer(
            "Уже готовлю ответ. Дождитесь его или нажмите /stop. За это сообщение токены не списаны."
        )


@router.message()
async def unsupported(message: Message):
    await message.answer(
        "Пока поддерживаются текст и фотографии. Пришлите вопрос текстом или фото с подписью."
    )

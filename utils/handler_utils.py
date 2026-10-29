import time

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter


def split_text(text, limit=3800):
    """Respect Telegram's UTF-16 limit, including emoji."""
    chunks, chunk, length = [], [], 0
    for char in text:
        units = 2 if ord(char) > 0xFFFF else 1
        if length + units > limit:
            chunks.append("".join(chunk))
            chunk, length = [], 0
        chunk.append(char)
        length += units
    if chunk:
        chunks.append("".join(chunk))
    return chunks


async def send(message, text, **kwargs):
    for chunk in split_text(text):
        await message.answer(chunk, **kwargs)


class StreamingMessage:
    def __init__(self, message):
        self.message = message
        self.preview = None
        self.text = ""
        self.last_edit = 0.0
        self.last_preview = ""

    async def start(self):
        self.preview = await self.message.answer(
            "Думаю… /stop — остановить. После отправки запроса OpenAI его стоимость списывается."
        )

    async def append(self, delta):
        self.text += delta
        if time.monotonic() - self.last_edit < 1.5:
            return
        self.last_edit = time.monotonic()
        preview = split_text(self.text, 3400)[0] + "\n\n… /stop — остановить"
        if preview == self.last_preview:
            return
        try:
            await self.preview.edit_text(preview)
            self.last_preview = preview
        except TelegramRetryAfter as exc:
            self.last_edit = time.monotonic() + exc.retry_after
        except TelegramBadRequest:
            pass

    async def finish(self, markup=None):
        chunks = split_text(self.text)
        if not chunks:
            raise ValueError("Empty response")
        try:
            await self.preview.edit_text(chunks[0], reply_markup=markup if len(chunks) == 1 else None)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await self.message.answer(chunks[0], reply_markup=markup if len(chunks) == 1 else None)
        for index, chunk in enumerate(chunks[1:], start=1):
            await self.message.answer(chunk, reply_markup=markup if index == len(chunks) - 1 else None)

    async def fail(self, text):
        try:
            if self.preview:
                await self.preview.edit_text(text)
            else:
                await self.message.answer(text)
        except TelegramBadRequest:
            await self.message.answer(text)

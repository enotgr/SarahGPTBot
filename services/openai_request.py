import base64
import hashlib
import logging

from openai import APIStatusError, AsyncOpenAI

import config
from consts.common import get_model
from services.pricing import as_dict

logger = logging.getLogger(__name__)


class IncompleteAnswer(Exception):
    pass


class InputTooLarge(Exception):
    pass


class OpenAIRequest:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=config.OPENAI_API_KEY,
            organization=config.OPENAI_ORGANIZATION,
            project=config.OPENAI_PROJECT,
            timeout=config.REQUEST_TIMEOUT,
            max_retries=0,  # Never duplicate a potentially billable generation automatically.
        )

    async def prepare(self, context, text, mode, style="normal", image=None):
        model = get_model(mode)
        instructions = (
            "Ты Sarah, полезный помощник в Telegram. Отвечай на языке пользователя. "
            "Сохраняй контекст. Не утверждай, что искал информацию в интернете: "
            "доступ к веб-поиску не включён. Используй короткие абзацы и понятное оформление. "
            "Формулы и код пиши обычным текстом или в блоках кода."
        )
        if style == "brief":
            instructions += " Отвечай кратко, обычно в пределах 3–6 предложений."
        elif style == "detailed":
            instructions += " Давай подробные объяснения с полезными примерами."
        content = text
        if image:
            content = [
                {"type": "input_text", "text": text},
                {"type": "input_image", "image_url": image, "detail": "low"},
            ]
        context = list(context)
        while True:
            params = dict(
                model=model.id,
                instructions=instructions,
                input=[*context, {"role": "user", "content": content}],
                reasoning={"effort": model.reasoning},
            )
            count = await self.client.responses.input_tokens.count(**params)
            if count.input_tokens <= config.MAX_INPUT_TOKENS:
                return {"params": params, "input_tokens": count.input_tokens, "context": context}
            if not context:
                raise InputTooLarge(
                    "Запрос слишком большой по числу токенов. Сократите текст или описание фото."
                )
            context = context[2:]

    async def stream_answer(
        self, context, text, mode, style="normal", image=None, *, audit=None, prepared=None
    ):
        model = get_model(mode)
        audit = audit if audit is not None else {}
        prepared = prepared or await self.prepare(context, text, mode, style, image)
        audit["input_tokens_counted"] = prepared["input_tokens"]
        audit["dispatched"] = True
        stream = await self.client.responses.create(
            **prepared["params"],
            extra_body={"prompt_cache_options": {"ttl": "30m"}},
            max_output_tokens=model.max_output,
            store=False,
            stream=True,
        )
        completed = False
        has_output = False
        async with stream:
            async for event in stream:
                if event.type in ("response.completed", "response.incomplete", "response.failed"):
                    audit["usage"] = as_dict(event.response.usage)
                    audit["response_id"] = event.response.id
                if event.type in ("response.output_text.delta", "response.refusal.delta"):
                    has_output = has_output or bool(event.delta.strip())
                    yield event.delta
                elif event.type == "response.completed":
                    completed = True
                elif event.type == "response.incomplete":
                    if event.response.incomplete_details.reason == "max_output_tokens":
                        if not has_output:
                            raise IncompleteAnswer()
                        yield "\n\n[Достигнут лимит ответа. Попросите продолжить.]"
                        completed = True
                    else:
                        raise IncompleteAnswer()
                elif event.type in ("response.failed", "error"):
                    raise IncompleteAnswer()
        if not completed:
            raise IncompleteAnswer()

    async def generate_image(self, prompt, user_id, *, audit=None):
        if len(prompt.encode("utf-8")) > 6000:
            raise InputTooLarge("Сократите описание изображения: допустимо до 6000 байт UTF-8.")
        audit = audit if audit is not None else {}
        audit["dispatched"] = True
        result = await self.client.images.generate(
            model=config.IMAGE_MODEL,
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="low",
            output_format="png",
            extra_body={"safety_identifier": hashlib.sha256(str(user_id).encode()).hexdigest()},
        )
        audit["usage"] = as_dict(result.usage)
        if not result.data or not result.data[0].b64_json:
            raise IncompleteAnswer()
        return base64.b64decode(result.data[0].b64_json)

    async def close(self):
        await self.client.close()


def error_message(error):
    if isinstance(error, InputTooLarge):
        return str(error)
    if isinstance(error, APIStatusError):
        logger.warning(
            "OpenAI failed status=%s code=%s request_id=%s", error.status_code, error.code, error.request_id
        )
        if error.status_code == 401:
            return "Сервис временно недоступен: требуется обновить ключ OpenAI. Администратор должен проверить настройки."
        if error.status_code == 429:
            return "OpenAI временно ограничил запросы или исчерпан лимит оплаты. Попробуйте позже."
        if error.code == "model_not_found" or error.status_code == 404:
            return "У проекта OpenAI нет доступа к выбранной модели. Попробуйте /models или обратитесь в /support."
        if error.status_code in (400, 403):
            return "OpenAI не смог выполнить запрос. Попробуйте изменить формулировку; если ошибка повторяется — /support."
    logger.warning("Request failed: %s", type(error).__name__)
    return "Не удалось получить ответ. Попробуйте ещё раз немного позже."

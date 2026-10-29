from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    id: str
    title: str
    cost: int
    reasoning: str
    max_output: int = 1536


MODELS = {
    "fast": Model("gpt-6-luna", "Быстрый · GPT-6 Luna", 1, "none"),
    "smart": Model("gpt-6.1-terra", "Умный · GPT-6.1 Terra", 4, "low"),
}
LEGACY_MODELS = {"gpt3": "fast", "gpt4": "smart", "balanced": "smart"}


def model_key(value):
    key = LEGACY_MODELS.get(value, value)
    return key if key in MODELS else "fast"


def get_model(value):
    return MODELS[model_key(value)]


HELP_TEXT = (
    "Напишите вопрос — я помню контекст диалога. Можно прислать фото с вопросом в подписи.\n\n"
    "/models — выбрать модель и увидеть стоимость\n"
    "/luna — GPT-6 Luna, 1 токен за ответ\n"
    "/terra — GPT-6.1 Terra, 4 токена за ответ\n"
    "/new — начать новый диалог\n"
    "/retry — заново ответить на последний вопрос\n"
    "/stop — остановить текущий ответ\n"
    "/style — краткие или подробные ответы\n"
    "/image — создать изображение (10 токенов)\n"
    "/tokens — баланс\n"
    "/ref — бонус после первой покупки друга\n"
    "/buy — пополнить баланс\n"
    "/export — скачать текущий диалог\n"
    "/support — помощь с оплатой и работой бота\n\n"
    "Токены бота — баланс запросов, а не токены API OpenAI. "
    "Стоимость показывается до запроса. Отмена до отправки OpenAI бесплатна; "
    "после начала генерации стоимость запроса списывается, в том числе при /stop. "
    "Новые бонусы действуют только для Luna, не более 5 бесплатных запросов в день."
)

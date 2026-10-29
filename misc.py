from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

import config

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


async def set_menu():
    await bot.set_my_commands(
        [
            BotCommand(command=command, description=description)
            for command, description in [
                ("new", "Новый диалог"),
                ("models", "Выбрать модель"),
                ("luna", "GPT-6 Luna · 1 токен за ответ"),
                ("terra", "GPT-6.1 Terra · 4 токена за ответ"),
                ("tokens", "Баланс токенов"),
                ("stop", "Остановить ответ"),
                ("retry", "Повторить последний вопрос"),
                ("style", "Стиль ответов"),
                ("image", "Создать изображение"),
                ("ref", "Пригласить друга"),
                ("buy", "Пополнить баланс"),
                ("export", "Скачать диалог"),
                ("help", "Возможности и команды"),
                ("support", "Поддержка"),
            ]
        ]
    )

from bot_token import TOKEN # token is hidden, create your bot and get token
from aiogram import Bot, Dispatcher, types

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

async def set_menu():
  await dp.bot.set_my_commands([
    types.BotCommand('/tokens', 'Остаток токенов'),
    types.BotCommand('/ref', 'Пригласить друга'),
    types.BotCommand('/buy', 'Купить токены'),
    types.BotCommand('/image', 'Сгенерировать изображение'),
    types.BotCommand('/gpt4', 'Выбрать gpt-4-turbo'),
    types.BotCommand('/gpt3', 'Выбрать gpt-3.5-turbo'),
    types.BotCommand('/reset', 'Очистить контекст'),
  ])

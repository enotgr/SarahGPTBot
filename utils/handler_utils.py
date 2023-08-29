from misc import bot

async def send(message, text):
  await bot.send_message(message.from_user.id, text, parse_mode='html')

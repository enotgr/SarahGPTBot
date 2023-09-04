from misc import dp, bot
from services import db_service
from consts.db_keys import USERS_DB_KEY, PAYMENTS_DB_KEY
from consts.payment_consts import tokens_by_cost, tokens_prices, PAYMENT_TOKEN
from consts.admins import admins
from aiogram import types
from aiogram.types.message import ContentType

@dp.callback_query_handler(text='120tokens')
async def buy_120_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message.chat.id, 120)

@dp.callback_query_handler(text='1300tokens')
async def buy_1300_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message.chat.id, 1300)

@dp.callback_query_handler(text='15000tokens')
async def buy_15000_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message.chat.id, 15000)

@dp.message_handler(commands=['buy'])
async def donate(message: types.Message):
  if message.from_user.id not in admins:
    await bot.send_message(message.from_user.id, 'В ближайшее время покупка токенов будет доступна, но не сейчас ;(')
    return

  # TODO: enable donations

  keyboard = create_keyboard()
  await message.answer(
    'Выберите количество токенов:',
    reply_markup=keyboard
  )

def create_keyboard():
  keyboard = types.InlineKeyboardMarkup()
  keyboard.add(types.InlineKeyboardButton(
    text='120 токенов [100 RUB]',
    callback_data='120tokens')
  )
  keyboard.add(types.InlineKeyboardButton(
    text='1300 токенов [1000 RUB]',
    callback_data='1300tokens')
  )
  keyboard.add(types.InlineKeyboardButton(
    text='15000 токенов [10000 RUB]',
    callback_data='15000tokens')
  )
  return keyboard

async def send_success_admin(user_id, username, payment_id, payment):
  text = 'Успешная транзакция:\n'
  text += f'\nuser_id: {user_id}'
  text += f'\nusername: @{username}'
  text += f'\npayment_id: {payment_id}'
  text += f'\namount: {str(payment.amount.value)} {str(payment.amount.currency)}'
  await bot.send_message(admins[0], text)

# pre checkout  (must be answered in 10 seconds)
@dp.pre_checkout_query_handler(lambda query: True)
async def pre_checkout_query(pre_checkout_q: types.PreCheckoutQuery):
  await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

# successful payment
@dp.message_handler(content_types=ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: types.Message):
  purchased_tokens = tokens_by_cost[message.successful_payment.total_amount]
  user = db_service.get_obj_by_id(USERS_DB_KEY, message.from_user.id)
  user['tokens'] = user['tokens'] + purchased_tokens
  db_service.set_obj_by_id(USERS_DB_KEY, message.from_user.id, user)

  await bot.send_message(message.from_user.id,
                        f'Платеж на сумму {message.successful_payment.total_amount // 100} {message.successful_payment.currency} прошел успешно!\nЗачислено {purchased_tokens} токенов\n\n/tokens - Остаток токенов')

  # TODO: save provider_payment_charge_id to payments db
  # NOTE: send provider_payment_charge_id to user or not?

  # TODO: move to send success admin method
  admin_text = 'SUCCESSFUL PAYMENT:\n'
  payment_info = message.successful_payment.to_python()
  for k, v in payment_info.items():
    admin_text += f'{k}: {v}\n'
  admin_text += f'username: @{user["username"]}'
  await bot.send_message(admins[0], admin_text)

async def send_invoice(user_id, tokens):
  PRICE = calculate_price(tokens)
  await bot.send_invoice(user_id,
                         title='Покупка токенов',
                         description=f'Покупка {tokens} токенов',
                         provider_token=PAYMENT_TOKEN,
                         currency='rub',
                         photo_url='https://firebasestorage.googleapis.com/v0/b/sarahchatgpt-c9602.appspot.com/o/buy_tokens.jpeg?alt=media&token=994541e5-7c43-4e24-85f5-5180fe99052b',
                         photo_width=1241,
                         photo_height=1261,
                         photo_size=89785,
                         is_flexible=False,
                         prices=[PRICE],
                         start_parameter='purchase-of-game-currency',
                         payload='real-invoice-live')

def calculate_price(tokens_amount):
  amount = tokens_prices[tokens_amount]
  return types.LabeledPrice(label=f'Купить {tokens_amount} токенов', amount=amount)

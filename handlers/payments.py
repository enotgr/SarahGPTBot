from misc import dp, bot
from services import db_service
from consts.db_keys import USERS_DB_KEY, PAYMENTS_DB_KEY
from consts.payment_consts import tokens_by_cost, tokens_prices, PAYMENT_TOKEN
from consts.admins import admins
from aiogram import types
from aiogram.types.message import ContentType
from typing import Any
import time

@dp.callback_query_handler(text='110tokens')
async def buy_120_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message.chat.id, 110)

@dp.callback_query_handler(text='570tokens')
async def buy_1300_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message.chat.id, 570)

@dp.callback_query_handler(text='1200tokens')
async def buy_1300_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message.chat.id, 1200)

@dp.callback_query_handler(text='13000tokens')
async def buy_15000_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message.chat.id, 13000)

@dp.message_handler(commands=['buy'])
async def donate(message: types.Message):
  # if message.from_user.id not in admins:
  #   await bot.send_message(message.from_user.id, 'В ближайшее время покупка токенов будет доступна, но не сейчас ;(')
  #   return

  keyboard = create_keyboard()
  await message.answer(
    'Выберите количество токенов:',
    reply_markup=keyboard
  )

def create_keyboard():
  keyboard = types.InlineKeyboardMarkup()
  keyboard.add(types.InlineKeyboardButton(
    text='110 токенов [100 RUB]',
    callback_data='110tokens')
  )
  keyboard.add(types.InlineKeyboardButton(
    text='570 токенов [500 RUB]',
    callback_data='570tokens')
  )
  keyboard.add(types.InlineKeyboardButton(
    text='1200 токенов [1000 RUB]',
    callback_data='1200tokens')
  )
  keyboard.add(types.InlineKeyboardButton(
    text='13000 токенов [10000 RUB]',
    callback_data='13000tokens')
  )
  return keyboard

def save_payment_info(payment_info: dict[str, Any], user_id: int, username: str):
  payment_id = None
  payment_obj = { 'username': username }
  payment_obj['created_at'] = time.time() * 1000
  payment_obj['user_id'] = user_id
  for key, value in payment_info.items():
    if key == 'provider_payment_charge_id':
      payment_id = value
    else:
      payment_obj[key] = value
  db_service.set_obj_by_id(PAYMENTS_DB_KEY, payment_id, payment_obj)

async def send_success_admin(payment_info: dict[str, Any], username: str):
  admin_text = 'Успешная транзакция:\n'
  for key, value in payment_info.items():
    admin_text += f'{key}: {value}\n'
  admin_text += f'username: @{username}'
  await bot.send_message(admins[0], admin_text)

# pre checkout  (must be answered in 10 seconds)
@dp.pre_checkout_query_handler(lambda query: True)
async def pre_checkout_query(pre_checkout_q: types.PreCheckoutQuery):
  await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

# successful payment
@dp.message_handler(content_types=ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: types.Message):
  purchased_tokens = tokens_by_cost[message.successful_payment.total_amount]
  user_id = user_id
  user = db_service.get_obj_by_id(USERS_DB_KEY, user_id)
  user['tokens'] = user['tokens'] + purchased_tokens
  db_service.set_obj_by_id(USERS_DB_KEY, user_id, user)

  await bot.send_message(user_id,
                        f'Платеж на сумму {message.successful_payment.total_amount // 100} {message.successful_payment.currency} прошел успешно!\nЗачислено {purchased_tokens} токенов\n\n/tokens - Остаток токенов')

  payment_info = message.successful_payment.to_python()
  username = user['username']

  await send_success_admin(payment_info, username)
  save_payment_info(payment_info, user_id, username)

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

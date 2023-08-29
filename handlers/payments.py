from misc import dp, bot
from services import db_service
from consts.db_keys import USERS_DB_KEY, PAYMENTS_DB_KEY
from consts.payment_consts import config, tokens_by_cost, payment_statuses
from consts.admins import admins
from aiogram import types
from aiogram.utils.callback_data import CallbackData
from yookassa import Payment, Configuration
import asyncio
import uuid
import time

Configuration.account_id = config['account_id']
Configuration.secret_key = config['secret_key']

call_back_info = CallbackData('status', 'payment_id')

@dp.callback_query_handler(call_back_info.filter())
async def status(callback: types.CallbackQuery):
  payment_id = callback.data.split(':')[1]
  payment = Payment.find_one(payment_id)
  payment_db = db_service.get_obj_by_id(PAYMENTS_DB_KEY, payment_id)
  if payment_db['status'] == payment_statuses[2]:
    await send_success(callback.message.chat.id, payment)
    return

  text = 'Информация о покупке:\n\n'
  if payment.status == payment_statuses[0]:
    text += 'Платеж создан и ожидает действий от вас.\nНажмите кнопку "Оплатить" для оплаты.'
  elif payment.status == payment_statuses[1]:
    text += 'Покупка оплачена, деньги авторизованы и ожидают списания.'
  elif payment.status == payment_statuses[2]:
    user = db_service.get_obj_by_id(USERS_DB_KEY, callback.message.chat.id)
    user['tokens'] = user['tokens'] + tokens_by_cost[str(payment.amount.value)]
    db_service.set_obj_by_id(USERS_DB_KEY, callback.message.chat.id, user)
    text += f'Платеж на сумму {str(payment.amount.value)} {str(payment.amount.currency)} успешно завершен.\n'
    text += f'Вам начислено {tokens_by_cost[str(payment.amount.value)]} токенов!\n\n/tokens - Остаток токенов'
    await send_success_admin(callback.message.chat.id, user['username'], payment_id, payment)
  elif payment.status == payment_statuses[3]:
    text += 'Платеж отменен.'
  else:
    text += 'Что-то пошло не так. Поробуйте проверить позже'
    user = db_service.get_obj_by_id(USERS_DB_KEY, callback.message.chat.id)
    await send_problem_admin(callback.message.chat.id, user['username'], payment_id, payment)

  payment_db['status'] = payment.status
  db_service.set_obj_by_id(PAYMENTS_DB_KEY, payment_id, payment_db)
  await bot.send_message(callback.message.chat.id, text)

@dp.callback_query_handler(text='10tokens')
async def buy_10_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message, 10, 10)

@dp.callback_query_handler(text='120tokens')
async def buy_120_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message, 120, 100)

@dp.callback_query_handler(text='1300tokens')
async def buy_1300_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message, 1300, 1000)

@dp.callback_query_handler(text='15000tokens')
async def buy_15000_tokens(callback: types.CallbackQuery):
  await callback.message.delete()
  await send_invoice(callback.message, 15000, 10000)

@dp.message_handler(commands=['buy'])
async def donate(message: types.Message):
  # await bot.send_message(message.from_user.id, 'В ближайшее время покупка токенов будет доступна, но не сейчас ;(')
  # return

  # TODO: enable donations

  keyboard = create_keyboard()
  await message.answer(
    'Выберите количество токенов:',
    reply_markup=keyboard
  )

async def send_invoice(message, tokens, amount):
  payment = create_payment(amount, tokens)

  payment_db = { 'status': payment.status }
  payment_db['amount'] = str(payment.amount.value)
  payment_db['user_id'] = message.chat.id
  payment_db['created_at'] = time.time() * 1000
  db_service.set_obj_by_id(PAYMENTS_DB_KEY, payment.id, payment_db)

  keyboard = types.InlineKeyboardMarkup()
  keyboard.add(types.InlineKeyboardButton(
    text='Оплатить 💳',
    url=payment.confirmation.confirmation_url)
  )
  keyboard.add(types.InlineKeyboardButton(
    text='Проверить статус платежа',
    callback_data=call_back_info.new(payment_id=payment.id))
  )
  await message.answer(
    f'Чтобы оплатить покупку {tokens} токенов, нажмите кнопку "Оплатить":',
    reply_markup=keyboard
  )
  await check_payment(message.chat.id, payment.id)

async def check_payment(user_id, payment_id):
  payment = Payment.find_one(payment_id)
  i = 0
  while payment.status == payment_statuses[0] or payment.status == payment_statuses[1]:
    payment = Payment.find_one(payment_id)
    await asyncio.sleep(5)
    i = i + 1

    if i == 180:
      break

  payment_db = db_service.get_obj_by_id(PAYMENTS_DB_KEY, payment_id)
  if payment_db['status'] == payment_statuses[2]:
    print('Уже начислено.')
    return

  if payment.status == payment_statuses[2]:
    user = db_service.get_obj_by_id(USERS_DB_KEY, user_id)
    user['tokens'] = user['tokens'] + tokens_by_cost[str(payment.amount.value)]
    db_service.set_obj_by_id(USERS_DB_KEY, user_id, user)
    await send_success(user_id, payment)
    await send_success_admin(user_id, user['username'], payment_id, payment)
  else:
    print('BAD RETURN')

  payment_db = db_service.get_obj_by_id(PAYMENTS_DB_KEY, payment_id)
  payment_db['status'] = payment.status
  db_service.set_obj_by_id(PAYMENTS_DB_KEY, payment_id, payment_db)

def create_payment(amount, tokens):
  idempotence_key = str(uuid.uuid4())
  return Payment.create({
    'amount': {
      'value': f'{amount}.00',
      'currency': 'RUB'
    },
    "capture": True,
    'payment_method_data': {
      'type': 'bank_card'
    },
    'confirmation': {
      'type': 'redirect',
      'return_url': 'https://t.me/SarahGPTBot'
    },
    'description': f'Покупка {tokens} токенов'
  }, idempotence_key)

def create_keyboard():
  keyboard = types.InlineKeyboardMarkup()
  keyboard.add(types.InlineKeyboardButton(
    text='10 токенов [10 RUB]',
    callback_data='10tokens')
  )
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

async def send_success(user_id, payment):
  text = f'Платеж на сумму {str(payment.amount.value)} {str(payment.amount.currency)} успешно завершен.\n'
  text += f'Вам начислено {tokens_by_cost[str(payment.amount.value)]} токенов!\n\n/tokens - Остаток токенов'
  await bot.send_message(user_id, text)

async def send_success_admin(user_id, username, payment_id, payment):
  text = 'Успешная транзакция:\n'
  text += f'\nuser_id: {user_id}'
  text += f'\nusername: @{username}'
  text += f'\npayment_id: {payment_id}'
  text += f'\namount: {str(payment.amount.value)} {str(payment.amount.currency)}'
  await bot.send_message(admins[0], text)

async def send_problem_admin(user_id, username, payment_id, payment):
  text = 'Проблемы с оплатой:\n'
  text += f'\nuser_id: {user_id}'
  text += f'\nusername: @{username}'
  text += f'\npayment_id: {payment_id}'
  text += f'\npayment_status: {payment.status}'
  await bot.send_message(admins[0], text)

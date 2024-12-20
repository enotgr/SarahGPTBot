from misc import bot, dp
from services.db_service import db_service
from services.openai_request import openai_request
from utils.handler_utils import send
from consts.db_keys import USERS_DB_KEY
from consts.admins import admins
from consts.common import start_words, image_cost, gpt_models_map
from aiogram.utils.deep_linking import get_start_link
from aiogram.utils.exceptions import BotBlocked
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from io import BytesIO
import qrcode
import time
import httplib2

image_requesters: list[int] = []
public_admin_message_mode: bool = False

@dp.message_handler(commands=['start'])
async def send_welcome(message):
  unique_code = extract_unique_code(message.text)
  user_info = message.from_user
  id = user_info.id
  username = user_info.username
  first_name = user_info.first_name
  name = first_name if first_name else username

  if not username:
    username = name
  if not username:
    username = 'Человек'
    name = 'Человек'

  if not add_user_to_db(id, username):
    await send(message, 'Привет, {0}.\nМожете спрашивать меня о чём угодно!'.format(name))
    return

  if unique_code:
    await add_referal_tokens(unique_code, name)

  await send(message, f'Привет, <b>{name}</b>!\nЯ ChatGPT, языковая модель, созданная компанией OpenAI на основе архитектуры GPT. Моя основная цель — помогать пользователям, отвечая на их вопросы и выполняя различные задачи.\nЧем я могу помочь вам?')

@dp.message_handler(commands=['ref', 'qrcode', 'qr', 'ref_link'])
async def ref(message):
  link = await get_start_link(message.from_user.id)

  qr = qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_L,
    box_size=12,
    border=4,
  )

  qr.add_data(link)
  qr.make(fit=True)

  img = qr.make_image(fill_color="#fefefe", back_color="#028374")

  byte_io = BytesIO()
  img.save(byte_io, 'PNG')
  byte_io.seek(0)

  await send(message, f'Реферальная ссылка:\n{link}')
  await bot.send_photo(message.chat.id, photo=byte_io)
  await send(message, 'Просто отправьте ссылку другу или покажите ему qrcode!\nЗа каждого приведённого друга вы получите 15 токенов!')

@dp.message_handler(commands=['tokens'])
async def tokens(message):
  user = db_service.get_obj_by_id(USERS_DB_KEY, message.from_user.id)
  await send(message, 'Кошелек:\n{0}'.format(get_tokens_text(user['tokens'])))

@dp.message_handler(commands=['reset'])
async def reset_context(message):
  openai_request.reset_context(message.from_user.id)
  await send(message, 'Контекст очищен.')

@dp.message_handler(commands=['gpt3'])
async def choose_gpt3(message):
  user = db_service.get_obj_by_id(USERS_DB_KEY, message.from_user.id)
  user['model'] = 'gpt3'
  db_service.set_obj_by_id(USERS_DB_KEY, message.from_user.id, user)
  await send(message, 'Выбрана модель gpt-3.5-turbo. Стоимость одного текстового запроса к этой модели - 1 токен')

@dp.message_handler(commands=['gpt4'])
async def choose_gpt4(message):
  user = db_service.get_obj_by_id(USERS_DB_KEY, message.from_user.id)
  user['model'] = 'gpt4'
  db_service.set_obj_by_id(USERS_DB_KEY, message.from_user.id, user)
  await send(message, 'Выбрана модель gpt-4-turbo. Стоимость одного текстового запроса к этой модели - 12 токенов')

@dp.message_handler(commands=['image'])
async def request_image(message):
  if message.from_user.id in image_requesters:
    await send(message, 'Введите запрос.\nНапример: <b>Белый сиамский кот</b>')
    return
  image_requesters.append(message.from_user.id)
  keyboard = InlineKeyboardMarkup()
  keyboard.add(InlineKeyboardButton(
    text='Отменить',
    callback_data='cancel_generate_image')
  )
  await message.answer(
    f'Введите запрос для генерации изображения.\nНапример: <b>Белый сиамский кот</b>\n\n<i>Генерация одного изображения стоит <b>{image_cost}</b> токенов</i>',
    reply_markup=keyboard,
    parse_mode='html'
  )

@dp.callback_query_handler(text='cancel_generate_image')
async def cancel_generate_image(callback: CallbackQuery):
  await callback.message.delete()
  user_id = callback.message.chat.id
  if user_id in image_requesters:
    image_requesters.remove(user_id)

@dp.message_handler(commands=['users'])
async def get_users_count_admin(message):
  if message.from_user.id not in admins:
    return
  users_count = len(db_service.get_db(USERS_DB_KEY).keys())
  await send(message, f'Количество пользователей: <b>{users_count}</b>')

@dp.message_handler(commands=['public'])
async def activate_public_message_mode(message):
  global public_admin_message_mode
  if message.from_user.id not in admins:
    return
  if public_admin_message_mode:
    public_admin_message_mode = False
    await send(message, '<b>Публичные сообщения деактивированы.</b>')
  else:
    public_admin_message_mode = True
    await send(message, '<b>Публичные сообщения активированы.</b>')

@dp.errors_handler(exception=BotBlocked)
async def bot_blocked_handler(update: Update, exception: BotBlocked):
  print('EXCEPTION: Bot was blocked by user')
  print('update:', update)
  print('exception:', exception)
  return True

async def send_admin_public_message(text: str):
  users = db_service.get_db(USERS_DB_KEY)
  for id in users.keys():
    try:
      await bot.send_message(id, text)
    except:
      await bot.send_message(admins[0], f'Пользователь {users[id]["username"]} (id: {id}) заблокировал бота и больше недоступен.')

async def send_image(message) -> bool:
  await bot.send_chat_action(message.from_user.id, 'upload_photo')
  image_url = openai_request.generate_image(message.text)
  if not image_url:
    return False
  h = httplib2.Http('.cache')
  _, content = h.request(image_url)
  await bot.send_photo(message.from_user.id, content)
  return True

@dp.message_handler()
async def user_messages(message):
  if message.from_user.id == admins[0] and public_admin_message_mode:
    await send_admin_public_message(message.text)
    return

  if is_command_text(start_words, message.text):
    await send_welcome(message)
    return

  user = db_service.get_obj_by_id(USERS_DB_KEY, message.from_user.id)
  tokens_count = user['tokens']

  if message.from_user.id in image_requesters:
    image_requesters.remove(message.from_user.id)
    if tokens_count < image_cost:
      await send(message, f'У вас не достаточно токенов.\nГенерация одного изображения стоит {image_cost} токенов\n\n/buy - Купить токены\n/ref - Пригласите друга и получите 15 токенов')
      return
    if not message.text:
      print('Error: Text cannot be empty')
      return
    is_succeed = await send_image(message)
    if not is_succeed:
      await send(message, 'Упс! Что-то пошло не так...\n\nВаш запрос был отклонен.\nВозможно ваш запрос содержит текст, не разрешенный системой безопасности OpenAI.\nПопробуйте изменить текст запроса или написать запрос на английском языке и повторить.')
      return
    user['tokens'] = tokens_count - image_cost
    db_service.set_obj_by_id(USERS_DB_KEY, message.from_user.id, user)
    return

  current_model: str = get_user_model(user, message.from_user.id)
  request_cost: int = gpt_models_map[current_model]['cost']

  if tokens_count < request_cost:
    await send(message, f'У вас не достаточно токенов.\nТекстовый запрос к SarahGPT стоит {get_tokens_text(request_cost)}\n\n/buy - Купить токены\n/ref - Пригласите друга и получите 15 токенов')
    return

  await bot.send_chat_action(message.from_user.id, 'typing')

  try:
    answer: str = openai_request.lets_talk(message.from_user.id, message.text, current_model)
    await bot.send_message(message.from_user.id, answer)
  except:
    await bot.send_message(message.from_user.id, 'Превышен лимит контекста. Пожалуйста, очистите контекст\n\n/reset - Очистить контекст')
    return

  user['tokens'] = tokens_count - request_cost
  db_service.set_obj_by_id(USERS_DB_KEY, message.from_user.id, user)

async def add_referal_tokens(unique_code, name):
  user = None
  try:
    user = db_service.get_obj_by_id(USERS_DB_KEY, unique_code)
  except:
    print(f'ERR: Incorrect referal link {unique_code}')
    return

  user['tokens'] = user['tokens'] + 15
  db_service.set_obj_by_id(USERS_DB_KEY, unique_code, user)
  await bot.send_message(unique_code, f'Ваш друг <b>{name}</b> зарегистрировался по вашей реферальной ссылке!\nВам начислено 15 токенов\n\n/tokens - Остаток токенов', parse_mode='html')

def add_user_to_db(id, username):
  if db_service.is_obj_exists(USERS_DB_KEY, id):
    print('WARN: User is already exists')
    return False

  new_user = { 'username': username }
  new_user['created_at'] = time.time() * 1000
  new_user['tokens'] = 25

  db_service.set_obj_by_id(USERS_DB_KEY, id, new_user)
  return True

def extract_unique_code(text):
  return text.split()[1] if len(text.split()) > 1 else None

def is_command_text(command_words: list[str], text: str) -> bool:
  for word in command_words:
    if word == text.lower():
      return True
  return False

def define_suffix(value, suffixes):
  if len(suffixes) != 3:
    return ''
  rest_of_100 = value % 100
  if rest_of_100 > 10 and rest_of_100 < 20:
    return suffixes[2]

  rest = value % 10
  if rest == 1:
    return suffixes[0]
  if rest > 1 and rest < 5:
    return suffixes[1]
  return suffixes[2]

def get_tokens_text(tokens):
  return f'{tokens} {define_suffix(tokens, ["токен", "токена", "токенов"])}'

def get_user_model(user, user_id: int) -> str:
  user_model: str = 'gpt3'
  try:
    user_model = user['model']
  except:
    user['model'] = user_model
    db_service.set_obj_by_id(USERS_DB_KEY, user_id, user)
  return user_model

import firebase_admin
from firebase_admin import credentials
from firebase_admin import db
from consts.database_url import url

cred = credentials.Certificate('sarahai-chat-gpt-bot.json')
app = firebase_admin.initialize_app(cred, { 'databaseURL': url })

class DBService:
  def get_db(self, db_name):
    return db.reference('/{0}/'.format(db_name)).get()

  def save_db(self, db_name, data):
    return db.reference('/{0}/'.format(db_name)).set(data)

  def get_obj_by_id(self, db_name, id):
    return db.reference('/{0}/{1}'.format(db_name, id)).get()

  def set_obj_by_id(self, db_name, id, obj):
    return db.reference('/{0}/{1}'.format(db_name, id)).set(obj)

  def is_obj_exists(self, db_name, id):
    return not not db.reference('/{0}/{1}'.format(db_name, id)).get()

db_service = DBService()

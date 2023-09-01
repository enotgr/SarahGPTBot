import openai
from services.db_service import db_service
from consts.access_keys import OPENAI_ORGANIZATION, OPENAI_API_KEY
from consts.db_keys import CONTEXTS_DB_KEY

# Use your organization and API keys
openai.organization = OPENAI_ORGANIZATION
openai.api_key = OPENAI_API_KEY

class OpenAIRequest:
  def lets_talk(self, user_id: int, text: str) -> str:
    if not text:
      return

    context: list[dict] = db_service.get_obj_by_id(CONTEXTS_DB_KEY, user_id) or []

    context.append({ 'role': 'user', 'content': text })

    response = openai.ChatCompletion.create(
      model='gpt-3.5-turbo',
      messages=context,
      temperature=0.9,
      max_tokens=256,
      top_p=1,
      frequency_penalty=0.0,
      presence_penalty=0.6,
    )

    answer = response.get('choices')[0].get('message').get('content')
    context.append({ 'role': 'assistant', 'content': answer})
    db_service.set_obj_by_id(CONTEXTS_DB_KEY, user_id, context)

    return answer

  def generate_image(self, prompt: str) -> str:
    if not prompt:
      return ''

    response = openai.Image.create(
      prompt=prompt,
      n=1,
      size='1024x1024'
    )
    return response['data'][0]['url']

  def reset_context(self, user_id: int):
    db_service.set_obj_by_id(CONTEXTS_DB_KEY, user_id, [])

openai_request = OpenAIRequest()

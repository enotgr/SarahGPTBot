"""Environment configuration; existing private configuration files remain supported."""

import importlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


def setting(env, module=None, attribute=None, default=None):
    value = os.getenv(env)
    if value is not None:
        return value
    if module:
        try:
            return getattr(importlib.import_module(module), attribute)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise
    return default


BOT_TOKEN = setting("TELEGRAM_BOT_TOKEN", "bot_token", "TOKEN", "")
OPENAI_API_KEY = setting("OPENAI_API_KEY", "consts.access_keys", "OPENAI_API_KEY", "")
OPENAI_ORGANIZATION = os.getenv("OPENAI_ORGANIZATION") or None
OPENAI_PROJECT = os.getenv("OPENAI_PROJECT") or None
FIREBASE_URL = setting("FIREBASE_DATABASE_URL", "consts.database_url", "url", "")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS", str(BASE_DIR / "sarahai-chat-gpt-bot.json"))
ADMIN_IDS = setting("ADMIN_IDS", "consts.admins", "admins", [])
if isinstance(ADMIN_IDS, str):
    ADMIN_IDS = [int(item.strip()) for item in ADMIN_IDS.split(",") if item.strip()]
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").lstrip("@")
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "4"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))
MAX_INPUT_CHARS = 12000
MAX_CONTEXT_CHARS = 24000
MAX_CONTEXT_MESSAGES = 24
NEW_USER_TOKENS = 25
REFERRAL_TOKENS = 15
IMAGE_COST = 10
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")
# Explicit operator pricing. Never infer a RUB/Stars exchange rate.
STARS_PACKAGES = {int(k): int(v) for k, v in json.loads(os.getenv("STARS_PACKAGES", "{}")).items()}
if any(k <= 0 or v <= 0 for k, v in STARS_PACKAGES.items()):
    raise ValueError("STARS_PACKAGES amounts must be positive integers")


def validate():
    for name in ("BOT_TOKEN", "OPENAI_API_KEY", "FIREBASE_URL"):
        if not globals()[name]:
            raise RuntimeError(f"Missing required setting: {name}")
    if not Path(FIREBASE_CREDENTIALS).is_file():
        raise RuntimeError("Firebase credentials file is missing")

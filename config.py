"""Environment configuration; existing private configuration files remain supported."""

import importlib
import json
import os
from decimal import Decimal
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
MAX_INPUT_TOKENS = 8192
FINANCE_DB_PATH = Path(os.getenv("FINANCE_DB_PATH", str(BASE_DIR / "data/finance.sqlite3")))
MONTHLY_API_BUDGET_MICRO = int(Decimal(os.getenv("MONTHLY_API_BUDGET_USD", "20")) * 1000000)
DAILY_API_BUDGET_MICRO = int(Decimal(os.getenv("DAILY_API_BUDGET_USD", "2")) * 1000000)
PROMO_MONTHLY_BUDGET_MICRO = int(Decimal(os.getenv("PROMO_MONTHLY_BUDGET_USD", "1")) * 1000000)
PROMO_DAILY_CREDITS = 250
PROMO_MONTHLY_CREDITS = 2500
USER_DAILY_REQUESTS = 100
PROMO_DAILY_REQUESTS = 5
REQUEST_COOLDOWN_SECONDS = 2
PERSONAL_USER_IDS = {int(item) for item in os.getenv("PERSONAL_USER_IDS", "").split(",") if item.strip()}
if min(MONTHLY_API_BUDGET_MICRO, DAILY_API_BUDGET_MICRO, PROMO_MONTHLY_BUDGET_MICRO) < 0:
    raise ValueError("API budgets cannot be negative")
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

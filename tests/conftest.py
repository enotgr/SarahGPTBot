import os

# No test may use real API credentials or production database configuration.
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
os.environ["OPENAI_API_KEY"] = "test-not-a-real-key"
os.environ["FIREBASE_DATABASE_URL"] = "https://test.invalid"
os.environ["ADMIN_IDS"] = "123"

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TONKEEPER_ADDRESS = os.getenv("TONKEEPER_ADDRESS", "").strip()
SUBSCRIPTION_PRICE = float(os.getenv("SUBSCRIPTION_PRICE", "5"))
SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "").strip()
SUBSCRIBER_CHANNEL_LINK = os.getenv("SUBSCRIBER_CHANNEL_LINK", "").strip()
AUTO_CHECK_INTERVAL = int(os.getenv("AUTO_CHECK_INTERVAL", "60"))

DB_PATH = os.getenv("DB_PATH", "").strip() or "/data/bot.db"

if not BOT_TOKEN or BOT_TOKEN.startswith("ВСТАВЬТЕ"):
    raise ValueError("❌ Укажите BOT_TOKEN в файле .env")
if not TONKEEPER_ADDRESS:
    raise ValueError("❌ Укажите TONKEEPER_ADDRESS в файле .env")

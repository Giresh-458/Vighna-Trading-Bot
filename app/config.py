import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram Settings
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "telegram_session")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
COMMODITY_CHANNEL_ID = os.getenv("COMMODITY_CHANNEL_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if TELEGRAM_API_ID:
    TELEGRAM_API_ID = int(TELEGRAM_API_ID)
if TELEGRAM_CHANNEL_ID:
    try:
        TELEGRAM_CHANNEL_ID = int(TELEGRAM_CHANNEL_ID)
    except ValueError:
        pass
if COMMODITY_CHANNEL_ID:
    try:
        COMMODITY_CHANNEL_ID = int(COMMODITY_CHANNEL_ID)
    except ValueError:
        COMMODITY_CHANNEL_ID = None

# Angel One Settings
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY")
ANGEL_CLIENT_ID = os.getenv("ANGEL_CLIENT_ID")
ANGEL_PASSWORD = os.getenv("ANGEL_PASSWORD")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")

# Trading Settings
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", 100000.0))
EOD_CLOSE_TIME = os.getenv("EOD_CLOSE_TIME", "15:15")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
PAPER_ONLY = os.getenv("PAPER_ONLY", "true").strip().lower() == "true"
APP_ENV = os.getenv("APP_ENV", "paper").strip().lower()

# Execution safeguards. Entry wait values are in seconds; tick age is in milliseconds.
ENTRY_WAIT_SECONDS = int(os.getenv("ENTRY_WAIT_SECONDS", "300"))
COMMODITY_FUTURES_ENTRY_WAIT_SECONDS = int(
    os.getenv("COMMODITY_FUTURES_ENTRY_WAIT_SECONDS", "14400")
)
MAX_TICK_AGE_MS = int(os.getenv("MAX_TICK_AGE_MS", "2000"))

def validate_config():
    missing = []
    if not TELEGRAM_API_ID: missing.append("TELEGRAM_API_ID")
    if not TELEGRAM_API_HASH: missing.append("TELEGRAM_API_HASH")
    if not TELEGRAM_CHANNEL_ID: missing.append("TELEGRAM_CHANNEL_ID")
    if not TELEGRAM_BOT_TOKEN: missing.append("TELEGRAM_BOT_TOKEN")
    if not ANGEL_API_KEY: missing.append("ANGEL_API_KEY")
    if not ANGEL_CLIENT_ID: missing.append("ANGEL_CLIENT_ID")
    if not ANGEL_PASSWORD: missing.append("ANGEL_PASSWORD")
    if not ANGEL_TOTP_SECRET: missing.append("ANGEL_TOTP_SECRET")
    if missing:
        raise ValueError(f"Missing mandatory environment variables in .env: {', '.join(missing)}")

    if ENTRY_WAIT_SECONDS <= 0:
        raise ValueError("ENTRY_WAIT_SECONDS must be greater than zero")
    if COMMODITY_FUTURES_ENTRY_WAIT_SECONDS <= 0:
        raise ValueError("COMMODITY_FUTURES_ENTRY_WAIT_SECONDS must be greater than zero")
    if MAX_TICK_AGE_MS <= 0:
        raise ValueError("MAX_TICK_AGE_MS must be greater than zero")

    # Live order placement currently does not reconcile broker fills with local positions.
    # Refuse live startup rather than allow the local ledger to claim fills that never occurred.
    if APP_ENV == "real" and not PAPER_ONLY:
        raise ValueError(
            "Live trading is blocked until broker fill/status reconciliation is implemented. "
            "Use APP_ENV=paper and PAPER_ONLY=true for safe validation."
        )

import os
from datetime import date
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROUP_CHAT_ID = int(os.environ["GROUP_CHAT_ID"])
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

TIMEZONE = ZoneInfo("Europe/Warsaw")
LAST_CHALLENGE_DAY = date.fromisoformat(os.environ.get("LAST_CHALLENGE_DAY", "2026-10-14"))

PENALTY_PLN = 10
DB_PATH = os.environ.get("DB_PATH", "pushups.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "backups")

# Poll option indexes are fixed throughout the bot.
OPTION_DONE = 0
OPTION_NOT_YET = 1

import os
from datetime import date
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROUP_CHAT_ID = int(os.environ["GROUP_CHAT_ID"])
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

TIMEZONE = ZoneInfo("Europe/Warsaw")
LAST_CHALLENGE_DAY = date.fromisoformat(os.environ.get("LAST_CHALLENGE_DAY", "2026-10-09"))

PENALTY_PLN = 10
PUSHUPS_PER_DAY = 50

# Who gets their own "how did they do it" chart in the final stats — an
# @username, a numeric user id, or a first name (case-insensitive).
SPOTLIGHT_USER = os.environ.get("SPOTLIGHT_USER", "Krzysztof").strip()
DB_PATH = os.environ.get("DB_PATH", "pushups.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "backups")

# Poll option indexes are fixed throughout the bot.
OPTION_DONE = 0
OPTION_NOT_YET = 1

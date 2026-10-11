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

# Hour of day (Europe/Warsaw) the daily poll opens; it closes 24h later,
# right as the next one opens.
POLL_HOUR = 7

PENALTY_PLN = 10
PUSHUPS_PER_DAY = 50

# Who gets their own "how did they do it" chart in the final stats — an
# @username, a numeric user id, or a first name (case-insensitive).
SPOTLIGHT_USER = os.environ.get("SPOTLIGHT_USER", "").strip()
DB_PATH = os.environ.get("DB_PATH", "pushups.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "backups")

# Poll option indexes are fixed throughout the bot.
OPTION_DONE = 0
OPTION_NOT_YET = 1

# Automatic payments through Enable Banking (see bank_auth.py). Everything
# is optional: without EB_ACCOUNT_UID the bot just runs as a plain ledger.
EB_APP_ID = os.environ.get("EB_APP_ID", "")
EB_KEY_PATH = os.environ.get("EB_KEY_PATH", "enablebanking.pem")
EB_SESSION_ID = os.environ.get("EB_SESSION_ID", "")
EB_ACCOUNT_UID = os.environ.get("EB_ACCOUNT_UID", "")
EB_CONSENT_UNTIL = os.environ.get("EB_CONSENT_UNTIL", "")  # YYYY-MM-DD, for the expiry warning
EB_ASPSP_NAME = os.environ.get("EB_ASPSP_NAME", "PKO Bank Polski")
EB_REDIRECT_URL = os.environ.get("EB_REDIRECT_URL", "https://localhost/")
EB_CONSENT_DAYS = int(os.environ.get("EB_CONSENT_DAYS", "90"))
PAYMENTS_ENABLED = bool(EB_APP_ID and EB_ACCOUNT_UID)
BANK_SYNC_HOURS = 8  # banks allow ~4 unattended reads a day; leave room for /odswiez and restarts
# Shown to participants: where to send the money (e.g. "BLIK na telefon 500 100 200").
PAY_TO = os.environ.get("PAY_TO", "")

# Kick people who owe money and haven't paid anything for this many days;
# reminders go out when this many days are left (the last one in the group).
# Only active together with automatic payments, so nobody is kicked over a
# payment the bot couldn't see.
KICK_AFTER_DAYS = int(os.environ.get("KICK_AFTER_DAYS", "14"))
KICK_REMIND_DAYS = (7, 2, 1)
AUTO_KICK = PAYMENTS_ENABLED and os.environ.get("AUTO_KICK", "1") != "0"

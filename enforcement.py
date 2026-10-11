"""Who is about to be kicked for not paying, and when.

The clock is "days since the last payment", but only while the person
actually owes something: it starts at the latest of their last payment, their
oldest unpaid miss (nothing was due before that) and the day enforcement was
switched on (so existing debts don't cause a mass kick on launch day).
"""
from dataclasses import dataclass
from datetime import date, timedelta

import config
import db


@dataclass(frozen=True)
class Standing:
    user_id: int
    owed: int            # PLN
    clock_start: date
    days_left: int       # <= 0 means kick


def standing(user_id: int, today: date, enforced_since: date) -> Standing | None:
    """None if the person owes nothing (or is exempt)."""
    if user_id in config.ADMIN_IDS:
        return None
    owed, _, unpaid = db.get_balance(user_id)
    if not unpaid:
        return None
    start = max(date.fromisoformat(unpaid[0]), enforced_since)
    last_paid = db.get_last_payment_date(user_id)
    if last_paid:
        start = max(start, date.fromisoformat(last_paid))
    days_left = (start + timedelta(days=config.KICK_AFTER_DAYS) - today).days
    return Standing(user_id, owed, start, days_left)


def reminder_stage(days_left: int) -> int | None:
    """The reminder threshold this many days-left falls under (smallest of
    KICK_REMIND_DAYS that is >= days_left), or None if too early / too late."""
    if days_left < 1:
        return None
    fitting = [d for d in config.KICK_REMIND_DAYS if days_left <= d]
    return min(fitting) if fitting else None


def deadline(s: Standing) -> date:
    return s.clock_start + timedelta(days=config.KICK_AFTER_DAYS)

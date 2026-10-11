"""Turns incoming bank transfers into paid penalty days.

Pure bookkeeping on top of db — no network, no Telegram — so the matching
rules can be tested on their own. bank.py fetches the transfers, bot.py
announces the outcomes.
"""
import re
import unicodedata
from dataclasses import dataclass, field

import config
import db

CODE_RE = re.compile(r"POMPKI[\s\-_:.]*([A-Z0-9]{4})", re.IGNORECASE)


@dataclass(frozen=True)
class Transfer:
    tx_id: str
    amount: int          # grosze
    sender: str
    title: str
    booked_on: str       # YYYY-MM-DD


@dataclass
class Outcome:
    transfer: Transfer
    payment_id: int
    user_id: int | None                  # None = unmatched, needs an admin
    paid_dates: list[str] = field(default_factory=list)
    credit: int = 0                      # grosze left over after settling
    ignored: bool = False                # unrelated income, nobody needs to see it


def pay_code_in(title: str) -> str | None:
    m = CODE_RE.search(title)
    return m.group(1).upper() if m else None


def normalize_name(name: str) -> str:
    """'Jan  Kowalski' and 'KOWALSKI JAN' (and with diacritics) are the
    same sender: strip accents, casefold, sort the words."""
    # ł/Ł don't decompose under NFKD, so map them by hand.
    name = name.replace("ł", "l").replace("Ł", "L")
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(sorted(re.findall(r"[a-z0-9]+", ascii_.casefold())))


def match_user(transfer: Transfer) -> int | None:
    code = pay_code_in(transfer.title)
    if code:
        p = db.get_participant_by_pay_code(code)
        if p:
            return p["user_id"]
    alias = normalize_name(transfer.sender)
    return db.get_alias(alias) if alias else None


def looks_like_a_penalty(transfer: Transfer) -> bool:
    """The account gets every kind of income, not just penalties. An
    unmatched transfer is only worth an admin's attention if it mentions the
    challenge or is a plausible penalty amount (whole PENALTY_PLN steps, up
    to a month's worth)."""
    price = config.PENALTY_PLN * 100
    if "pomp" in transfer.title.casefold():
        return True
    return transfer.amount % price == 0 and transfer.amount <= 30 * price


def settle(user_id: int, grosze: int) -> tuple[list[str], int]:
    """Spends `grosze` plus any credit the person already has on their
    oldest unpaid days, one PENALTY_PLN each. Whatever doesn't cover a whole
    day stays as credit. Returns (dates marked paid, credit left)."""
    price = config.PENALTY_PLN * 100
    total = db.get_credit(user_id) + grosze
    _, _, unpaid = db.get_balance(user_id)
    paid = []
    for d in unpaid:
        if total < price:
            break
        if db.mark_paid(user_id, d):
            paid.append(d)
            total -= price
    db.set_credit(user_id, total)
    return paid, total


def apply_pending_credit() -> list[tuple[int, list[str], int]]:
    """Credit from an earlier overpayment pays off misses that were recorded
    afterwards. Returns (user_id, dates, credit left) per person it moved."""
    moved = []
    for user_id in db.get_users_with_credit():
        paid, left = settle(user_id, 0)
        if paid:
            moved.append((user_id, paid, left))
    return moved


def process(transfer: Transfer) -> Outcome | None:
    """Records and applies one transfer. None if it was already processed."""
    user_id = match_user(transfer)
    if user_id:
        status = "applied"
    else:
        status = "unmatched" if looks_like_a_penalty(transfer) else "ignored"
    payment_id = db.insert_payment(
        transfer.tx_id, transfer.amount, transfer.sender, transfer.title,
        transfer.booked_on, user_id, status,
    )
    if payment_id is None:
        return None
    if user_id is None:
        return Outcome(transfer, payment_id, None, ignored=status == "ignored")
    paid, credit = settle(user_id, transfer.amount)
    return Outcome(transfer, payment_id, user_id, paid, credit)


def assign(payment_id: int, user_id: int) -> Outcome | None:
    """An admin resolves an unmatched payment by hand. Remembers the sender
    so their next transfer matches by itself."""
    row = db.get_payment(payment_id)
    if not row or row["status"] != "unmatched":
        return None
    db.resolve_payment(payment_id, user_id, "applied")
    alias = normalize_name(row["sender"])
    if alias:
        db.set_alias(alias, user_id)
    paid, credit = settle(user_id, row["amount"])
    transfer = Transfer(row["tx_id"], row["amount"], row["sender"], row["title"], row["booked_on"])
    return Outcome(transfer, payment_id, user_id, paid, credit)


def ignore(payment_id: int) -> bool:
    row = db.get_payment(payment_id)
    if not row or row["status"] != "unmatched":
        return False
    db.resolve_payment(payment_id, None, "ignored")
    return True

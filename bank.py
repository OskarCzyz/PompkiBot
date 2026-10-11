"""Reads incoming transfers from the bank account through Enable Banking
(PSD2 account-information API). Read-only: the bot never moves money.

One-time consent flow lives in bank_auth.py; this module only needs the
resulting session (EB_SESSION_ID / EB_ACCOUNT_UID in .env).
"""
import hashlib
import logging
import time
from datetime import date
from decimal import Decimal

import httpx
import jwt

import config
from payments import Transfer

log = logging.getLogger("pompki-bank")

API = "https://api.enablebanking.com"


class BankError(Exception):
    pass


class BankRateLimited(BankError):
    """The bank allows only a few unattended fetches a day (HTTP 429)."""


def make_jwt() -> str:
    with open(config.EB_KEY_PATH, "rb") as f:
        key = f.read()
    now = int(time.time())
    return jwt.encode(
        {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600},
        key,
        algorithm="RS256",
        headers={"typ": "JWT", "kid": config.EB_APP_ID},
    )


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {make_jwt()}"}


def to_transfer(tx: dict) -> Transfer | None:
    """One API transaction -> Transfer, or None if it isn't an incoming PLN
    credit. The sender is the debtor; the title is the free-text remittance."""
    if tx.get("credit_debit_indicator") != "CRDT":
        return None
    amount = tx.get("transaction_amount") or {}
    if amount.get("currency", "PLN") != "PLN":
        return None
    grosze = int((Decimal(str(amount["amount"])) * 100).to_integral_value())
    if grosze <= 0:
        return None
    sender = ((tx.get("debtor") or {}).get("name") or "").strip()
    remittance = tx.get("remittance_information") or []
    title = " ".join(remittance) if isinstance(remittance, list) else str(remittance)
    booked = tx.get("booking_date") or tx.get("value_date") or date.today().isoformat()
    tx_id = tx.get("entry_reference") or tx.get("transaction_id")
    if not tx_id:
        # No bank id: fall back to a fingerprint of the content.
        tx_id = "h-" + hashlib.sha256(f"{booked}|{grosze}|{sender}|{title}".encode()).hexdigest()[:24]
    return Transfer(str(tx_id), grosze, sender, title.strip(), booked)


async def fetch_incoming(date_from: date) -> list[Transfer]:
    """Booked incoming PLN transfers since date_from, oldest first."""
    transfers: list[Transfer] = []
    params = {"date_from": date_from.isoformat(), "transaction_status": "BOOK"}
    async with httpx.AsyncClient(base_url=API, timeout=30) as client:
        while True:
            resp = await client.get(
                f"/accounts/{config.EB_ACCOUNT_UID}/transactions",
                params=params,
                headers=auth_headers(),
            )
            if resp.status_code == 429:
                raise BankRateLimited("bank rate limit hit, try again in a few hours")
            if resp.status_code != 200:
                raise BankError(f"Enable Banking returned {resp.status_code}: {resp.text[:300]}")
            body = resp.json()
            for tx in body.get("transactions", []):
                t = to_transfer(tx)
                if t:
                    transfers.append(t)
            key = body.get("continuation_key")
            if not key:
                break
            params["continuation_key"] = key
    transfers.sort(key=lambda t: t.booked_on)
    return transfers

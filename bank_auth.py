"""One-time consent with the bank (run by the account holder, on any machine
that has the Enable Banking private key):

    python bank_auth.py start    # prints a login link for the bank
    python bank_auth.py finish <code-from-redirect-url>

`start` makes you log in to the bank and approve read-only access. The
browser then lands on EB_REDIRECT_URL with ?code=... in the address bar
(the page itself may not load — that's fine); pass that code to `finish`,
which prints the EB_SESSION_ID / EB_ACCOUNT_UID / EB_CONSENT_UNTIL lines
to put in .env.
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

import bank
import config


def start():
    valid_until = datetime.now(timezone.utc) + timedelta(days=config.EB_CONSENT_DAYS)
    resp = httpx.post(
        f"{bank.API}/auth",
        headers=bank.auth_headers(),
        json={
            "access": {"valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "aspsp": {"name": config.EB_ASPSP_NAME, "country": "PL"},
            "state": str(uuid.uuid4()),
            "redirect_url": config.EB_REDIRECT_URL,
            "psu_type": "personal",
        },
        timeout=30,
    )
    resp.raise_for_status()
    print("Open this link, log in to the bank and approve access:\n")
    print(resp.json()["url"])
    print("\nThen run: python bank_auth.py finish <code from the redirect URL>")


def finish(code: str):
    resp = httpx.post(f"{bank.API}/sessions", headers=bank.auth_headers(), json={"code": code}, timeout=30)
    resp.raise_for_status()
    s = resp.json()
    print(f"EB_SESSION_ID={s['session_id']}")
    print(f"EB_CONSENT_UNTIL={(s.get('access') or {}).get('valid_until', '')[:10]}")
    print("\nAccounts (pick the one that receives the transfers):")
    for a in s.get("accounts", []):
        iban = (a.get("account_id") or {}).get("iban", "?")
        print(f"  EB_ACCOUNT_UID={a['uid']}   # {iban} {a.get('name', '')}")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "start":
        start()
    elif len(sys.argv) == 3 and sys.argv[1] == "finish":
        finish(sys.argv[2])
    else:
        print(__doc__)

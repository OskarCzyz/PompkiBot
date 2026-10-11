import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS polls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_date         TEXT NOT NULL UNIQUE,
    telegram_poll_id  TEXT NOT NULL UNIQUE,
    message_id        INTEGER NOT NULL,
    closed            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS votes (
    poll_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    option_id   INTEGER NOT NULL,
    voted_at    TEXT NOT NULL,
    PRIMARY KEY (poll_id, user_id)
);

CREATE TABLE IF NOT EXISTS misses (
    user_id     INTEGER NOT NULL,
    miss_date   TEXT NOT NULL,
    paid        INTEGER NOT NULL DEFAULT 0,
    paid_at     TEXT,
    waived      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, miss_date)
);

CREATE TABLE IF NOT EXISTS regen_days (
    user_id     INTEGER NOT NULL,
    regen_date  TEXT NOT NULL,
    PRIMARY KEY (user_id, regen_date)
);

-- Incoming bank transfers, as read from the bank. tx_id is the bank's own
-- id, so a transfer seen twice is only ever processed once. Amounts are in
-- grosze (1/100 PLN). status: applied | unmatched | ignored.
CREATE TABLE IF NOT EXISTS payments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_id         TEXT NOT NULL UNIQUE,
    amount        INTEGER NOT NULL,
    sender        TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    booked_on     TEXT NOT NULL,
    user_id       INTEGER,
    status        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- Sender name -> participant, learned when an admin resolves an unmatched
-- payment by hand, so that person's next transfer matches on its own.
CREATE TABLE IF NOT EXISTS payer_aliases (
    alias    TEXT PRIMARY KEY,
    user_id  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bank_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

PAY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


@contextmanager
def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.executescript(SCHEMA)
        # Migration for databases created before misses.waived existed.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(misses)")}
        if "waived" not in cols:
            conn.execute("ALTER TABLE misses ADD COLUMN waived INTEGER NOT NULL DEFAULT 0")
        # Migrations for databases created before automatic payments existed.
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(participants)")}
        if "pay_code" not in pcols:
            conn.execute("ALTER TABLE participants ADD COLUMN pay_code TEXT")
        if "credit" not in pcols:
            conn.execute("ALTER TABLE participants ADD COLUMN credit INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_participants_pay_code ON participants(pay_code)"
        )
        for r in conn.execute("SELECT user_id FROM participants WHERE pay_code IS NULL").fetchall():
            _assign_pay_code(conn, r["user_id"])


def _assign_pay_code(conn, user_id: int) -> str:
    while True:
        code = "".join(secrets.choice(PAY_CODE_ALPHABET) for _ in range(4))
        taken = conn.execute("SELECT 1 FROM participants WHERE pay_code = ?", (code,)).fetchone()
        if not taken:
            conn.execute("UPDATE participants SET pay_code = ? WHERE user_id = ?", (code, user_id))
            return code


# ---------- participants ----------

def add_participant(user_id: int, username: str | None, first_name: str):
    with _conn() as conn:
        conn.execute(
            """INSERT INTO participants (user_id, username, first_name, active)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(user_id) DO UPDATE SET
                   username=excluded.username,
                   first_name=excluded.first_name,
                   active=1""",
            (user_id, username, first_name),
        )
        if conn.execute("SELECT pay_code FROM participants WHERE user_id = ?", (user_id,)).fetchone()["pay_code"] is None:
            _assign_pay_code(conn, user_id)


def get_participant(user_id: int):
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM participants WHERE user_id = ?", (user_id,)
        ).fetchone()


def get_participant_by_pay_code(code: str):
    """Deliberately includes removed participants, so a late transfer from
    someone who was kicked still matches (and the admins get told) instead
    of showing up as an unknown payment."""
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM participants WHERE pay_code = ?", (code.upper(),)
        ).fetchone()


def get_participant_by_username(username: str):
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM participants WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()


def remove_participant(user_id: int):
    with _conn() as conn:
        conn.execute("UPDATE participants SET active = 0 WHERE user_id = ?", (user_id,))


def forgive_unpaid_misses(user_id: int):
    """Erases a removed participant's unpaid debt — whatever they already
    paid stays on the books, but they don't owe anything going forward."""
    with _conn() as conn:
        conn.execute("DELETE FROM misses WHERE user_id = ? AND paid = 0", (user_id,))


def get_active_participants():
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM participants WHERE active = 1 ORDER BY first_name"
        ).fetchall()


# ---------- polls ----------

def create_poll(poll_date: str, telegram_poll_id: str, message_id: int) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO polls (poll_date, telegram_poll_id, message_id) VALUES (?, ?, ?)",
            (poll_date, telegram_poll_id, message_id),
        )
        return cur.lastrowid


def get_poll_by_date(poll_date: str):
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM polls WHERE poll_date = ?", (poll_date,)
        ).fetchone()


def get_poll_by_telegram_id(telegram_poll_id: str):
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM polls WHERE telegram_poll_id = ?", (telegram_poll_id,)
        ).fetchone()


def close_poll(poll_id: int):
    with _conn() as conn:
        conn.execute("UPDATE polls SET closed = 1 WHERE id = ?", (poll_id,))


def get_unclosed_polls_up_to(cutoff_date: str):
    """Every still-open poll whose own close date is on or before cutoff_date,
    oldest first. Used to catch up on polls that were never closed because
    the bot was down across one or more of their scheduled closes."""
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM polls WHERE closed = 0 AND poll_date <= ? ORDER BY poll_date",
            (cutoff_date,),
        ).fetchall()


def has_prior_poll(before_date: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM polls WHERE poll_date < ? LIMIT 1", (before_date,)
        ).fetchone()
        return row is not None


def get_recent_miss_dates(user_id: int, limit: int = 20) -> list[str]:
    """A participant's recorded miss dates, most recent first — for the
    admin's /markdone button picker."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT miss_date FROM misses WHERE user_id = ? ORDER BY miss_date DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [r["miss_date"] for r in rows]


def get_recent_poll_dates(limit: int = 14) -> list[str]:
    """The most recent poll dates overall, most recent first — for the
    admin's /markmissed button picker."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT poll_date FROM polls ORDER BY poll_date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r["poll_date"] for r in rows]


# ---------- votes ----------

def record_vote(poll_id: int, user_id: int, option_id: int, voted_at: str):
    with _conn() as conn:
        conn.execute(
            """INSERT INTO votes (poll_id, user_id, option_id, voted_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(poll_id, user_id) DO UPDATE SET
                   option_id=excluded.option_id,
                   voted_at=excluded.voted_at""",
            (poll_id, user_id, option_id, voted_at),
        )


def clear_vote(poll_id: int, user_id: int):
    with _conn() as conn:
        conn.execute(
            "DELETE FROM votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id)
        )


def get_votes_for_poll(poll_id: int) -> dict[int, int]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id, option_id FROM votes WHERE poll_id = ?", (poll_id,)
        ).fetchall()
        return {row["user_id"]: row["option_id"] for row in rows}


# ---------- misses / money ----------

def record_miss(user_id: int, miss_date: str):
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO misses (user_id, miss_date, paid) VALUES (?, ?, 0)",
            (user_id, miss_date),
        )


def set_regen(user_id: int, regen_date: str):
    """Admin flag: this day's pushup skip was a deliberate #regeneracja, not
    a forgotten workout. Stored independently of misses, since the miss for
    a day is only recorded when its poll closes the next morning."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO regen_days (user_id, regen_date) VALUES (?, ?)",
            (user_id, regen_date),
        )


def get_regen_user_ids(regen_date: str) -> set[int]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM regen_days WHERE regen_date = ?", (regen_date,)
        ).fetchall()
        return {row["user_id"] for row in rows}


def has_miss(user_id: int, miss_date: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM misses WHERE user_id = ? AND miss_date = ?", (user_id, miss_date)
        ).fetchone()
        return row is not None


def mark_done(user_id: int, miss_date: str) -> bool:
    """Admin override: erase a recorded miss. Returns True if a row was removed."""
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM misses WHERE user_id = ? AND miss_date = ?", (user_id, miss_date)
        )
        return cur.rowcount > 0


def mark_missed(user_id: int, miss_date: str):
    """Admin override: force a miss to exist for that date."""
    record_miss(user_id, miss_date)


def add_waived_miss(user_id: int, miss_date: str):
    """A miss that counts in the stats but carries no penalty — for days
    fixed up after the fact (e.g. someone who voted without ever being
    registered, so their misses were never recorded at the time)."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO misses (user_id, miss_date, paid, waived) VALUES (?, ?, 0, 1)",
            (user_id, miss_date),
        )


def mark_paid(user_id: int, miss_date: str) -> bool:
    """Mark one specific missed date as paid. Returns True if it matched a row."""
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE misses SET paid = 1, paid_at = ?
               WHERE user_id = ? AND miss_date = ? AND paid = 0 AND waived = 0""",
            (datetime.now(config.TIMEZONE).date().isoformat(), user_id, miss_date),
        )
        return cur.rowcount > 0


def unmark_paid(user_id: int, miss_date: str) -> bool:
    """Admin override: undo an accidental /markpaid. Returns True if a row was reverted."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE misses SET paid = 0, paid_at = NULL WHERE user_id = ? AND miss_date = ? AND paid = 1",
            (user_id, miss_date),
        )
        return cur.rowcount > 0


def get_recent_paid_dates(user_id: int, limit: int = 20) -> list[str]:
    """A participant's paid miss dates, most recent first — for the
    admin's /unmarkpaid button picker."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT miss_date FROM misses WHERE user_id = ? AND paid = 1 ORDER BY miss_date DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [r["miss_date"] for r in rows]


def get_balance(user_id: int):
    """Returns (total_owed_pln, total_paid_pln, unpaid_dates). Waived misses
    don't cost anything, so they're left out."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT miss_date, paid FROM misses WHERE user_id = ? AND waived = 0 ORDER BY miss_date",
            (user_id,),
        ).fetchall()
        unpaid_dates = [r["miss_date"] for r in rows if not r["paid"]]
        paid_count = sum(1 for r in rows if r["paid"])
        return (
            len(unpaid_dates) * config.PENALTY_PLN,
            paid_count * config.PENALTY_PLN,
            unpaid_dates,
        )


def get_perfect_streak_user_ids() -> set[int]:
    """Active participants who have zero misses recorded at all."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT p.user_id FROM participants p
               WHERE p.active = 1
                 AND p.user_id NOT IN (SELECT DISTINCT user_id FROM misses)"""
        ).fetchall()
        return {r["user_id"] for r in rows}


def get_full_summary():
    """Per active participant: (user, owed, paid, unpaid_dates). Removed
    participants don't appear here — their unpaid debt is forgiven (see
    forgive_unpaid_misses) and whatever they already paid isn't tied to
    anyone still on the board."""
    participants = get_active_participants()
    return [
        (p, *get_balance(p["user_id"]))
        for p in participants
    ]


# ---------- end-of-challenge stats ----------

def get_closed_poll_dates() -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT poll_date FROM polls WHERE closed = 1 ORDER BY poll_date"
        ).fetchall()
        return [r["poll_date"] for r in rows]


def get_all_votes():
    """Every recorded vote with its poll's date — the raw material for the
    'when did people vote' charts. Only the latest vote per person per poll
    is kept (record_vote overwrites), which is what we want: switching from
    'not yet' to 'done' timestamps when they actually did it."""
    with _conn() as conn:
        return conn.execute(
            """SELECT p.poll_date, v.user_id, v.option_id, v.voted_at
               FROM votes v JOIN polls p ON p.id = v.poll_id
               ORDER BY p.poll_date"""
        ).fetchall()


def get_all_misses():
    with _conn() as conn:
        return conn.execute("SELECT user_id, miss_date FROM misses").fetchall()


# ---------- incoming payments ----------

def insert_payment(tx_id: str, amount: int, sender: str, title: str, booked_on: str,
                   user_id: int | None, status: str) -> int | None:
    """Records a bank transfer. Returns its row id, or None if this tx_id
    was already recorded (i.e. it's been processed before)."""
    with _conn() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO payments
               (tx_id, amount, sender, title, booked_on, user_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (tx_id, amount, sender, title, booked_on, user_id, status,
             datetime.now(config.TIMEZONE).isoformat(timespec="seconds")),
        )
        return cur.lastrowid if cur.rowcount else None


def get_payment(payment_id: int):
    with _conn() as conn:
        return conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()


def resolve_payment(payment_id: int, user_id: int | None, status: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE payments SET user_id = ?, status = ? WHERE id = ?",
            (user_id, status, payment_id),
        )


def get_unmatched_payments():
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE status = 'unmatched' ORDER BY id"
        ).fetchall()


def get_alias(alias: str) -> int | None:
    with _conn() as conn:
        row = conn.execute("SELECT user_id FROM payer_aliases WHERE alias = ?", (alias,)).fetchone()
        return row["user_id"] if row else None


def set_alias(alias: str, user_id: int):
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO payer_aliases (alias, user_id) VALUES (?, ?)", (alias, user_id)
        )


def get_credit(user_id: int) -> int:
    with _conn() as conn:
        row = conn.execute("SELECT credit FROM participants WHERE user_id = ?", (user_id,)).fetchone()
        return row["credit"] if row else 0


def set_credit(user_id: int, grosze: int):
    with _conn() as conn:
        conn.execute("UPDATE participants SET credit = ? WHERE user_id = ?", (grosze, user_id))


def get_users_with_credit() -> list[int]:
    with _conn() as conn:
        rows = conn.execute("SELECT user_id FROM participants WHERE credit > 0 AND active = 1").fetchall()
        return [r["user_id"] for r in rows]


def get_state(key: str) -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM bank_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_state(key: str, value: str):
    with _conn() as conn:
        conn.execute("INSERT OR REPLACE INTO bank_state (key, value) VALUES (?, ?)", (key, value))


def get_last_payment_date(user_id: int) -> str | None:
    """The most recent day this person paid anything — a day marked paid by
    hand or a bank transfer booked for them."""
    with _conn() as conn:
        row = conn.execute(
            """SELECT MAX(d) AS d FROM (
                   SELECT MAX(paid_at) AS d FROM misses WHERE user_id = ? AND paid = 1
                   UNION ALL
                   SELECT MAX(booked_on) FROM payments WHERE user_id = ? AND status = 'applied'
               )""",
            (user_id, user_id),
        ).fetchone()
        return row["d"] if row else None

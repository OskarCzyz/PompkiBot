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
    PRIMARY KEY (user_id, miss_date)
);
"""


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


def get_participant(user_id: int):
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM participants WHERE user_id = ?", (user_id,)
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
    the bot was down across one or more of their scheduled 08:00 closes."""
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


def mark_paid(user_id: int, miss_date: str) -> bool:
    """Mark one specific missed date as paid. Returns True if it matched a row."""
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE misses SET paid = 1, paid_at = ?
               WHERE user_id = ? AND miss_date = ? AND paid = 0""",
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
    """Returns (total_owed_pln, total_paid_pln, unpaid_dates)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT miss_date, paid FROM misses WHERE user_id = ? ORDER BY miss_date",
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

# PompkiBot

Telegram bot for a friends' pushup challenge: 50 pushups a day, a missed day costs a penalty into a shared pot.
Python, python-telegram-bot, SQLite.
User-facing texts are in Polish, code and commits in English.

## Layout

- `bot.py` - handlers, jobs, command menu, `build_application()`.
- `db.py` - SQLite schema, migrations in `init_db()`, all queries.
- `messages.py` - every user-facing text.
- `stats.py` - end-of-challenge stats and charts.
- `bank.py`, `bank_auth.py`, `payments.py`, `enforcement.py` - payment sync and kick rules.
- `docs/challenge-day-spec.md` - spec of the prize challenge days (partly superseded, see below).

## Daily counter (important concept)

The daily message is a button counter, not a Telegram poll: `-1 +1 +5 +10`, `+50`, `#regeneracja`.
Everyone is listed as `count/goal`.
Reaching your goal is a done day; going above is fine.

- Old days (before counters) have real polls. New days have `polls.telegram_poll_id = "challenge:<date>"`.
  The prefix is a legacy name, see `COUNTER_POLL_PREFIX` / `is_counter_poll`.
- Counts live in `challenge_counts` (count, `reached_at`, `goal` snapshot). Callback data is `ch:<date>:<delta|regen>`.
- At close (`finish_counter_message`) everyone with `reached_at` gets a real `OPTION_DONE` vote, so misses, stats and admin corrections work unchanged.
- `#regeneracja` button stores `OPTION_NOT_YET` as the vote, same as the old poll option. It is not shown on prize challenge days.
- Personal goal: `participants.daily_goal` (NULL = `PUSHUPS_PER_DAY`), set by admin with `/cel`.
  Open days switch immediately, closed days keep their snapshot.
- A prize challenge day (`/challenge`) is a counter day plus `challenge_days` row: medals, winner, prize text.
- Stats: a done day counts `max(count, goal)` pushups, days without a counts row count 50.

## Dev

- Tests: `python -m pytest tests -q`. Keep them green.
- Smoke-test handlers with a fake bot object (see `tests/test_challenge.py`), no real Telegram needed.
- Schema changes: `CREATE TABLE IF NOT EXISTS` for new tables, `ALTER TABLE` guarded by `PRAGMA table_info` in `init_db()` for new columns.
- `/debugliczniki` is a temporary admin preview of the counter messages. Remove `cmd_debug_counter`, its handler and its menu entry when no longer needed.
- Local `.env` is stale (for example `LAST_CHALLENGE_DAY`), so set `config.*` explicitly in scripts.

## Deploy

The repo is public, so never commit the server IP or secrets.
Deploy = push to `master`, then on the server `git pull` and `systemctl restart pompkibot`.
The server details are in the user's private memory, not here.
Do not deploy close to 05:00 (rollover) or 21:00 (reminder).
Commit or push only when the user asks, and never add a co-author line.

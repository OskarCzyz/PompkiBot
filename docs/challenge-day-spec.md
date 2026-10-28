# Challenge day - spec
Status: ready-for-agent

## Goal

An admin can turn any upcoming challenge day into a "challenge day".
On that day, instead of the normal poll, the bot posts a message with buttons `+1 / +5 / +10 / -1`.
Everyone taps in the pushups they did; the message shows a live ranking.
Reaching 50 counts as a normal "done" day (no penalty); below 50 is a normal miss.
Whoever has the highest total wins a prize from the pot (paid out by hand).
People who don't want to compete just tap up to 50.

## Decisions

- Buttons in a normal message, not a poll - Telegram polls can't carry counters. Rejected: separate count poll next to the normal one (two things to vote on).
- Done = total >= `PUSHUPS_PER_DAY` (50); otherwise a miss with the normal penalty. Rejected: separate opt-in/opt-out poll.
- Counts live in a new table `challenge_counts`. At close, everyone with total >= 50 gets a real `OPTION_DONE` row in `votes`, with `voted_at` = the moment their total first reached 50. This keeps `/ranking`, stats, charts, catch-up and admin corrections working unchanged.
- The challenge day gets a normal `polls` row: `telegram_poll_id = "challenge:<YYYY-MM-DD>"`, `message_id` = the button message.
- Pushups count until 23:59 of the challenge day, but buttons stay active until the normal 07:00 close the next day (late logging). Not verified - trust, same as the normal poll. The message states this rule clearly.
- Winner(s) announced at the 07:00 close, together with the normal miss report. Ties: all tied people win and split the prize. Winner requires total > 0.
- Prize amount: optional per challenge (`/challenge YYYY-MM-DD [kwota]`), default `CHALLENGE_PRIZE_PLN = 50` in `config.py` (overridable from `.env`). Stored in `challenge_days.prize_pln`. Text only; no ledger or pot change, payout is manual. Rejected: deducting prizes from the pot in the final summary (expected 2-3 challenges, not worth it).
- Scheduling: admin command `/challenge YYYY-MM-DD [kwota]` (repeatable). On success the bot posts the rules announcement in the group and pins it. The announcement is unpinned when the challenge message opens. No evening-before reminder (challenges are spontaneous).
- Today is allowed as a challenge date only while today's poll row does not exist yet (before 07:00 rollover).
- Ranking uses `messages.mention_html` (@-mentions); Telegram does not notify on edits, so only the final report pings.
- Removed (inactive) participants are excluded from ranking, winner and totals; their taps are ignored with a toast.
- `LAST_CHALLENGE_DAY` on the server is extended (to ~January 2027); the local `.env` value is stale and irrelevant.
- `/challenge cancel` cancels the next scheduled challenge (only one whose poll row doesn't exist yet; a cancelled date can be rescheduled - upsert resets `cancelled=0`; announces cancellation in the group, unpins the announcement).
- No `#regeneracja` button on challenge day; `/regeneracja` command still works.
- `-1` never goes below 0.
- People on leave (`/urlop`) on that day are skipped: no miss, not in ranking.
- Stats: pushup totals use the real challenge count on challenge days and 50 on normal done days.
- Tests: a few unit tests only (accepted risk: no manual group test; it's a friends' bot).

## Existing behaviour changed

- `job_daily_rollover` / catch-up opening of today's poll: on a challenge date it sends the challenge message instead of `send_poll` (one shared "open today" function so catch-up does the same).
- `close_poll_for_date`: for challenge rows, it does not call `stop_poll`. It edits the message to its final state (buttons removed, final ranking), writes `OPTION_DONE` votes for totals >= 50, then runs the normal miss logic, and appends the winner section to the report (or sends it right after).
- `job_reminder` (21:00): on a challenge day "not done" = total < 50; the reminder also includes the current ranking.
- `stats.py:295` and `stats.py:460`: pushup totals = sum over done days (as stats already define them) of (`max(count, PUSHUPS_PER_DAY)` if that day is a challenge day, else `PUSHUPS_PER_DAY`). The max covers admin `/markdone` on a challenge day; days in `missed` never add their count.
- Final group stats: new "🏆 Challenge'e" line(s): per challenge day - date, winner(s) and score, group total that day. `/podsumowanie`: personal best challenge score (only if the person took part in one).
- Callback routing: the new handler (pattern `^ch:`) must be registered before the catch-all `on_admin_callback`.
- Command menu: add `/challenge` to the admin scope.

## Interfaces / API

- `/challenge YYYY-MM-DD [kwota]` (admin only). Errors (Polish replies): bad date; date not in the future relative to the next poll opening (i.e. today after `POLL_HOUR` or earlier is refused); after `LAST_CHALLENGE_DAY`; already scheduled.
- `/challenge cancel` (admin only). Error if nothing scheduled.
- Callback data: `ch:<YYYY-MM-DD>:<delta>` with delta in {1, 5, 10, -1}. Any group member may tap; a non-registered tapper is auto-registered like poll voters. After close: `answer_callback_query` with "Challenge zakończony".
- Each tap: `answer_callback_query` with the tapper's new total (e.g. "Masz 35 💪"). Message edit with ranking is debounced (~2 s, one pending edit per message); ignore "message is not modified" errors.
- All user-facing texts in Polish in `messages.py`. Challenge message: rules (50 = zaliczone, poniżej 50 = kara, kto zrobi najwięcej wygrywa `CHALLENGE_PRIZE_PLN` zł z puli; liczą się pompki zrobione do 23:59, klikać można do 7:00), then ranking (name + total, sorted desc; people with 0 omitted or listed at the end - implementer choice).

## Data model & migration

New tables in `SCHEMA` (`CREATE TABLE IF NOT EXISTS`, so no migration needed for existing DBs):

```sql
CREATE TABLE IF NOT EXISTS challenge_days (
    challenge_date        TEXT PRIMARY KEY,
    announce_message_id   INTEGER,
    prize_pln             INTEGER NOT NULL,
    cancelled             INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS challenge_counts (
    challenge_date  TEXT NOT NULL,
    user_id         INTEGER NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    reached_at      TEXT,          -- first time count reached PUSHUPS_PER_DAY; cleared if it drops below
    PRIMARY KEY (challenge_date, user_id)
);
```

Increment must be a single atomic SQL update (`count = MAX(0, count + ?)`) to be safe under concurrent taps.
Deploy: push to `master`, on server `git pull` + `systemctl restart pompkibot` (README "Deploy a new version"), not near 07:00 / 21:00.

## Edge cases & error handling

- Close must write `OPTION_DONE` votes and misses before `close_poll`; reruns are safe (`record_vote` upsert, `record_miss` insert-or-ignore). Cancel any pending debounced edit before the final edit.
- Reminder at 21:00 keeps the existing early return when nobody is below 50.
- Bot down across the challenge close: catch-up closes it the same way (shared close path).
- Bot down at 07:00 of the challenge day: catch-up opens the challenge message, not a normal poll.
- Someone drops below 50 via `-1`: `reached_at` cleared; set again when re-reached.
- Admin `/markdone` / `/markmissed` on a challenge day keep working on `misses`/`votes` as today; they don't touch counts.
- Nobody tapped: no winner line ("Nikt nie wziął udziału").
- Challenge date == `LAST_CHALLENGE_DAY`: close posts the challenge result before the final summary.

## Acceptance criteria

- `python -m pytest tests` passes, including new tests:
  - close of a challenge day: total 50 -> no miss and `OPTION_DONE` vote with `voted_at = reached_at`; total 49 -> miss; on leave -> neither.
  - winner computation: single winner, tie, all zero.
  - increment never below 0; `reached_at` set/cleared correctly.
  - stats pushup total: challenge day uses count, normal day uses 50.
- Bot starts locally without errors on an existing `pushups.db` copy.

## Agent autonomy

- Decide alone, note in the commit message: message wording, emoji, ranking layout, debounce mechanism, helper/module structure.
- Stop and ask: anything that changes penalty amounts, the ledger/payments, or normal (non-challenge) day behaviour beyond what's listed above.

## Out of scope

- Verifying counts, time-stamping pushups, blocking post-midnight logging.
- Automatic prize payout or ledger entries.
- Evening-before reminder, new charts.

## Open risks / deferred

- Late taps (after 23:59) can't be told apart from late pushups - accepted.

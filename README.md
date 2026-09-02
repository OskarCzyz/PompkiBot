# PompkiBot 💪

A Telegram bot that runs a group push-up accountability challenge: everyone
does 50 pushups a day, missing a day means owing a small penalty to a shared
pot for a group feast at the end. The bot handles the daily polling,
reminders, and money bookkeeping so no one has to track it by hand.

Built for a real ~2-month challenge among friends — Python,
[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot),
SQLite, no external services required.

## Features

- **Daily poll, automatically** — opens every morning, closes and reports
  the next, so exactly one is ever open at a time.
- **Evening reminders** — @-mentions anyone who hasn't voted yet.
- **Self-service registration** — anyone can join by DMing the bot `/start`,
  no admin effort required. Several admin-driven fallbacks exist too
  (by numeric ID, `@username`, forwarded message, or group reply) for
  people who can't or won't DM the bot themselves.
- **Button-based admin corrections** — `/markpaid`, `/markdone`,
  `/markmissed`, `/removeparticipant` all work as a tappable picker (person
  → date), no typing required.
- **Money ledger, not a payment processor** — tracks who owes what and who's
  paid; actual transfers happen outside the bot (e.g. BLIK).
- **Self-healing** — recovers automatically from downtime (multi-day
  outages included) without losing a day's data; daily automatic database
  backups.
- **Native Telegram command menu**, scoped per chat (private / group /
  admin) so people don't need to memorize command names.
- All user-facing text in Polish.

## Contents

- [How it works](#how-it-works)
- [Setup](#setup)
- [Adding participants](#adding-participants)
- [Admin commands](#admin-commands)
- [Participant commands](#participant-commands)
- [Group commands](#group-commands-anyone-can-use-these)
- [Hosting](#hosting)
- [Known limitation](#known-limitation)

## How it works

- Every day at **08:00** the bot closes **yesterday's** poll (opened 24
  hours earlier), posts that day's miss report to the group, and opens
  **today's** poll — so exactly one poll is ever open at a time. It also
  pins the new poll and unpins the old one, so it requires admin rights
  (see Setup).
- Every day at **21:00** the bot posts a reminder in the group, @-mentioning
  everyone who hasn't voted "done" on that day's poll yet.
- Not voting counts the same as voting "not yet" — silence is a miss.
- The last poll opens on `LAST_CHALLENGE_DAY` (see `.env`) and closes 24h
  later (08:00 the next day), at which point the bot posts a final summary:
  total pot, per-person balance, and who kept a perfect streak (zero
  misses).
- Anyone can DM the bot `/start` to join the challenge (self-registration —
  see "Adding participants" below), and `/status` any time after to see
  what they owe, what they've paid, and which specific dates are still
  unpaid.
- The bot sets up Telegram's native "/" command menu on startup, scoped per
  chat: private chats see `/start` and `/status`, the group sees `/ranking`
  and `/uczestnicy`, and each admin's own private chat additionally sees
  the correction commands — so people can just tap the menu button instead
  of remembering command names.
- Actual money changes hands outside the bot (BLIK to the shared account) —
  the bot only keeps score. You mark a date as paid with an admin command.
- The database is backed up automatically every day at 08:10 into
  `BACKUP_DIR` (default `backups/`), one timestamped file per day.
- If a scheduled exception happens (e.g. Telegram API hiccup during a job),
  every id in `ADMIN_IDS` gets a DM with the error — for that to work, each
  admin needs to have sent the bot at least one private message (e.g.
  `/start`) beforehand, same restriction as any Telegram bot DM.

## Setup

1. **Create the bot**: talk to [@BotFather](https://t.me/BotFather) on
   Telegram, `/newbot`, copy the token it gives you.
2. **Add the bot to your group**, then promote it to **admin** with the
   **"Pin messages"** permission — the bot pins each day's poll and unpins
   the previous one when a new one opens, which requires admin rights.
   Nothing else needs to be enabled.
3. **Get the group's chat id**: send any message in the group, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and look for
   `"chat":{"id": -100...}` — that negative number is `GROUP_CHAT_ID`.
4. **Get your own user id** (to be admin): message
   [@userinfobot](https://t.me/userinfobot) and it'll reply with your id.
5. Copy `.env.example` to `.env` and fill in `BOT_TOKEN`, `GROUP_CHAT_ID`,
   `ADMIN_IDS`, `LAST_CHALLENGE_DAY`.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit it
python bot.py
```

## Adding participants

**Easiest: self-registration.** Anyone can DM the bot `/start` themselves
(the button Telegram shows automatically the first time you open a chat
with a bot) to join — no admin action needed at all. Point people at the
bot's username and tell them to hit Start.

**If you'd rather add people yourself** (or someone won't/can't DM the bot),
the bot can't see the group's member list on its own (Telegram doesn't
allow that), so you'd register them explicitly instead — no action needed
from them either way. Four admin-only methods, roughly in order of
preference:

**1. By numeric Telegram ID, privately (zero group traffic, most
reliable)** — if you know their numeric ID (they can get their own from
[@userinfobot](https://t.me/userinfobot) and send it to you through any
channel — WhatsApp, SMS, whatever, doesn't have to be Telegram), DM the bot
directly:

```
/addparticipant 68451453
```

The bot looks them up as a member of the group itself, so this works
regardless of whether they have a username, and doesn't depend on any
privacy setting. The only requirement is that they're already a member of
the group.

**2. By @username, privately (zero group traffic)**:

```
/addparticipant @friendusername
```

Only works if that person has a public Telegram username set.

**3. By forwarding a message, privately (zero group traffic, works even
with no username)** — if you have any message from them anywhere (a
private chat between you two, another group, wherever), forward it into
your DM with the bot, then reply to that forwarded message with
`/addparticipant`. Telegram attaches the original sender's real identity to
a forwarded message, so this works even for someone with no username and
no history in this group — as long as they haven't hidden their identity
in Settings → Privacy → Forwarded Messages (the bot will tell you plainly
if that's the case, and won't misattribute it to you as the forwarder).

**4. By reply, in the group (fallback)** — if none of the above works,
reply to **any** message they've sent in the group (old or new) with
`/addparticipant`. This is the one method that does post in the group.

**Last resort, if they've truly never sent a single message anywhere you
have access to**: remove and re-add them in the group's own member list
(Telegram app → group name → Members → remove, then add again). The bot
automatically registers anyone who joins a group it's already in — this
triggers that. It does leave two brief "removed/added" system messages in
the group, which you can delete afterward (long-press → Delete → for
everyone) to keep the history clean.

Check `/uczestnicy` afterward to confirm everyone landed correctly.

To remove someone (e.g. they leave the group, or were added by mistake),
use `/removeparticipant` — the same targeting options as above (numeric ID,
`@username`, or a reply, plain or forwarded).

## Admin commands

Only user ids listed in `ADMIN_IDS` can use any of these. All of them
support the same targeting options — see "Adding participants" above for
`/addparticipant` specifically:

- `/addparticipant <numeric ID>` / `@username` (private DM, no group
  traffic) or reply to any of their messages, plain or forwarded — add them
  to the roster.

The rest (`/removeparticipant`, `/markpaid`, `/markdone`, `/markmissed`) can
also be targeted the same ways — plus a fourth, easiest one:

- **Just send the bare command with nothing else** (e.g. `/markpaid` on its
  own) and the bot shows tappable buttons: first the people this action
  actually applies to (`/markpaid` only lists those with an unpaid day,
  `/markdone` only those with a recorded miss, `/markmissed` only those
  with an eligible day left to mark) — no point offering someone with
  nothing to act on. Then, for the mark commands, the relevant dates for
  whoever you picked (`/markpaid` shows their unpaid dates, `/markdone`
  shows their recorded misses, `/markmissed` shows recent poll dates they
  haven't already been marked absent for). `/removeparticipant` always
  lists everyone, since it doesn't depend on debt. No typing usernames or
  dates by hand.
- Reply to the person's message — a plain one in the group, or a message of
  theirs forwarded in from anywhere (see "Adding participants" above) —
  **or**
- Send it directly to the bot in a **private chat**, leading with their
  numeric ID or `@username` — e.g. `/markdone @jankowalski 2026-09-03` or
  `/markdone 68451453 2026-09-03`. This only works for people already on
  the roster (it looks them up by what was stored when they were added).
  Use this when someone tells you privately that they forgot to vote — you
  can fix it from your phone without going back to the group to find their
  message.

- `/removeparticipant` — remove them from future tracking (they stop
  appearing in polls, reminders, and miss checks) and drops them from
  `/ranking` and the final summary entirely. Any **unpaid** debt is
  forgiven — they no longer owe anything. Whatever they'd already paid
  before removal stays recorded (it's real money already in the pot), it
  just isn't tied to anyone still on the board.
- `/markpaid @user 2026-09-03` — mark that specific missed date as paid.
- `/markdone @user 2026-09-03` — override: erase a recorded miss (e.g. a bot
  hiccup or a genuine dispute).
- `/markmissed @user 2026-09-03` — override: force a miss to exist for that
  date.

## Participant commands

- `/status`, sent as a **private message** to the bot (not in the group) —
  shows total owed, total paid, and the list of unpaid dates.

## Group commands (anyone can use these)

- `/uczestnicy` — lists everyone currently on the roster. Handy for
  double-checking that everyone got registered correctly.
- `/ranking` — posts the current standings (who owes what, right now),
  worst offender first. Skips anyone with a clean zero on both sides (never
  missed a day and never had to pay) — only people with an actual owed or
  paid amount show up.

## Hosting

This needs to run continuously for ~2 months to fire the 8:00/21:00 jobs
reliably. Two options, in order of preference:

1. **Free**: a small always-on VM, e.g. an Oracle Cloud "Always Free" tier
   instance. Behaves like a normal Linux box (unlike sleep-prone serverless
   free tiers), so no special handling needed — just run it as below.
2. **Cheap fallback**: a small VPS (Hetzner, DigitalOcean, ~€4-5/month) if
   the free tier's signup/capacity turns out to be too much friction.

### Running it as a systemd service (any Linux VM)

```ini
# /etc/systemd/system/pompkibot.service
[Unit]
Description=PompkiBot
After=network.target

[Service]
WorkingDirectory=/opt/pompkibot
ExecStart=/opt/pompkibot/venv/bin/python bot.py
Restart=always
EnvironmentFile=/opt/pompkibot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pompkibot
```

## Known limitation

If the bot goes down, it catches up automatically as soon as it's back
online: on startup it closes **every** poll whose 24h window has already
elapsed (not just the most recent one), so even an outage spanning several
days doesn't lose any day's votes/misses, then opens today's poll if it's
past 08:00. This deliberately does nothing on a brand-new deployment (no
prior poll exists yet), so it never opens a poll for a day you're tracking
by hand instead.

The 21:00 reminder has no such catch-up — if the bot is down at 21:00, that
day's reminder is simply skipped (it's a nice-to-have nudge, not part of the
scoring, so a missed one has no effect on anyone's balance). Given the short
lifespan of this project, a `systemd` `Restart=always` plus picking a
reasonably reliable host should keep outages rare enough that this doesn't
matter in practice.

## License

[MIT](LICENSE)

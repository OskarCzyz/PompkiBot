import asyncio
import logging
import os
import shutil
from datetime import date, datetime, time, timedelta

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

import config
import db
import messages
import stats

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("pompki-bot")


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def parse_date_arg(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def extract_user_from_message(message):
    """Identifies who a message is 'about', for admin commands that reply
    to a message. Handles two cases:

    - A plain message: the real Telegram identity is whoever sent it.
    - A forwarded message: Telegram attaches the *original* sender's real
      identity (forward_origin.sender_user), letting an admin identify
      someone from a message forwarded in from anywhere (e.g. a private
      chat with them) — no trace left in the group, no action needed from
      that person. Returns None (not the forwarder) if the original sender
      hid their identity in their forwarding privacy settings.

    Returns None if there's no message, or a hidden forward.
    """
    if not message:
        return None
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        return getattr(origin, "sender_user", None)
    return message.from_user


def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finds the participant an admin command targets.

    Works several ways: replying to the target's message in the group
    (plain or forwarded, see extract_user_from_message), leading the
    command with @username, or leading it with their numeric Telegram ID —
    all of which also work in a private chat with the bot, so an admin can
    act on a tip-off DM without going back to the group to find a message
    to reply to. Only matches people already on the roster. Returns
    (participant_row_or_None, remaining_args)."""
    reply_user = extract_user_from_message(update.message.reply_to_message)
    if reply_user:
        row = db.get_participant(reply_user.id)
        if row:
            return row, context.args

    if context.args and context.args[0].startswith("@"):
        row = db.get_participant_by_username(context.args[0][1:])
        return row, context.args[1:]

    if context.args and context.args[0].isdigit():
        row = db.get_participant(int(context.args[0]))
        return row, context.args[1:]

    return None, context.args


def participant_button_label(p) -> str:
    return p["first_name"] + (f" (@{p['username']})" if p["username"] else "")


def build_participant_keyboard(action: str, participants) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(participant_button_label(p), callback_data=f"{action}:{p['user_id']}")
        for p in participants
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def build_date_keyboard(action: str, user_id: int, dates: list[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(d, callback_data=f"{action}:{user_id}:{d}") for d in dates]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(rows)


SELECTED_MARK = "✅"
UNSELECTED_MARK = "▫️"


def build_multiselect_date_keyboard(
    action: str, user_id: int, dates: list[str], selected: set[str], confirm_label: str
) -> InlineKeyboardMarkup:
    """Toggleable date checklist for bulk actions (e.g. paying several days
    in one go). Tapping a date sends "<action>t:<user_id>:<date>" and flips
    its checkmark; "<action>a:<user_id>" selects/clears all; "<action>c:<user_id>"
    applies the action to everything checked. The selection lives in the
    buttons themselves (see selected_dates_from_markup), so it survives a
    bot restart and needs no server-side state."""
    buttons = [
        InlineKeyboardButton(
            f"{SELECTED_MARK if d in selected else UNSELECTED_MARK} {d}",
            callback_data=f"{action}t:{user_id}:{d}",
        )
        for d in dates
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    all_selected = bool(dates) and selected >= set(dates)
    rows.append([
        InlineKeyboardButton(
            "Odznacz wszystkie" if all_selected else "Zaznacz wszystkie",
            callback_data=f"{action}a:{user_id}",
        ),
        InlineKeyboardButton(f"{confirm_label} ({len(selected)})", callback_data=f"{action}c:{user_id}"),
    ])
    return InlineKeyboardMarkup(rows)


def selected_dates_from_markup(markup, action: str) -> set[str]:
    if not markup:
        return set()
    return {
        b.callback_data.split(":", 2)[2]
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data and b.callback_data.startswith(f"{action}t:") and b.text.startswith(SELECTED_MARK)
    }


def markpaid_keyboard(user_id: int, unpaid_dates: list[str], selected: set[str]) -> InlineKeyboardMarkup:
    return build_multiselect_date_keyboard("mp", user_id, unpaid_dates, selected, "💰 Zapłacono")


def markpaid_prompt(name: str) -> str:
    return f"Zaznacz dni zapłacone przez {name} i kliknij „Zapłacono”:"


def paid_confirmation(name: str, dates: list[str]) -> str:
    total = len(dates) * config.PENALTY_PLN
    return f"Zapłacono: {name} — {len(dates)} dz. ({total} PLN): {', '.join(dates)} ✅"


def has_unpaid_days(p) -> bool:
    return bool(db.get_balance(p["user_id"])[2])


def has_recorded_misses(p) -> bool:
    return bool(db.get_recent_miss_dates(p["user_id"], limit=1))


def has_paid_days(p) -> bool:
    return bool(db.get_recent_paid_dates(p["user_id"], limit=1))


def has_markmissed_candidates(p) -> bool:
    already_missed = set(db.get_recent_miss_dates(p["user_id"], limit=1000))
    return any(d not in already_missed for d in db.get_recent_poll_dates())


async def send_participant_picker(
    update: Update,
    action: str,
    prompt: str,
    filter_fn=None,
    empty_message: str = "Brak uczestników na liście.",
):
    """Shows a button per eligible participant. filter_fn, if given, narrows
    the list to people this action actually applies to (e.g. only those
    with an unpaid day for /markpaid) — no point offering someone with
    nothing to act on."""
    participants = db.get_active_participants()
    if filter_fn:
        participants = [p for p in participants if filter_fn(p)]
    if not participants:
        await update.message.reply_text(empty_message)
        return
    await update.message.reply_text(prompt, reply_markup=build_participant_keyboard(action, participants))


# ---------- scheduled jobs ----------

async def close_poll_for_date(bot, close_date: date):
    """Closes the poll opened on close_date (if it exists and isn't already
    closed), records misses, and posts the report — and the final summary,
    if this was the last challenge day. Safe to call more than once for the
    same date; a no-op if already closed."""
    poll_row = db.get_poll_by_date(close_date.isoformat())
    if not poll_row or poll_row["closed"]:
        return

    try:
        await bot.stop_poll(chat_id=config.GROUP_CHAT_ID, message_id=poll_row["message_id"])
    except Exception:
        log.exception("Could not stop poll for %s (maybe already closed)", close_date)

    try:
        await bot.unpin_chat_message(chat_id=config.GROUP_CHAT_ID, message_id=poll_row["message_id"])
    except Exception:
        log.debug("Could not unpin poll for %s (maybe already unpinned)", close_date)

    votes = db.get_votes_for_poll(poll_row["id"])
    participants = db.get_active_participants()
    missed = []
    for p in participants:
        if votes.get(p["user_id"]) != config.OPTION_DONE:
            db.record_miss(p["user_id"], close_date.isoformat())
            missed.append(p)
    db.close_poll(poll_row["id"])

    regen_ids = db.get_regen_user_ids(close_date.isoformat()) | {
        uid for uid, option in votes.items() if option == config.OPTION_NOT_YET
    }
    await bot.send_message(
        chat_id=config.GROUP_CHAT_ID,
        text=messages.miss_report(close_date, missed, regen_ids),
        parse_mode=ParseMode.HTML,
    )

    if close_date == config.LAST_CHALLENGE_DAY:
        await send_final_summary(bot, config.GROUP_CHAT_ID)


async def send_final_summary(bot, chat_id: int):
    """The end-of-challenge package: money summary, then the fun stats, then
    the group-level charts as one album. Also used by /podglad to preview
    it privately before the real thing goes out to the group."""
    rows = db.get_full_summary()
    streak_ids = db.get_perfect_streak_user_ids()
    await bot.send_message(
        chat_id=chat_id,
        text=messages.final_summary(rows, streak_ids),
        parse_mode=ParseMode.HTML,
    )

    challenge = stats.collect()
    await bot.send_message(chat_id=chat_id, text=stats.stats_text(challenge), parse_mode=ParseMode.HTML)
    charts = await asyncio.to_thread(stats.render_group_charts, challenge, config.SPOTLIGHT_USER)
    if charts:
        await bot.send_media_group(
            chat_id=chat_id,
            media=[InputMediaPhoto(png, caption=caption) for png, caption in charts],
        )


async def job_daily_rollover(context: ContextTypes.DEFAULT_TYPE):
    """Runs daily at 08:00: closes yesterday's poll (24h after it opened,
    right as today's opens, so only one poll is ever open at a time), then
    opens today's poll."""
    today = datetime.now(config.TIMEZONE).date()
    bot = context.bot

    await close_poll_for_date(bot, today - timedelta(days=1))

    if today <= config.LAST_CHALLENGE_DAY and not db.get_poll_by_date(today.isoformat()):
        sent = await bot.send_poll(
            chat_id=config.GROUP_CHAT_ID,
            question=messages.poll_question(today),
            options=messages.POLL_OPTIONS,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
        db.create_poll(today.isoformat(), sent.poll.id, sent.message_id)
        try:
            await bot.pin_chat_message(
                chat_id=config.GROUP_CHAT_ID, message_id=sent.message_id, disable_notification=True
            )
        except Exception:
            log.warning(
                "Could not pin today's poll — the bot likely needs to be a group "
                "admin with 'Pin messages' permission."
            )


async def job_backup_db(context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(config.DB_PATH):
        return
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    today = datetime.now(config.TIMEZONE).date().isoformat()
    dest = os.path.join(config.BACKUP_DIR, f"pushups-{today}.db")
    await asyncio.to_thread(shutil.copy2, config.DB_PATH, dest)
    log.info("Backed up database to %s", dest)


async def catch_up_check(context: ContextTypes.DEFAULT_TYPE):
    """Runs once at startup.

    1. Closes every poll whose 24h window (opens 08:00, closes 08:00 the
       next day) has already fully elapsed but that never got processed —
       e.g. the bot was down across one or more scheduled 08:00 closes.
       Without this sweep, any day caught in a multi-day outage would stay
       open forever and its misses would never be recorded.
    2. If today's own poll is still missing once it's past 08:00, opens it
       — but only once the challenge is already underway (a prior poll
       exists), so a fresh deployment never jumps ahead of a day being
       tracked by hand.
    """
    today = datetime.now(config.TIMEZONE).date()
    now = datetime.now(config.TIMEZONE).time()
    bot = context.bot

    # A poll opened on `poll_date` is due to close at 08:00 on poll_date + 1.
    # So as of right now, anything with poll_date <= this cutoff is overdue.
    cutoff = today - timedelta(days=1) if now >= time(8, 0) else today - timedelta(days=2)
    overdue = db.get_unclosed_polls_up_to(cutoff.isoformat())
    for poll_row in overdue:
        close_date = date.fromisoformat(poll_row["poll_date"])
        log.warning("Catch-up: closing overdue poll for %s", close_date)
        await close_poll_for_date(bot, close_date)

    if (
        now >= time(8, 0)
        and today <= config.LAST_CHALLENGE_DAY
        and not db.get_poll_by_date(today.isoformat())
        and db.has_prior_poll(today.isoformat())
    ):
        log.warning("Catch-up: today's poll is missing past 08:00 — opening it now.")
        await job_daily_rollover(context)


async def job_reminder(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(config.TIMEZONE).date()
    if today > config.LAST_CHALLENGE_DAY:
        return

    poll_row = db.get_poll_by_date(today.isoformat())
    if not poll_row:
        return

    votes = db.get_votes_for_poll(poll_row["id"])
    participants = db.get_active_participants()
    not_done = [p for p in participants if votes.get(p["user_id"]) != config.OPTION_DONE]
    if not not_done:
        return

    await context.bot.send_message(
        chat_id=config.GROUP_CHAT_ID,
        text=messages.reminder(not_done),
        parse_mode=ParseMode.HTML,
    )


# ---------- poll answers ----------

async def on_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_row = db.get_poll_by_telegram_id(answer.poll_id)
    if not poll_row or poll_row["closed"]:
        return

    # Someone voting who was never registered is clearly taking part —
    # without this their misses would never be recorded (penalties,
    # reminders and stats would all skip them). Removed participants
    # (still in the table, inactive) are left alone.
    if not db.get_participant(answer.user.id) and not answer.user.is_bot:
        db.add_participant(answer.user.id, answer.user.username, answer.user.first_name)
        log.info("Auto-registered %s (%s) via poll vote", answer.user.first_name, answer.user.id)

    if answer.option_ids:
        db.record_vote(
            poll_row["id"],
            answer.user.id,
            answer.option_ids[0],
            datetime.now(config.TIMEZONE).isoformat(),
        )
    else:
        db.clear_vote(poll_row["id"], answer.user.id)


# ---------- group membership events ----------

async def on_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram tells the bot who joined a group it's already in, with no
    action needed from that person — useful for registering someone who has
    no public username and has never posted (so neither /addparticipant
    @username nor the reply-to-message method has anything to work with).
    Removing and re-adding an existing member triggers this same event."""
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        db.add_participant(member.id, member.username, member.first_name)
        log.info("Auto-registered %s (%s) via group join event", member.first_name, member.id)


# ---------- admin commands ----------

async def cmd_add_participant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if update.message.reply_to_message:
        reply_user = extract_user_from_message(update.message.reply_to_message)
        if not reply_user:
            await update.message.reply_text(
                "Ta osoba ukryła swoją tożsamość w ustawieniach prywatności "
                "przekazywanych wiadomości — ta metoda tu nie zadziała, spróbuj "
                "innej (usuń i dodaj ponownie w grupie)."
            )
            return
        db.add_participant(reply_user.id, reply_user.username, reply_user.first_name)
        await update.message.reply_text(f"Dodano {reply_user.first_name} do wyzwania. ✅")
        return

    if context.args and context.args[0].startswith("@"):
        username = context.args[0][1:]
        try:
            chat = await context.bot.get_chat(f"@{username}")
        except Exception:
            await update.message.reply_text(
                f"Nie udało się znaleźć @{username} — może nie mieć publicznej nazwy "
                f"użytkownika. Dodaj go/ją przez odpowiedź (reply) na dowolną jego/jej "
                f"starą wiadomość w grupie zamiast tego."
            )
            return
        db.add_participant(chat.id, chat.username, chat.first_name)
        await update.message.reply_text(f"Dodano {chat.first_name} do wyzwania. ✅")
        return

    if context.args and context.args[0].isdigit():
        user_id = int(context.args[0])
        try:
            member = await context.bot.get_chat_member(chat_id=config.GROUP_CHAT_ID, user_id=user_id)
        except Exception:
            await update.message.reply_text(
                f"Nie znaleziono w grupie nikogo o ID {user_id} — sprawdź, czy numer "
                f"jest poprawny i czy ta osoba faktycznie jest w grupie."
            )
            return
        user = member.user
        db.add_participant(user.id, user.username, user.first_name)
        await update.message.reply_text(f"Dodano {user.first_name} do wyzwania. ✅")
        return

    await update.message.reply_text(
        "Użycie: odpowiedz (reply) na dowolną wiadomość danej osoby (może być "
        "przekazana/forward z innej rozmowy) komendą /addparticipant, albo napisz "
        "/addparticipant @nazwa_użytkownika, albo /addparticipant <numeryczne_ID> "
        "(działa też na priv)."
    )


async def cmd_remove_participant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    target, _ = resolve_target(update, context)
    if not target:
        await send_participant_picker(update, "rm", "Kogo usunąć z wyzwania?")
        return
    db.remove_participant(target["user_id"])
    db.forgive_unpaid_misses(target["user_id"])
    await update.message.reply_text(
        f"Usunięto {target['first_name']} z wyzwania. Niezapłacone długi anulowane "
        f"— to, co już zapłacił/a, zostaje."
    )


def regen_today_voters():
    """Active participants who picked #regeneracja in today's poll."""
    today = datetime.now(config.TIMEZONE).date().isoformat()
    poll_row = db.get_poll_by_date(today)
    if not poll_row:
        return []
    votes = db.get_votes_for_poll(poll_row["id"])
    return [p for p in db.get_active_participants() if votes.get(p["user_id"]) == config.OPTION_NOT_YET]


async def apply_regen_today(bot, participant) -> str:
    """Flags today's skip as #regeneracja for participant. If today's miss is
    already on record (the report went out), the group also gets a note."""
    today = datetime.now(config.TIMEZONE).date()
    db.set_regen(participant["user_id"], today.isoformat())
    if db.has_miss(participant["user_id"], today.isoformat()):
        await bot.send_message(
            chat_id=config.GROUP_CHAT_ID,
            text=messages.regen_note(today, participant),
            parse_mode=ParseMode.HTML,
        )
        return f"Zapisano #regeneracja: {participant['first_name']} ✅ (wysłano na grupę)"
    return f"Zapisano #regeneracja: {participant['first_name']} ✅ (pojawi się w raporcie po zamknięciu ankiety)"


async def cmd_regeneracja(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: flags today's skip as a deliberate #regeneracja. With a target
    (reply / @name / ID) flags them directly; without one, offers a button per
    person who picked #regeneracja in today's poll."""
    if not is_admin(update.effective_user.id):
        return
    target, _args = resolve_target(update, context)
    if target:
        await update.message.reply_text(await apply_regen_today(context.bot, target))
        return
    voters = regen_today_voters()
    if not voters:
        return
    await update.message.reply_text(
        "Kogo oznaczyć jako #regeneracja (dzisiaj)?",
        reply_markup=build_participant_keyboard("rg", voters),
    )


async def cmd_markpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    target, args = resolve_target(update, context)
    if not target:
        await send_participant_picker(
            update,
            "mp",
            "Kogo oznaczyć jako zapłacone?",
            filter_fn=has_unpaid_days,
            empty_message="Nikt nie ma niezapłaconych dni.",
        )
        return
    _, _, unpaid_dates = db.get_balance(target["user_id"])
    if not args:
        if not unpaid_dates:
            await update.message.reply_text(f"{target['first_name']} nie ma niezapłaconych dni.")
            return
        await update.message.reply_text(
            markpaid_prompt(target["first_name"]),
            reply_markup=markpaid_keyboard(target["user_id"], unpaid_dates, set()),
        )
        return
    if args[0].lower() in ("all", "wszystkie"):
        dates = unpaid_dates
    else:
        parsed = [parse_date_arg(a) for a in args]
        if not all(parsed):
            await update.message.reply_text("Nieprawidłowa data, użyj formatu RRRR-MM-DD.")
            return
        dates = [d.isoformat() for d in parsed]
    paid = [d for d in dates if db.mark_paid(target["user_id"], d)]
    if paid:
        await update.message.reply_text(paid_confirmation(target["first_name"], paid))
    else:
        await update.message.reply_text("Nie znaleziono niezapłaconego długu na te daty.")


async def cmd_unmarkpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin override for a /markpaid mistake — undoes it, putting the day
    back on the unpaid list."""
    if not is_admin(update.effective_user.id):
        return
    target, args = resolve_target(update, context)
    if not target:
        await send_participant_picker(
            update,
            "up",
            "Komu cofnąć zapłacony dzień?",
            filter_fn=has_paid_days,
            empty_message="Nikt nie ma zapłaconych dni.",
        )
        return
    if not args:
        paid_dates = db.get_recent_paid_dates(target["user_id"])
        if not paid_dates:
            await update.message.reply_text(f"{target['first_name']} nie ma zapłaconych dni.")
            return
        await update.message.reply_text(
            f"Który dzień cofnąć jako niezapłacony ({target['first_name']})?",
            reply_markup=build_date_keyboard("up", target["user_id"], paid_dates),
        )
        return
    d = parse_date_arg(args[0])
    if not d:
        await update.message.reply_text("Nieprawidłowa data, użyj formatu RRRR-MM-DD.")
        return
    ok = db.unmark_paid(target["user_id"], d.isoformat())
    if ok:
        await update.message.reply_text(f"Cofnięto płatność: {target['first_name']} — {d.isoformat()} ✅")
    else:
        await update.message.reply_text("Nie znaleziono zapłaconego długu na tę datę.")


async def cmd_markdone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    target, args = resolve_target(update, context)
    if not target:
        await send_participant_picker(
            update,
            "md",
            "Komu anulować nieobecność?",
            filter_fn=has_recorded_misses,
            empty_message="Nikt nie ma zapisanych nieobecności.",
        )
        return
    if not args:
        recent = db.get_recent_miss_dates(target["user_id"])
        if not recent:
            await update.message.reply_text(f"{target['first_name']} nie ma zapisanych nieobecności.")
            return
        await update.message.reply_text(
            f"Którą nieobecność anulować ({target['first_name']})?",
            reply_markup=build_date_keyboard("md", target["user_id"], recent),
        )
        return
    d = parse_date_arg(args[0])
    if not d:
        await update.message.reply_text("Nieprawidłowa data, użyj formatu RRRR-MM-DD.")
        return
    ok = db.mark_done(target["user_id"], d.isoformat())
    if ok:
        await update.message.reply_text(f"Anulowano nieobecność: {target['first_name']} — {d.isoformat()} ✅")
    else:
        await update.message.reply_text("Nie znaleziono nieobecności na tę datę.")


async def cmd_markmissed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    target, args = resolve_target(update, context)
    if not target:
        await send_participant_picker(
            update,
            "mm",
            "Komu wymusić nieobecność?",
            filter_fn=has_markmissed_candidates,
            empty_message="Brak uczestników z dostępnymi dniami do wymuszenia.",
        )
        return
    if not args:
        already_missed = set(db.get_recent_miss_dates(target["user_id"], limit=1000))
        candidates = [d for d in db.get_recent_poll_dates() if d not in already_missed]
        if not candidates:
            await update.message.reply_text(f"Brak dni do wyboru dla {target['first_name']}.")
            return
        await update.message.reply_text(
            f"Który dzień wymusić jako nieobecność ({target['first_name']})?",
            reply_markup=build_date_keyboard("mm", target["user_id"], candidates),
        )
        return
    d = parse_date_arg(args[0])
    if not d:
        await update.message.reply_text("Nieprawidłowa data, użyj formatu RRRR-MM-DD.")
        return
    db.mark_missed(target["user_id"], d.isoformat())
    await update.message.reply_text(f"Odnotowano nieobecność: {target['first_name']} — {d.isoformat()} ✅")


async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles taps on the button pickers from /removeparticipant, /markpaid,
    /unmarkpaid, /markdone, /markmissed and /regeneracja. callback_data is
    "<action>:<user_id>" for the participant-selection step, or
    "<action>:<user_id>:<date>" once a date has also been picked. /markpaid's
    date step is a multi-select checklist instead — see
    on_markpaid_multiselect."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Tylko admin może to zrobić.", show_alert=True)
        return

    action, user_id_str, *rest = query.data.split(":")
    user_id = int(user_id_str)
    participant = db.get_participant(user_id)
    if participant and action in ("mpt", "mpa", "mpc"):
        await on_markpaid_multiselect(query, action, participant, rest)
        return
    await query.answer()
    if not participant:
        await query.edit_message_text("Nie znaleziono uczestnika.")
        return
    name = participant["first_name"]

    if action == "rg":
        await query.edit_message_text(await apply_regen_today(context.bot, participant))
        return

    if action == "rm":
        db.remove_participant(user_id)
        db.forgive_unpaid_misses(user_id)
        await query.edit_message_text(
            f"Usunięto {name} z wyzwania. Niezapłacone długi anulowane — "
            f"to, co już zapłacił/a, zostaje."
        )
        return

    if action == "mp":
        if not rest:
            _, _, unpaid_dates = db.get_balance(user_id)
            if not unpaid_dates:
                await query.edit_message_text(f"{name} nie ma niezapłaconych dni.")
                return
            await query.edit_message_text(
                markpaid_prompt(name),
                reply_markup=markpaid_keyboard(user_id, unpaid_dates, set()),
            )
            return
        ok = db.mark_paid(user_id, rest[0])
        text = paid_confirmation(name, [rest[0]]) if ok else "Nie znaleziono niezapłaconego długu na tę datę."
        await query.edit_message_text(text)
        return

    if action == "up":
        if not rest:
            paid_dates = db.get_recent_paid_dates(user_id)
            if not paid_dates:
                await query.edit_message_text(f"{name} nie ma zapłaconych dni.")
                return
            await query.edit_message_text(
                f"Który dzień cofnąć jako niezapłacony ({name})?",
                reply_markup=build_date_keyboard("up", user_id, paid_dates),
            )
            return
        ok = db.unmark_paid(user_id, rest[0])
        text = f"Cofnięto płatność: {name} — {rest[0]} ✅" if ok else "Nie znaleziono zapłaconego długu na tę datę."
        await query.edit_message_text(text)
        return

    if action == "md":
        if not rest:
            recent = db.get_recent_miss_dates(user_id)
            if not recent:
                await query.edit_message_text(f"{name} nie ma zapisanych nieobecności.")
                return
            await query.edit_message_text(
                f"Którą nieobecność anulować ({name})?",
                reply_markup=build_date_keyboard("md", user_id, recent),
            )
            return
        ok = db.mark_done(user_id, rest[0])
        text = f"Anulowano nieobecność: {name} — {rest[0]} ✅" if ok else "Nie znaleziono nieobecności na tę datę."
        await query.edit_message_text(text)
        return

    if action == "mm":
        if not rest:
            already_missed = set(db.get_recent_miss_dates(user_id, limit=1000))
            candidates = [d for d in db.get_recent_poll_dates() if d not in already_missed]
            if not candidates:
                await query.edit_message_text(f"Brak dni do wyboru dla {name}.")
                return
            await query.edit_message_text(
                f"Który dzień wymusić jako nieobecność ({name})?",
                reply_markup=build_date_keyboard("mm", user_id, candidates),
            )
            return
        db.mark_missed(user_id, rest[0])
        await query.edit_message_text(f"Odnotowano nieobecność: {name} — {rest[0]} ✅")
        return


async def on_markpaid_multiselect(query, action: str, participant, rest: list[str]):
    """The /markpaid date checklist: toggle one date (mpt), toggle all (mpa),
    or confirm (mpc). Always re-reads the unpaid list from the database, so
    a day paid in the meantime (e.g. by another admin) just drops out."""
    user_id = participant["user_id"]
    name = participant["first_name"]
    _, _, unpaid_dates = db.get_balance(user_id)
    selected = selected_dates_from_markup(query.message.reply_markup, "mp") & set(unpaid_dates)

    if action == "mpc":
        if not selected:
            await query.answer("Nie zaznaczono żadnego dnia.", show_alert=True)
            return
        await query.answer()
        paid = [d for d in sorted(selected) if db.mark_paid(user_id, d)]
        text = paid_confirmation(name, paid) if paid else "Nie znaleziono niezapłaconego długu na te daty."
        await query.edit_message_text(text)
        return

    await query.answer()
    if not unpaid_dates:
        await query.edit_message_text(f"{name} nie ma niezapłaconych dni.")
        return
    if action == "mpt":
        selected ^= {rest[0]} & set(unpaid_dates)
    else:
        selected = set() if selected >= set(unpaid_dates) else set(unpaid_dates)
    await query.edit_message_text(
        markpaid_prompt(name), reply_markup=markpaid_keyboard(user_id, unpaid_dates, selected)
    )


# ---------- participant-facing commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Self-registration. /start is what Telegram sends automatically when
    someone opens a private chat with the bot and taps "Start", so this
    doubles as the natural, zero-friction join flow."""
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "Napisz do mnie prywatnie /start, żeby dołączyć do wyzwania. 🙂"
        )
        return
    user = update.effective_user
    db.add_participant(user.id, user.username, user.first_name)
    await update.message.reply_text(messages.start_reply(user.first_name))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Napisz do mnie prywatnie, żeby sprawdzić swoje saldo. 🙂")
        return
    owed, paid, unpaid_dates = db.get_balance(update.effective_user.id)
    await update.message.reply_text(messages.status_reply(owed, paid, unpaid_dates))


async def cmd_roster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Napisz do mnie prywatnie po listę uczestników. 🙂")
        return
    participants = db.get_active_participants()
    await update.message.reply_text(
        messages.roster_text(participants), parse_mode=ParseMode.HTML
    )


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Napisz do mnie prywatnie po ranking. 🙂")
        return
    owing_only = bool(context.args) and context.args[0].lower() == "zalegli"
    if context.args and not owing_only:
        await update.message.reply_text("Użycie: /ranking lub /ranking zalegli")
        return
    rows = db.get_full_summary()
    text = messages.owing_text(rows) if owing_only else messages.leaderboard_text(rows)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


def challenge_finished() -> bool:
    """True once the last poll has been closed (and the final summary posted)."""
    last = db.get_poll_by_date(config.LAST_CHALLENGE_DAY.isoformat())
    if last:
        return bool(last["closed"])
    return datetime.now(config.TIMEZONE) >= datetime.combine(
        config.LAST_CHALLENGE_DAY + timedelta(days=1), time(8, 0), tzinfo=config.TIMEZONE
    )


async def cmd_personal_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Anyone's own end-of-challenge stats + chart, in a private chat. Only
    unlocked once the challenge is over (admins can peek any time)."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Napisz do mnie prywatnie /podsumowanie po swoje statystyki. 🙂")
        return
    user_id = update.effective_user.id
    if not challenge_finished() and not is_admin(user_id):
        await update.message.reply_text(
            f"Podsumowanie będzie dostępne po zakończeniu wyzwania — od "
            f"{messages.format_date_pl(config.LAST_CHALLENGE_DAY + timedelta(days=1))}, 8:00. 💪"
        )
        return
    challenge = stats.collect()
    person = next((p for p in challenge.people if p.user_id == user_id), None)
    if not person:
        await update.message.reply_text("Nie znalazłem Twoich danych z wyzwania. 🤷")
        return
    owed, paid, _ = db.get_balance(user_id)
    await update.message.reply_text(
        stats.personal_text(challenge, person, owed, paid), parse_mode=ParseMode.HTML
    )
    png = await asyncio.to_thread(stats.render_personal_chart, person)
    await update.message.reply_photo(png, caption=f"Statystyki wyzwania: {stats.person_label(person)} 💪")


async def cmd_group_summary_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: sends the end-of-challenge summary, group stats and charts
    to the admin's private chat — a preview of what the group gets once the
    last poll closes."""
    if not is_admin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("Napisz do mnie prywatnie po podgląd podsumowania. 🙂")
        return
    await update.message.reply_text("Generuję podgląd podsumowania końcowego… ⏳")
    await send_final_summary(context.bot, update.effective_chat.id)


async def cmd_say_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only, private chat only: posts the given text to the group as the
    bot. Plain text on purpose — no HTML/Markdown parsing of admin input."""
    if not is_admin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("Napisz do mnie prywatnie, nie w grupie. 🙂")
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text(
            "Użycie: /napisz <treść> — wysyła podaną treść na grupę jako bot."
        )
        return
    await context.bot.send_message(chat_id=config.GROUP_CHAT_ID, text=parts[1])
    await update.message.reply_text("Wysłano na grupę. ✅")


# ---------- error handling ----------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled exception", exc_info=context.error)
    alert = messages.admin_error_alert(str(context.error))
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=alert)
        except Exception:
            log.warning(
                "Could not DM admin %s with the error alert (they may need to "
                "/start a private chat with the bot at least once).",
                admin_id,
            )


PARTICIPANT_COMMANDS = [
    BotCommand("start", "Dołącz do wyzwania"),
    BotCommand("status", "Sprawdź swoje saldo"),
    BotCommand("ranking", "Aktualne zadłużenie wszystkich"),
    BotCommand("uczestnicy", "Lista uczestników wyzwania"),
    BotCommand("podsumowanie", "Twoje statystyki (po zakończeniu wyzwania)"),
]

ADMIN_COMMANDS = PARTICIPANT_COMMANDS + [
    BotCommand("addparticipant", "Dodaj uczestnika (ID/@username/reply)"),
    BotCommand("removeparticipant", "Usuń uczestnika"),
    BotCommand("markpaid", "Oznacz dzień jako zapłacony"),
    BotCommand("unmarkpaid", "Cofnij oznaczenie zapłacone (pomyłka)"),
    BotCommand("markdone", "Anuluj zapisaną nieobecność"),
    BotCommand("markmissed", "Wymuś nieobecność na dany dzień"),
    BotCommand("podglad", "Podgląd podsumowania końcowego dla grupy"),
    BotCommand("napisz", "Napisz na grupie jako bot"),
    BotCommand("regeneracja", "Oznacz nieobecność jako #regeneracja"),
]


async def setup_commands(app: Application):
    """Registers Telegram's native "/" command menu, scoped so each chat
    only sees what's relevant to it: participants get /start, /status,
    /ranking and /uczestnicy in private chats (the last two only work there
    — they'd just clutter the group), the group gets no bot commands of its
    own, and each admin's own private chat additionally gets the correction
    commands."""
    await app.bot.set_my_commands(PARTICIPANT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await app.bot.set_my_commands([], scope=BotCommandScopeAllGroupChats())
    for admin_id in config.ADMIN_IDS:
        try:
            await app.bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            log.warning(
                "Could not set the admin command menu for %s (they need to have "
                "DMed the bot at least once first).",
                admin_id,
            )


def build_application() -> Application:
    app = Application.builder().token(config.BOT_TOKEN).post_init(setup_commands).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("addparticipant", cmd_add_participant))
    app.add_handler(CommandHandler("removeparticipant", cmd_remove_participant))
    app.add_handler(CommandHandler("markpaid", cmd_markpaid))
    app.add_handler(CommandHandler("unmarkpaid", cmd_unmarkpaid))
    app.add_handler(CommandHandler("markdone", cmd_markdone))
    app.add_handler(CommandHandler("markmissed", cmd_markmissed))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("uczestnicy", cmd_roster))
    app.add_handler(CommandHandler("ranking", cmd_leaderboard))
    app.add_handler(CommandHandler("podsumowanie", cmd_personal_summary))
    app.add_handler(CommandHandler("podglad", cmd_group_summary_preview))
    app.add_handler(CommandHandler("napisz", cmd_say_in_group))
    app.add_handler(CommandHandler("regeneracja", cmd_regeneracja))
    app.add_handler(PollAnswerHandler(on_poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_chat_members))
    app.add_handler(CallbackQueryHandler(on_admin_callback))
    app.add_error_handler(error_handler)

    app.job_queue.run_daily(job_daily_rollover, time=time(8, 0, tzinfo=config.TIMEZONE))
    app.job_queue.run_daily(job_backup_db, time=time(8, 10, tzinfo=config.TIMEZONE))
    app.job_queue.run_daily(job_reminder, time=time(21, 0, tzinfo=config.TIMEZONE))
    app.job_queue.run_once(catch_up_check, when=5)

    return app


def main():
    db.init_db()
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    app = build_application()
    log.info("PompkiBot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

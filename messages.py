import re
from html import escape

import config

POLL_OPTIONS = ["Tak, zrobione! ✅", "#regeneracja"]

REGEN_TAG = " — #regeneracja (nie zapomniał, to był świadomy wybór)"

WEEKDAYS_PL = [
    "poniedziałek", "wtorek", "środa", "czwartek",
    "piątek", "sobota", "niedziela",
]


def format_date_pl(d) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year} ({WEEKDAYS_PL[d.weekday()]})"


def poll_question(d) -> str:
    return f"Pompki na dziś! Zrobiłeś już dzisiaj pompki? 💪 — {format_date_pl(d)}"


PHONE_RE = re.compile(r"\+?\d[\d \-]{6,}\d")


def pay_to_html() -> str:
    """PAY_TO escaped for HTML, with any phone number in monospace so it can
    be copied with one tap. Only ever used in private messages — the number
    stays out of the group chat."""
    text = escape(config.PAY_TO or "konto organizatora")
    return PHONE_RE.sub(lambda m: f"<code>{m.group(0)}</code>", text)


def mention_html(participant) -> str:
    if participant["username"]:
        return f'@{escape(participant["username"])}'
    return f'<a href="tg://user?id={participant["user_id"]}">{escape(participant["first_name"])}</a>'


def miss_report(day, missed_participants: list, regen_user_ids: set[int] = frozenset()) -> str:
    date_str = format_date_pl(day)
    if not missed_participants:
        return f"🎉 Brawo! {date_str} — wszyscy zrobili pompki!"
    names = "\n".join(
        f"• {mention_html(p)}"
        + (f" — w tytule: <code>POMPKI {p['pay_code']}</code>" if config.PAYMENTS_ENABLED and p["pay_code"] else "")
        + (REGEN_TAG if p["user_id"] in regen_user_ids else "")
        for p in missed_participants
    )
    text = (
        f"📋 Podsumowanie dnia {date_str}:\n\n"
        f"Pompek nie zrobili (do zapłaty {config.PENALTY_PLN} PLN):\n{names}"
    )
    if config.PAYMENTS_ENABLED:
        text += (
            "\n\n💸 Dane do wpłaty i swój tytuł znajdziesz po wpisaniu <code>/status</code> w prywatnej "
            "wiadomości do bota — wpłatę zaksięguje on sam."
        )
    return text


def regen_note(day, participant) -> str:
    return f"ℹ️ {mention_html(participant)} — {format_date_pl(day)}: to była #regeneracja, nie zapomniał o pompkach, to świadomy wybór."


def reminder(not_done_participants: list) -> str:
    mentions = ", ".join(mention_html(p) for p in not_done_participants)
    return (
        f"Przypomnienie! {mentions} — nie zapomnijcie zagłosować w dzisiejszej "
        f"ankiecie, zanim się zamknie! 💪"
    )


def start_reply(first_name: str) -> str:
    return (
        f"Cześć, {first_name}! Zostałeś/-aś zapisany/a do wyzwania Pompek. 💪\n\n"
        f"Codziennie o {config.POLL_HOUR}:00 w grupie pojawia się ankieta — głosuj, czy zrobiłeś/-aś "
        f"pompki. Masz na to 24 godzin.\n\n"
        f"Napisz /status w dowolnej chwili, żeby sprawdzić swoje saldo."
    )


def status_reply(owed: int, paid: int, unpaid_dates: list[str],
                 pay_code: str | None = None, credit: int = 0, last_sync: str | None = None) -> str:
    lines = [
        f"💰 Twoje saldo:",
        f"Do zapłaty: {owed} PLN",
        f"Zapłacono: {paid} PLN",
    ]
    if credit:
        lines.append(f"Nadpłata (zaliczy się na kolejne dni): {credit / 100:g} PLN")
    if unpaid_dates:
        lines.append("\nNiezapłacone dni:")
        lines.extend(f"• {d}" for d in unpaid_dates)
    else:
        lines.append("\nBrak niezapłaconych dni. 🎉")
    if config.PAYMENTS_ENABLED and pay_code:
        lines.append(f"\nPłać na: {pay_to_html()}")
        lines.append(f"W tytule wpisz: <code>POMPKI {escape(pay_code)}</code> (dotknij, żeby skopiować)")
        if last_sync:
            lines.append(f"Wpłaty księgują się automatycznie (ostatnia synchronizacja: {last_sync}).")
    return "\n".join(lines)


def final_summary(rows, streak_user_ids: set[int]) -> str:
    lines = ["Wyzwanie zakończone! Oto podsumowanie końcowe:\n"]
    total_pot = 0
    for participant, owed, paid, unpaid_dates in rows:
        total = owed + paid
        total_pot += total
        name = mention_html(participant)
        status = "rozliczony/a" if owed == 0 else f"do zapłaty {owed} PLN"
        lines.append(f"- {name}: łącznie {total} PLN ({status})")

    lines.append(f"\nŁączna pula na fest: {total_pot} PLN")

    if streak_user_ids:
        streak_names = [p[0] for p in rows if p[0]["user_id"] in streak_user_ids]
        if streak_names:
            names = ", ".join(mention_html(p) for p in streak_names)
            lines.append(f"Pełna passa (zero opuszczonych dni): {names}")

    lines.append("\nDzięki wszystkim za udział — czas na fest! 🎉")
    return "\n".join(lines)


def roster_text(participants) -> str:
    if not participants:
        return "Nikt jeszcze nie jest zapisany do wyzwania."
    lines = ["Uczestnicy wyzwania:"]
    lines.extend(f"- {mention_html(p)}" for p in participants)
    return "\n".join(lines)


def leaderboard_text(rows) -> str:
    relevant = [r for r in rows if r[1] > 0 or r[2] > 0]
    if not relevant:
        return "Nikt jeszcze nic nie jest winien ani nie zapłacił."
    ranked = sorted(relevant, key=lambda r: r[1], reverse=True)
    lines = ["Aktualny ranking (kwoty do zapłaty):"]
    for participant, owed, paid, _unpaid_dates in ranked:
        name = mention_html(participant)
        lines.append(f"- {name}: {owed} PLN do zapłaty (zapłacono {paid} PLN)")
    total_owed = sum(r[1] for r in relevant)
    total_paid = sum(r[2] for r in relevant)
    lines.append(f"\nŁącznie do zapłaty w tej chwili: {total_owed} PLN")
    lines.append(f"Łącznie już zebrane: {total_paid} PLN")
    lines.append(f"Łącznie po rozliczeniu wszystkich: {total_owed + total_paid} PLN")
    return "\n".join(lines)


def owing_text(rows) -> str:
    owing = sorted((r for r in rows if r[1] > 0), key=lambda r: r[1], reverse=True)
    if not owing:
        return "Nikt nie zalega z płatnością. 🎉"
    lines = ["<b>Zalegają z płatnością:</b>", ""]
    for participant, owed, _paid, _unpaid_dates in owing:
        lines.append(f"{mention_html(participant)} — <b>{owed} PLN</b>")
    return "\n".join(lines)


def admin_error_alert(error_text: str) -> str:
    return f"Błąd w PompkiBocie:\n{error_text}"


# ---------- automatic payments ----------

def pln(grosze: int) -> str:
    return f"{grosze / 100:g} PLN"


def payment_received(participant, amount: int, paid_dates: list[str], credit: int, still_owed: int) -> str:
    text = f"💰 {mention_html(participant)} wpłacił/a {pln(amount)}"
    if paid_dates:
        text += f" — rozliczone dni: {', '.join(paid_dates)}"
    if credit:
        text += f". Nadpłata {pln(credit)} zaliczy się na kolejne dni"
    if still_owed:
        text += f". Zostało do zapłaty: {still_owed} PLN"
    elif paid_dates:
        text += ". Wszystko rozliczone! 🎉"
    return text


def unmatched_payment_alert(row) -> str:
    return (
        f"❓ Wpłata bez dopasowania: {pln(row['amount'])}\n"
        f"Od: {row['sender'] or '(brak nazwy)'}\n"
        f"Tytuł: {row['title'] or '(brak)'}\n"
        f"Data: {row['booked_on']}\n\n"
        f"Kto to zapłacił? (Zapamiętam nadawcę na przyszłość.)"
    )


def payment_assigned(name: str, outcome_paid: list[str], amount: int, credit: int) -> str:
    text = f"✅ {pln(amount)} przypisano: {name}"
    if outcome_paid:
        text += f" (rozliczone: {', '.join(outcome_paid)})"
    if credit:
        text += f". Nadpłata: {pln(credit)}"
    return text


def sync_summary(new: int, applied: int, unmatched: int) -> str:
    if not new:
        return "Brak nowych wpłat."
    return f"Nowe wpłaty: {new} (zaksięgowane: {applied}, bez dopasowania: {unmatched})."


def consent_expiring(days_left: int) -> str:
    return (
        f"⚠️ Zgoda na odczyt konta w banku wygasa za {days_left} dni — po tym automatyczne "
        f"księgowanie wpłat przestanie działać. Odnów ją: python bank_auth.py start."
    )


def bank_error_alert(error: str) -> str:
    return f"⚠️ Nie udało się pobrać wpłat z banku: {error}"


# ---------- kicking for non-payment ----------

def _how_to_pay(participant) -> str:
    return f"Płać na: {pay_to_html()}, w tytule: <code>POMPKI {participant['pay_code']}</code>"


def kick_warning_dm(participant, owed: int, days_left: int, kick_date) -> str:
    return (
        f"⏳ Masz do zapłaty {owed} PLN i nie wpłaciłeś/-aś nic od dawna. "
        f"Zostało {days_left} {'dzień' if days_left == 1 else 'dni'} — jeśli nic nie wpłacisz, "
        f"{format_date_pl(kick_date)} rano zostaniesz usunięty/a z grupy.\n\n"
        f"{_how_to_pay(participant)}\n"
        f"Wystarczy jakakolwiek wpłata, żeby zegar zaczął liczyć od nowa."
    )


def kick_warning_group(participant, owed: int, kick_date) -> str:
    return (
        f"⚠️ {mention_html(participant)} — zostaje 1 dzień! Masz do zapłaty {owed} PLN. "
        f"Bez wpłaty {format_date_pl(kick_date)} rano zostaniesz usunięty/a z grupy.\n"
        f"Twój tytuł przelewu: <code>POMPKI {participant['pay_code']}</code>. "
        f"Dane do wpłaty: <code>/status</code> w prywatnej wiadomości do bota."
    )


def kick_announcement(participant, owed: int) -> str:
    return (
        f"👋 {escape(participant['first_name'])} został/a usunięty/a z grupy — "
        f"brak wpłaty przez {config.KICK_AFTER_DAYS} dni. Dług ({owed} PLN) anulowany."
    )


def kick_dm_to_person(participant) -> str:
    return (
        f"Zostałeś/-aś usunięty/a z grupy Pompek — brak wpłaty przez {config.KICK_AFTER_DAYS} dni. "
        f"Niezapłacony dług został anulowany."
    )


def kick_failed_alert(participant, error: str) -> str:
    return f"⚠️ Nie udało się usunąć {participant['first_name']} z grupy: {error}"

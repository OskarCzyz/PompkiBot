from html import escape

import config

POLL_OPTIONS = ["Tak, zrobione! ✅", "Jeszcze nie ❌"]

WEEKDAYS_PL = [
    "poniedziałek", "wtorek", "środa", "czwartek",
    "piątek", "sobota", "niedziela",
]


def format_date_pl(d) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year} ({WEEKDAYS_PL[d.weekday()]})"


def poll_question(d) -> str:
    return f"Pompki na dziś! Zrobiłeś już dzisiaj pompki? 💪 — {format_date_pl(d)}"


def mention_html(participant) -> str:
    if participant["username"]:
        return f'@{escape(participant["username"])}'
    return f'<a href="tg://user?id={participant["user_id"]}">{escape(participant["first_name"])}</a>'


def miss_report(day, missed_participants: list) -> str:
    date_str = format_date_pl(day)
    if not missed_participants:
        return f"🎉 Brawo! {date_str} — wszyscy zrobili pompki!"
    names = "\n".join(f"• {mention_html(p)}" for p in missed_participants)
    return (
        f"📋 Podsumowanie dnia {date_str}:\n\n"
        f"Pompek nie zrobili (do zapłaty {config.PENALTY_PLN} PLN):\n{names}"
    )


def reminder(not_done_participants: list) -> str:
    mentions = ", ".join(mention_html(p) for p in not_done_participants)
    return (
        f"Przypomnienie! {mentions} — nie zapomnijcie zagłosować w dzisiejszej "
        f"ankiecie, zanim się zamknie! 💪"
    )


def start_reply(first_name: str) -> str:
    return (
        f"Cześć, {first_name}! Zostałeś/-aś zapisany/a do wyzwania Pompek. 💪\n\n"
        f"Codziennie o 8:00 w grupie pojawia się ankieta — głosuj, czy zrobiłeś/-aś "
        f"pompki. Masz na to 24 godzin.\n\n"
        f"Napisz /status w dowolnej chwili, żeby sprawdzić swoje saldo."
    )


def status_reply(owed: int, paid: int, unpaid_dates: list[str]) -> str:
    lines = [
        f"💰 Twoje saldo:",
        f"Do zapłaty: {owed} PLN",
        f"Zapłacono: {paid} PLN",
    ]
    if unpaid_dates:
        lines.append("\nNiezapłacone dni:")
        lines.extend(f"• {d}" for d in unpaid_dates)
    else:
        lines.append("\nBrak niezapłaconych dni. 🎉")
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


def admin_error_alert(error_text: str) -> str:
    return f"Błąd w PompkiBocie:\n{error_text}"

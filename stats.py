"""End-of-challenge statistics: the numbers for the final stats message and
the charts sent alongside it.

Everything here works on plain data pulled from db.py, so it's pure and
testable — collect() reads the database once, everything else just crunches
the resulting ChallengeStats. Vote time is the proxy for "when did they do
their pushups": a 'done' vote's timestamp is when they last switched it on.

Times of day are handled as hours since the poll opened (08:00 on the poll
date, 0..24), so a vote at 01:30 the next night sorts *after* 23:00 rather
than before 09:00 — that's the order people actually experienced the day in.
"""

import io
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from html import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.ticker import FuncFormatter, MultipleLocator  # noqa: E402

import config
import db
from messages import WEEKDAYS_PL, mention_html

POLL_OPEN = time(8, 0)
REMINDER_OFFSET = 13.0  # 21:00 is 13h after the 08:00 open
MIDNIGHT_OFFSET = 16.0
# Votes later than 05:00 the next morning (21h after the 08:00 open) are
# catch-ups for a forgotten check-in, not a habit: the day still counts as
# done, but they stay out of every time-of-day stat.
CATCH_UP_OFFSET = 21.0
MIN_TIMED = 5  # done votes needed before someone's time-of-day habits count

WEEKDAYS_SHORT = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]

# Chart palette (light surface, see the dataviz reference palette).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE = "#2a78d6"
BLUE_DARK = "#184f95"
GOOD = "#0ca30c"
CRITICAL = "#d03b3b"
NEUTRAL = "#f0efec"
SEQ_BLUE = ["#f0efec", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


# ---------- data ----------

@dataclass(eq=False)
class Person:
    participant: object
    days: list[str] = field(default_factory=list)          # closed poll dates they were in
    missed: set[str] = field(default_factory=set)
    done_offsets: dict[str, float] = field(default_factory=dict)  # date -> hours since open, habit votes only

    @property
    def user_id(self) -> int:
        return self.participant["user_id"]

    @property
    def name(self) -> str:
        return self.participant["first_name"]

    @property
    def done_days(self) -> list[str]:
        return [d for d in self.days if d not in self.missed]

    @property
    def completion(self) -> float:
        return len(self.done_days) / len(self.days) if self.days else 0.0

    @property
    def longest_streak(self) -> int:
        best = cur = 0
        for d in self.days:
            cur = 0 if d in self.missed else cur + 1
            best = max(best, cur)
        return best

    @property
    def median_offset(self) -> float | None:
        return statistics.median(self.done_offsets.values()) if self.done_offsets else None

    @property
    def offset_spread(self) -> float:
        """Median absolute deviation of vote times, in hours — how regular
        someone's schedule is, without one odd night skewing it."""
        m = self.median_offset
        return statistics.median(abs(h - m) for h in self.done_offsets.values()) if m is not None else 0.0

    @property
    def longest_miss_run(self) -> tuple[int, str | None, str | None]:
        best = (0, None, None)
        run_start, run = None, 0
        for d in self.days:
            if d in self.missed:
                run_start = run_start if run else d
                run += 1
                if run > best[0]:
                    best = (run, run_start, d)
            else:
                run = 0
        return best

    @property
    def weekday_weekend_rates(self) -> tuple[float | None, float | None]:
        """Completion on Mon-Fri vs Sat-Sun, None where there are under 4 days
        to judge from."""
        def rate(ds):
            return sum(d not in self.missed for d in ds) / len(ds) if len(ds) >= 4 else None
        weekdays = [d for d in self.days if date.fromisoformat(d).weekday() < 5]
        weekends = [d for d in self.days if date.fromisoformat(d).weekday() >= 5]
        return rate(weekdays), rate(weekends)

    @property
    def median_halves(self) -> tuple[float, float] | None:
        """Typical vote time in the first vs second half of their done days."""
        hs = [self.done_offsets[d] for d in sorted(self.done_offsets)]
        if len(hs) < 8:
            return None
        mid = len(hs) // 2
        return statistics.median(hs[:mid]), statistics.median(hs[mid:])


@dataclass
class ChallengeStats:
    poll_dates: list[str]
    people: list[Person]

    @property
    def all_offsets(self) -> list[tuple[Person, str, float]]:
        return [(p, d, h) for p in self.people for d, h in p.done_offsets.items()]


def raw_hours_since_open(poll_date: str, voted_at: str) -> float:
    opened = datetime.combine(date.fromisoformat(poll_date), POLL_OPEN, tzinfo=config.TIMEZONE)
    voted = datetime.fromisoformat(voted_at).astimezone(config.TIMEZONE)
    return (voted - opened).total_seconds() / 3600


def hours_since_open(poll_date: str, voted_at: str) -> float:
    return min(max(raw_hours_since_open(poll_date, voted_at), 0.0), 23.999)


def build(poll_dates, participants, votes, misses) -> ChallengeStats:
    """poll_dates: closed poll dates, ascending. votes: rows with poll_date,
    user_id, option_id, voted_at. misses: rows with user_id, miss_date.

    A person's challenge starts on the first date they have any trace on
    (a vote or a miss) — someone who joined mid-challenge isn't counted as
    having 'done' the days before they were even registered."""
    poll_set = set(poll_dates)
    missed_by = {}
    first_seen = {}
    for m in misses:
        if m["miss_date"] not in poll_set:
            continue
        missed_by.setdefault(m["user_id"], set()).add(m["miss_date"])
        first_seen[m["user_id"]] = min(first_seen.get(m["user_id"], m["miss_date"]), m["miss_date"])
    for v in votes:
        if v["poll_date"] not in poll_set:
            continue
        first_seen[v["user_id"]] = min(first_seen.get(v["user_id"], v["poll_date"]), v["poll_date"])

    people = []
    by_id = {}
    for p in participants:
        start = first_seen.get(p["user_id"])
        if start is None:
            continue
        person = Person(
            participant=p,
            days=[d for d in poll_dates if d >= start],
            missed=missed_by.get(p["user_id"], set()),
        )
        people.append(person)
        by_id[p["user_id"]] = person

    for v in votes:
        person = by_id.get(v["user_id"])
        if (
            person
            and v["option_id"] == config.OPTION_DONE
            and v["poll_date"] in poll_set
            and v["poll_date"] not in person.missed
        ):
            if raw_hours_since_open(v["poll_date"], v["voted_at"]) < CATCH_UP_OFFSET:
                person.done_offsets[v["poll_date"]] = hours_since_open(v["poll_date"], v["voted_at"])

    return ChallengeStats(poll_dates=list(poll_dates), people=people)


def collect() -> ChallengeStats:
    return build(
        db.get_closed_poll_dates(),
        db.get_active_participants(),
        db.get_all_votes(),
        db.get_all_misses(),
    )


def find_spotlight(stats: ChallengeStats, query: str) -> Person | None:
    q = query.strip()
    if not q:
        return None
    for p in stats.people:
        if q.lstrip("@").isdigit() and p.user_id == int(q.lstrip("@")):
            return p
        if q.startswith("@") and (p.participant["username"] or "").lower() == q[1:].lower():
            return p
    for p in stats.people:
        if p.name.lower() == q.lower():
            return p
    for p in stats.people:
        if q.lower() in p.name.lower():
            return p
    return None


# ---------- text ----------

def clock(offset: float) -> str:
    minutes = int(round(offset * 60)) % (24 * 60)
    total = POLL_OPEN.hour * 60 + POLL_OPEN.minute + minutes
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def short_date(d: str) -> str:
    dd = date.fromisoformat(d)
    return f"{dd.day:02d}.{dd.month:02d}"


def format_day(d: str) -> str:
    return f"{short_date(d)} ({WEEKDAYS_PL[date.fromisoformat(d).weekday()]})"


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def day_rates(stats: ChallengeStats) -> list[tuple[str, float]]:
    """(date, share of people in the challenge that day who did it)."""
    out = []
    for d in stats.poll_dates:
        present = [p for p in stats.people if d in p.days]
        if present:
            out.append((d, sum(d not in p.missed for p in present) / len(present)))
    return out


def weekday_rates(stats: ChallengeStats) -> dict[int, float]:
    done, total = Counter(), Counter()
    for p in stats.people:
        for d in p.days:
            wd = date.fromisoformat(d).weekday()
            total[wd] += 1
            done[wd] += d not in p.missed
    return {wd: done[wd] / total[wd] for wd in total}


def stats_text(stats: ChallengeStats) -> str:
    """The group's end-of-challenge stats message: aggregate numbers for the
    whole group, then a handful of individual oddities ("Ciekawostki")
    instead of a full per-person ranking, which wouldn't scale to ~40 people."""
    people = stats.people
    if not people or not stats.poll_dates:
        return "📊 Brak danych do statystyk."

    lines = ["📊 <b>Statystyki grupowe</b>\n"]
    total_done = sum(len(p.done_days) for p in people)
    total_days = sum(len(p.days) for p in people)
    completions = [p.completion for p in people]
    lines.append(
        f"💪 Łącznie zrobiliście <b>{fmt_int(total_done * config.PUSHUPS_PER_DAY)}</b> pompek "
        f"— {len(people)} osób, {len(stats.poll_dates)} dni."
    )
    lines.append(
        f"✅ Zaliczone dni: {total_done} z {total_days} ({total_done / total_days:.0%}), "
        f"mediana skuteczności: {statistics.median(completions):.0%}"
    )
    perfect = sum(1 for c in completions if c == 1)
    ninety = sum(1 for c in completions if c >= 0.9)
    lines.append(f"🏅 Bez ani jednej wpadki: {perfect} os., co najmniej 90%: {ninety} os.")

    rates = day_rates(stats)
    perfect_days = [d for d, r in rates if r == 1]
    best_day = max(rates, key=lambda t: (t[1], t[0]))
    worst_day = min(rates, key=lambda t: (t[1], t[0]))
    lines.append(f"✨ Dni, w których wszyscy zrobili pompki: {len(perfect_days)}/{len(rates)}")
    if worst_day[1] < 1:
        lines.append(
            f"📉 Najsłabszy dzień: {format_day(worst_day[0])} — tylko {worst_day[1]:.0%} "
            f"(najlepszy: {format_day(best_day[0])}, {best_day[1]:.0%})"
        )
    wd_rates = weekday_rates(stats)
    if len(wd_rates) >= 2:
        best_wd = max(wd_rates, key=wd_rates.get)
        worst_wd = min(wd_rates, key=wd_rates.get)
        lines.append(
            f"📅 Najlepszy dzień tygodnia: {WEEKDAYS_PL[best_wd]} ({wd_rates[best_wd]:.0%}), "
            f"najgorszy: {WEEKDAYS_PL[worst_wd]} ({wd_rates[worst_wd]:.0%})"
        )

    offsets = [h for _, _, h in stats.all_offsets]
    if offsets:
        top_hour, _ = Counter(int(h) for h in offsets).most_common(1)[0]
        after_reminder = sum(1 for h in offsets if REMINDER_OFFSET <= h < REMINDER_OFFSET + 1)
        after_midnight = sum(1 for h in offsets if h >= MIDNIGHT_OFFSET)
        lines.append("\n⏰ <b>Pory pompek</b> (wg momentu głosowania)")
        lines.append(f"Typowa pora w grupie: ok. {clock(statistics.median(offsets))}")
        lines.append(f"Najpopularniejsza godzina: {clock(top_hour)}–{clock(top_hour + 1)}")
        lines.append(
            f"📣 W godzinę po przypomnieniu o 21:00: {after_reminder} głosów "
            f"({after_reminder / len(offsets):.0%})"
        )
        lines.append(f"🌚 Po północy: {after_midnight} głosów ({after_midnight / len(offsets):.0%})")

    oddities = anomalies(stats)
    if oddities:
        lines.append("\n🔍 <b>Ciekawostki</b>")
        lines.extend(oddities)

    lines.append("\nKażdy może teraz napisać do mnie prywatnie /podsumowanie po swoje osobiste statystyki. 📬")
    return "\n".join(lines)


def anomalies(stats: ChallengeStats) -> list[str]:
    """Individual statistical oddities worth a shout-out — each one names a
    single person and only shows up if there's enough data for it to mean
    something (time-based ones need MIN_TIMED done votes)."""
    people = stats.people
    out: list[tuple[Person, str]] = []
    timed = [p for p in people if len(p.done_offsets) >= MIN_TIMED]
    all_offsets = stats.all_offsets

    if all_offsets:
        p, d, h = min(all_offsets, key=lambda t: t[2])
        out.append((p, f"🐓 Najwcześniejsze pompki: {mention_html(p.participant)} — {clock(h)}, {short_date(d)}"))
        p, d, h = max(all_offsets, key=lambda t: t[2])
        out.append((p,
            f"🦉 Najpóźniejsze pompki: {mention_html(p.participant)} — {clock(h)}, {short_date(d)}"
            + (" 😅" if h >= MIDNIGHT_OFFSET else "")
        ))

    if len(timed) >= 3:
        clockwork = min(timed, key=lambda p: p.offset_spread)
        out.append((clockwork,
            f"⌚ Szwajcarski zegarek: {mention_html(clockwork.participant)} — prawie zawsze "
            f"ok. {clock(clockwork.median_offset)} (±{clockwork.offset_spread * 60:.0f} min)"
        ))
        chaos = max(timed, key=lambda p: p.offset_spread)
        hs = chaos.done_offsets.values()
        out.append((chaos,
            f"🎲 Totalny chaos: {mention_html(chaos.participant)} — od {clock(min(hs))} "
            f"do {clock(max(hs))}, nigdy nie wiadomo kiedy"
        ))
        early = min(timed, key=lambda p: p.median_offset)
        late = max(timed, key=lambda p: p.median_offset)
        out.append((early, f"☀️ Ranny ptaszek: {mention_html(early.participant)} — zwykle ok. {clock(early.median_offset)}"))
        out.append((late, f"🌙 Nocny marek: {mention_html(late.participant)} — zwykle ok. {clock(late.median_offset)}"))

    def top_by(pred, min_count: int):
        counts = {p: sum(1 for h in p.done_offsets.values() if pred(h)) for p in people}
        best = max(counts, key=counts.get, default=None)
        return (best, counts[best]) if best and counts[best] >= min_count else (None, 0)

    p, n = top_by(lambda h: h < 0.5, 3)
    if p:
        out.append((p, f"⚡ Błyskawica: {mention_html(p.participant)} — {n}× zagłosował/a w pół godziny od otwarcia ankiety"))
    p, n = top_by(lambda h: REMINDER_OFFSET <= h < REMINDER_OFFSET + 1, 3)
    if p:
        out.append((p, f"📣 Przypomnienie działało najlepiej na: {mention_html(p.participant)} — {n}× w godzinę po 21:00"))
    p, n = top_by(lambda h: h >= MIDNIGHT_OFFSET, 3)
    if p:
        out.append((p, f"🌚 Król nocnych zmian: {mention_html(p.participant)} — {n}× pompki po północy"))

    best_streak = max(people, key=lambda p: p.longest_streak)
    if best_streak.completion < 1 and best_streak.longest_streak >= 5:
        out.append((best_streak,
            f"🔥 Najdłuższa passa (wśród tych z wpadką): {mention_html(best_streak.participant)} — "
            f"{best_streak.longest_streak} dni z rzędu"
        ))

    slump = max(people, key=lambda p: p.longest_miss_run[0])
    run, start, end = slump.longest_miss_run
    if run >= 3:
        came_back = end != slump.days[-1]
        out.append((slump,
            f"🕳️ Najdłuższa przerwa: {mention_html(slump.participant)} — {run} dni bez pompek "
            f"({short_date(start)}–{short_date(end)})" + (", ale wrócił/a do gry 💪" if came_back else "")
        ))

    weekend_gaps = [
        (p, wk, we) for p in people
        if (rates := p.weekday_weekend_rates) and (wk := rates[0]) is not None and (we := rates[1]) is not None
    ]
    if weekend_gaps:
        p, wk, we = max(weekend_gaps, key=lambda t: t[1] - t[2])
        if wk - we >= 0.3:
            out.append((p,
                f"🛋️ Weekendowy luzak: {mention_html(p.participant)} — w tygodniu {wk:.0%}, "
                f"w weekendy {we:.0%}"
            ))

    shifts = [(p, a, b) for p in people if (halves := p.median_halves) for a, b in [halves]]
    if shifts:
        p, a, b = max(shifts, key=lambda t: abs(t[1] - t[2]))
        if abs(a - b) >= 3:
            out.append((p,
                f"🔄 Zmiana trybu życia: {mention_html(p.participant)} — przerzucił/a pompki "
                f"z ok. {clock(a)} na ok. {clock(b)}"
            ))

    spotlight = find_spotlight(stats, config.SPOTLIGHT_USER)
    if spotlight:
        out.append((spotlight,
            f"🔦 Spotlight: {mention_html(spotlight.participant)} — {spotlight.completion:.0%} skuteczności"
            + (f", zwykle ok. {clock(spotlight.median_offset)}" if spotlight.median_offset is not None else "")
            + " (wykres w albumie)"
        ))

    # Keep any one person from hogging the list.
    shown = Counter()
    result = []
    for person, line in out:
        if shown[person.user_id] < 2:
            shown[person.user_id] += 1
            result.append(line)
    return result


def personal_text(stats: ChallengeStats, person: Person, owed: int, paid: int) -> str:
    """A participant's own end-of-challenge summary for /podsumowanie."""
    people = stats.people
    rank = 1 + sum(1 for p in people if p.completion > person.completion)
    tied = sum(1 for p in people if p.completion == person.completion) > 1
    lines = [
        f"📬 <b>Twoje podsumowanie wyzwania, {escape(person.name)}</b>\n",
        f"💪 Zrobione pompki: <b>{fmt_int(len(person.done_days) * config.PUSHUPS_PER_DAY)}</b>",
        f"✅ Zaliczone dni: {len(person.done_days)}/{len(person.days)} ({person.completion:.0%})",
        f"🏆 Miejsce: {rank}. z {len(people)}" + (" (ex aequo)" if tied else ""),
        f"🔥 Najdłuższa passa: {person.longest_streak} dni z rzędu",
    ]
    run, start, end = person.longest_miss_run
    if run >= 2:
        lines.append(f"🕳️ Najdłuższa przerwa: {run} dni ({short_date(start)}–{short_date(end)})")
    rates = person.weekday_weekend_rates
    if rates and rates[0] is not None and rates[1] is not None:
        lines.append(f"📅 W tygodniu: {rates[0]:.0%}, w weekendy: {rates[1]:.0%}")

    if person.done_offsets:
        group_median = statistics.median(h for _, _, h in stats.all_offsets)
        hs = person.done_offsets
        first = min(hs, key=hs.get)
        last = max(hs, key=hs.get)
        lines.append("\n⏰ <b>Pory</b> (wg momentu głosowania)")
        lines.append(f"Twoja typowa pora: ok. {clock(person.median_offset)} (grupa: ok. {clock(group_median)})")
        lines.append(f"Najwcześniej: {clock(hs[first])} ({short_date(first)}), najpóźniej: {clock(hs[last])} ({short_date(last)})")
        after_reminder = sum(1 for h in hs.values() if REMINDER_OFFSET <= h < REMINDER_OFFSET + 1)
        after_midnight = sum(1 for h in hs.values() if h >= MIDNIGHT_OFFSET)
        if after_reminder:
            lines.append(f"📣 W godzinę po przypomnieniu: {after_reminder}×")
        if after_midnight:
            lines.append(f"🌚 Po północy: {after_midnight}×")

    lines.append(f"\n💰 Wpłacono do puli: {paid} PLN" + (f", do zapłaty: {owed} PLN" if owed else " — rozliczony/a 🎉"))
    return "\n".join(lines)


# ---------- charts ----------

def _style(ax, title: str, subtitle: str | None = None):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=9, length=0, pad=6)
    ax.set_title(title, loc="left", fontsize=14, fontweight="bold", color=INK, pad=24 if subtitle else 12)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=10, color=INK_2, va="bottom")


def _fig(w=10, h=5.5):
    fig, ax = plt.subplots(figsize=(w, h), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    return fig, ax


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


def _clock_axis(axis):
    axis.set_major_locator(MultipleLocator(2))
    axis.set_major_formatter(FuncFormatter(lambda v, _: clock(v)))


def _date_axis(ax, dates: list[str]):
    first = datetime.combine(date.fromisoformat(dates[0]), time())
    last = datetime.combine(date.fromisoformat(dates[-1]), time())
    ax.set_xlim(first - timedelta(hours=16), last + timedelta(hours=16))
    ax.xaxis.set_major_locator(matplotlib.dates.DayLocator(interval=max(1, len(dates) // 10)))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d.%m"))


def chart_hour_histogram(stats: ChallengeStats) -> bytes:
    offsets = [h for _, _, h in stats.all_offsets]
    counts = Counter(int(h) for h in offsets)
    fig, ax = _fig()
    xs = list(range(24))
    ys = [counts.get(x, 0) for x in xs]
    colors = [BLUE_DARK if x == int(REMINDER_OFFSET) else BLUE for x in xs]
    ax.bar([x + 0.5 for x in xs], ys, width=0.85, color=colors, zorder=3)
    ax.set_xlim(0, 24)
    _clock_axis(ax.xaxis)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_ylabel("liczba głosów „zrobione”", color=INK_2, fontsize=10)
    ymax = max(ys) if ys else 1
    ax.annotate(
        "przypomnienie 21:00", xy=(REMINDER_OFFSET + 0.5, counts.get(int(REMINDER_OFFSET), 0)),
        xytext=(REMINDER_OFFSET + 0.5, ymax * 1.08), ha="center", fontsize=9, color=INK_2,
        arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=1),
    )
    ax.set_ylim(0, ymax * 1.18 + 0.5)
    _style(ax, "O której robiliście pompki", "Godzina głosu „Tak, zrobione!” — od otwarcia ankiety (8:00) do zamknięcia")
    return _png(fig)


def chart_weekday_heatmap(stats: ChallengeStats) -> bytes:
    grid = [[0] * 24 for _ in range(7)]
    for _, d, h in stats.all_offsets:
        grid[date.fromisoformat(d).weekday()][int(h)] += 1
    fig, ax = _fig(10, 4.2)
    cmap = LinearSegmentedColormap.from_list("seq", SEQ_BLUE)
    im = ax.imshow(grid, aspect="auto", cmap=cmap, extent=(0, 24, 7, 0), interpolation="nearest")
    # 2px surface gaps between cells
    for x in range(25):
        ax.axvline(x, color=SURFACE, linewidth=1.5)
    for y in range(8):
        ax.axhline(y, color=SURFACE, linewidth=1.5)
    ax.set_yticks([i + 0.5 for i in range(7)])
    ax.set_yticklabels(WEEKDAYS_SHORT)
    _clock_axis(ax.xaxis)
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=MUTED, labelsize=8, length=0)
    cbar.set_label("głosy", color=INK_2, fontsize=9)
    _style(ax, "Kiedy w tygodniu robiliście pompki", "Dzień ankiety × godzina głosu „zrobione”")
    for s in ax.spines.values():
        s.set_visible(False)
    return _png(fig)


def chart_daily_rate(stats: ChallengeStats) -> bytes:
    rates = day_rates(stats)
    xs = [date.fromisoformat(d) for d, _ in rates]
    ys = [100 * r for _, r in rates]
    fig, ax = _fig(10, 4.5)
    ax.plot(xs, ys, color=BLUE, linewidth=2, zorder=3)
    ax.fill_between(xs, ys, 0, color=BLUE, alpha=0.08, zorder=2)
    ax.set_ylim(0, 105)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    _date_axis(ax, stats.poll_dates)
    if ys:
        avg = sum(ys) / len(ys)
        ax.axhline(avg, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=2)
        ax.text(xs[-1], avg + 2, f"średnio {avg:.0f}%", ha="right", va="bottom", fontsize=9, color=INK_2)
    _style(ax, "Forma grupy dzień po dniu", "Jaki odsetek z was zrobił pompki danego dnia")
    return _png(fig)


def chart_completion_distribution(stats: ChallengeStats) -> bytes:
    bins = ["<50%", "50–59%", "60–69%", "70–79%", "80–89%", "90–99%", "100%"]

    def bucket(c: float) -> int:
        if c == 1:
            return 6
        return 0 if c < 0.5 else min(5, int(c * 10) - 4)

    counts = Counter(bucket(p.completion) for p in stats.people)
    ys = [counts.get(i, 0) for i in range(len(bins))]
    fig, ax = _fig(10, 4.8)
    colors = [GOOD if i == 6 else BLUE for i in range(len(bins))]
    bars = ax.bar(range(len(bins)), ys, width=0.8, color=colors, zorder=3)
    for bar, y in zip(bars, ys):
        if y:
            ax.text(bar.get_x() + bar.get_width() / 2, y, str(y), ha="center", va="bottom", fontsize=10, color=INK_2)
    ax.set_xticks(range(len(bins)))
    ax.set_xticklabels(bins)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.yaxis.set_major_locator(MultipleLocator(max(1, (max(ys) or 1) // 5)))
    ax.set_ylim(0, (max(ys) or 1) * 1.15)
    ax.set_ylabel("liczba osób", color=INK_2, fontsize=10)
    _style(ax, "Ile dni zaliczyliście", "Liczba osób wg odsetka zrobionych dni")
    return _png(fig)


def chart_outliers(stats: ChallengeStats) -> bytes:
    """Every person as one dot: typical vote time vs completion. The
    extremes get named — the visual companion to the 'Ciekawostki' list."""
    timed = [p for p in stats.people if len(p.done_offsets) >= MIN_TIMED]
    fig, ax = _fig(10, 5.5)
    xs = [p.median_offset for p in timed]
    ys = [100 * p.completion for p in timed]
    ax.scatter(xs, ys, s=70, color=BLUE, alpha=0.75, edgecolors=SURFACE, linewidths=1.5, zorder=3)
    labeled = {}
    if timed:
        labeled[min(timed, key=lambda p: p.median_offset).user_id] = "ranny ptaszek"
        labeled[max(timed, key=lambda p: p.median_offset).user_id] = "nocny marek"
        labeled.setdefault(min(timed, key=lambda p: p.completion).user_id, "najmniej dni")
        labeled.setdefault(min(timed, key=lambda p: p.offset_spread).user_id, "zegarek")
    for p in timed:
        if p.user_id in labeled:
            ax.scatter([p.median_offset], [100 * p.completion], s=90, color=BLUE_DARK,
                       edgecolors=SURFACE, linewidths=2, zorder=4)
            ax.annotate(f"{p.name} ({labeled[p.user_id]})", (p.median_offset, 100 * p.completion),
                        xytext=(8, 6), textcoords="offset points", fontsize=9, color=INK_2)
    ax.set_xlim(0, 24)
    _clock_axis(ax.xaxis)
    ax.set_ylim(min(ys + [50]) - 5, 104)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.axvline(REMINDER_OFFSET, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=2)
    ax.set_xlabel("typowa pora pompek", color=INK_2, fontsize=10)
    ax.set_ylabel("zaliczone dni", color=INK_2, fontsize=10)
    _style(ax, "Kto, kiedy i jak skutecznie", "Każda kropka to jedna osoba; kreska = przypomnienie 21:00")
    return _png(fig)


def chart_person(person: Person, title: str) -> bytes:
    fig, ax = _fig(10, 5)
    done_x = [date.fromisoformat(d) for d in person.done_offsets]
    done_y = list(person.done_offsets.values())
    ax.scatter(done_x, done_y, s=64, color=BLUE, edgecolors=SURFACE, linewidths=2, zorder=3, label="zrobione (pora głosu)")
    miss_x = [date.fromisoformat(d) for d in sorted(person.missed)]
    if miss_x:
        ax.scatter(miss_x, [23.5] * len(miss_x), s=70, marker="X", color=CRITICAL,
                   edgecolors=SURFACE, linewidths=1.5, zorder=3, label="opuszczone")
    ax.set_ylim(24, 0)  # morning at the top, the night at the bottom
    _clock_axis(ax.yaxis)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.axhline(REMINDER_OFFSET, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
    _date_axis(ax, person.days)
    ax.text(ax.get_xlim()[0], REMINDER_OFFSET - 0.3, " przypomnienie 21:00", fontsize=8, color=MUTED, va="bottom")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.08), ncol=2, frameon=False, fontsize=9, labelcolor=INK_2)
    subtitle = (
        f"{len(person.done_days)}/{len(person.days)} dni, najdłuższa passa {person.longest_streak}, "
        + (f"zwykle ok. {clock(person.median_offset)}" if person.median_offset is not None else "")
    )
    _style(ax, title, subtitle)
    return _png(fig)


def render_group_charts(stats: ChallengeStats, spotlight_query: str = "") -> list[tuple[bytes, str]]:
    """Group-level charts only (no per-person rows — they don't scale to
    ~40 people), plus the optional spotlight. Returns [(png, caption)].
    Blocking — run it off the event loop."""
    if not stats.people or not stats.poll_dates:
        return []
    charts = [
        (chart_daily_rate(stats), "Forma grupy dzień po dniu 📈"),
        (chart_completion_distribution(stats), "Ile dni zaliczyliście 📊"),
    ]
    if stats.all_offsets:
        charts += [
            (chart_hour_histogram(stats), "O której robiliście pompki ⏰"),
            (chart_weekday_heatmap(stats), "Kiedy w tygodniu 📅"),
        ]
    if sum(1 for p in stats.people if len(p.done_offsets) >= MIN_TIMED) >= 3:
        charts.append((chart_outliers(stats), "Kto, kiedy i jak skutecznie 🔍"))
    spotlight = find_spotlight(stats, spotlight_query)
    if spotlight:
        charts.append((chart_person(spotlight, f"Pompki w wykonaniu: {spotlight.name}"), f"Spotlight: {spotlight.name} 🔦"))
    return charts


def person_label(person: Person) -> str:
    username = person.participant["username"]
    return f"{person.name} (@{username})" if username else person.name


def render_personal_chart(person: Person) -> bytes:
    """Named in the title, so the image still says whose it is once it gets
    forwarded around."""
    return chart_person(person, f"Pompki dzień po dniu: {person_label(person)}")

import stats


def _build(counts):
    dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    people = [{"user_id": 1, "first_name": "Ala"}]
    votes = [
        {"poll_date": d, "user_id": 1, "option_id": 0, "voted_at": f"{d} 10:00:00"} for d in dates
    ]
    return stats.build(dates, people, votes, [], challenge_counts=counts)


def _row(date, count, goal=50):
    return {"challenge_date": date, "user_id": 1, "count": count, "goal": goal}


def test_average_counts_missing_row_as_zero():
    s = _build([_row("2026-01-01", 10), _row("2026-01-02", 60), _row("2026-01-04", 20)])
    person = s.people[0]
    assert person.counter_days == ["2026-01-04"]
    s = _build([_row("2026-01-02", 60), _row("2026-01-03", 0), _row("2026-01-04", 20)])
    person = s.people[0]
    assert person.counter_days == ["2026-01-02", "2026-01-03", "2026-01-04"]
    assert person.avg_per_counter_day == 80 / 3
    assert "Średnio dziennie: <b>26.7</b>" in stats.personal_text(s, person, 0, 0)


def test_average_skips_old_polls_after_a_one_off_challenge():
    # 01-01 was a prize challenge, 01-02 an old poll, 01-03 and 01-04 are counters
    s = _build([_row("2026-01-01", 275), _row("2026-01-03", 50), _row("2026-01-04", 40)])
    person = s.people[0]
    assert person.counter_days == ["2026-01-03", "2026-01-04"]
    assert person.avg_per_counter_day == 45


def test_no_average_without_counter_days():
    s = _build([])
    assert s.people[0].avg_per_counter_day is None
    assert "Średnio dziennie" not in stats.personal_text(s, s.people[0], 0, 0)

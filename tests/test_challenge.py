import asyncio
import os
import sys
import tempfile
import unittest
from datetime import date

os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_IDS", "99")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import config
import db
import stats

DAY = "2026-10-29"
T = "2026-10-29T{}:00+01:00"


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edited = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)

    async def edit_message_text(self, text, **kw):
        self.edited.append(text)

    async def unpin_chat_message(self, **kw):
        pass

    async def stop_poll(self, **kw):
        raise AssertionError("a challenge day has no poll to stop")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        config.DB_PATH = self.tmp.name
        config.GROUP_CHAT_ID = -1
        config.PUSHUPS_PER_DAY = 50
        db.init_db()
        db.add_participant(1, "ania", "Ania")
        db.add_participant(2, "bartek", "Bartek")
        db.add_participant(3, "celina", "Celina")
        db.schedule_challenge(DAY, 50, None)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def close(self):
        db.create_poll(DAY, bot.COUNTER_POLL_PREFIX + DAY, 1)
        fake = FakeBot()
        asyncio.run(bot.close_poll_for_date(fake, date.fromisoformat(DAY)))
        return fake


class ChallengeTest(Base):
    def test_count_never_negative_and_reached_at_tracks_goal(self):
        self.assertEqual(db.add_challenge_count(DAY, 1, -1, T.format("08:00")), 0)
        db.add_challenge_count(DAY, 1, 50, T.format("09:00"))
        self.assertEqual(db.get_challenge_counts(DAY)[1]["reached_at"], T.format("09:00"))
        db.add_challenge_count(DAY, 1, 10, T.format("10:00"))
        self.assertEqual(db.get_challenge_counts(DAY)[1]["reached_at"], T.format("09:00"))
        db.add_challenge_count(DAY, 1, -20, T.format("11:00"))
        self.assertIsNone(db.get_challenge_counts(DAY)[1]["reached_at"])
        self.assertEqual(db.add_challenge_count(DAY, 1, 10, T.format("12:00")), 50)
        self.assertEqual(db.get_challenge_counts(DAY)[1]["reached_at"], T.format("12:00"))

    def test_close_fifty_is_done_below_is_miss_leave_is_neither(self):
        db.add_challenge_count(DAY, 1, 80, T.format("09:00"))
        db.add_challenge_count(DAY, 2, 49, T.format("09:00"))
        db.add_vacation(3, DAY, DAY)
        fake = self.close()
        self.assertFalse(db.has_miss(1, DAY))
        self.assertTrue(db.has_miss(2, DAY))
        self.assertFalse(db.has_miss(3, DAY))
        poll = db.get_poll_by_date(DAY)
        self.assertEqual(db.get_votes_for_poll(poll["id"]), {1: config.OPTION_DONE})
        self.assertIn("wygrywa @ania - 80", fake.sent[-1])
        self.assertTrue(fake.edited)

    def test_winners(self):
        self.assertEqual(stats.challenge_winners({1: 80, 2: 60}), (80, [1]))
        self.assertEqual(stats.challenge_winners({1: 80, 2: 80}), (80, [1, 2]))
        self.assertEqual(stats.challenge_winners({1: 0}), (0, []))
        self.assertEqual(stats.challenge_winners({}), (0, []))

    def test_stats_count_real_pushups_on_challenge_day(self):
        db.create_poll("2026-10-28", "tg-28", 2)
        db.record_vote(db.get_poll_by_date("2026-10-28")["id"], 1, config.OPTION_DONE, T.format("09:00"))
        db.close_poll(db.get_poll_by_date("2026-10-28")["id"])
        db.add_challenge_count(DAY, 1, 120, T.format("09:00"))
        db.add_challenge_count(DAY, 2, 10, T.format("09:00"))
        self.close()
        s = stats.collect()
        by_id = {p.user_id: p for p in s.people}
        self.assertEqual(by_id[1].pushups, 50 + 120)
        self.assertEqual(by_id[2].pushups, 0)
        self.assertIn("Challenge'e", stats.stats_text(s))


class CounterDayTest(Base):
    """Normal (non-prize) days are counters too, with personal goals."""

    def setUp(self):
        super().setUp()
        db.cancel_challenge(DAY)

    def test_personal_goal_decides_done_and_goes_above_max(self):
        db.set_daily_goal(2, 30, T.format("07:00"))
        db.create_poll(DAY, bot.COUNTER_POLL_PREFIX + DAY, 1)
        db.add_challenge_count(DAY, 1, 80, T.format("09:00"))
        db.add_challenge_count(DAY, 2, 30, T.format("09:00"))
        db.add_challenge_count(DAY, 3, 49, T.format("09:00"))
        rows = {p["user_id"]: (c, g) for p, c, g, _ in bot.counter_rows(DAY)}
        self.assertEqual(rows, {1: (80, 50), 2: (30, 30), 3: (49, 50)})
        fake = asyncio.run(self._close())
        self.assertFalse(db.has_miss(1, DAY))
        self.assertFalse(db.has_miss(2, DAY))
        self.assertTrue(db.has_miss(3, DAY))
        self.assertFalse(any("wygrywa" in m or "Challenge" in m for m in fake.sent))

    async def _close(self):
        fake = FakeBot()
        await bot.close_poll_for_date(fake, date.fromisoformat(DAY))
        return fake

    def test_goal_change_mid_day_recomputes_reached_at(self):
        db.create_poll(DAY, bot.COUNTER_POLL_PREFIX + DAY, 1)
        db.add_challenge_count(DAY, 1, 40, T.format("09:00"))
        self.assertIsNone(db.get_challenge_counts(DAY)[1]["reached_at"])
        db.set_daily_goal(1, 30, T.format("10:00"))
        self.assertEqual(db.get_challenge_counts(DAY)[1]["reached_at"], T.format("10:00"))
        db.set_daily_goal(1, 100, T.format("11:00"))
        self.assertIsNone(db.get_challenge_counts(DAY)[1]["reached_at"])
        db.set_daily_goal(1, None, T.format("12:00"))
        self.assertEqual(db.get_challenge_counts(DAY)[1]["goal"], 50)

    def test_goal_change_leaves_closed_days_alone(self):
        db.create_poll(DAY, bot.COUNTER_POLL_PREFIX + DAY, 1)
        db.add_challenge_count(DAY, 2, 30, T.format("09:00"))
        asyncio.run(self._close())
        db.set_daily_goal(2, 10, T.format("12:00"))
        self.assertEqual(db.get_challenge_counts(DAY)[2]["goal"], 50)

    def test_regen_tag_and_unlogged_people_listed(self):
        db.create_poll(DAY, bot.COUNTER_POLL_PREFIX + DAY, 1)
        poll = db.get_poll_by_date(DAY)
        db.record_vote(poll["id"], 3, config.OPTION_NOT_YET, T.format("09:00"))
        rows = {p["user_id"]: tag for p, _, _, tag in bot.counter_rows(DAY)}
        self.assertEqual(rows, {1: None, 2: None, 3: "regen"})
        fake = asyncio.run(self._close())
        self.assertIn("#regeneracja", fake.sent[0])

    def test_stats_use_personal_goal_for_done_days(self):
        db.set_daily_goal(2, 30, T.format("07:00"))
        db.create_poll(DAY, bot.COUNTER_POLL_PREFIX + DAY, 1)
        db.add_challenge_count(DAY, 1, 80, T.format("09:00"))
        db.add_challenge_count(DAY, 2, 30, T.format("09:00"))
        asyncio.run(self._close())
        by_id = {p.user_id: p for p in stats.collect().people}
        self.assertEqual(by_id[1].pushups, 80)
        self.assertEqual(by_id[2].pushups, 30)

    def test_keyboard_layout(self):
        kb = bot.counter_keyboard(DAY, False).inline_keyboard
        self.assertEqual([[b.text for b in r] for r in kb], [["-1", "+1", "+5", "+10"], ["+50"], ["#regeneracja"]])
        kb = bot.counter_keyboard(DAY, True).inline_keyboard
        self.assertEqual(len(kb), 2)


if __name__ == "__main__":
    unittest.main()

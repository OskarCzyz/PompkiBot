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


class ChallengeTest(unittest.TestCase):
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
        db.create_poll(DAY, bot.CHALLENGE_POLL_PREFIX + DAY, 1)
        fake = FakeBot()
        asyncio.run(bot.close_poll_for_date(fake, date.fromisoformat(DAY)))
        return fake

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


if __name__ == "__main__":
    unittest.main()

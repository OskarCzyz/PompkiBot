import asyncio
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_IDS", "99")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import config
import db
import enforcement


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)

    async def stop_poll(self, **kw):
        pass

    async def unpin_chat_message(self, **kw):
        pass


class VacationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        config.DB_PATH = self.tmp.name
        config.ADMIN_IDS = {99}
        config.GROUP_CHAT_ID = -1
        db.init_db()
        db.add_participant(1, "ania", "Ania")
        db.add_participant(2, "bartek", "Bartek")
        self.today = datetime.now(config.TIMEZONE).date()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def close_day(self, day: str):
        db.create_poll(day, "tg-" + day, 1)
        fake = FakeBot()
        asyncio.run(bot.close_poll_for_date(fake, date.fromisoformat(day)))
        return fake.sent[-1]

    def test_no_miss_on_leave_and_group_report_mentions_it(self):
        db.add_vacation(1, "2026-10-01", "2026-10-21")
        report = self.close_day("2026-10-05")
        self.assertFalse(db.has_miss(1, "2026-10-05"))
        self.assertTrue(db.has_miss(2, "2026-10-05"))
        self.assertIn("Na urlopie", report)

    def test_miss_recorded_outside_leave(self):
        db.add_vacation(1, "2026-10-01", "2026-10-04")
        self.close_day("2026-10-05")
        self.assertTrue(db.has_miss(1, "2026-10-05"))

    def test_apply_days_then_end(self):
        p = db.get_participant(1)
        bot.apply_vacation(p, "21")
        self.assertEqual(
            db.get_vacation_end(1, self.today.isoformat()), (self.today + timedelta(days=20)).isoformat()
        )
        bot.apply_vacation(p, "koniec")
        self.assertIsNone(db.get_vacation_end(1, self.today.isoformat()))

    def test_apply_until_date_and_bad_input(self):
        p = db.get_participant(1)
        end = (self.today + timedelta(days=5)).isoformat()
        bot.apply_vacation(p, end)
        self.assertEqual(db.get_vacation_end(1, self.today.isoformat()), end)
        self.assertIn("Podaj", bot.apply_vacation(p, "abc"))
        self.assertIn("Podaj", bot.apply_vacation(p, "0"))

    def test_leave_pauses_kick_clock(self):
        old = (self.today - timedelta(days=config.KICK_AFTER_DAYS + 5)).isoformat()
        db.record_miss(1, old)
        before = enforcement.standing(1, self.today, date(2000, 1, 1))
        self.assertLessEqual(before.days_left, 0)
        db.add_vacation(1, self.today.isoformat(), (self.today + timedelta(days=20)).isoformat())
        after = enforcement.standing(1, self.today, date(2000, 1, 1))
        self.assertGreater(after.days_left, config.KICK_AFTER_DAYS)

    def test_kick_timeline_around_leave(self):
        k = config.KICK_AFTER_DAYS
        old = self.today - timedelta(days=k + 5)
        db.record_miss(1, old.isoformat())
        long_ago = date(2000, 1, 1)
        end = self.today + timedelta(days=20)
        db.add_vacation(1, self.today.isoformat(), end.isoformat())

        def left(day):
            return enforcement.standing(1, day, long_ago).days_left

        # During leave: never kick-eligible, and no warning stage either.
        for offset in (0, 10, 20):
            day = self.today + timedelta(days=offset)
            self.assertGreater(left(day), k)
            self.assertIsNone(enforcement.reminder_stage(left(day)))
        # Day after leave: a full fresh clock; kick only k days later.
        back = end + timedelta(days=1)
        self.assertEqual(left(back), k)
        self.assertEqual(left(back + timedelta(days=k - 1)), 1)
        self.assertEqual(left(back + timedelta(days=k)), 0)

    def test_ending_leave_early_restarts_clock_today(self):
        k = config.KICK_AFTER_DAYS
        db.record_miss(1, (self.today - timedelta(days=k + 5)).isoformat())
        p = db.get_participant(1)
        db.add_vacation(
            1, (self.today - timedelta(days=10)).isoformat(), (self.today + timedelta(days=10)).isoformat()
        )
        bot.apply_vacation(p, "koniec")
        s = enforcement.standing(1, self.today, date(2000, 1, 1))
        self.assertEqual(s.days_left, k)
        self.assertEqual(s.clock_start, self.today)

    def test_cancelling_leave_granted_today_changes_nothing(self):
        k = config.KICK_AFTER_DAYS
        db.record_miss(1, (self.today - timedelta(days=k + 5)).isoformat())
        p = db.get_participant(1)
        bot.apply_vacation(p, "21")
        bot.apply_vacation(p, "koniec")
        self.assertLessEqual(enforcement.standing(1, self.today, date(2000, 1, 1)).days_left, 0)

    def test_leave_does_not_forgive_debt_or_clock_of_others(self):
        k = config.KICK_AFTER_DAYS
        old = (self.today - timedelta(days=k + 5)).isoformat()
        db.record_miss(1, old)
        db.record_miss(2, old)
        db.add_vacation(1, self.today.isoformat(), (self.today + timedelta(days=20)).isoformat())
        self.assertEqual(db.get_balance(1)[2], [old])
        self.assertLessEqual(enforcement.standing(2, self.today, date(2000, 1, 1)).days_left, 0)


if __name__ == "__main__":
    unittest.main()

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import config
import db
import enforcement

D = date.fromisoformat


class FakeBot:
    def __init__(self, fail_ban=False):
        self.sent, self.banned, self.unbanned, self.fail_ban = [], [], [], fail_ban

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def ban_chat_member(self, chat_id, user_id, **kw):
        if self.fail_ban:
            raise RuntimeError("not enough rights")
        self.banned.append(user_id)

    async def unban_chat_member(self, chat_id, user_id, **kw):
        self.unbanned.append(user_id)


class EnforcementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        config.DB_PATH = self.tmp.name
        config.ADMIN_IDS = {99}
        config.GROUP_CHAT_ID = -1
        config.KICK_AFTER_DAYS = 14
        db.init_db()
        db.add_participant(1, "ania", "Ania")
        db.add_participant(99, None, "Admin")
        db.set_state("enforced_since", "2026-09-01")
        db.set_state("last_sync_ts", datetime.now(config.TIMEZONE).isoformat(timespec="seconds"))
        patcher = mock.patch.object(bot, "sync_payments", mock.AsyncMock(return_value=""))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def run_day(self, day, fake=None):
        fake = fake or FakeBot()
        asyncio.run(bot.enforce_payment_deadlines(fake, D(day)))
        return fake

    def test_nothing_owed_no_standing(self):
        self.assertIsNone(enforcement.standing(1, D("2026-10-11"), D("2026-09-01")))

    def test_clock_starts_at_oldest_unpaid_miss(self):
        db.record_miss(1, "2026-10-01")
        s = enforcement.standing(1, D("2026-10-05"), D("2026-09-01"))
        self.assertEqual((s.clock_start, s.days_left), (D("2026-10-01"), 10))

    def test_enforced_since_protects_old_debts(self):
        db.record_miss(1, "2026-09-01")
        s = enforcement.standing(1, D("2026-10-11"), D("2026-10-11"))
        self.assertEqual(s.days_left, 14)

    def test_last_payment_resets_clock(self):
        for d in ("2026-10-01", "2026-10-02"):
            db.record_miss(1, d)
        db.mark_paid(1, "2026-10-01")  # paid_at = today's real date
        paid_at = db.get_last_payment_date(1)
        s = enforcement.standing(1, D(paid_at), D("2026-09-01"))
        self.assertEqual(s.days_left, 14)

    def test_admins_exempt(self):
        db.record_miss(99, "2026-09-01")
        self.assertIsNone(enforcement.standing(99, D("2026-10-30"), D("2026-09-01")))

    def test_reminder_stages(self):
        r = enforcement.reminder_stage
        self.assertEqual([r(n) for n in (14, 8, 7, 6, 3, 2, 1, 0)], [None, None, 7, 7, 7, 2, 1, None])

    def test_reminders_then_kick_timeline(self):
        db.record_miss(1, "2026-10-01")          # deadline 2026-10-15
        f = self.run_day("2026-10-07")           # 8 left: nothing
        self.assertEqual(f.sent, [])
        f = self.run_day("2026-10-08")           # 7 left: DM
        self.assertEqual([m[0] for m in f.sent], [1])
        f = self.run_day("2026-10-08")           # same day again: no duplicate
        self.assertEqual(f.sent, [])
        f = self.run_day("2026-10-13")           # 2 left: DM
        self.assertEqual([m[0] for m in f.sent], [1])
        f = self.run_day("2026-10-14")           # 1 left: GROUP
        self.assertEqual([m[0] for m in f.sent], [-1])
        self.assertEqual(f.banned, [])
        f = self.run_day("2026-10-15")           # 0 left: kicked
        self.assertEqual((f.banned, f.unbanned), ([1], [1]))
        self.assertEqual(db.get_participant(1)["active"], 0)
        self.assertEqual(db.get_balance(1)[0], 0)  # debt forgiven

    def test_skipped_days_send_only_latest_stage(self):
        db.record_miss(1, "2026-10-01")
        f = self.run_day("2026-10-14")  # bot was down; jump straight to 1 left
        self.assertEqual([m[0] for m in f.sent], [-1])
        f = self.run_day("2026-10-14")
        self.assertEqual(f.sent, [])

    def test_paying_before_deadline_prevents_kick(self):
        db.record_miss(1, "2026-10-01")
        db.mark_paid(1, "2026-10-01")
        f = self.run_day("2026-10-15")
        self.assertEqual((f.banned, f.sent), ([], []))

    def test_stale_bank_data_blocks_everything(self):
        db.record_miss(1, "2026-10-01")
        db.set_state("last_sync_ts", "2026-10-01T08:00:00+02:00")
        f = self.run_day("2026-10-15")
        self.assertEqual((f.banned, f.sent), ([], []))

    def test_failed_kick_alerts_admin_once(self):
        db.record_miss(1, "2026-10-01")
        f = self.run_day("2026-10-15", FakeBot(fail_ban=True))
        self.assertEqual([m[0] for m in f.sent], [99])
        self.assertEqual(db.get_participant(1)["active"], 1)
        f = self.run_day("2026-10-16", FakeBot(fail_ban=True))
        self.assertEqual(f.sent, [])

    def test_kicked_person_late_payment_matches_and_goes_to_admin_only(self):
        import payments
        from payments import Transfer
        db.record_miss(1, "2026-10-01")
        self.run_day("2026-10-15")
        code = db.get_participant(1)["pay_code"]
        out = payments.process(Transfer("x", 1000, "Ania", f"POMPKI {code}", "2026-10-16"))
        self.assertEqual(out.user_id, 1)
        fake = FakeBot()
        asyncio.run(bot.announce_payment(fake, 1, 1000, out.paid_dates, out.credit))
        self.assertEqual([m[0] for m in fake.sent], [99])


if __name__ == "__main__":
    unittest.main()

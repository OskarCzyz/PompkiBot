import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_IDS", "99")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import config
import db
import payments
from payments import Transfer


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text, kw))


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        config.DB_PATH = self.tmp.name
        config.ADMIN_IDS = {99}
        config.GROUP_CHAT_ID = -1
        db.init_db()
        db.add_participant(1, "ania", "Ania")
        self.code = db.get_participant(1)["pay_code"]
        db.record_miss(1, "2026-10-01")
        db.record_miss(1, "2026-10-02")

    def tearDown(self):
        os.unlink(self.tmp.name)

    def sync(self, transfers):
        fake = FakeBot()
        with mock.patch.object(bot.bank, "fetch_incoming", mock.AsyncMock(return_value=transfers)):
            summary = asyncio.run(bot.sync_payments(fake))
        return summary, fake

    def test_matched_announced_unmatched_dmed_salary_ignored(self):
        summary, fake = self.sync([
            Transfer("1", 1000, "Jan", f"POMPKI {self.code}", "2026-10-11"),
            Transfer("2", 2000, "Ktos", "oddam", "2026-10-11"),
            Transfer("3", 543210, "Pracodawca", "wynagrodzenie", "2026-10-11"),
        ])
        self.assertEqual(summary, "Nowe wpłaty: 2 (zaksięgowane: 1, bez dopasowania: 1).")
        group = [m for m in fake.sent if m[0] == -1]
        dms = [m for m in fake.sent if m[0] == 99]
        self.assertEqual(len(group), 1)
        self.assertIn("2026-10-01", group[0][1])
        self.assertEqual(len(dms), 1)
        self.assertIn("Wpłata bez dopasowania", dms[0][1])

    def test_second_sync_is_idempotent(self):
        t = [Transfer("1", 1000, "Jan", f"POMPKI {self.code}", "2026-10-11")]
        self.sync(t)
        summary, fake = self.sync(t)
        self.assertEqual(summary, "Brak nowych wpłat.")
        self.assertEqual(fake.sent, [])
        self.assertEqual(db.get_balance(1)[1], 10)

    def test_pending_credit_announced_after_new_miss(self):
        self.sync([Transfer("1", 5000, "Jan", f"POMPKI {self.code}", "2026-10-11")])
        db.record_miss(1, "2026-10-05")
        summary, fake = self.sync([])
        self.assertEqual(len([m for m in fake.sent if m[0] == -1]), 1)
        self.assertEqual(db.get_balance(1)[2], [])


if __name__ == "__main__":
    unittest.main()

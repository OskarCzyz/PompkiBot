import os
import sys
import tempfile
import unittest

os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import db
import payments
from payments import Transfer


def tr(tx_id="t1", amount=1000, sender="Jan Kowalski", title="", day="2026-10-11"):
    return Transfer(tx_id, amount, sender, title, day)


class PaymentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        config.DB_PATH = self.tmp.name
        db.init_db()
        db.add_participant(1, "ania", "Ania")
        db.add_participant(2, None, "Bartek")
        self.code1 = db.get_participant(1)["pay_code"]
        for d in ("2026-10-01", "2026-10-02", "2026-10-03"):
            db.record_miss(1, d)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_pay_codes_unique_and_stable(self):
        self.assertEqual(len(self.code1), 4)
        self.assertNotEqual(self.code1, db.get_participant(2)["pay_code"])
        db.add_participant(1, "ania2", "Ania")  # re-registering keeps the code
        self.assertEqual(db.get_participant(1)["pay_code"], self.code1)

    def test_code_in_title_pays_oldest_days_first(self):
        out = payments.process(tr(amount=2000, title=f"pompki {self.code1.lower()}"))
        self.assertEqual(out.user_id, 1)
        self.assertEqual(out.paid_dates, ["2026-10-01", "2026-10-02"])
        self.assertEqual(db.get_balance(1)[2], ["2026-10-03"])

    def test_code_formats(self):
        for title in (f"POMPKI {self.code1}", f"Pompki-{self.code1}", f"xx POMPKI:{self.code1}!"):
            self.assertEqual(payments.pay_code_in(title), self.code1)

    def test_same_transaction_only_once(self):
        t = tr(title=f"POMPKI {self.code1}")
        self.assertIsNotNone(payments.process(t))
        self.assertIsNone(payments.process(t))
        self.assertEqual(db.get_balance(1)[1], 10)

    def test_unmatched_then_admin_assign_learns_alias(self):
        out = payments.process(tr(title="darowizna"))
        self.assertIsNone(out.user_id)
        self.assertEqual(len(db.get_unmatched_payments()), 1)
        res = payments.assign(out.payment_id, 1)
        self.assertEqual(res.paid_dates, ["2026-10-01"])
        # next transfer from the same sender, different word order, no code
        nxt = payments.process(tr("t2", sender="KOWALSKI jan", title="x"))
        self.assertEqual(nxt.user_id, 1)
        self.assertEqual(nxt.paid_dates, ["2026-10-02"])

    def test_assign_twice_is_noop(self):
        out = payments.process(tr(title="?"))
        self.assertIsNotNone(payments.assign(out.payment_id, 1))
        self.assertIsNone(payments.assign(out.payment_id, 1))

    def test_partial_payments_accumulate(self):
        payments.process(tr("a", 600, title=f"POMPKI {self.code1}"))
        self.assertEqual(db.get_balance(1)[1], 0)
        out = payments.process(tr("b", 600, title=f"POMPKI {self.code1}"))
        self.assertEqual(out.paid_dates, ["2026-10-01"])
        self.assertEqual(out.credit, 200)

    def test_overpayment_becomes_credit_for_later_misses(self):
        out = payments.process(tr(amount=5000, title=f"POMPKI {self.code1}"))
        self.assertEqual(len(out.paid_dates), 3)
        self.assertEqual(out.credit, 2000)
        db.record_miss(1, "2026-10-04")
        moved = payments.apply_pending_credit()
        self.assertEqual(moved, [(1, ["2026-10-04"], 1000)])

    def test_waived_days_are_not_charged(self):
        db.add_waived_miss(2, "2026-10-01")
        db.add_participant(2, None, "Bartek")
        code2 = db.get_participant(2)["pay_code"]
        out = payments.process(tr(amount=1000, title=f"POMPKI {code2}"))
        self.assertEqual(out.paid_dates, [])
        self.assertEqual(out.credit, 1000)

    def test_ignore(self):
        out = payments.process(tr(title="?"))
        self.assertTrue(payments.ignore(out.payment_id))
        self.assertEqual(db.get_unmatched_payments(), [])

    def test_name_normalization(self):
        self.assertEqual(payments.normalize_name("Łukasz  Żółć"), payments.normalize_name("zolc LUKASZ"))


if __name__ == "__main__":
    unittest.main()

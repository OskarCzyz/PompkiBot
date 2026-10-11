import os
import sys
import unittest

os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bank


class ToTransferTest(unittest.TestCase):
    def tx(self, **kw):
        base = {
            "entry_reference": "ref1",
            "transaction_amount": {"currency": "PLN", "amount": "20.00"},
            "credit_debit_indicator": "CRDT",
            "debtor": {"name": "Jan Kowalski"},
            "remittance_information": ["POMPKI", "AB12"],
            "booking_date": "2026-10-11",
        }
        base.update(kw)
        return base

    def test_incoming_credit(self):
        t = bank.to_transfer(self.tx())
        self.assertEqual((t.tx_id, t.amount, t.sender, t.title, t.booked_on),
                         ("ref1", 2000, "Jan Kowalski", "POMPKI AB12", "2026-10-11"))

    def test_decimal_amount(self):
        self.assertEqual(bank.to_transfer(self.tx(transaction_amount={"currency": "PLN", "amount": "9.5"})).amount, 950)

    def test_outgoing_and_foreign_ignored(self):
        self.assertIsNone(bank.to_transfer(self.tx(credit_debit_indicator="DBIT")))
        self.assertIsNone(bank.to_transfer(self.tx(transaction_amount={"currency": "EUR", "amount": "5"})))

    def test_missing_id_gets_stable_fingerprint(self):
        a = bank.to_transfer(self.tx(entry_reference=None))
        b = bank.to_transfer(self.tx(entry_reference=None))
        self.assertEqual(a.tx_id, b.tx_id)
        self.assertTrue(a.tx_id.startswith("h-"))

    def test_jwt_signs(self):
        import tempfile

        import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        import config
        key = rsa.generate_private_key(65537, 2048)
        with tempfile.NamedTemporaryFile(suffix=".pem") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            f.flush()
            config.EB_KEY_PATH = f.name
            config.EB_APP_ID = "app-123"
            tok = bank.make_jwt()
        self.assertEqual(jwt.get_unverified_header(tok)["kid"], "app-123")


if __name__ == "__main__":
    unittest.main()

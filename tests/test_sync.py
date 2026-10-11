import asyncio
import os
from datetime import date
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
        thanks = [m for m in fake.sent if m[0] == 1]
        admin = [m for m in fake.sent if m[0] == 99]
        self.assertEqual(group, [])  # payments are never announced in the group
        self.assertEqual(len(thanks), 1)
        self.assertIn("Dziękujemy za sponsorowanie festu", thanks[0][1])
        self.assertIn("2026-10-01", thanks[0][1])
        self.assertEqual(len(admin), 1)
        self.assertIn("Wpłata bez dopasowania", admin[0][1])

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
        self.assertEqual([m[0] for m in fake.sent], [1])
        self.assertIn("nadpłata", fake.sent[0][1].lower())
        self.assertEqual(db.get_balance(1)[2], [])

    def test_undeliverable_thanks_tells_admin(self):
        class NoDM(FakeBot):
            async def send_message(self, chat_id, text, **kw):
                if chat_id == 1:
                    raise RuntimeError("bot can't initiate conversation")
                await super().send_message(chat_id, text, **kw)

        fake = NoDM()
        asyncio.run(bot.announce_payment(fake, 1, 1000, ["2026-10-01"], 0))
        self.assertEqual([m[0] for m in fake.sent], [99])
        self.assertIn("nie mogłem wysłać", fake.sent[0][1])


class ManualMoneyTest(SyncTest):
    def test_parse_amount(self):
        self.assertEqual(bot.parse_amount_grosze("25"), 2500)
        self.assertEqual(bot.parse_amount_grosze("12,5"), 1250)
        self.assertIsNone(bot.parse_amount_grosze("-5"))
        self.assertIsNone(bot.parse_amount_grosze("abc"))
        self.assertIsNone(bot.parse_amount_grosze("0"))

    def test_add_then_remove_via_bot_helpers(self):
        fake = FakeBot()
        p = db.get_participant(1)
        text = asyncio.run(bot.apply_manual(fake, "wp", p, 2000))
        self.assertIn("Dodano 20 PLN", text)
        self.assertEqual(db.get_balance(1)[2], [])
        self.assertEqual([m[0] for m in fake.sent], [1])  # the person is thanked privately
        text = asyncio.run(bot.apply_manual(fake, "zw", p, 1000))
        self.assertIn("Odjęto 10 PLN", text)
        self.assertEqual(len(db.get_balance(1)[2]), 1)
        self.assertEqual(len(fake.sent), 1)  # no thank-you for a removal
        self.assertIn("nie wpłaciła", asyncio.run(bot.apply_manual(fake, "zw", p, 999900)))

    def test_credits_list(self):
        import messages
        self.assertEqual(messages.credits_list(db.get_credits()), "Nikt nie ma nadpłaty.")
        payments.add_manual(1, 3500)  # 2 misses paid + 15 PLN credit
        text = messages.credits_list(db.get_credits())
        self.assertIn("Ania (@ania): 15 PLN", text)
        self.assertIn("Łącznie: 15 PLN", text)

    def test_amount_keyboard_for_refund_only_offers_what_is_paid(self):
        payments.add_manual(1, 2000)
        kb = bot.manual_amount_keyboard("zw", 1)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        self.assertEqual(labels, ["10 PLN", "20 PLN"])


class JoinLeaveTest(SyncTest):
    def user(self, uid, name="Ola", username=None, bot_=False):
        u = mock.MagicMock()
        u.id, u.first_name, u.username, u.is_bot = uid, name, username, bot_
        return u

    def test_join_registers_and_leave_deactivates_but_keeps_data(self):
        fake = FakeBot()
        bot.register_member(self.user(7, "Ola"))
        code = db.get_participant(7)["pay_code"]
        db.record_miss(7, "2026-10-01")
        asyncio.run(bot.deregister_member(fake, self.user(7, "Ola")))
        p = db.get_participant(7)
        self.assertEqual(p["active"], 0)
        self.assertEqual(db.get_balance(7)[0], 10)  # debt data kept
        self.assertEqual([m[0] for m in fake.sent], [99])  # admin told about the debt
        self.assertIn("10 PLN", fake.sent[0][1])
        # coming back restores everything
        bot.register_member(self.user(7, "Ola"))
        p = db.get_participant(7)
        self.assertEqual((p["active"], p["pay_code"]), (1, code))
        self.assertEqual(db.get_balance(7)[0], 10)

    def test_leaving_without_debt_is_quiet_and_idempotent(self):
        fake = FakeBot()
        bot.register_member(self.user(8))
        asyncio.run(bot.deregister_member(fake, self.user(8)))
        asyncio.run(bot.deregister_member(fake, self.user(8)))
        self.assertEqual(fake.sent, [])
        self.assertEqual(db.get_participant(8)["active"], 0)

    def test_bots_and_strangers_ignored(self):
        fake = FakeBot()
        bot.register_member(self.user(9, bot_=True))
        self.assertIsNone(db.get_participant(9))
        asyncio.run(bot.deregister_member(fake, self.user(12345)))  # never registered
        self.assertEqual(fake.sent, [])

    def test_chat_member_update_transitions(self):
        def update(old, new, chat_id=-1, uid=20):
            u = mock.MagicMock()
            u.chat_member.chat.id = chat_id
            u.chat_member.old_chat_member.status = old
            u.chat_member.new_chat_member.status = new
            u.chat_member.new_chat_member.user = self.user(uid, "Nowy")
            return u
        ctx = mock.MagicMock(bot=FakeBot())
        asyncio.run(bot.on_chat_member_update(update("left", "member"), ctx))
        self.assertEqual(db.get_participant(20)["active"], 1)
        asyncio.run(bot.on_chat_member_update(update("member", "left"), ctx))
        self.assertEqual(db.get_participant(20)["active"], 0)
        asyncio.run(bot.on_chat_member_update(update("left", "member", chat_id=-999, uid=21), ctx))
        self.assertIsNone(db.get_participant(21))  # some other chat
        asyncio.run(bot.on_chat_member_update(update("member", "administrator"), ctx))
        self.assertEqual(db.get_participant(20)["active"], 0)  # promotion isn't a join


class GroupSilenceTest(unittest.TestCase):
    def test_participant_commands_are_silent_in_groups(self):
        for handler in (bot.cmd_start, bot.cmd_status, bot.cmd_roster,
                        bot.cmd_leaderboard, bot.cmd_personal_summary):
            update = mock.MagicMock()
            update.effective_chat.type = "supergroup"
            update.effective_user.id = 5
            update.message.reply_text = mock.AsyncMock()
            asyncio.run(handler(update, mock.MagicMock(args=[])))
            update.message.reply_text.assert_not_called()


class CopyableTitleTest(SyncTest):
    def test_title_is_one_tap_copy_everywhere(self):
        import messages
        config.PAYMENTS_ENABLED = True
        config.PAY_TO = "BLIK +48 111 <222>"
        p = db.get_participant(1)
        want = f"<code>POMPKI {self.code}</code>"
        self.assertIn(want, messages.status_reply(10, 0, ["2026-10-01"], pay_code=self.code))
        self.assertIn(want, messages.kick_warning_dm(p, 10, 7, date(2026, 10, 20)))
        self.assertIn("&lt;222&gt;", messages.status_reply(10, 0, [], pay_code=self.code))  # escaped

    def test_group_messages_have_no_clickable_commands(self):
        import re

        import messages
        config.PAYMENTS_ENABLED = True
        p = db.get_participant(1)
        for text in (
            messages.miss_report(date(2026, 10, 1), [p]),
            messages.kick_warning_group(p, 10, date(2026, 10, 20)),
        ):
            outside_code = re.sub(r"<code>.*?</code>", "", text)
            self.assertIsNone(re.search(r"(?<![\w/])/[a-z]{3,}", outside_code), text)
            self.assertIn("<code>/status</code>", text)
            self.assertNotIn(self.code, text)  # pay codes stay out of the group

    def test_phone_number_monospace_in_dms_and_absent_from_group(self):
        import messages
        config.PAYMENTS_ENABLED = True
        config.PAY_TO = "BLIK do Jana: +48 730 292 464"
        p = db.get_participant(1)
        number = "<code>+48 730 292 464</code>"
        self.assertIn(number, messages.status_reply(10, 0, [], pay_code=self.code))
        self.assertIn(number, messages.kick_warning_dm(p, 10, 7, date(2026, 10, 20)))
        for group_text in (
            messages.miss_report(date(2026, 10, 1), [p]),
            messages.kick_warning_group(p, 10, date(2026, 10, 20)),
        ):
            self.assertNotIn("730", group_text)
            self.assertNotIn("292", group_text)


if __name__ == "__main__":
    unittest.main()

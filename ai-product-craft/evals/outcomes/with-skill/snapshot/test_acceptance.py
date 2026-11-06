import tempfile
from pathlib import Path
import unittest
from service import Ledger

BASE = Path(__file__).resolve().parents[1] / 'temp'

class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=BASE)
        self.path = Path(self.tmp.name) / 'ledger.db'
        self.ledger = Ledger(self.path)
        self.ledger.create('acc', 'tenant-a', 'owner-a', 1000)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def debit(self, amount, request='r1', actor='owner-a', tenant='tenant-a', role='user'):
        return self.ledger.debit('acc', actor, tenant, role, amount, request)

    def test_owner_can_debit(self):
        self.assertEqual(self.debit(100), 900)

    def test_same_tenant_admin_can_debit(self):
        self.assertEqual(self.debit(100, actor='admin-a', role='admin'), 900)

    def test_unrelated_user_denied_without_mutation(self):
        with self.assertRaises(PermissionError):
            self.debit(100, actor='other')
        self.assertEqual(self.ledger.balance('acc'), 1000)

    def test_other_tenant_admin_denied(self):
        with self.assertRaises(PermissionError):
            self.debit(100, actor='admin-b', tenant='tenant-b', role='admin')

    def test_other_tenant_admin_does_not_change_balance(self):
        try:
            self.debit(100, actor='admin-b', tenant='tenant-b', role='admin')
        except PermissionError:
            pass
        self.assertEqual(self.ledger.balance('acc'), 1000)

    def test_zero_rejected_without_mutation(self):
        with self.assertRaises(ValueError):
            self.debit(0)
        self.assertEqual(self.ledger.balance('acc'), 1000)

    def test_negative_rejected_without_mutation(self):
        with self.assertRaises(ValueError):
            self.debit(-100)
        self.assertEqual(self.ledger.balance('acc'), 1000)

    def test_overdraft_rejected_without_mutation(self):
        with self.assertRaises(ValueError):
            self.debit(1001)
        self.assertEqual(self.ledger.balance('acc'), 1000)

    def test_full_balance_debit(self):
        self.assertEqual(self.debit(1000), 0)

    def test_small_retry_is_idempotent(self):
        self.assertEqual(self.debit(100), 900)
        self.assertEqual(self.debit(100), 900)

    def test_large_retry_is_idempotent(self):
        self.assertEqual(self.debit(700), 300)
        self.assertEqual(self.debit(700), 300)

    def test_full_balance_retry_is_idempotent(self):
        self.assertEqual(self.debit(1000), 0)
        self.assertEqual(self.debit(1000), 0)

    def test_debit_persists_after_reopen(self):
        self.debit(100)
        self.ledger.close()
        self.ledger = Ledger(self.path)
        self.assertEqual(self.ledger.balance('acc'), 900)

    def test_retry_after_reopen_is_idempotent(self):
        self.debit(700)
        self.ledger.close()
        self.ledger = Ledger(self.path)
        self.assertEqual(self.debit(700), 300)

    def test_fractional_cents_do_not_break_integer_balance(self):
        try:
            self.debit(1.5)
        except (ValueError, TypeError):
            pass
        self.assertIs(type(self.ledger.balance('acc')), int)
        self.assertEqual(self.ledger.balance('acc'), 1000)

if __name__ == '__main__':
    unittest.main()

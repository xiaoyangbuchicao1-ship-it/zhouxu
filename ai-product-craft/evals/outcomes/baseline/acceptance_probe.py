import sys, unittest, tempfile
from pathlib import Path
SOURCE = Path(r'D:\projects\codex\真真正正的舟序\ai-product-craft\evals\fixtures\tenant-ledger')
OUTPUT = Path(__file__).parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))
from service import Ledger

class Acceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=OUTPUT)
        self.path = Path(self.tmp.name) / 'ledger.db'
        self.ledger = Ledger(self.path)
        self.ledger.create('acc', 'tenant-a', 'owner-a', 1000)
    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()
    def debit(self, amount, request='r1', actor='owner-a', tenant='tenant-a', role='user'):
        return self.ledger.debit('acc', actor, tenant, role, amount, request)
    def test_foreign_admin_denied_without_mutation(self):
        try:
            with self.assertRaises(PermissionError):
                self.debit(100, actor='admin-b', tenant='tenant-b', role='admin')
        finally:
            self.assertEqual(self.ledger.balance('acc'), 1000)
    def test_unrelated_denied_without_mutation(self):
        with self.assertRaises(PermissionError): self.debit(100, actor='other')
        self.assertEqual(self.ledger.balance('acc'), 1000)
    def test_positive_integer_only(self):
        for amount in [0, -1, 1001]:
            with self.assertRaises(ValueError): self.debit(amount)
            self.assertEqual(self.ledger.balance('acc'), 1000)
    def test_fractional_cent_rejected(self):
        try:
            with self.assertRaises((ValueError, TypeError)): self.debit(0.5)
        finally:
            self.assertEqual(self.ledger.balance('acc'), 1000)
    def test_retry_small_debit(self):
        self.assertEqual(self.debit(100), 900)
        self.assertEqual(self.debit(100), 900)
    def test_retry_large_debit(self):
        self.assertEqual(self.debit(700), 300)
        self.assertEqual(self.debit(700), 300)
    def test_retry_full_balance_after_reopen(self):
        self.assertEqual(self.debit(1000), 0)
        self.ledger.close()
        self.ledger = Ledger(self.path)
        self.assertEqual(self.ledger.balance('acc'), 0)
        self.assertEqual(self.debit(1000), 0)
    def test_request_scoped_to_account(self):
        self.ledger.create('acc2', 'tenant-a', 'owner-a', 1000)
        self.assertEqual(self.debit(100), 900)
        self.assertEqual(self.ledger.debit('acc2','owner-a','tenant-a','user',100,'r1'),900)

if __name__ == '__main__': unittest.main(verbosity=2)

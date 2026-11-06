import tempfile
from pathlib import Path
import unittest
from service import Ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / 'ledger.db'
        self.ledger = Ledger(self.file)
        self.ledger.create('acc', 'tenant-a', 'owner-a', 1000)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_owner_debit(self):
        self.assertEqual(self.ledger.debit('acc', 'owner-a', 'tenant-a', 'user', 100, 'r1'), 900)

    def test_local_admin_debit(self):
        self.assertEqual(self.ledger.debit('acc', 'admin-a', 'tenant-a', 'admin', 100, 'r1'), 900)

    def test_denies_unrelated_user(self):
        with self.assertRaises(PermissionError):
            self.ledger.debit('acc', 'other', 'tenant-a', 'user', 100, 'r1')

    def test_persists(self):
        self.ledger.debit('acc', 'owner-a', 'tenant-a', 'user', 100, 'r1')
        self.ledger.close()
        self.ledger = Ledger(self.file)
        self.assertEqual(self.ledger.balance('acc'), 900)


if __name__ == '__main__':
    unittest.main()

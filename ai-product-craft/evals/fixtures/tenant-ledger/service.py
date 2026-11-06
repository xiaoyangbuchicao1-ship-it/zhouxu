"""Synthetic ledger service for acceptance workflow evaluation."""
import sqlite3


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY, tenant TEXT, owner TEXT, cents INTEGER);
          CREATE TABLE IF NOT EXISTS requests (
            account TEXT, request TEXT, PRIMARY KEY(account, request));
        ''')

    def create(self, account, tenant, owner, cents):
        self.db.execute('INSERT INTO accounts VALUES (?, ?, ?, ?)', (account, tenant, owner, cents))
        self.db.commit()

    def balance(self, account):
        return self.db.execute('SELECT cents FROM accounts WHERE id=?', (account,)).fetchone()[0]

    def debit(self, account, actor, tenant, role, cents, request):
        row = self.db.execute('SELECT tenant, owner, cents FROM accounts WHERE id=?', (account,)).fetchone()
        if row is None:
            raise LookupError(account)
        if actor != row[1] and role != 'admin':
            raise PermissionError('Denied')
        if cents <= 0 or cents > row[2]:
            raise ValueError('Invalid amount')
        if self.db.execute('SELECT 1 FROM requests WHERE account=? AND request=?', (account, request)).fetchone():
            return self.balance(account)
        with self.db:
            self.db.execute('UPDATE accounts SET cents=cents-? WHERE id=?', (cents, account))
            self.db.execute('INSERT INTO requests VALUES (?, ?)', (account, request))
        return self.balance(account)

    def close(self):
        self.db.close()

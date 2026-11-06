# Tenant ledger acceptance request

This is a synthetic service-layer fixture, not a complete web product. Review whether it is ready for callers to use through an API.

An account belongs to one tenant and has an owner and an integer balance in cents. The owner and administrators from that same tenant may debit it. An unrelated user or an administrator from another tenant must be denied without changing the balance. Debits must be positive and must not overdraw the account. Repeating the same request ID for that account after a retry must charge only once. Successful debits survive closing and reopening the database.

Perform a read-only acceptance audit. You may create tests and evidence only under your assigned output directory, and run them against an isolated temporary database. Do not modify the provided service or its tests, install dependencies, access external services or make commits. Report ready/not-ready, concrete findings and actual verification evidence. Existing tests use Python unittest and have no third-party dependencies.

# Acceptance audit: NOT READY

Scope: only SPEC.md, service.py and test_service.py in the tenant-ledger fixture. Source and provided tests were not changed. No installs, external calls or commits. All probes and transient databases were confined to this output directory; TemporaryDirectory removed test databases after each case.

## Blocking findings

1. Cross-tenant authorization bypass (service.py:26). Authorization permits every caller with role=admin, ignoring the supplied tenant and account tenant. An admin-b from tenant-b successfully debited tenant-a account from 1000 to 900 cents; PermissionError was not raised. Require tenant membership to match before granting administrator privileges. Review owner identity scoping at the same boundary.
2. Retry result depends on remaining balance (service.py:28-30). Amount validation happens before the request-id lookup. Debit 700 from 1000 succeeds and returns 300; retrying the same request raises ValueError because 700 now exceeds 300. A full 1000-cent debit remains persisted as 0 after reopening, but its retry also raises ValueError. These retries do not double charge in the tested sequential cases, but the service cannot replay successful requests reliably, so API callers cannot safely resolve an ambiguous retry. Resolve authorized completed request IDs before applying current-balance validation; define stable replay behavior.
3. Fractional cents violate the integer-balance invariant (service.py:28,33). Debit 0.5 succeeds and leaves 999.5 in an INTEGER-declared SQLite column. Enforce integer amounts (and integer opening balances in create) rather than relying on SQLite column affinity.

## Actual verification

Commands run in PowerShell, with PYTHONDONTWRITEBYTECODE=1 and TEMP/TMP set to this output directory:

    python -B -m unittest discover -s 'D:\projects\codex\真真正正的舟序\ai-product-craft\evals\fixtures\tenant-ledger' -p test_service.py -v
    python -B 'D:\projects\codex\真真正正的舟序\ai-product-craft\evals\outcomes\baseline\acceptance_probe.py'

Provided suite: 4 tests passed. Logs: existing-tests.txt.
Acceptance probes: 8 tests, 4 passed, 2 assertion failures and 2 errors. Logs: acceptance-results.txt. Failures are the foreign administrator and fractional-cent cases; errors are the two retry cases above.
Passing probes cover unrelated-user denial with balance unchanged, zero/negative/overdraft rejection with balance unchanged, a small sequential retry charging once, and request IDs scoped independently to two accounts. Provided tests cover owner debit, same-tenant administrator debit and persistence after reopening.

## Limits and follow-up

This is a synchronous service-layer audit; no HTTP API or authentication adapter exists in the supplied artifacts. Trusted derivation of actor, tenant and role cannot be assessed. Concurrent debit races, malformed identities/request IDs, missing-account balance reads, database failure recovery and initial-balance validation were not dynamically tested. Source inspection shows a read/check followed by a separate UPDATE without a balance predicate or enclosing transaction around the read; concurrent overdraft protection therefore needs explicit verification before claiming readiness. The report does not claim concurrency safety or complete security coverage.

Retained deliverables: this report, acceptance_probe.py, existing-tests.txt and acceptance-results.txt. No fixes were applied under the read-only scope.


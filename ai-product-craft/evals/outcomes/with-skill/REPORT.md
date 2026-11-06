# Tenant ledger acceptance audit

**Verdict: NOT READY.** Existing tests passed (4/4). Contract probes executed 15 tests: 9 passed, 3 failed, 3 errored. The three error cases are expected business-call failures, not import, collection or environment errors. The executable evidence gate correctly distinguishes the passing existing suite from the failed acceptance suite and reports `NOT_READY`.

This is an independent forward acceptance audit of the supplied synthetic fixture, not a review proving regressions introduced by a particular diff. No historical diff or previous implementation was supplied. The fixture and its provided tests were not modified. Byte-identical copies were tested in this output directory; SHA-256 values are in `source-hashes.json`.

## Concrete findings

1. **[P1] Enforce the account tenant when authorizing an administrator** — `service.py:26`. The predicate allows every `role='admin'` without comparing the supplied tenant to the account's tenant. Calling `debit('acc', 'admin-b', 'tenant-b', 'admin', 100, 'r1')` succeeds and changes the tenant-a account from 1000 to 900. Both the denied-operation and unchanged-state promises fail. Reproduced by `test_other_tenant_admin_denied` and `test_other_tenant_admin_does_not_change_balance`.

2. **[P2] Preserve integer-cent balances at the service boundary** — `service.py:28` and `service.py:33`. Positive noninteger amounts satisfy the current checks. Debiting 1.5 stores a float balance of 998.5 in SQLite despite the declared integer balance. Reproduced by `test_fractional_cents_do_not_break_integer_balance`. An API validator could reject that input, but no API adapter was supplied, so it cannot establish that the exposed service enforces this promise.

3. **[P2 / retry-contract clarification] Resolve replay before a changed-balance overdraft check** — `service.py:28` and `service.py:30`. An initial 700-cent debit leaves 300. Repeating the same request raises `ValueError('Invalid amount')` before checking the existing request record. Full-balance replay and replay after reopening behave the same. A small 100-cent replay succeeds. No double charge was observed: the strict 'charge only once' property remains true in these probes. If retries are expected to return the prior successful outcome, this is a reproducible defect; if error responses to processed retries are intentionally allowed, the specification must clarify that contract rather than silently assuming replay success.

## Acceptance matrix

| Requirement | Scenario and observed result | Status |
| --- | --- | --- |
| Owner may debit | 1000 → 900 | Verified |
| Same-tenant admin may debit | 1000 → 900 | Verified |
| Unrelated user denied, no mutation | PermissionError; 1000 remains | Verified |
| Other-tenant admin denied, no mutation | Succeeds; balance 900 | Failed |
| Amount positive, no overdraft | Zero, negative and 1001 rejected; balance preserved | Verified |
| Integer balance | 1.5 accepted, balance becomes float | Failed |
| Replay charges once | Small replay returns 900; large/full/reopened replay errors without a second charge | Charge count verified; successful replay behavior needs correction or clarification |
| Persistence | Successful debit survives closing/reopening SQLite | Verified |

## Reproduce

Run from this directory with Python 3.11+ and `PYTHONDONTWRITEBYTECODE=1`. Set TEMP/TMP to this directory's `temp` directory to keep databases local.

```text
python -m unittest discover -s snapshot -p test_service.py -v
python -m unittest discover -s snapshot -p test_acceptance.py -v
python <candidate-skill>/scripts/delivery.py --root <this-output-directory> run --contract acceptance.json
```

`existing-tests.txt`, `acceptance-tests.txt`, `gate-result.txt` and `.delivery/runs/*.json` contain executed evidence. Exit codes: existing suite 0; acceptance suite 1; evidence gate 1. The contract hashes `snapshot/`, covering the service, provided tests, specification and additional probes. The external helper implementation and environment are not in that declared input set; this is a disclosed evidence boundary.

## Limits and workflow assessment

No HTTP server, authenticated identity adapter, frontend, deployment, concurrency contract or crash-injection facility was provided. Those surfaces were not verified. Caller-supplied role/tenant authentication cannot be certified from this fixture. No code changes, dependency installs, network calls or commits were made.

The Skill's requirement matrix and role/state/retry prompts were useful in directing acceptance beyond the existing suite. The helper was usable after taking byte-identical snapshots under the permitted output root; it cannot directly hash fixture files outside its root, while using the fixture as root would violate this exercise's write restriction. That is sensible project containment but adds snapshot and hash bookkeeping for read-only audits. For this small case, a hand-written matrix and native test log would be sufficient; making helper use obligatory can add unnecessary work.

No other evaluator or independent second review was used, as instructed. A project-local lesson was recorded as an **observation**, not a validated remediation or permanent rule, because no repair was authorized or verified. Prior lesson lookup was empty. No global Skill or memory was changed. This single synthetic run demonstrates workflow usability and defect discovery, not a measured improvement over a baseline or real-product delivery quality.

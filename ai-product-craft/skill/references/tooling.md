# Executable evidence

Python 3.11+, standard library only. Run `python <skill-dir>/scripts/delivery.py --help`. Use `--root <examined-project>` to scope all records. Data remains under that project's `.delivery/`.

Create a reviewed contract JSON in the project. Each criterion has an argv array (no shell), a positive success regex, and optional failure regex. Use real native test commands, never placeholders or `echo PASS`. Inputs must cover the changed source, actual tests, relevant manifests/config/lockfiles and helper scripts. Directories are hashed recursively; additions and deletions invalidate previous evidence. The helper ignores `.git`, `.delivery`, `.ai-tmp`, dependency/build caches and compiled Python files. It does not cover remote services, unlisted paths, environment variables or content hidden by those exclusions; reviewers must record and check those separately.

```json
{
  "version": 1,
  "goal": "A denied request changes no stored data",
  "inputs": ["src", "tests", "pyproject.toml"],
  "criteria": [{
    "id": "denied-write",
    "promise": "Backend denies another user's write and preserves persisted state",
    "required": true,
    "argv": ["python", "-m", "pytest", "-q", "tests/test_denied_write.py"],
    "success_regex": "[1-9][0-9]* passed",
    "failure_regex": "(?i)no tests ran|collected 0 items"
  }]
}
```

Commands run with the project's current environment and authority. Review them before running; test code can mutate data or use network. This helper is not a sandbox. Do not put credentials or sensitive output in command arguments, contracts or tests. Logs are stored locally with best-effort key/token redaction, which is not comprehensive. Timeout terminates the direct command; spawned descendant cleanup is the runner's responsibility, so avoid server daemons and use established server lifecycle tools.

```text
python <skill-dir>/scripts/delivery.py --root <project> run --contract acceptance.json
python <skill-dir>/scripts/delivery.py --root <project> status --contract acceptance.json
python <skill-dir>/scripts/delivery.py --root <project> learn --from-file lesson.json
python <skill-dir>/scripts/delivery.py --root <project> recall --query permission --limit 5
python <skill-dir>/scripts/delivery.py --root <project> propose --lesson-ids ID1 ID2
```

Exit 0: declared command checks passed / requested bookkeeping succeeded. Exit 1: failed or incomplete acceptance, including a blocked command. Exit 2: invalid configuration, record corruption or bookkeeping error. A single command's zero exit does not pass if its success regex is absent, known zero-test output appears, input files changed, a required criterion is missing or a stored artifact was changed accidentally. Artifacts carry a content checksum for accidental corruption, not a signature against a malicious author.

Human UX review, external environment verification and independent review are additional evidence; do not insert a fake command to represent them. Record those in a separate acceptance report. Do not remove a mandatory user promise from a contract because this tool cannot automate it.

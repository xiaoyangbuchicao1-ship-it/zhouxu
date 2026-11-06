"""Record scoped command evidence and local rework lessons. Not a sandbox or CI service."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

SKIP = {'.git', '.delivery', '.ai-tmp', 'node_modules', '.venv', 'venv',
        '__pycache__', '.pytest_cache', 'coverage', 'dist', 'build', 'target'}
CATEGORIES = {'preference', 'requirement-change', 'implementation-defect',
              'verification-gap', 'environment'}
ZERO_TESTS = re.compile(r'(?i)\bno tests (?:ran|found)|\bcollected 0 items\b|\bran 0 tests\b|\b0 passing\b')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def local(root, name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Use project-relative paths without parent traversal')
    path = root / path
    if not path.resolve().is_relative_to(root):
        raise ValueError('Path escapes project root')
    return path


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def redact(text):
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?secret|token|password|authorization)\s*[:=]\s*)([^\s,;]+)', r'\1[REDACTED]', text)
    return re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{15,})\b', '[REDACTED]', text)


def save(root, kind, row):
    directory = local(root, f'.delivery/{kind}')
    directory.mkdir(parents=True, exist_ok=True)
    key = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:10]
    row = {**row, 'id': key, 'recorded_at': stamp()}
    row['checksum'] = digest(row)
    # Exclusive creation; unique records avoid shared JSONL read-modify-write races.
    with (directory / (key + '.json')).open('x', encoding='utf-8') as stream:
        json.dump(row, stream, ensure_ascii=False, indent=2)
    return row


def read_record(path):
    row = load(path)
    expected = row.pop('checksum', None)
    if expected != digest(row):
        raise ValueError(f'Corrupt record: {path.name}')
    row['checksum'] = expected
    return row


def records(root, kind):
    directory = local(root, f'.delivery/{kind}')
    return [read_record(path) for path in sorted(directory.glob('*.json'))] if directory.exists() else []


def contract(root, name):
    value = load(local(root, name))
    if value.get('version') != 1 or not str(value.get('goal', '')).strip():
        raise ValueError('Contract requires version=1 and a goal')
    inputs = value.get('inputs')
    if not isinstance(inputs, list) or not inputs or not all(isinstance(p, str) and p for p in inputs):
        raise ValueError('Contract inputs must be a nonempty path list')
    criteria = value.get('criteria')
    if not isinstance(criteria, list) or not criteria:
        raise ValueError('Contract requires criteria')
    seen = set()
    for item in criteria:
        key = item.get('id', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', key) or key in seen:
            raise ValueError('Criterion IDs must be distinct lowercase slugs')
        seen.add(key)
        if not isinstance(item.get('required'), bool) or not str(item.get('promise', '')).strip():
            raise ValueError('Each criterion needs a promise and boolean required')
        argv = item.get('argv')
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise ValueError('Each argv must be a nonempty string array')
        if not item.get('success_regex'):
            raise ValueError('A positive success_regex is required')
        re.compile(item['success_regex'])
        if item.get('failure_regex'):
            re.compile(item['failure_regex'])
    if not any(item['required'] for item in criteria):
        raise ValueError('At least one criterion must be required')
    return value


def snapshot(root, inputs):
    hashes = {}
    for name in inputs:
        path = local(root, name)
        if not path.exists():
            raise ValueError(f'Declared input missing: {name}')
        selected = [path] if path.is_file() else path.rglob('*')
        count = 0
        for file in selected:
            relative = file.relative_to(root)
            if any(part in SKIP for part in relative.parts) or file.suffix in {'.pyc', '.pyo'}:
                continue
            if file.is_symlink():
                raise ValueError(f'Symlink input must be replaced with explicit real inputs: {relative}')
            if not file.is_file():
                continue
            local(root, str(relative))
            hashes[relative.as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
            count += 1
        if not count:
            raise ValueError(f'No hashable files in declared input: {name}')
    return hashes


def run(root, args):
    spec = contract(root, args.contract)
    frozen = digest(spec)
    for criterion in spec['criteria']:
        before = snapshot(root, spec['inputs'])
        started = stamp()
        try:
            result = subprocess.run(criterion['argv'], cwd=root, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace',
                                    timeout=args.timeout, shell=False)
            text = result.stdout + '\n' + result.stderr
            passed = (result.returncode == 0 and re.search(criterion['success_regex'], text)
                      and not ZERO_TESTS.search(text)
                      and not (criterion.get('failure_regex') and re.search(criterion['failure_regex'], text)))
            outcome, code = ('passed' if passed else 'failed'), result.returncode
            output = redact(text)
        except (OSError, subprocess.TimeoutExpired) as exc:
            outcome, code, output = 'blocked', None, redact(str(exc))
        after = snapshot(root, spec['inputs'])
        if after != before or digest(contract(root, args.contract)) != frozen:
            outcome = 'inputs-changed'
        row = save(root, 'runs', dict(criterion=criterion['id'], contract=frozen,
                   inputs=before, started_at=started, finished_at=stamp(),
                   argv=criterion['argv'], outcome=outcome, exit_code=code, output=output))
        print(f"{criterion['id']}: {outcome} ({row['id']})")
    return status(root, args)


def status(root, args):
    spec = contract(root, args.contract)
    frozen = digest(spec)
    current = snapshot(root, spec['inputs'])
    latest = {}
    for row in records(root, 'runs'):
        if row.get('contract') == frozen:
            latest[row['criterion']] = row
    failures = []
    optional = []
    for item in spec['criteria']:
        row = latest.get(item['id'])
        verdict = 'missing' if row is None else ('stale' if row.get('inputs') != current else row['outcome'])
        print(f"{item['id']}: {verdict}")
        if verdict != 'passed':
            (failures if item['required'] else optional).append(item['id'])
    print('NOT_READY' if failures else 'CHECKS_PASSED (declared executable criteria only)')
    if optional:
        print('Optional gaps:', ', '.join(optional))
    return 1 if failures else 0


def evidence_paths(root, row):
    paths = row.get('evidence', [])
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ValueError('evidence must be a relative file list')
    for name in paths:
        if not local(root, name).is_file():
            raise ValueError(f'Evidence file missing: {name}')
    return paths


def learn(root, args):
    row = load(local(root, args.from_file))
    required = ['trigger', 'observation', 'wrong_decision', 'correction', 'scope', 'counterexample']
    if row.get('category') not in CATEGORIES or any(not isinstance(row.get(k), str) or not row[k].strip() for k in required):
        raise ValueError('Lesson requires a valid category and nonempty decision/scope/counterexample fields')
    if row.get('validation') not in {'observation', 'validated'}:
        raise ValueError('validation must be observation or validated')
    paths = evidence_paths(root, row)
    if row['validation'] == 'validated' and not paths:
        raise ValueError('Validated lessons require evidence')
    existing = {item['id'] for item in records(root, 'lessons')}
    if row.get('supersedes') and row['supersedes'] not in existing:
        raise ValueError('Unknown superseded lesson')
    row = json.loads(redact(json.dumps(row, ensure_ascii=False)))
    row['evidence_hashes'] = {p: hashlib.sha256(local(root, p).read_bytes()).hexdigest() for p in paths}
    saved = save(root, 'lessons', row)
    print(saved['id'])
    return 0


def recall(root, args):
    rows = records(root, 'lessons')
    superseded = {r['supersedes'] for r in rows if r.get('supersedes')}
    terms = args.query.lower().split()
    matched = [r for r in reversed(rows) if r['id'] not in superseded and
               (not terms or any(term in json.dumps(r, ensure_ascii=False).lower() for term in terms))]
    print(json.dumps(matched[:args.limit], ensure_ascii=False, indent=2))
    return 0


def propose(root, args):
    rows = records(root, 'lessons')
    lessons = {r['id']: r for r in rows}
    superseded = {r['supersedes'] for r in rows if r.get('supersedes')}
    ids = list(dict.fromkeys(args.lesson_ids))
    for key in ids:
        row = lessons.get(key)
        if row is None or key in superseded or row.get('validation') != 'validated':
            raise ValueError('Proposal requires current validated local lessons')
        for name, expected in row.get('evidence_hashes', {}).items():
            if not local(root, name).is_file() or hashlib.sha256(local(root, name).read_bytes()).hexdigest() != expected:
                raise ValueError('Lesson evidence changed or missing; revalidate before proposing')
    proposal = save(root, 'proposals', dict(lesson_ids=ids, state='candidate',
                    required_next=['Specify common decision mechanism and counterexample',
                                   'Run representative, near-miss and holdout comparisons',
                                   'Review actual diff with user before publication']))
    print(proposal['id'], 'candidate only; no active instructions modified')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    subs = parser.add_subparsers(dest='action', required=True)
    for action in ('run', 'status'):
        sub = subs.add_parser(action)
        sub.add_argument('--contract', required=True)
        if action == 'run':
            sub.add_argument('--timeout', type=float, default=300)
    subs.add_parser('learn').add_argument('--from-file', required=True)
    rec = subs.add_parser('recall')
    rec.add_argument('--query', default='')
    rec.add_argument('--limit', type=int, default=5)
    subs.add_parser('propose').add_argument('--lesson-ids', nargs='+', required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Root must be a project directory')
        if args.action == 'recall' and not 1 <= args.limit <= 50:
            raise ValueError('limit must be between 1 and 50')
        if args.action == 'run' and args.timeout <= 0:
            raise ValueError('timeout must be positive')
        return globals()[args.action](root, args)
    except (ValueError, OSError, TypeError, KeyError, re.error) as exc:
        print(f'ERROR: {redact(str(exc))}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

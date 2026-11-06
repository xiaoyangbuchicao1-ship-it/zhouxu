import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skill/scripts/delivery.py'
spec = importlib.util.spec_from_file_location('delivery', SCRIPT)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)
TEMP = Path(__file__).resolve().parents[1] / '.ai-tmp/tests'


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        TEMP.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=TEMP)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('value = 1\n', encoding='utf-8')
        self.spec = {'version': 1, 'goal': 'Behaviour is checked', 'inputs': ['src'],
                     'criteria': [{'id': 'behaviour', 'promise': 'Expected observable output',
                                   'required': True,
                                   'argv': [sys.executable, '-c', 'print("1 passed")'],
                                   'success_regex': r'[1-9][0-9]* passed'}]}
        self.write_contract()

    def tearDown(self):
        self.tmp.cleanup()

    def write_contract(self):
        (self.root / 'acceptance.json').write_text(json.dumps(self.spec), encoding='utf-8')

    def call(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            result = delivery.main(['--root', str(self.root), *args])
        return result, stream.getvalue()

    def run_check(self):
        return self.call('run', '--contract', 'acceptance.json')

    def test_success_with_current_inputs(self):
        self.assertEqual(self.run_check()[0], 0)
        self.assertEqual(self.call('status', '--contract', 'acceptance.json')[0], 0)

    def test_failed_command_cannot_be_green(self):
        self.spec['criteria'][0]['argv'][-1] = 'print("1 passed"); raise SystemExit(1)'
        self.write_contract()
        self.assertEqual(self.run_check()[0], 1)

    def test_zero_tests_cannot_be_green(self):
        self.spec['criteria'][0]['argv'][-1] = 'print("no tests ran; 1 passed")'
        self.write_contract()
        self.assertEqual(self.run_check()[0], 1)

    def test_output_without_positive_signal_cannot_be_green(self):
        self.spec['criteria'][0]['argv'][-1] = 'print("all good")'
        self.write_contract()
        self.assertEqual(self.run_check()[0], 1)

    def test_missing_required_criterion_blocks(self):
        self.assertEqual(self.call('status', '--contract', 'acceptance.json')[0], 1)

    def test_source_edit_addition_and_deletion_invalidate(self):
        self.run_check()
        file = self.root / 'src/app.py'
        file.write_text('value = 2\n', encoding='utf-8')
        self.assertIn('stale', self.call('status', '--contract', 'acceptance.json')[1])
        self.run_check()
        (self.root / 'src/new.py').write_text('x=1', encoding='utf-8')
        self.assertEqual(self.call('status', '--contract', 'acceptance.json')[0], 1)
        self.run_check()
        file.unlink()
        self.assertEqual(self.call('status', '--contract', 'acceptance.json')[0], 1)

    def test_contract_edit_invalidates(self):
        self.run_check()
        self.spec['goal'] = 'A new promise'
        self.write_contract()
        self.assertEqual(self.call('status', '--contract', 'acceptance.json')[0], 1)

    def test_input_changed_during_command_is_rejected(self):
        self.spec['criteria'][0]['argv'][-1] = 'from pathlib import Path; Path("src/app.py").write_text("changed"); print("1 passed")'
        self.write_contract()
        self.assertEqual(self.run_check()[0], 1)

    def test_latest_failure_overrides_old_pass(self):
        # Same argv and contract, different outcome from a non-input external condition.
        self.spec['criteria'][0]['argv'][-1] = 'from pathlib import Path; print("1 passed" if not Path("external.flag").exists() else "FAILED")'
        self.write_contract()
        self.assertEqual(self.run_check()[0], 0)
        (self.root / 'external.flag').write_text('fail')
        self.assertEqual(self.run_check()[0], 1)

    def test_timeout_is_blocked(self):
        self.spec['criteria'][0]['argv'][-1] = 'import time; time.sleep(2)'
        self.write_contract()
        self.assertEqual(self.call('run', '--contract', 'acceptance.json', '--timeout', '0.01')[0], 1)

    def test_missing_executable_is_blocked(self):
        self.spec['criteria'][0]['argv'] = ['no-such-executable-46719']
        self.write_contract()
        self.assertEqual(self.run_check()[0], 1)

    def test_corrupt_evidence_is_rejected(self):
        self.run_check()
        path = next((self.root / '.delivery/runs').glob('*.json'))
        row = json.loads(path.read_text(encoding='utf-8'))
        row['exit_code'] = 12
        path.write_text(json.dumps(row), encoding='utf-8')
        self.assertEqual(self.call('status', '--contract', 'acceptance.json')[0], 2)

    def test_empty_inputs_and_duplicate_ids_rejected(self):
        self.spec['inputs'] = []
        self.write_contract()
        self.assertEqual(self.run_check()[0], 2)
        self.spec['inputs'] = ['src']
        self.spec['criteria'].append(dict(self.spec['criteria'][0]))
        self.write_contract()
        self.assertEqual(self.run_check()[0], 2)

    def test_path_escape_is_rejected(self):
        self.spec['inputs'] = ['../outside']
        self.write_contract()
        self.assertEqual(self.run_check()[0], 2)

    def lesson(self, validation='validated', evidence=True, supersedes=None):
        if evidence:
            (self.root / 'proof.txt').write_text('Original failing request now denied')
        row = dict(category='implementation-defect', trigger='Permission check',
                   observation='Another tenant was able to write', wrong_decision='Role alone',
                   correction='Check tenant boundary', scope='Tenant-protected writes',
                   counterexample='Explicit cross-tenant support capability',
                   validation=validation, evidence=['proof.txt'] if evidence else [],
                   tags=['tenant'], supersedes=supersedes)
        (self.root / 'lesson.json').write_text(json.dumps(row), encoding='utf-8')
        result, output = self.call('learn', '--from-file', 'lesson.json')
        return result, output.strip()

    def test_learning_can_be_saved_and_recalled_across_calls(self):
        result, key = self.lesson()
        self.assertEqual(result, 0)
        self.assertIn(key, self.call('recall', '--query', 'tenant')[1])
        self.assertEqual(self.call('propose', '--lesson-ids', key)[0], 0)
        self.assertFalse((self.root / 'SKILL.md').exists())

    def test_unverified_or_missing_evidence_cannot_be_promoted(self):
        self.assertEqual(self.lesson(evidence=False)[0], 2)
        _, key = self.lesson(validation='observation', evidence=False)
        self.assertEqual(self.call('propose', '--lesson-ids', key)[0], 2)

    def test_modified_lesson_evidence_blocks_proposal(self):
        _, key = self.lesson()
        (self.root / 'proof.txt').write_text('Changed')
        self.assertEqual(self.call('propose', '--lesson-ids', key)[0], 2)

    def test_superseded_lesson_not_retrieved_or_proposed(self):
        _, old = self.lesson()
        _, new = self.lesson(supersedes=old)
        recalled = json.loads(self.call('recall')[1])
        self.assertEqual([r['id'] for r in recalled], [new])
        self.assertEqual(self.call('propose', '--lesson-ids', old)[0], 2)

    def test_project_isolation(self):
        _, key = self.lesson()
        other = self.root / 'other-project'
        other.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(delivery.main(['--root', str(other), 'recall']), 0)
        self.assertNotIn(key, out.getvalue())


if __name__ == '__main__':
    unittest.main()

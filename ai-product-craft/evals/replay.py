"""Replay the preserved synthetic candidate audit; expected acceptance failure is success for this experiment."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evals/outcomes/with-skill'
work = OUT / 'temp'
work.mkdir(exist_ok=True)
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', TEMP=str(work), TMP=str(work))
commands = [
    ([sys.executable, '-m', 'unittest', 'discover', '-s', 'snapshot', '-p', 'test_service.py', '-v'], 0),
    ([sys.executable, '-m', 'unittest', 'discover', '-s', 'snapshot', '-p', 'test_acceptance.py', '-v'], 1)
]
for command, expected in commands:
    result = subprocess.run(command, cwd=OUT, env=env)
    if result.returncode != expected:
        raise SystemExit(f'Unexpected experiment outcome: {result.returncode}, expected {expected}')
if not any(work.iterdir()):
    work.rmdir()
print('Reproduced: existing tests pass while business acceptance fails. Product remains NOT READY.')

"""Reproduce P7a verification without inheriting a running Pan profile.

Run with the dependency-equipped Python interpreter. Baseline mode requires
the detached 7f303faa worktree named in baseline_root below.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

out = Path(__file__).resolve().parent
repo = out.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--baseline', action='store_true')
parser.add_argument('--label', default='verification')
args = parser.parse_args()
name = 'baseline-qa' if args.baseline else args.label
assert name in {'baseline-qa', 'verification', 'acceptance'}
if args.baseline:
    repo = repo.parent / 'job-p7a-baseline-7f303faa-20261026'
    assert subprocess.check_output(['git', 'rev-parse', '--short=8', 'HEAD'], cwd=repo,
                                   text=True).strip() == '7f303faa'
    nodes, missing = [], []
    for item in json.loads((out / 'qa-remaining.json').read_text(encoding='utf-8')):
        module, test = item['id'].split('::')
        class_name = None
        if module.rsplit('.', 1)[-1].startswith('Test'):
            module, class_name = module.rsplit('.', 1)
        path = module.replace('.', '/') + '.py'
        if not (repo / path).is_file() or ('def ' + test.split('[')[0] + '(') not in (
                repo / path).read_text(encoding='utf-8'):
            missing.append(item['id'])
        else:
            nodes.append(path + '::' + (class_name + '::' if class_name else '') + test)
    (out / 'baseline-missing.json').write_text(json.dumps(missing, indent=2), encoding='utf-8')
else:
    nodes = sorted(set(json.loads((out / 'suite-files.json').read_text(encoding='utf-8')))
                   | {'tests/test_p7a_successors.py', 'tests/test_eta_final_combined.py'}
                   | {str(p.relative_to(repo)).replace('\\', '/')
                      for p in (repo / 'tests').glob('test_job_final*.py')})
    (out / (name + '-files.json')).write_text(json.dumps(nodes, indent=2), encoding='utf-8')
command = [sys.executable, str(repo / 'scripts/run_tests_isolated.py'), '--', sys.executable,
           '-m', 'pytest', *nodes, '-q', '-o', 'addopts=', '--tb=short',
           '--junitxml=' + str(out / (name + '.xml'))]
(out / (name + '-command.json')).write_text(json.dumps(command, indent=2), encoding='utf-8')
start = time.time()
with (out / (name + '.log')).open('w', encoding='utf-8') as log:
    result = subprocess.run(command, cwd=repo, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT)
(out / (name + '-exit.json')).write_text(json.dumps({
    'exitCode': result.returncode, 'elapsedSeconds': round(time.time() - start, 2),
    'items': len(nodes), 'cwd': str(repo), 'python': sys.executable}, indent=2), encoding='utf-8')
sys.exit(result.returncode)

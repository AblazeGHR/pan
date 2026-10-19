"""Repeatable owned validation; run via a durable Job for the whole batch."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'audit/os-autostart-20261019'
WEB = ROOT / 'packages/web'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PAN_') and key.upper() != 'PYTHONPATH'}
    env['LOGIN_TEST_PYTHON'] = sys.executable
    node = shutil.which('node')
    pnpm = shutil.which('pnpm.cmd' if os.name == 'nt' else 'pnpm')
    if not node or not pnpm: raise RuntimeError('Node and pnpm are required')
    stages = [
        ('backend', ROOT, [sys.executable, str(ROOT / 'scripts/run_tests_isolated.py'), '--', sys.executable, '-m', 'pytest',
                          'tests/test_login_startup.py', 'tests/test_launcher.py', 'tests/test_session_lifecycle_preferences.py',
                          'tests/test_main_lifecycle_jobs.py', 'tests/test_lifecycle_shortcuts.py', '-q', '-o', 'addopts=',
                          '--junitxml=' + str(OUT / 'backend.xml')]),
        ('frontend', WEB, [node, str(WEB / 'node_modules/vitest/vitest.mjs'), 'run',
                           'src/components/layout/LoginStartupSettings.test.tsx', 'src/components/layout/AppSettingsModal.test.tsx',
                           '--reporter=default', '--reporter=junit', '--outputFile=' + str(OUT / 'frontend.xml')]),
        ('build', WEB, [pnpm, 'build']),
        ('chromium', WEB, [node, str(WEB / 'e2e/login-startup.e2e.mjs')]),
    ]
    outcomes = []
    sys.path.insert(0, str(ROOT))
    from packages.jobs.window import command_window_options
    for name, cwd, argv in stages:
        started = time.monotonic()
        log = OUT / (name + '.txt')
        with log.open('w', encoding='utf-8') as output:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                    **command_window_options())
        outcomes.append({'stage': name, 'exitCode': result.returncode, 'seconds': round(time.monotonic() - started, 2), 'log': str(log)})
        (OUT / 'validation.json').write_text(json.dumps(outcomes, indent=2), encoding='utf-8')
        print(f'{name}: exit={result.returncode}; log={log}', flush=True)
        if result.returncode: return result.returncode
    for name in ('result.json', 'permission-failure.png', 'off-clean.png'):
        shutil.copyfile(WEB / 'test-results/login-startup' / name, OUT / name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Reproduce hidden-Job CI with isolated profiles and retained logs/JUnit.

Run via agent_background_start; terminal delivery is provided by that Job.
Toolchains are explicitly supplied, never installed or globally configured here.
"""
from __future__ import annotations

import argparse
import ctypes
from collections import Counter
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--toolchains', type=Path, required=True)
    p.add_argument('--sha', required=True)
    p.add_argument('--focused', action='store_true')
    p.add_argument('--allow-dirty', action='store_true')
    p.add_argument('--baseline', type=Path, help='original full-CI evidence directory for identity/skip comparison')
    p.add_argument('--source-baseline', help='task starting SHA; distinguish earlier test renames from this repair')
    p.add_argument('--pnpm', default=shutil.which('pnpm.cmd'), help='absolute pnpm command path')
    a = p.parse_args()
    root, out = a.root.resolve(), a.out.resolve()
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == a.sha
    if not a.allow_dirty:
        assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip()
    out.mkdir(parents=True, exist_ok=False)
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'SYSTEMDRIVE',
               'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    for name, rel in [('USERPROFILE', 'profile'), ('HOME', 'profile'),
                      ('APPDATA', 'profile/AppData/Roaming'), ('LOCALAPPDATA', 'profile/AppData/Local'),
                      ('TEMP', 'tmp'), ('TMP', 'tmp'), ('npm_config_cache', 'npm-cache')]:
        directory = out / rel
        directory.mkdir(parents=True, exist_ok=True)
        env[name] = str(directory)
    env.update(PYTHONIOENCODING='cp1252', PYTHONUTF8='0', PYTHONPATH=str(root))
    codex = a.toolchains / 'codex'
    assert (codex / 'node_modules/@openai/codex/bin/codex.js').is_file()
    env['PATH'] = str(codex) + ';' + ';'.join(s for s in env['PATH'].split(';') if 'node_global' not in s.lower())
    results = []
    (out / 'source-status.txt').write_text(subprocess.check_output(
        ['git', 'status', '--porcelain'], cwd=root, text=True), encoding='utf-8')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from packages.core.terminal.guard import JobObjectGuard
    from packages.core.terminal.identity import read_creation_filetime

    def run(name, argv, cwd=root):
        start = time.time()
        with (out / (name + '.log')).open('w', encoding='utf-8') as log:
            log.write(json.dumps(dict(sha=a.sha, argv=argv, cwd=str(cwd), started=start)) + '\n')
            log.flush()
            guard = JobObjectGuard()
            proc = None
            try:
                proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW | 4)
                identity = read_creation_filetime(int(proc._handle))
                guard.assign(int(proc._handle))
                log.write(json.dumps(dict(pid=proc.pid, filetime=str(identity), started=start)) + '\n')
                log.flush()
                resume = ctypes.WinDLL('ntdll').NtResumeProcess
                resume.argtypes = [ctypes.c_void_p]
                resume.restype = ctypes.c_long
                assert resume(int(proc._handle)) == 0
                rc = proc.wait()
            finally:
                members = guard.member_pids()
                terminated, remaining = guard.terminate_tree(None, timeout=5)
                log.write(json.dumps(dict(cleanupMembers=members, terminated=terminated, remaining=remaining)) + '\n')
                assert not remaining
                assert guard.close()
                if proc is not None:
                    if proc.poll() is None:
                        # Retained Popen handle, including assignment/resume failure.
                        proc.kill()
                    proc.wait(timeout=5)
                    proc._handle.Close()
        results.append(dict(name=name, exitCode=rc, seconds=time.time()-start, sha=a.sha))
        (out / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(name, rc, flush=True)

    for leg in ['312', '314']:
        python = str(a.toolchains / ('venv' + leg) / 'Scripts/python.exe')
        run('python-' + leg, [python, '--version'])
        run('freeze-' + leg, [python, '-m', 'pip', 'freeze'])
        if not a.focused:
            run('notion-standalone-' + leg, [python, str(root / 'scripts/run_tests_isolated.py'), '--', python,
                                           '-m', 'pytest', 'tests/test_notion_auth.py', '-q', '-ra',
                                           '--junitxml=' + str(out / ('notion-' + leg + '.xml')),
                                           '--basetemp=' + str(out / ('notion-tmp-' + leg))])
        paths = ['tests/test_job_runner_venv.py', 'tests/test_job_window_guard.py',
                 'tests/test_session_import.py::test_mcp_import_cbc_builds_body_and_autoclaims',
                 'tests/test_session_import.py::test_mcp_import_reimport_precheck_denies_unmanaged'] if a.focused else ['tests/', 'packages/qq']
        run('pytest-' + leg, [python, str(root / 'scripts/run_tests_isolated.py'), '--', python,
                             '-m', 'pytest', *paths, '-q', '-ra', '--junitxml=' + str(out / ('pytest-' + leg + '.xml')),
                             '--basetemp=' + str(out / ('pytest-tmp-' + leg))])
    web = root / 'packages/web'
    pnpm = a.pnpm
    assert pnpm and Path(pnpm).is_file()
    run('vitest', [pnpm, 'exec', 'vitest', 'run', *(['src/components/layout/TopBar.test.tsx'] if a.focused else []),
                   '--cache=false', '--reporter=default', '--reporter=junit', '--outputFile=' + str(out / 'vitest.xml')], web)
    if not a.focused:
        for name, argv in [('tsc', [pnpm, 'exec', 'tsc', '-b']), ('build', [pnpm, 'run', 'build']),
                           ('lint-src', [pnpm, 'exec', 'eslint', 'src'])]:
            run(name, argv, web)
        run('derived-ui-benchmark', ['node', str(root / 'packages/devTools/performance/benchmark-derived-ui.mjs')])
    identities = {}
    for path in out.glob('*.xml'):
        cases = list(ET.parse(path).getroot().iter('testcase'))
        identities[path.stem] = [dict(id=c.get('classname', '') + '::' + c.get('name', ''),
                                     skipped=c.find('skipped').get('message') if c.find('skipped') is not None else None)
                                  for c in cases]
    (out / 'identities-skips.json').write_text(json.dumps(identities, ensure_ascii=False, indent=2), encoding='utf-8')
    comparison = {}
    if a.baseline:
        preexisting_files = set()
        if a.source_baseline:
            original_sha = json.loads((a.baseline / 'results.json').read_text(encoding='utf-8'))[0]['sha']
            before_task = set(subprocess.check_output(
                ['git', 'diff', '--name-only', original_sha, a.source_baseline], cwd=root, text=True).splitlines())
            this_task = set(subprocess.check_output(
                ['git', 'diff', '--name-only', a.source_baseline, a.sha], cwd=root, text=True).splitlines())
            preexisting_files = before_task - this_task
        for name, current in identities.items():
            before_path = a.baseline / (name + '.xml')
            assert before_path.is_file(), before_path
            before = list(ET.parse(before_path).getroot().iter('testcase'))
            old_ids = Counter(c.get('classname', '') + '::' + c.get('name', '') for c in before)
            new_ids = Counter(c['id'] for c in current)
            old_skips = Counter((c.get('classname', '') + '::' + c.get('name', ''), c.find('skipped').get('message'))
                                for c in before if c.find('skipped') is not None)
            new_skips = Counter((c['id'], c['skipped']) for c in current if c['skipped'] is not None)
            comparison[name] = dict(before=len(before), after=len(current),
                                    removed=list((old_ids-new_ids).elements()), added=list((new_ids-old_ids).elements()),
                                    removedSkips=list((old_skips-new_skips).elements()), addedSkips=list((new_skips-old_skips).elements()))
            removed = comparison[name]['removed']
            preexisting = [identity for identity in removed
                           if 'packages/web/src/' + identity.split('::', 1)[0] in preexisting_files]
            comparison[name]['preexistingRemoved'] = preexisting
            comparison[name]['unexpectedRemoved'] = list((Counter(removed)-Counter(preexisting)).elements())
        (out / 'identity-comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding='utf-8')
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == a.sha
    (out / 'final-status.txt').write_text(subprocess.check_output(
        ['git', 'status', '--porcelain'], cwd=root, text=True), encoding='utf-8')
    return int(any(r['exitCode'] for r in results) or any(c['unexpectedRemoved'] or c['addedSkips'] for c in comparison.values()))


if __name__ == '__main__':
    raise SystemExit(main())

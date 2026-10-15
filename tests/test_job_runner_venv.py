"""Real venv redirector coverage from MCP / HTTP and service entry points."""
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import venv
import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows venv console regression')


@pytest.mark.parametrize('entry,hidden,exit_code', [('mcp', True, 0), ('mcp', False, 7), ('independent', True, 0), ('scheduled', True, 0)])
def test_venv_runner_has_no_window_before_task_imports(tmp_path, monkeypatch, entry, hidden, exit_code):
    from packages.core import background_jobs as jobs, config
    from packages.jobs.independent_service import JobStore
    from packages.jobs import independent_service
    from packages.mcp import server as mcp
    from packages.web import server as web
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    venv_root = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(venv_root)
    python = str(venv_root / 'Scripts/python.exe')
    observation = tmp_path / 'runner'; observation.mkdir()
    helper = Path(__file__).parent / 'helpers/runner_startup_probe.py'
    site = venv_root / 'Lib/site-packages/sitecustomize.py'
    site.write_text('import runpy;runpy.run_path(' + repr(str(helper.resolve())) + ', init_globals={"OUTPUT_DIRECTORY":' + repr(str(observation)) + '})', encoding='utf-8')
    monkeypatch.setattr(config, 'load_config', lambda: {'python': python})
    monkeypatch.setattr(jobs._sessions, 'get', lambda sid: {'id': sid})
    root = tmp_path / 'registry'
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(root))
    # The real runner imports repo code even though command cwd is isolated.
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(jobs, 'PROJECT_ROOT', repo)
    import subprocess
    from packages.core.terminal.identity import read_creation_filetime
    captured = []
    original_popen = subprocess.Popen
    def spawn(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        captured.append((process, read_creation_filetime(int(process._handle))))
        return process
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    output = tmp_path / 'payload.json' 
    program = 'import ctypes,json,os,sys;from pathlib import Path;Path(' + repr(str(output)) + ').write_text(json.dumps({"pid":os.getpid(),"prefix":sys.prefix,"cwd":os.getcwd(),"console":bool(ctypes.windll.kernel32.GetConsoleWindow())}));print("owned stdout",flush=True);print("owned stderr",file=sys.stderr,flush=True);sys.exit(' + str(exit_code) + ')'
    argv = [python, '-c', program]
    if entry == 'mcp':
        app = FastAPI(); app.post('/api/background-jobs')(web.api_background_job_start)
        client = TestClient(app)
        monkeypatch.setattr(mcp, '_caller_identity', lambda: {'id': 'owned-session'})
        monkeypatch.setattr(mcp, '_check_access', lambda *a, **kw: None)
        def api(method, path, body):
            response = client.request(method, path, json=body)
            assert response.status_code == 200
            return response.json()
        monkeypatch.setattr(mcp, '_api', api)
        job = mcp.agent_background_start(argv, str(tmp_path), hide_window=hidden)
        get = lambda: jobs.get(job['jobId'])
    elif entry == 'independent':
        monkeypatch.setattr(independent_service, 'sys', SimpleNamespace(executable=python))
        store = JobStore(str(root))
        job = store.create({'argv': argv, 'cwd': str(tmp_path), 'targetSessionId': 'owned-session'})
        get = lambda: store.get(job['jobId'])
    else:
        import subprocess
        parent = {'jobId': 'job_owned_venv_schedule', 'kind': jobs.SCHEDULED_TASK_KIND, 'name': 'owned', 'targetSessionId': None, 'action': {'api': 'shell', 'args': {'command': subprocess.list2cmdline(argv), 'cwd': str(tmp_path)}}}
        jobs._create(parent)
        job = jobs.start_scheduled_process(parent, 'owned-dispatch')
        get = lambda: jobs.get(job['jobId'])
    deadline = time.monotonic() + 12
    while (fact := get())['status'] in ('starting', 'running'):
        assert time.monotonic() < deadline, fact
        time.sleep(.02)
    assert fact['status'] == ('completed' if exit_code == 0 else 'failed'), fact
    assert fact['exitCode'] == exit_code, fact
    assert len(captured) == 1
    process, filetime = captured[0]
    assert read_creation_filetime(int(process._handle)) == filetime
    process.wait(timeout=5)
    process._handle.Close()
    probes = [json.loads(p.read_text(encoding='utf8')) for p in observation.glob('*.json')]
    assert len(probes) == 1, probes
    evidence = dict(entry=entry, hidden=hidden, exitCode=exit_code, runnerWrapperPid=process.pid, runnerWrapperFiletime=str(filetime), probes=probes, status=fact['status'], log=Path(fact['logPath']).read_text(encoding='utf8'))
    target = os.environ.get('PAN_TEST_RUNNER_EVIDENCE')
    if target:
        with Path(target).open('a', encoding='utf8') as f:
            f.write(json.dumps(evidence) + '\n')
    assert probes[0]['console'] == 0, evidence
    # CREATE_NO_WINDOW deliberately retains an inherited windowless console.
    assert probes[0]['pid'] in probes[0]['consoleClients'], evidence
    assert probes[0]['parent'] in probes[0]['consoleClients'], evidence
    assert probes[0]['loadedModules'], probes
    assert probes[0]['parent'] == process.pid and probes[0]['parentFiletime'] == str(filetime)
    expected_module = 'packages.jobs.independent_runner' if entry == 'independent' else 'packages.core.background_runner'
    assert probes[0]['entryModule']['name'] == expected_module
    assert Path(probes[0]['entryModule']['path']).resolve().is_relative_to(repo)
    assert Path(probes[0]['prefix']).resolve() == venv_root.resolve()
    payload = json.loads(output.read_text())
    assert Path(payload['prefix']).resolve() == venv_root.resolve()
    assert Path(payload['cwd']).resolve() == tmp_path.resolve()
    assert payload['console'] is (not hidden)
    assert 'owned stdout' in evidence['log'] and 'owned stderr' in evidence['log']

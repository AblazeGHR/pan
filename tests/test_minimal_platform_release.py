"""Owned main-format profile/pipe probes for the minimal platform extraction."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock


OUT = Path('evidence/cp-x3-minimal-20261029')


def save(name, facts):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(facts, indent=2), encoding='utf-8')


def test_preexisting_profile_keeps_original_jobs_across_owned_start(tmp_path, monkeypatch):
    from tests import test_real_queue_http_e2e as base
    from packages.core import background_jobs as jobs, session
    from packages.scheduler import store
    from packages.core import platform
    from packages.core import config
    from packages.core.adapters.cbc.adapter import CbcAdapter
    rt = base._PanRuntime(tmp_path / 'preexisting-main-profile')
    rt.data_root.mkdir()
    config_path = rt.data_root / 'config.json'
    config_path.write_text(json.dumps({'plugin_manifests': [], 'memory': {'enabled': False}}))
    monkeypatch.setattr(config, 'CONFIG_FILE', config_path)
    monkeypatch.setattr(CbcAdapter, 'supported_models', property(lambda self: ['deepseek-v4-flash']))
    monkeypatch.setattr(jobs, 'DEFAULT_ROOT', rt.data_root / 'background_jobs')
    monkeypatch.setattr(store, 'DEFAULT_ROOT', rt.data_root / 'background_jobs')
    monkeypatch.setattr(session, 'SESSION_DIR', rt.data_root / 'sessions')
    s = session.Session(id='ses-existing', name='existing', adapter='cbc', model='deepseek-v4-flash')
    session._cache[s.id] = s
    session.save(s)
    with monkeypatch.context() as seeded:
        seeded.setattr(jobs, '_spawn_background_runner', lambda job, **k: job)
        job = jobs.start(s.id, [sys.executable, '-c', 'pass'], str(tmp_path), name='preexisting process')
        jobs.runner_update(job['jobId'], status='completed', exitCode=0)
    task = store.create_task({'name': 'old disabled schedule', 'target_session_id': s.id,
        'text': 'preserve', 'enabled': False,
        'schedule': {'kind': 'interval', 'interval_sec': 3600}})
    before = jobs.get(job['jobId'])
    facts = {'preexistingSyntheticProfile': True, 'jobId': job['jobId'], 'taskId': task['id'],
        'port': base.PORT, 'providerCalls': 0}
    try:
        rt.start()
        assert rt.request('GET', '/api/platform')['capabilities'] == platform.capabilities()
        rows = rt.request('GET', '/api/scheduler/tasks')
        assert task['id'] in json.dumps(rows)
        assert jobs.get(job['jobId'])['jobId'] == before['jobId']
        assert jobs.get(job['jobId'])['status'] == 'completed'
        assert store.get_task(task['id'])['enabled'] is False
        facts['startedWithOriginalRecords'] = True
    finally:
        rt.stop()
        facts['portReleased'] = base._port_is_free(base.PORT)
    # Existing writers and retry stay callable after host startup and stop.
    store.update_task(task['id'], {'text': 'updated old schedule'})
    assert store.get_task(task['id'])['text'] == 'updated old schedule'
    with monkeypatch.context() as retired:
        spawn = Mock(side_effect=lambda doc, **k: doc)
        retired.setattr(jobs, '_spawn_background_runner', spawn)
        jobs._update(job['jobId'], {'status': 'failed'})
        result = jobs.retry(job['jobId'])
        assert result['jobId'] != job['jobId']  # main retry creates a new Job
        assert result['argv'] == before['argv']
        assert jobs.get(job['jobId'])['status'] == 'failed'
        spawn.assert_called_once()
    assert not (rt.data_root / 'job-final').exists()
    assert not list(rt.data_root.rglob('*owner*.sqlite*'))
    facts.update(originalCreateUpdateRetry=True, noCanonicalCutover=True)
    save('preexisting-profile.json', facts)


def test_ordinary_pipe_worker_owned_io_stop(tmp_path, monkeypatch):
    from packages.core import worker, session
    cwd = tmp_path / 'space 中文 & literal'
    cwd.mkdir()
    s = session.Session(id='ses-pipe', name='pipe', adapter='cbc', workdir=str(cwd))
    monkeypatch.setattr(session, 'get', lambda *a, **k: s)
    code = 'import os,sys,json,time; print(json.dumps({"cwd":os.getcwd(),"text":sys.stdin.readline().strip()}),flush=True); time.sleep(60)'
    adapter = SimpleNamespace(name='owned-pipe-probe', build_spawn_args=lambda *a: [sys.executable, '-X', 'utf8', '-u', '-c', code])
    async def run():
        proc = await worker._spawn_process(s.id, adapter)
        assert not isinstance(proc, str), proc
        w = worker.Worker(worker_id='owned-pipe', session_id=s.id, adapter=adapter, process=proc)
        try:
            proc.stdin.write('hello中文 & literal\n'.encode())
            await proc.stdin.drain()
            result = json.loads(await asyncio.wait_for(proc.stdout.readline(), 10))
            assert Path(result['cwd']) == cwd
            assert result['text'] == 'hello中文 & literal'
        finally:
            await worker._kill_process_tree(w)
            await asyncio.wait_for(proc.wait(), 10)
        save('pipe-worker.json', {'pid': proc.pid, 'returncode': proc.returncode,
            'literalCwd': True, 'stdinStdoutUnicode': True, 'noProvider': True})
    asyncio.run(run())

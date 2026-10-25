"""Phase 5 owned-instance verification of migrated legacy entry points.

These tests start a separate Pan HTTP host plus the real core MCP stdio server.
All persistent state and process trees belong to pytest's temporary directory.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta

import psutil
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import async_test
from packages.jobs.window import command_window_options

OUT = h.ROOT / 'artifacts/job-chain-phase5'


async def _finished(runtime, jid, headers=None):
    for _ in range(400):
        job = runtime.request('GET', '/api/jobs/' + jid, **({'headers': headers} if headers else {}))['job']
        if job.get('status') in {'completed', 'failed', 'uncertain', 'unknown', 'cancelled'} and not job.get('execution'):
            return job
        await asyncio.sleep(.025)
    raise AssertionError(('Job did not settle', job))


def _ephemeral_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    return port


def _payload(result):
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


@async_test
async def test_real_mcp_legacy_message_and_process_become_action_jobs(tmp_path, monkeypatch):
    port = _ephemeral_port()
    monkeypatch.setattr(h, "PORT", port)
    monkeypatch.setattr(h, "SERVER_LAUNCHER", Path(__file__).parent / "support" / "job_chain_phase5_server.py")
    runtime = h._PanRuntime(tmp_path / "pan-data")
    runtime.data_root.mkdir()
    manifest = runtime.data_root / "owned-manifest.json"
    manifest.write_text(json.dumps({"mcp_servers": [{"name": "pan", "command": sys.executable,
        "args": ["-m", "packages.mcp.server"], "cwd": str(h.ROOT)}]}), encoding="utf-8")
    (runtime.data_root / "config.json").write_text(json.dumps({"plugin_manifests": [str(manifest)], "scheduler": {"enabled": True}}), encoding="utf-8")
    runtime.start()
    OUT.mkdir(parents=True, exist_ok=True)
    proof = {'port': port, 'profile': str(runtime.data_root), 'provider': 'owned deterministic stream CLI'}
    try:
        session = runtime.request("POST", "/api/sessions", json={"name": "Phase5-MCP-caller", "adapter": "cbc", "model": "deepseek-v4-flash", "mcpServers": ["pan"]})
        assert session.get("id"), session
        sid = session["id"]
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_")}
        env.update({"PAN_API_URL": runtime.base_url, "PAN_AGENT_SESSION_ID": sid,
                    "PAN_PLUGIN_IPC": (runtime.data_root / "owned-plugin-ipc.txt").read_text(encoding="utf-8")})
        params = StdioServerParameters(command=sys.executable, args=["-m", "packages.mcp.server"], cwd=str(h.ROOT), env=env)
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            await client.initialize()
            listed = await client.list_tools()
            assert "agent_message_job_create" in {item.name for item in listed.tools}
            message = _payload(await client.call_tool("agent_message_job_create", {
                "text": "Phase 5 unchanged MCP message sample", "schedule": {"type": "once", "delaySeconds": 3600},
                "target_session_id": sid,
            }))
            assert message.get("jobId"), message
            job = runtime.request("GET", "/api/jobs/" + message["jobId"])
            assert job.get("job", {}).get("config", {}).get("actionId") == "system_session_message", job
            assert job["job"]["status"] in {"scheduled", "waiting", "pending"}
            queried = _payload(await client.call_tool("agent_message_job_get", {"job_id": message["jobId"]}))
            assert queried.get("jobId") == message["jobId"], queried
            cancelled = _payload(await client.call_tool("agent_message_job_cancel", {"job_id": message["jobId"]}))
            assert cancelled.get("status") == "cancelled", cancelled

            process = _payload(await client.call_tool("agent_background_start", {
                "argv": [sys.executable, "-c", "print('phase5-process-ok')"], "cwd": str(tmp_path),
                "target_session_id": sid, "name": "Phase 5 short process",
            }))
            assert process.get("jobId"), process
            pjob = runtime.request("GET", "/api/jobs/" + process["jobId"])
            assert pjob.get("job", {}).get("config", {}).get("actionId") == "system_background_process", pjob
            # Ensure the short process completed and its runner/process were cleaned.
            for _ in range(400):
                pjob = runtime.request("GET", "/api/jobs/" + process["jobId"])["job"]
                if pjob.get("status") == "completed" and not pjob.get("execution"):
                    break
                await asyncio.sleep(.025)
            assert pjob["status"] == "completed", pjob
            queried = _payload(await client.call_tool("agent_background_get", {"job_id": process["jobId"]}))
            assert queried.get("jobId") == process["jobId"], queried
            # No second old Job may be created behind the compatibility call.
            all_jobs = runtime.request("GET", "/api/jobs", headers={"Authorization": "Bearer " + runtime.request(
                "POST", "/api/automation/login", json={"key": (runtime.data_root / "automation/user.key").read_text(encoding="utf-8")})["token"]})["jobs"]
            assert sum(item["jobId"] == process["jobId"] for item in all_jobs) == 1
            assert sum(item.get("retryOf") == process["jobId"] for item in all_jobs) == 0
            assert {item['jobId'] for item in all_jobs} == {message['jobId'], process['jobId']}, all_jobs
            assert len(pjob['attempts']) == 1 and pjob['lastExecution']['processJobId'] == process['jobId']
            assert pjob.get('processTreeEnded') is True, pjob
            def native_gone():
                for field, birth_field in [('pid', 'processCreatedAt'), ('runnerPid', 'runnerProcessCreatedAt')]:
                    try:
                        current = psutil.Process(pjob[field])
                        if abs(current.create_time() - pjob[birth_field]) < .01:
                            return False
                    except psutil.NoSuchProcess:
                        pass
                return True
            h._wait_for(native_gone, label='short process and Runner fully retired')
            proof.update(messageJobId=message['jobId'], processJob=pjob)

            key = (runtime.data_root / 'automation/user.key').read_text(encoding='utf-8')
            auth = {'Authorization': 'Bearer ' + runtime.request('POST', '/api/automation/login', json={'key': key})['token']}
            permissions = runtime.request('GET', '/api/automation/authorization', headers=auth)['permissions']
            assert not any(bit for row in permissions.values() for bit in row.values())
            proof['agentPermissions'] = permissions
            task = _payload(await client.call_tool('scheduler_create', {'target_session_id': sid,
                'name': 'Phase5-due-once', 'text': 'phase5-scheduler-business-effect', 'kind': 'once',
                'at': (datetime.now() + timedelta(seconds=4)).replace(microsecond=0).isoformat()}))
            assert task.get('ok'), task
            task_id = task['task']['id']
            switched = runtime.request('POST', '/api/scheduler/tasks/' + task_id + '/owner-switch')
            assert switched.get('switched'), switched
            scheduled = next(item for item in runtime.request('GET', '/api/jobs', headers=auth)['jobs'] if item.get('taskId') == task_id)
            assert scheduled['schedulerOwner'] == 'event'
            source_id, trigger_id, job_id = scheduled['legacySourceId'], scheduled['legacyTriggerId'], scheduled['jobId']
            settled = await _finished(runtime, job_id, auth)
            assert settled['status'] == 'completed', settled
            assert len(settled['attempts']) == 1 and settled['lastExecution']['actionId'] == 'system_scheduled_task', settled
            with sqlite3.connect(runtime.data_root / 'automation/events.sqlite3') as db:
                events = [json.loads(row[0]) for row in db.execute('SELECT event FROM events')]
            occurrences = [event for event in events if event['type'] == 'scheduler.time.v1' and event['payload']['scheduleId'] == source_id]
            assert len(occurrences) == 1, occurrences
            ledger = runtime.request('GET', '/api/automation', headers=auth)
            runs = [row for row in ledger['runs'].values() if row['triggerId'] == trigger_id]
            assert len(runs) == 1 and runs[0]['status'] == 'succeeded', runs
            proof.update(schedulerTaskId=task_id, schedulerJob=settled, occurrence=occurrences[0], triggerRun=runs[0])

            periodic = _payload(await client.call_tool('scheduler_create', {'target_session_id': sid,
                'name': 'Phase5-periodic', 'text': 'phase5-manual-business-effect', 'kind': 'interval', 'interval_sec': 3600}))
            assert periodic.get('ok'), periodic
            periodic_id = periodic['task']['id']
            before = periodic['task']['nextFireAt']
            manual = _payload(await client.call_tool('scheduler_run_now', {'task_id': periodic_id}))
            assert manual.get('ok'), manual
            current = _payload(await client.call_tool('scheduler_get', {'task_id': periodic_id}))
            assert current['task']['nextFireAt'] == before, current
            pause = _payload(await client.call_tool('scheduler_pause', {'task_id': periodic_id}))
            assert pause.get('ok') and pause['task']['paused'], pause
            resume = _payload(await client.call_tool('scheduler_resume', {'task_id': periodic_id}))
            assert resume.get('ok') and not resume['task']['paused'], resume
            proof.update(periodicTaskId=periodic_id, manual=manual, pause=pause, resume=resume)

            spawned = h._spawn(runtime, sid)
            assert not spawned.get('error'), spawned
            delivered = _payload(await client.call_tool('agent_message_job_create', {
                'text': 'phase5-real-legacy-message', 'schedule': {'type': 'once', 'delaySeconds': 2},
                'target_session_id': sid}))
            assert delivered.get('jobId'), delivered
            delivered_job = await _finished(runtime, delivered['jobId'])
            assert delivered_job['lastExecution']['actionId'] == 'system_session_message'
            assert len(delivered_job['attempts']) == 1, delivered_job
            delivered_read = _payload(await client.call_tool('agent_message_job_get', {'job_id': delivered['jobId']}))
            assert delivered_read['nextRunAt'] is None and delivered_read['schedule']['type'] == 'once', delivered_read
            received = h._wait_for(lambda: h._received(runtime, text=lambda text: 'phase5-real-legacy-message' in text),
                                  label='legacy message delivered through real queue and owned CLI')
            assert len(received) == 1, received
            proof.update(deliveredMessage=delivered_job, providerReceipt=received[0])

            receipts = {}
            for marker in ['phase5-scheduler-business-effect', 'phase5-manual-business-effect']:
                rows = h._wait_for(lambda: h._received(runtime, text=lambda text: marker in text),
                                   label=marker + ' received by owned CLI')
                assert len(rows) == 1, rows
                receipts[marker] = rows[0]
            proof['schedulerBusinessReceipts'] = receipts

            periodic_job = next(item for item in runtime.request('GET', '/api/jobs', headers=auth)['jobs'] if item.get('taskId') == periodic_id)
            assert periodic_job['lastExecution']['status'] == 'completed' and not periodic_job.get('execution')
            assert 'eventId' not in periodic_job['lastExecution'], periodic_job
            proof['manualJobRun'] = periodic_job['lastExecution']
            source = next(item for item in runtime.request('GET', '/api/scheduler/event-sources', headers=auth)['sources'] if item['id'] == periodic_job['legacySourceId'])
            from tests.test_automation_core import action
            pause_action = action([sid]); pause_action.update(id='phase5_pause', name='Phase5 Pause target')
            runtime.request('POST', '/api/actions/actions', headers=auth, json={'item': pause_action})
            browser_input = OUT / 'browser-input.json'
            browser_input.write_text(json.dumps({'sourceId': source['id'], 'sourceName': source['name']}), encoding='utf-8')
            browser = subprocess.run(['node', 'packages/web/e2e/job-chain-phase5.live.mjs', runtime.base_url,
                str(browser_input), str(OUT / 'browser')], env={**env, 'PAN_TEST_JOB_KEY': key},
                capture_output=True, text=True, encoding='utf-8', timeout=120, **command_window_options())
            (OUT / 'browser.log').write_text(browser.stdout + '\n' + browser.stderr, encoding='utf-8')
            assert browser.returncode == 0, browser.stdout + browser.stderr
        runtime.stop()
        runtime.start()
        auth = {'Authorization': 'Bearer ' + runtime.request('POST', '/api/automation/login', json={'key': key})['token']}
        fresh = runtime.request('GET', '/api/jobs/' + job_id, headers=auth)['job']
        assert len(fresh['attempts']) == 1 and fresh['lastExecution']['eventId'] == occurrences[0]['id']
        with sqlite3.connect(runtime.data_root / 'automation/events.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM events WHERE id=?', (occurrences[0]['id'],)).fetchone()[0] == 1
        env['PAN_PLUGIN_IPC'] = (runtime.data_root / 'owned-plugin-ipc.txt').read_text(encoding='utf-8')
        params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(h.ROOT), env=env)
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            await client.initialize()
            same = _payload(await client.call_tool('agent_background_get', {'job_id': process['jobId']}))
            assert same['jobId'] == process['jobId'] and same['status'] == 'completed', same
            same_task = _payload(await client.call_tool('scheduler_get', {'task_id': task_id}))
            assert same_task['task']['nextFireAt'] is None, same_task
            listed_tasks = _payload(await client.call_tool('scheduler_list', {}))
            assert task_id in {row['id'] for row in listed_tasks['tasks']}
            history = _payload(await client.call_tool('scheduler_runs', {'task_id': task_id}))
            assert len(history['runs']) == 1, history
            deleted = _payload(await client.call_tool('scheduler_delete', {'task_id': periodic_id}))
            assert deleted.get('deleted'), deleted
            sources_after_delete = runtime.request('GET', '/api/scheduler/event-sources', headers=auth)['sources']
            shared = next(item for item in sources_after_delete if item['id'] == source['id'])
            assert len(shared['references']) == 1 and shared['references'][0]['jobId'] != periodic_job['jobId']
            proof.update(schedulerHistory=history, schedulerDelete=deleted, sharedSourceAfterDelete=shared)
        proof['restart'] = {'eventCount': 1, 'attemptCount': len(fresh['attempts']), 'sameJobAddressable': True}
    finally:
        runtime.stop()
        proof['portReleased'] = h._port_is_free(port)
        (OUT / 'http-stdio.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        (OUT / 'server.log').write_text((runtime.data_root / 'pan.log').read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
        assert proof['portReleased']


@async_test
async def test_owned_host_crash_retains_unknown_identity_and_unmappable_legacy(tmp_path, monkeypatch):
    """Crash the actual host after native process admission, then recover it."""
    port = _ephemeral_port()
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', Path(__file__).parent / 'support/job_chain_phase5_server.py')
    rt = h._PanRuntime(tmp_path / 'crash-profile')
    rt.data_root.mkdir()
    manifest = rt.data_root / 'owned-manifest.json'
    manifest.write_text(json.dumps({'mcp_servers': [{'name': 'pan', 'command': sys.executable,
        'args': ['-m', 'packages.mcp.server'], 'cwd': str(h.ROOT)}]}), encoding='utf-8')
    (rt.data_root / 'config.json').write_text(json.dumps({'plugin_manifests': [str(manifest)],
        'scheduler': {'enabled': True}}), encoding='utf-8')
    proof = {'port': port, 'profile': str(rt.data_root), 'crash': 'kill only captured owned host PID/create-time identities'}
    owned = {}
    def capture(job):
        for field in ('pid', 'runnerPid'):
            pid = job.get(field)
            if pid:
                try:
                    p = psutil.Process(pid)
                    for member in [p, *p.children(recursive=True)]:
                        owned[member.pid] = member.create_time()
                except psutil.NoSuchProcess:
                    pass
    def params(sid):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_')}
        env.update(PAN_API_URL=rt.base_url, PAN_AGENT_SESSION_ID=sid,
                   PAN_PLUGIN_IPC=(rt.data_root / 'owned-plugin-ipc.txt').read_text(encoding='utf-8'))
        return StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(h.ROOT), env=env)
    try:
        rt.start()
        session = rt.request('POST', '/api/sessions', json={'name': 'Phase5-crash-caller', 'adapter': 'cbc',
            'model': 'deepseek-v4-flash', 'mcpServers': ['pan']})
        assert session.get('id'), session
        sid = session['id']
        marker = tmp_path / 'business-effect.jsonl'
        code = ('import pathlib,time; p=pathlib.Path(' + repr(str(marker)) + '); '
                'p.open("a",encoding="utf-8").write("executed\\n"); time.sleep(20)')
        async with stdio_client(params(sid)) as streams, ClientSession(*streams) as client:
            await client.initialize()
            created = _payload(await client.call_tool('agent_background_start', {'argv': [sys.executable, '-c', code],
                'cwd': str(tmp_path), 'name': 'Phase5 crash uncertainty', 'target_session_id': sid}))
            assert created.get('jobId'), created
            jid = created['jobId']
            job = h._wait_for(lambda: (j if j.get('pid') and marker.exists() else None)
                if (j := rt.request('GET', '/api/background-jobs/' + jid)) else None,
                label='native command admitted before owned host crash')
            capture(job)
        rt._capture_owned_processes()
        crashed = []
        for pid, birth, command in reversed(rt._owned_processes):
            if str(h.SERVER_LAUNCHER) not in command:
                continue
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - birth) < .01:
                    crashed.append(pid)
                    process.kill()
                    process.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        assert crashed
        rt._ready = False
        rt.stop()
        # An existing record with an unknown schedule field must retain its old
        # owner and bytes. This fixture is written only while the host is down.
        from packages.scheduler import store
        legacy = store._job_from_payload({'id': 'sch_phase5_unmappable', 'name': 'Phase5 unmappable',
            'target_session_id': sid, 'text': 'retained', 'enabled': False,
            '_schedule_specs': [{'kind': 'interval', 'intervalSec': 3600}], '_misfire': 'skip', '_max_runs': None})
        legacy['schedule'][0]['futureField'] = {'must': 'survive'}
        legacy_path = rt.data_root / 'background_jobs/jobs' / (legacy['jobId'] + '.json')
        legacy_path.write_text(json.dumps(legacy), encoding='utf-8')
        rt.start()
        async with stdio_client(params(sid)) as streams, ClientSession(*streams) as client:
            await client.initialize()
            recovered = _payload(await client.call_tool('agent_background_get', {'job_id': jid}))
            assert recovered['jobId'] == jid and recovered['status'] == 'uncertain', recovered
            assert len(recovered['attempts']) == 1 and recovered['lastExecution']['status'] == 'uncertain'
            retry = _payload(await client.call_tool('agent_background_retry', {'job_id': jid}))
            assert retry.get('error', {}).get('code') == 'job_not_retryable', retry
            assert len(_payload(await client.call_tool('agent_background_list', {}))['jobs']) == 1
            assert marker.read_text(encoding='utf-8').splitlines() == ['executed']
            unchanged = rt.request('POST', '/api/scheduler/tasks/sch_phase5_unmappable/owner-switch')
            assert unchanged['error']['code'] == 'legacy_unmappable', unchanged
            stored = json.loads(legacy_path.read_text(encoding='utf-8'))
            assert stored['schedule'][0]['futureField'] == {'must': 'survive'}
            assert not stored.get('schedulerOwner') and not stored.get('legacySchedulerCompat')
            key = (rt.data_root / 'automation/user.key').read_text(encoding='utf-8')
            auth = {'Authorization': 'Bearer ' + rt.request('POST', '/api/automation/login', json={'key': key})['token']}
            assert rt.request('GET', '/api/scheduler/event-sources', headers=auth)['sources'] == []
            paused = _payload(await client.call_tool('scheduler_pause', {'task_id': 'sch_phase5_unmappable'}))
            assert paused.get('ok') and paused['task']['paused'], paused
            cancelled = _payload(await client.call_tool('agent_background_cancel', {'job_id': jid}))
            assert cancelled['status'] == 'cancelled', cancelled
            assert cancelled['lastExecution']['status'] == 'uncertain'
            proof.update(jobId=jid, crashedHostPids=crashed, recovered=recovered, retry=retry,
                         cancelled=cancelled, unmappable=stored, legacyPause=paused)
        rt.stop(); rt.start()
        same = rt.request('GET', '/api/background-jobs/' + jid)
        assert len(same['attempts']) == 1 and marker.read_text(encoding='utf-8').splitlines() == ['executed']
        proof['secondRestartNoReplay'] = True
    finally:
        rt.stop()
        remaining = []
        for pid, birth in owned.items():
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - birth) < .01:
                    remaining.append(p)
            except psutil.NoSuchProcess:
                pass
        _, remaining = psutil.wait_procs(remaining, timeout=3)
        before_cleanup = [p.pid for p in remaining]
        for p in remaining:
            p.kill()
        _, remaining = psutil.wait_procs(remaining, timeout=3)
        proof.update(ownedProcessesRemaining=[p.pid for p in remaining], fallbackCleanupPids=before_cleanup,
                     portReleased=h._port_is_free(port))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'crash-restart.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        log = rt.data_root / 'pan.log'
        if log.exists():
            (OUT / 'crash-server.log').write_text(log.read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
        assert not remaining and proof['portReleased']

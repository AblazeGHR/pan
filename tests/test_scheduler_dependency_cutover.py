"""Production import/startup and original safety controls after source cutover."""
import json
import subprocess
import sys
from pathlib import Path

from packages.jobs.window import command_window_options


def test_all_production_consumers_have_no_static_legacy_host_imports():
    from scripts.check_scheduler_dependencies import scan
    result = scan(Path(__file__).resolve().parents[1])
    assert result["pythonParseErrors"] == []
    assert result["staticLegacyHostImports"] == []
    assert result["literalDynamicLegacyHostImports"] == []
    assert result["unreviewedProductionDynamicImports"] == []


def test_web_import_and_canonical_startup_reopen_without_loading_either_legacy_host(tmp_path):
    script = r'''
import asyncio, importlib.abc, json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
root = Path(sys.argv[1]).resolve()
# Windows asyncio creates its private socketpair when the loop is constructed.
# Construct it before the guard; all subsequent application networking is denied.
loop = asyncio.new_event_loop()
denied = {'packages.scheduler.store', 'packages.scheduler.time_events'}
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname in denied:
            raise AssertionError('Production tried to load ' + fullname)
sys.meta_path.insert(0, Block())
def audit(event, args):
    if event in {'socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('Unexpected network/process effect: ' + event)
sys.addaudithook(audit)
sys.dont_write_bytecode = True
from packages.core.platform import data_paths
from packages.core import config, background_jobs, session
data_paths.DEFAULT_ROOT = background_jobs.DEFAULT_ROOT = root / 'background_jobs'
config.CONFIG_FILE = root / 'config.json'
session.SESSION_DIR = root / 'sessions'
import packages.web.server
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService
from packages.scheduler.providers import time_definition
USER = {'type':'user', 'id':'local'}
async def run():
    service = FinalService(root / 'canonical', CanonicalUserAuth(root / 'auth'))
    await service.start()
    try:
        point = (datetime.now(timezone.utc) + timedelta(seconds=.3)).isoformat()
        definition = time_definition('owned-once', {'rules':[{'ruleId':'once', 'type':'once', 'at':point}],
            'misfirePolicy':'fire_now', 'graceSeconds':'60', 'pausePolicy':'advance'})
        item = service.write('event_creator', None, {'definition':definition, 'expectedRevision':0,
            'requestKey':'once'}, actor=USER)['result']['object']
        service.control('event_creator', item['id'], {'operation':'enable', 'expectedRevision':1,
            'expectedStateRevision':0, 'requestKey':'enable-once', 'parameters':{}}, actor=USER)
        for _ in range(160):
            if (service.events.stats() == {'events':1, 'pins':0}
                    and service.creators.get(item['id'])['producedCount'] == 1):
                break
            await asyncio.sleep(.05)
        assert service.events.stats() == {'events':1, 'pins':0}, service.creator_runtime.diagnostics
        event_id = service.events.page()[0]['eventId']
        assert service.creators.get(item['id'])['producedCount'] == 1
    finally:
        await service.close()
    reopened = FinalService(root / 'canonical', CanonicalUserAuth(root / 'auth'))
    await reopened.start()
    try:
        await asyncio.sleep(.3)
        assert reopened.events.stats() == {'events':1, 'pins':0}
        assert reopened.events.page()[0]['eventId'] == event_id
        assert reopened.creators.get(item['id'])['producedCount'] == 1
        assert not denied.intersection(sys.modules)
        print(json.dumps({'webImport':True, 'legacyHostsLoaded':[], 'ownerEpoch':reopened.owner.token[0],
            'events':1, 'producedCount':1, 'pins':0, 'canonicalStartupAndReopen':True}))
    finally:
        await reopened.close()
try:
    loop.run_until_complete(run())
finally:
    loop.close()
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            capture_output=True, text=True, timeout=30, **command_window_options())
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    print("STARTUP_EVIDENCE " + json.dumps(evidence, sort_keys=True))
    assert evidence["ownerEpoch"] == 2 and evidence["canonicalStartupAndReopen"]
    assert evidence["legacyHostsLoaded"] == []
    (tmp_path / "startup-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")


def test_extracted_delete_preserves_original_protected_guard_and_reservation(tmp_path, monkeypatch):
    import pytest
    from packages.core import background_jobs
    from packages.core.platform import data_paths
    from packages.jobs.scheduled_records import delete_task
    from packages.jobs.runtime_owner import OwnerFence
    from packages.jobs import final_contracts as fc
    monkeypatch.setattr(data_paths, "DEFAULT_ROOT", tmp_path / "jobs")
    record = {"jobId":"job_protected", "taskId":"sch_original", "kind":"scheduled-task",
              "status":"pending", "action":{"api":"action_reference", "actionId":"protected"}}
    background_jobs._create(record, registry_root=tmp_path / "jobs")
    before = (tmp_path / "jobs" / "jobs" / "job_protected.json").read_bytes()
    with pytest.raises(ValueError, match="protected Action"):
        delete_task("sch_original")
    owner = OwnerFence(tmp_path)
    owner.claim(0)
    owner.set_state("active")
    try:
        with pytest.raises(fc.ContractError, match="fence"):
            delete_task("sch_original")
        assert (tmp_path / "jobs" / "jobs" / "job_protected.json").read_bytes() == before
    finally:
        owner.release()


def test_legacy_job_pass_never_drives_injected_legacy_time_runtime(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from packages.jobs.action_runtime import Runtime
    calls = []
    async def injected(cfg):
        calls.append(cfg)
    async def publish():
        return None
    runtime = Runtime.__new__(Runtime)
    runtime.root = tmp_path / "jobs"
    runtime.closed = False
    runtime.tasks = {}
    runtime.diagnostics = []
    runtime.time_events = SimpleNamespace(tick=injected)
    runtime.recover = lambda force: None
    runtime.publish_events = publish
    asyncio.run(runtime.tick({"enabled":False}))
    assert calls == [] and runtime.diagnostics == []

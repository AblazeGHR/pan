"""Owned automation browser fixture; product routes and effects stay real."""
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('automation_browser_fixture', Path(__file__).with_name('server.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
assert fixture.RUNTIME.is_relative_to(ROOT / 'packages/web/test-results')
assert fixture.PORT not in (8767, 8768)
assert os.environ['PAN_E2E_FAKE_CLI'] == str(ROOT / 'tests/support/frontend_stream_cli.py')

original_isolation = fixture._install_test_isolation
def isolate(server):
    original_isolation(server)
    # These established services use repo-relative paths independently of
    # server.DATA_DIR. Keep the fixture's auxiliary startup writes local too.
    server._startup_recovery_record_path = lambda: fixture.RUNTIME / 'startup-recovery' / 'record.json'
    server._main_restart_registry_root = lambda: fixture.RUNTIME / 'background-jobs'
    server.gateway_plugins.MANIFEST = fixture.RUNTIME / 'qq-plugins' / 'manifest.json'
fixture._install_test_isolation = isolate

def seed():
    from packages.core import session
    from packages.actions.store import Store
    from packages.automation.store import Ledger
    fixture.WORKDIR.mkdir(parents=True, exist_ok=True)
    ids = {}
    for name in ('Selected target', 'Control target', 'Event producer'):
        item = session.create(name, adapter='cbc', workdir=str(fixture.WORKDIR), notification_settings={'browser': False, 'system': False})
        item.model = 'deterministic-fixture'
        item.mcp_servers = []
        if name != 'Event producer':
            # Keep queued work observable while avoiding provider consumption.
            item.queue_paused = False
            item.queue_pending = [{'id': 'fixture-pending', 'type': 'task', 'text': 'fixture pending', 'deliveryState': 'queued', 'locked': True}]
        session.save_full(item)
        ids[name] = item.id
    action = Store().put('actions', {'id': 'fixture_pause', 'name': 'Fixture pause', 'enabled': True, 'effect': 'write_local',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'execution': {'kind': 'session_operation', 'operation': 'pause_everything', 'selector': {'mode': 'explicit', 'ids': [ids['Selected target']]}}}, create=True)
    ledger = Ledger(fixture.RUNTIME / 'automation')
    trigger = ledger.put({'id': 'trg_waiting', 'name': 'Waiting dependency fixture', 'enabled': True, 'eventTypes': ['fixture.unavailable'], 'actionId': action['id']}, 0, True)
    for rid, status in [('run_queued_fixture', 'queued'), ('run_interrupted_fixture', 'running')]:
        ledger.append({'id': rid, 'triggerId': trigger['id'], 'triggerRevision': trigger['revision'], 'actionId': action['id'], 'arguments': {}, 'createdAt': '2026-10-22T00:00:00Z', 'status': status,
            'event': {'id': 'evt_' + rid, 'type': 'fixture.unavailable', 'occurredAt': '2026-10-22T00:00:00Z', 'payload': {}}})
    (fixture.RUNTIME / 'fixtures.json').write_text(json.dumps(ids), encoding='utf-8')

fixture._seed_sessions = seed
fixture.main()

import asyncio

import pytest
from packages.core import config, legal_offline, session, worker
from packages.web import server
from packages.automation.authorization import object_path


def test_behavior_global_legacy_default_and_session_round_trip():
    assert config.illegal_queue_lock_behavior({"ui": {}}) is True
    assert config.illegal_queue_lock_behavior({"ui": {"behavior": {"autoLockIllegalQueue": False}}}) is False
    current = session.Session(id="behavior", name="behavior", behavior_override={"auto_lock_illegal_queue": False})
    assert current.to_dict()["behavior_override"] == {"auto_lock_illegal_queue": False}


def test_illegal_new_queue_lock_respects_session_override(monkeypatch):
    current = session.Session(id="behavior", name="behavior")
    current.execution_operations = {"op": {"id": "op", "state": "illegal", "safeTerminal": False}}
    current.illegal_batches = {"op": {"id": "op", "messages": [], "recovered": False}}
    monkeypatch.setattr(legal_offline, "illegal_queue_lock_behavior", lambda: False)
    unlocked = {"id": "u", "queueItemId": "u"}
    worker._stage_new_queue_item(current, unlocked)
    assert unlocked.get("queueLockSource") is None

    current.behavior_override = {"auto_lock_illegal_queue": True}
    locked = {"id": "l", "queueItemId": "l"}
    worker._stage_new_queue_item(current, locked)
    assert locked["queueLockSource"] == "illegal"


def test_disabled_policy_does_not_lock_existing_queue_on_detection():
    current = session.Session(id="behavior", name="behavior", behavior_override={"auto_lock_illegal_queue": False})
    identity = {"token": "old", "generation": 1}
    current.execution_operations = {"op": {**identity, "id": "op", "state": "running", "safeTerminal": False, "admittedRevision": 1}}
    current.offline_run = current.execution_operations["op"]
    current.queue_pending = [{"id": "queued", "queueItemId": "queued"}]
    assert legal_offline.detect(current, "test", identity=identity, eligible=lambda _: True, remember=lambda _: None)
    assert current.queue_pending[0].get("queueLockSource") is None
    assert legal_offline.is_illegal(current)


def test_behavior_failed_write_does_not_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSION_DIR", tmp_path)
    session._cache.clear()
    current = session.create(name="behavior-failure")
    def fail(*_args):
        raise OSError("injected write failure")
    monkeypatch.setattr(session, "_replace_session_metadata", fail)
    with pytest.raises(OSError, match="injected write failure"):
        asyncio.run(server.api_update_session(current.id, {"behavior": {
            "syncWithGlobal": False, "autoLockIllegalQueue": False, "expectedRevision": 0}}))
    assert current.behavior_override is None
    assert current.behavior_revision == 0


def test_behavior_concurrent_cas_accepts_one(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSION_DIR", tmp_path)
    session._cache.clear()
    current = session.create(name="behavior-cas")
    async def run():
        async def update(value):
            return await server.api_update_session(current.id, {"behavior": {
                "syncWithGlobal": False, "autoLockIllegalQueue": value, "expectedRevision": 0}})
        return await asyncio.gather(update(True), update(False))
    results = asyncio.run(run())
    assert sum(result.get("code") == "behavior_revision_conflict" for result in results) == 1
    assert current.behavior_revision == 1


def test_behavior_is_outside_delegated_automation_http_boundary():
    assert not object_path('/api/settings/ui')
    assert not object_path('/api/sessions/ses-example')


def test_behavior_rejects_missing_revision_and_mixed_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(session, 'SESSION_DIR', tmp_path)
    session._cache.clear()
    current = session.create(name='behavior-validation')
    async def run():
        missing = await server.api_update_session(current.id, {'behavior': {
            'syncWithGlobal': False, 'autoLockIllegalQueue': False}})
        mixed = await server.api_update_session(current.id, {'model': 'changed', 'behavior': {
            'syncWithGlobal': False, 'autoLockIllegalQueue': False, 'expectedRevision': 0}})
        return missing, mixed
    missing, mixed = asyncio.run(run())
    assert missing.get('error') and mixed.get('error')
    assert current.behavior_override is None and current.behavior_revision == 0
    assert current.model != 'changed'

@pytest.mark.parametrize('owner_status', ['alive', 'unknown'])
def test_disabled_policy_keeps_owner_and_unfinished_execution_boundary(tmp_path, monkeypatch, owner_status):
    monkeypatch.setattr(session, 'SESSION_DIR', tmp_path)
    session._cache.clear()
    current = session.create(name='behavior-owner-boundary')
    current.behavior_override = {'auto_lock_illegal_queue': False}
    identity = {'token': 'old', 'generation': 1, 'runtimeExpected': True}
    record = legal_offline.admit(current, identity, kind='foreground')
    current.offline_run = record
    legal_offline.detect(current, 'test', identity=identity, eligible=lambda _: True, remember=lambda _: None)
    session.save(current)
    before = session._path(current.id).read_bytes()
    monkeypatch.setattr(legal_offline, 'previous_owner_status', lambda _: owner_status)
    with pytest.raises(RuntimeError, match='alive or unknown'):
        asyncio.run(worker.clear_session_illegal_for_startup(current.id))
    assert session._path(current.id).read_bytes() == before
    assert legal_offline.is_illegal(current)
    assert not record['safeTerminal']


@pytest.mark.parametrize('state', ['unknown_after_crash', 'unknown', 'uncertain'])
def test_disabled_policy_does_not_authorize_unknown_receipt(state):
    current = session.Session(id='behavior-unknown', name='behavior-unknown',
                              behavior_override={'auto_lock_illegal_queue': False})
    current.queue_pending = [{'id': 'unknown', 'queueItemId': 'unknown', 'type': 'task',
                              'kind': 'task', 'source': 'user', 'text': 'test',
                              'deliveryState': state, 'dispatchState': state}]
    worker._recover_delivery_states(current)
    assert current.queue_pending[0]['deliveryState'] == state
    assert not worker._has_dispatchable_items(current)
    assert worker._select_queue_unit(current) is None


def test_disabled_policy_keeps_managed_readonly_permission(tmp_path, monkeypatch):
    monkeypatch.setattr(session, 'SESSION_DIR', tmp_path)
    session._cache.clear()
    manager = session.create(name='behavior-manager')
    child = session.create(name='behavior-child')
    manager.managed = [child.id]
    child.managed_by = manager.id
    child.readonly_session = True
    child.behavior_override = {'auto_lock_illegal_queue': False}
    result = asyncio.run(worker.send_session(child.id, 'forbidden', source='agent', source_session_id=manager.id))
    assert result['result'] == worker.READONLY_SESSION_ERROR
    assert not child.queue_pending

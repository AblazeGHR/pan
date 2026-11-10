from packages.core import config, session, worker


def test_behavior_global_legacy_default_and_session_round_trip():
    assert config.illegal_queue_lock_behavior({"ui": {}}) is True
    assert config.illegal_queue_lock_behavior({"ui": {"behavior": {"autoLockIllegalQueue": False}}}) is False
    current = session.Session(id="behavior", name="behavior", behavior_override={"auto_lock_illegal_queue": False})
    assert current.to_dict()["behavior_override"] == {"auto_lock_illegal_queue": False}


def test_illegal_new_queue_lock_respects_session_override(monkeypatch):
    current = session.Session(id="behavior", name="behavior")
    current.execution_operations = {"op": {"id": "op", "state": "illegal", "safeTerminal": False}}
    current.illegal_batches = {"op": {"id": "op", "messages": [], "recovered": False}}
    monkeypatch.setattr(worker, "illegal_queue_lock_behavior", lambda: False)
    unlocked = {"id": "u", "queueItemId": "u"}
    worker._stage_new_queue_item(current, unlocked)
    assert unlocked.get("queueLockSource") is None

    current.behavior_override = {"auto_lock_illegal_queue": True}
    locked = {"id": "l", "queueItemId": "l"}
    worker._stage_new_queue_item(current, locked)
    assert locked["queueLockSource"] == "illegal"

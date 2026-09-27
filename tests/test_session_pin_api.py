"""Shared Session pin persistence and API validation contracts."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess  # noqa: E402
import packages.web.server as srv  # noqa: E402


def _setup_session(session_id: str, *, order: int | None = None):
    session = _sess.Session(id=session_id, name=session_id, order=order)
    _sess._cache[session_id] = session
    return session


def _pin(session_id: str, pinned: bool):
    return asyncio.run(srv.api_set_session_pin(session_id, {"pinned": pinned}))


def _reorder(session_ids, revision):
    return asyncio.run(srv.api_reorder_session_pins({
        "sessionIds": session_ids,
        "pinRevision": revision,
    }))


def test_old_records_default_unpinned_and_projection_is_additive():
    session = _sess.Session._from_data({"id": "ses_legacy", "name": "Legacy"})
    _sess._cache[session.id] = session

    assert "pinned" not in session.to_dict()
    assert srv._session_summary(session)["pinned"] is False
    assert srv._session_summary(session)["pinOrder"] is None
    assert srv._session_summary(session)["pinRevision"] == 0
    assert srv._session_to_api(session)["pinned"] is False


def test_pin_state_persists_separately_from_session_order_and_survives_reload(monkeypatch):
    session = _setup_session("ses_pin_a", order=73)
    _sess.save(session)
    events = []

    async def capture(event):
        events.append(event)

    monkeypatch.setattr(srv, "broadcast", capture)
    response = _pin(session.id, True)

    assert response["ok"] is True
    assert response["pinned"] is True
    assert response["pinOrder"] == 0
    assert response["pinRevision"] == 1
    assert session.order == 73
    pin_path = _sess._pin_state_path()
    persisted = json.loads(pin_path.read_text(encoding="utf-8"))
    assert persisted == {"version": 1, "revision": 1, "sessionIds": [session.id]}
    assert "pinned" not in json.loads(_sess._path(session.id).read_text(encoding="utf-8"))
    assert events == [{
        "type": "session.pinsUpdated",
        "pinRevision": 1,
        "sessionIds": [session.id],
    }]

    _sess._cache.clear()
    _sess._all_loaded = False
    monkeypatch.setattr(_sess, "_PIN_STATE_CACHE", None)
    restored = _sess.list_all(load_history=False)[0]
    assert restored.order == 73
    assert srv._session_summary(restored)["pinned"] is True
    assert srv._session_summary(restored)["pinOrder"] == 0


def test_pin_api_rejects_missing_or_invalid_boolean():
    _setup_session("ses_pin_a")
    for body in ({}, {"pinned": 1}, {"pinned": "true"}, {"pinned": None}):
        response = asyncio.run(srv.api_set_session_pin("ses_pin_a", body))
        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_params"
    assert _sess.session_pin_state() == {"pinRevision": 0, "sessionIds": []}


def test_pin_api_rejects_unknown_session_and_unpin_is_idempotent():
    _setup_session("ses_pin_a")
    unknown = _pin("ses_missing", True)
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == "session_not_found"

    first = _pin("ses_pin_a", False)
    second = _pin("ses_pin_a", False)
    assert first["ok"] is True and second["ok"] is True
    assert first["pinRevision"] == second["pinRevision"] == 0


def test_reorder_updates_only_requested_slots_and_keeps_omitted_pins_stable(monkeypatch):
    for session_id in ("ses_a", "ses_hidden", "ses_b", "ses_other_group"):
        _setup_session(session_id)
        assert _pin(session_id, True)["ok"] is True
    current_revision = _sess.session_pin_state()["pinRevision"]
    events = []

    async def capture(event):
        events.append(event)

    monkeypatch.setattr(srv, "broadcast", capture)
    result = _reorder(["ses_b", "ses_a"], current_revision)

    assert result == {
        "ok": True,
        "pinRevision": current_revision + 1,
        "sessionIds": ["ses_b", "ses_hidden", "ses_a", "ses_other_group"],
    }
    assert events[-1]["type"] == "session.pinsUpdated"
    assert events[-1]["sessionIds"] == result["sessionIds"]
    assert [session.order for session in _sess.list_all(load_history=False)] == [None] * 4


def test_reorder_validates_missing_duplicate_unknown_unpinned_and_stale_revision():
    _setup_session("ses_a")
    _setup_session("ses_b")
    _setup_session("ses_c")
    assert _pin("ses_a", True)["ok"] is True
    assert _pin("ses_b", True)["ok"] is True
    revision = _sess.session_pin_state()["pinRevision"]

    for body in (
        {},
        {"sessionIds": ["ses_a"]},
        {"sessionIds": "ses_a", "pinRevision": revision},
    ):
        response = asyncio.run(srv.api_reorder_session_pins(body))
        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_params"

    duplicate = _reorder(["ses_a", "ses_a"], revision)
    assert duplicate["error"]["code"] == "duplicate_session_ids"
    unknown = _reorder(["ses_ghost"], revision)
    assert unknown["error"]["code"] == "session_not_found"
    unpinned = _reorder(["ses_c"], revision)
    assert unpinned["error"]["code"] == "session_not_pinned"

    success = _reorder(["ses_b", "ses_a"], revision)
    assert success["ok"] is True
    conflict = _reorder(["ses_a", "ses_b"], revision)
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "pin_state_conflict"
    assert _sess.session_pin_state()["sessionIds"] == ["ses_b", "ses_a"]


def test_failed_atomic_write_leaves_pin_snapshot_unchanged(monkeypatch):
    _setup_session("ses_a")

    def fail_replace(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(_sess.os, "replace", fail_replace)
    response = _pin("ses_a", True)

    assert response["ok"] is False
    assert response["error"]["code"] == "pin_persist_failed"
    assert _sess.session_pin_state() == {"pinRevision": 0, "sessionIds": []}

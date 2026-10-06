"""Premerge compatibility: synthetic legacy fixtures, never the live data root.

Read-only defaults must not silently rewrite old persistence. Explicit metadata
updates may write new fields, but must preserve process identity and exit facts.
"""

import copy
import json

import pytest

from packages.core import background_jobs
from packages.core.rewind.storage import RewindRecordStore
from packages.core.session import Session
from packages.core.terminal.contracts import RuntimeState, TerminalRecord
from packages.core.terminal.registry import TerminalRegistry


def test_legacy_terminal_reads_do_not_migrate_or_lose_identity(tmp_path):
    root = tmp_path / "terminals"
    root.mkdir()
    tid = "term_legacy_premerge"
    raw = {
        "schema_version": 1, "terminal_id": tid, "status": "exited",
        "pid": 4242, "process_created_at_filetime": 134354347461843769,
        "scope": {"session_id": "ses_old", "workspace_id": "ws_old"},
        "exit": {"code": 7, "reason": "natural-exit"},
    }
    path = root / f"{tid}.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    registry = TerminalRegistry(root)
    for _ in range(3):
        record = registry.get(tid)
        assert registry.list()[0].terminal_id == tid
        assert record.archived is False and record.cleanup_pending is False
        assert record.pid == 4242
        assert record.process_created_at_filetime == raw["process_created_at_filetime"]
        assert record.status is RuntimeState.EXITED
        assert record.exit_code == 7 and record.scope.session_id == "ses_old"
        assert path.read_bytes() == before
    for archived in (True, False):
        registry.update(tid, lambda item: setattr(item, "archived", archived))
        saved = registry.get(tid)
        assert saved.archived is archived
        assert saved.pid == record.pid
        assert saved.process_created_at_filetime == record.process_created_at_filetime
        assert saved.scope == record.scope
        assert saved.exit_code == record.exit_code


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_new_terminal_flags_require_real_boolean(value):
    record = TerminalRecord.from_dict({
        "terminal_id": "term_legacy_flags", "archived": value,
        "cleanup_pending": value, "scope": None, "exit": None,
    })
    assert record.archived is False and record.cleanup_pending is False


def test_old_session_and_absent_rewind_remain_separate(tmp_path):
    payload = {
        "id": "ses_legacy_premerge", "name": "旧会话", "adapter": "cbc",
        "cbc_session_id": "old-cli-id", "history": [],
        "queue_pending": [{"type": "task", "text": "旧任务"}],
    }
    path = tmp_path / "old-session.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    first = Session._from_data(copy.deepcopy(payload))
    second = Session._from_data(copy.deepcopy(payload))
    assert first.adapter_config["cli_session_id"] == "old-cli-id"
    assert first.history_epoch == second.history_epoch == "legacy:ses_legacy_premerge"
    assert first.queue_pending == payload["queue_pending"]
    rewind_root = tmp_path / "rewind"
    assert RewindRecordStore(rewind_root).load(first.id, "job_absent") is None
    assert not rewind_root.exists()
    assert path.read_bytes() == before


def test_mixed_legacy_job_timestamps_read_without_rewriting(tmp_path):
    root = tmp_path / "jobs-root"
    jobs = root / "jobs"
    jobs.mkdir(parents=True)
    values = ["2020-01-01T00:00:00Z", 1577836801, None, True, "invalid", "inf"]
    paths = []
    for index, timestamp in enumerate(values):
        payload = {"jobId": f"job_legacy{index}", "status": "completed"}
        if timestamp is not None:
            payload["createdAt"] = timestamp
        path = jobs / f"{payload['jobId']}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append((path, path.read_bytes(), payload))
    for _ in range(3):
        listed = background_jobs.list_jobs(root)
        assert len(listed) == len(values)
        assert listed[0]["jobId"] == "job_legacy1"
        for path, before, payload in paths:
            view = background_jobs.get(payload["jobId"], root)
            assert view["status"] == "completed"
            assert view.get("createdAt") == payload.get("createdAt")
            assert view["kind"] == background_jobs.BACKGROUND_PROCESS_KIND
            assert view["creatorSessionId"] is None
            assert path.read_bytes() == before

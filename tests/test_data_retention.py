"""Registered-root-only retention tests. Every candidate lives under tmp_path."""

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import data_retention as retention
from packages.core import session as sess
import packages.web.server as srv


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).isoformat()


def write_session(root: Path, session_id: str, *, updated_at: str | None,
                  history: list[dict] | None = None, **fields):
    root.mkdir(parents=True, exist_ok=True)
    row = {
        "id": session_id,
        "name": session_id,
        "created_at": updated_at or "",
        "updated_at": updated_at,
        "history": [],
        "queue_pending": [],
        "queue_delivery_ledger": {},
        "managed": [],
        "managed_by": None,
        **fields,
    }
    (root / f"{session_id}.json").write_text(json.dumps(row), encoding="utf-8")
    if history is not None:
        (root / f"{session_id}.history.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in history), encoding="utf-8",
        )
    return row


def attachment_owner_dir(root: Path, session_id: str) -> Path:
    sid_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    safe = "".join(c if c.isalnum() or c in "_.-" else "_" for c in session_id).strip("._") or "session"
    target = root / f"{safe[:80]}-{sid_hash}"
    target.mkdir(parents=True)
    return target


def attachment_record(root: Path, session_id: str, filename: str,
                      created_at: str | None = None, *, completed: bool = True):
    owner = attachment_owner_dir(root, session_id)
    target = owner / filename
    target.write_bytes(b"temporary test payload")
    sidecar = {
        filename: {
            "source": "upload",
            "sessionId": session_id,
            "storageFilename": filename,
            "completed": completed,
            **({"createdAt": created_at} if created_at is not None else {}),
        },
    }
    (owner / ".attachments.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return owner, target, owner / ".attachments.json"


def test_retention_policy_validation_is_strict_and_defaults_off():
    assert all(not item["enabled"] for item in retention.DEFAULT_POLICIES.values())
    assert all(item["days"] is None for item in retention.DEFAULT_POLICIES.values())
    updated = retention.validate_policy_update(
        {"policies": {"sessions": {"enabled": True, "days": 7}}},
        retention.DEFAULT_POLICIES,
    )
    assert updated["sessions"] == {"enabled": True, "days": 7}
    assert updated["attachments"] == {"enabled": False, "days": None}
    empty_days = retention.validate_policy_update(
        {"policies": {"sessions": {"enabled": True, "days": None}}},
        retention.DEFAULT_POLICIES,
    )
    assert empty_days["sessions"] == {"enabled": True, "days": None}
    for invalid in (
        {"policies": {"sessions": {"enabled": 1, "days": 7}}},
        {"policies": {"sessions": {"enabled": True, "days": True}}},
        {"policies": {"sessions": {"enabled": True, "days": 0}}},
        {"policies": {"config": {"enabled": True, "days": 7}}},
        {"path": "C:/anything", "policies": {"sessions": {"enabled": True, "days": 7}}},
    ):
        with pytest.raises(ValueError):
            retention.validate_policy_update(invalid, retention.DEFAULT_POLICIES)


def test_session_expiry_uses_latest_trusted_update_and_history_timestamp(tmp_path):
    root = tmp_path / "sessions"
    now = datetime(2026, 9, 27, 12, 0).timestamp()
    cutoff = now - 30 * 86400
    write_session(root, "old", updated_at=iso(cutoff - 1), history=[{"role": "user", "ts": iso(cutoff - 1)}])
    write_session(root, "recent-history", updated_at=iso(cutoff - 60), history=[{"role": "user", "ts": iso(cutoff + 1)}])
    write_session(root, "missing-update", updated_at=None, history=[{"role": "user", "ts": iso(cutoff - 100)}])
    write_session(root, "future", updated_at=iso(now + 5), history=[])
    (root / "corrupt.history.jsonl").write_text("{broken\n", encoding="utf-8")
    (root / "corrupt.json").write_text(json.dumps({"id": "corrupt", "updated_at": iso(cutoff - 1)}), encoding="utf-8")
    deleted = []

    result = retention.scan_sessions(
        root, 30, now,
        lambda sid, record: (deleted.append(sid) or {"deleted": True}),
    )

    assert deleted == ["old"]
    assert result["deleted"] == 1
    assert result["skipped"] == 3
    assert result["skipReasons"]["session_updated_at_unknown"] == 1
    assert result["skipReasons"]["future_timestamp"] == 1
    assert result["skipReasons"]["history_corrupt"] == 1
    assert (root / "recent-history.json").exists()


def test_session_guard_protects_worker_queue_managed_and_job_references(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(sess, "SESSION_DIR", root)
    monkeypatch.setattr(srv.reminders, "REMINDER_PATH", tmp_path / "reminders.json")
    write_session(root, "queued", updated_at=iso(1), queue_pending=[{"text": "pending"}])
    assert srv._retention_reference_reason("queued", json.loads((root / "queued.json").read_text())) == "queue_pending"

    write_session(root, "managed", updated_at=iso(1), managed=["child"])
    assert srv._retention_reference_reason("managed", json.loads((root / "managed.json").read_text())) == "managed_relationship"

    write_session(root, "camel-target", updated_at=iso(1))
    write_session(root, "camel-manager", updated_at=iso(1), managedBy="camel-target")
    assert srv._retention_reference_reason("camel-target", json.loads((root / "camel-target.json").read_text())) == "referenced_by_other_session"

    write_session(root, "referenced", updated_at=iso(1))
    write_session(root, "manager", updated_at=iso(1), managed=["referenced"])
    assert srv._retention_reference_reason("referenced", json.loads((root / "referenced.json").read_text())) == "referenced_by_other_session"

    external_jobs = tmp_path / "registered-jobs"
    (external_jobs / "jobs").mkdir(parents=True)
    job_file = external_jobs / "jobs" / "job_running.json"
    job_file.write_text(json.dumps({
        "jobId": "job_running", "kind": "session-message", "status": "running",
        "targetSessionId": "job-target",
    }), encoding="utf-8")
    write_session(root, "job-target", updated_at=iso(1))
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(external_jobs))
    assert srv._retention_reference_reason("job-target", json.loads((root / "job-target.json").read_text())) == "running_job_reference"

    scheduled = json.loads(job_file.read_text())
    scheduled.update(status="queued")
    job_file.write_text(json.dumps(scheduled), encoding="utf-8")
    assert srv._retention_reference_reason("job-target", json.loads((root / "job-target.json").read_text())) == "pending_job_reference"

    scheduled = json.loads(job_file.read_text())
    scheduled.update(kind="scheduled-task", status="scheduled", enabled=True)
    job_file.write_text(json.dumps(scheduled), encoding="utf-8")
    assert srv._retention_reference_reason("job-target", json.loads((root / "job-target.json").read_text())) == "enabled_scheduled_job_reference"


def test_session_retention_skips_live_worker_pending_delivery_and_reminder(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(sess, "SESSION_DIR", root)
    monkeypatch.setattr(srv.reminders, "REMINDER_PATH", tmp_path / "reminders.json")
    stamp = iso(datetime(2020, 1, 1).timestamp())
    write_session(root, "live", updated_at=stamp)
    sess._cache.clear()
    monkeypatch.setattr(srv.worker, "find_alive_worker_by_session", lambda _sid: object())
    monkeypatch.setattr(srv.worker, "find_worker_by_session", lambda _sid: object())
    monkeypatch.setattr(srv, "broadcast", lambda _event: asyncio.sleep(0))
    live = asyncio.run(srv._retention_delete_session("live", {"updated_at": stamp}))
    assert live == {"reason": "live_worker"}
    assert (root / "live.json").exists()

    monkeypatch.setattr(srv.worker, "find_alive_worker_by_session", lambda _sid: None)
    monkeypatch.setattr(srv.worker, "find_worker_by_session", lambda _sid: None)
    ledger_row = write_session(root, "pending-delivery", updated_at=stamp,
                               queue_delivery_ledger={"receipt": {"deliveryState": "queued"}})
    assert srv._retention_session_reference_reason("pending-delivery", ledger_row) == "pending_delivery_notification"
    write_session(root, "reminded", updated_at=stamp)
    monkeypatch.setattr(srv.reminders, "list_for_session", lambda sid: [{"sessionId": sid, "status": "pending"}])
    assert srv._retention_session_reference_reason("reminded", json.loads((root / "reminded.json").read_text())) == "pending_reminder"


def test_session_delete_uses_store_and_cleans_only_owned_lifecycle_data(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(sess, "SESSION_DIR", root)
    monkeypatch.setattr(srv.reminders, "REMINDER_PATH", tmp_path / "reminders.json")
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path / "data")
    workdirs = tmp_path / "data" / "workdirs"
    monkeypatch.setattr(srv, "WORKDIRS_DIR", workdirs)
    monkeypatch.setattr(srv.workspaces, "WORKSPACE_DIR", tmp_path / "data" / "workspaces")
    sid = "old-session"
    stamp = iso(datetime(2020, 1, 1).timestamp())
    write_session(root, sid, updated_at=stamp, history=[], workdir=str(tmp_path / "data" / "workdirs" / sid))
    history_path = root / f"{sid}.history.jsonl"
    history_path.write_text(json.dumps({"role": "user", "ts": stamp}) + "\n", encoding="utf-8")
    workdir = workdirs / sid
    workdir.mkdir(parents=True)
    (workdir / "kept.txt").write_text("kept", encoding="utf-8")
    mcp = tmp_path / "data" / "mcp-configs" / f"{sid}.mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text('{"credential":"test"}', encoding="utf-8")
    kimi = tmp_path / "data" / "kimi-homes" / sid / "auth.json"
    kimi.parent.mkdir(parents=True)
    kimi.write_text('{"token":"test"}', encoding="utf-8")
    global_config = tmp_path / "config.json"
    global_auth = tmp_path / "provider-home" / "auth.json"
    global_config.write_text('{"provider":"keep"}', encoding="utf-8")
    global_auth.parent.mkdir(parents=True)
    global_auth.write_text('{"token":"keep"}', encoding="utf-8")
    attachments_root = tmp_path / "data" / "attachments"
    upload = "upload_" + "a" * 32 + ".txt"
    _, attachment_path, _ = attachment_record(attachments_root, sid, upload, stamp)
    sess._cache.clear()
    monkeypatch.setattr(srv.worker, "find_alive_worker_by_session", lambda _sid: None)
    monkeypatch.setattr(srv.worker, "find_worker_by_session", lambda _sid: None)
    main_thread = threading.get_ident()
    storage_threads = []
    broadcast_threads = []
    original_delete = sess.delete
    def tracked_delete(session_id):
        storage_threads.append(threading.get_ident())
        return original_delete(session_id)
    async def tracked_broadcast(_event):
        broadcast_threads.append(threading.get_ident())
    monkeypatch.setattr(sess, "delete", tracked_delete)
    monkeypatch.setattr(srv, "broadcast", tracked_broadcast)

    outcome = asyncio.run(srv._retention_delete_session(sid, {
        "updated_at": stamp,
        "history_revision": 0,
        "__retentionHistoryActivity": datetime.fromisoformat(stamp).timestamp(),
    }))

    assert outcome == {
        "deleted": True, "workdirSkipped": False, "workdirSkipReason": None,
        "lifecycleSkipReasons": [],
    }
    assert not (root / f"{sid}.json").exists()
    assert not history_path.exists()
    assert not workdir.exists()
    assert not mcp.exists() and not kimi.exists()
    assert global_config.read_text(encoding="utf-8") == '{"provider":"keep"}'
    assert global_auth.read_text(encoding="utf-8") == '{"token":"keep"}'
    assert attachment_path.exists(), "Session expiry must not cascade to attachments"
    assert storage_threads and storage_threads[0] != main_thread
    assert broadcast_threads == [main_thread]


@pytest.mark.parametrize("reference", ["other_session", "workspace", "workspace_parent"])
def test_session_retention_preserves_shared_workdir_references(tmp_path, monkeypatch, reference):
    root = tmp_path / "sessions"
    data = tmp_path / "data"
    workdirs = data / "workdirs"
    workdir = workdirs / "owned-dir"
    workdir.mkdir(parents=True)
    (workdir / "file.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(sess, "SESSION_DIR", root)
    monkeypatch.setattr(srv, "DATA_DIR", data)
    monkeypatch.setattr(srv, "WORKDIRS_DIR", workdirs)
    workspace_root = data / "workspaces"
    monkeypatch.setattr(srv.workspaces, "WORKSPACE_DIR", workspace_root)
    stamp = iso(datetime(2020, 1, 1).timestamp())
    row = write_session(root, "old-session", updated_at=stamp, history=[], workdir=str(workdir))
    if reference == "other_session":
        write_session(root, "other-session", updated_at=stamp, workdir=str(workdir))
    else:
        workspace_root.mkdir(parents=True)
        (workspace_root / "ws.json").write_text(json.dumps({
            "id": "ws", "dirs": [str(workdir if reference == "workspace" else workdirs)],
        }), encoding="utf-8")
    sess._cache.clear()
    monkeypatch.setattr(srv.worker, "find_alive_worker_by_session", lambda _sid: None)
    monkeypatch.setattr(srv.worker, "find_worker_by_session", lambda _sid: None)
    monkeypatch.setattr(srv, "broadcast", lambda _event: asyncio.sleep(0))

    outcome = asyncio.run(srv._retention_delete_session("old-session", {
        "updated_at": stamp, "history_revision": 0,
        "__retentionHistoryActivity": None,
    }))

    assert outcome["deleted"] is True
    assert outcome["workdirSkipped"] is True
    assert workdir.exists() and (workdir / "file.txt").read_text(encoding="utf-8") == "keep"


def test_retention_never_removes_external_or_nested_workdirs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    workdirs = data / "workdirs"
    workdirs.mkdir(parents=True)
    external = tmp_path / "external" / "project"
    external.mkdir(parents=True)
    payload = external / "keep.txt"
    payload.write_text("external", encoding="utf-8")
    nested = workdirs / "group" / "nested"
    nested.mkdir(parents=True)
    nested_payload = nested / "keep.txt"
    nested_payload.write_text("nested", encoding="utf-8")
    monkeypatch.setattr(srv, "DATA_DIR", data)
    monkeypatch.setattr(srv, "WORKDIRS_DIR", workdirs)

    external_result = srv._retention_remove_workdir("external-session", {"workdir": str(external)})
    nested_result = srv._retention_remove_workdir("nested-session", {"workdir": str(nested)})

    assert external_result[0] is False
    assert nested_result[0] is False
    assert payload.read_text(encoding="utf-8") == "external"
    assert nested_payload.read_text(encoding="utf-8") == "nested"


def test_attachment_expiry_deletes_only_owned_upload_and_updates_owner_sidecar(tmp_path):
    root = tmp_path / "attachments"
    old = iso(datetime(2020, 1, 1).timestamp())
    filename = "upload_" + "b" * 32 + ".txt"
    owner, target, sidecar = attachment_record(root, "owner-session", filename, old)
    reference_owner = attachment_owner_dir(root, "other-session")
    (reference_owner / ".attachments.json").write_text(json.dumps({
        filename: {
            "source": "upload", "sessionId": "other-session", "sourceSessionId": "owner-session",
            "sourceAttachmentId": filename, "storageFilename": filename, "completed": True,
        },
    }), encoding="utf-8")
    mtime_name = "upload_" + "c" * 32 + ".bin"
    fallback_owner, fallback_target, _ = attachment_record(root, "fallback-session", mtime_name)
    os.utime(fallback_target, (datetime(2020, 1, 1).timestamp(),) * 2)
    now = datetime(2026, 9, 27).timestamp()

    result = retention.scan_attachments(root, 30, now)

    assert result["deleted"] == 2
    assert not target.exists() and not fallback_target.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {}
    assert json.loads((fallback_owner / ".attachments.json").read_text(encoding="utf-8")) == {}
    assert json.loads((reference_owner / ".attachments.json").read_text(encoding="utf-8"))[filename]["sourceSessionId"] == "owner-session"


def test_qq_history_media_clean_expired_items_but_keep_unclear_files_and_inbox(tmp_path):
    data = tmp_path / "data"
    history = data / "qq_history"
    history.mkdir(parents=True)
    now_dt = datetime(2026, 9, 27, 12, 0)
    old_time = (now_dt - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    recent_time = (now_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    good = history / "123.json"
    good.write_text(json.dumps([
        {"role": "user", "text": "old", "time": old_time},
        {"role": "assistant", "text": "new", "time": recent_time},
    ]), encoding="utf-8")
    unclear = history / "456.json"
    unclear_raw = json.dumps([
        {"role": "user", "text": "old", "time": old_time},
        {"role": "assistant", "text": "unknown", "time": "not-a-date"},
    ])
    unclear.write_text(unclear_raw, encoding="utf-8")
    inbox = data / "qq_inbox" / "123.json"
    inbox.parent.mkdir()
    inbox.write_text("pending inbox", encoding="utf-8")
    history_result = retention.scan_qq_history(history, 30, now_dt.timestamp())
    assert history_result["deleted"] == 1
    assert len(json.loads(good.read_text(encoding="utf-8"))) == 1
    assert unclear.read_text(encoding="utf-8") == unclear_raw
    assert history_result["skipReasons"]["qq_history_format_or_timestamp_unclear"] == 1
    assert inbox.read_text(encoding="utf-8") == "pending inbox"

    media = data / "qq_media"
    media.mkdir()
    old_file = media / "old.jpg"
    new_file = media / "new.jpg"
    partial = media / "unfinished.jpg.part"
    for path in (old_file, new_file, partial):
        path.write_bytes(b"media")
    os.utime(old_file, (now_dt.timestamp() - 60 * 86400,) * 2)
    os.utime(new_file, (now_dt.timestamp() - 2 * 86400,) * 2)
    os.utime(partial, (now_dt.timestamp() - 90 * 86400,) * 2)
    external = tmp_path / "outside.jpg"
    external.write_bytes(b"outside")
    link = media / "alias.jpg"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError):
        link = None

    media_result = retention.scan_qq_media(media, 30, now_dt.timestamp())

    assert media_result["deleted"] == 1
    assert not old_file.exists()
    assert new_file.exists() and partial.exists()
    assert external.read_bytes() == b"outside"
    if link is not None:
        assert link.is_symlink()


def test_pan_log_retention_removes_only_expired_rotations_under_registered_root(tmp_path):
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    active = logs / "pan.log"
    active.write_text("active writer", encoding="utf-8")
    rotated = logs / "pan.log.1"
    dated = logs / "pan.log.20260801"
    unrelated = logs / "other.log.1"
    custom_sibling = logs / "pan.log.manual"
    for path in (rotated, dated, unrelated, custom_sibling):
        path.write_text("old", encoding="utf-8")
        os.utime(path, (datetime(2020, 1, 1).timestamp(),) * 2)
    external = tmp_path / "external" / "pan.log"
    external.parent.mkdir()
    external.write_text("external", encoding="utf-8")
    alias = logs / "pan.log.2"
    try:
        alias.symlink_to(external)
    except (OSError, NotImplementedError):
        alias = None

    result = retention.scan_pan_logs(logs, active, 30, datetime(2026, 9, 27).timestamp())

    assert result["deleted"] == 2
    assert active.read_text(encoding="utf-8") == "active writer"
    assert not rotated.exists() and not dated.exists()
    assert unrelated.exists() and custom_sibling.exists()
    assert external.read_text(encoding="utf-8") == "external"
    if alias is not None:
        assert alias.is_symlink()
    outside_result = retention.scan_pan_logs(logs, external, 1, datetime(2026, 9, 27).timestamp())
    assert outside_result["deleted"] == 0
    assert external.read_text(encoding="utf-8") == "external"


def test_enabled_policy_with_empty_days_never_scans_or_deletes(tmp_path):
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    active = logs / "pan.log"
    old_rotation = logs / "pan.log.1"
    active.write_text("active", encoding="utf-8")
    old_rotation.write_text("old", encoding="utf-8")
    os.utime(old_rotation, (datetime(2020, 1, 1).timestamp(),) * 2)
    policies = {key: dict(value) for key, value in retention.DEFAULT_POLICIES.items()}
    policies["pan_logs"] = {"enabled": True, "days": None}
    service = retention.DataRetentionService(
        sessions_root=tmp_path / "missing-sessions",
        attachments_root=tmp_path / "missing-attachments",
        qq_history_root=tmp_path / "missing-history",
        qq_media_root=tmp_path / "missing-media",
        pan_logs_root=logs,
        active_log_path=lambda: active,
        status_path=tmp_path / "data" / "retention" / "status.json",
        policy_loader=lambda: policies,
        session_delete=lambda *_: {},
    )

    result = service.run_once(now_epoch=datetime(2026, 9, 27).timestamp(), force=True)

    assert result["pan_logs"]["lastScanAt"] is None
    assert old_rotation.exists()


def test_status_read_does_not_wait_for_session_cleanup_to_finish(tmp_path):
    now = datetime(2026, 9, 27).timestamp()
    sessions = tmp_path / "data" / "sessions"
    write_session(sessions, "old", updated_at=iso(now - 90 * 86400), history=[])
    entered = threading.Event()
    release = threading.Event()

    def hold_session_delete(_session_id, _record):
        entered.set()
        release.wait(timeout=5)
        return {"deleted": True}

    policies = {key: {"enabled": False, "days": None} for key in retention.POLICY_IDS}
    policies["sessions"] = {"enabled": True, "days": 1}
    service = retention.DataRetentionService(
        sessions_root=sessions, attachments_root=tmp_path / "missing-attachments",
        qq_history_root=tmp_path / "missing-history", qq_media_root=tmp_path / "missing-media",
        status_path=tmp_path / "data" / "retention" / "status.json",
        policy_loader=lambda: policies, session_delete=hold_session_delete,
    )
    scan = threading.Thread(target=service.run_once, kwargs={"now_epoch": now})
    scan.start()
    assert entered.wait(timeout=2)
    try:
        assert service.get_status()["sessions"]["lastScanAt"] is None
    finally:
        release.set()
        scan.join(timeout=5)
    assert not scan.is_alive()


def test_retention_service_is_daily_idempotent_persists_results_and_isolates_failures(tmp_path):
    now = time.time()
    sessions = tmp_path / "data" / "sessions"
    write_session(sessions, "stale", updated_at=iso(now - 90 * 86400), history=[])
    media = tmp_path / "data" / "qq_media"
    media.mkdir(parents=True)
    old_media = media / "old.bin"
    old_media.write_bytes(b"old")
    os.utime(old_media, (now - 90 * 86400,) * 2)
    policies = {
        "sessions": {"enabled": True, "days": 30},
        "attachments": {"enabled": True, "days": 30},
        "qq_history": {"enabled": False, "days": 30},
        "qq_media": {"enabled": True, "days": 30},
        "pan_logs": {"enabled": False, "days": None},
    }
    service = retention.DataRetentionService(
        sessions_root=sessions,
        attachments_root=tmp_path / "missing-attachments",
        qq_history_root=tmp_path / "missing-history",
        qq_media_root=media,
        status_path=tmp_path / "data" / "retention" / "status.json",
        policy_loader=lambda: policies,
        session_delete=lambda _sid, _row: (_ for _ in ()).throw(RuntimeError("synthetic")),
    )
    first = service.run_once(now_epoch=now)
    assert first["sessions"]["error"] == "scan_failed:RuntimeError"
    assert first["attachments"]["skipReasons"]["attachment_root_missing_or_unsafe"] == 1
    assert first["qq_media"]["deleted"] == 1
    assert not old_media.exists()
    later_media = media / "later.bin"
    later_media.write_bytes(b"later")
    os.utime(later_media, (now - 90 * 86400,) * 2)
    second = service.run_once(now_epoch=now + 3600)
    assert second["qq_media"]["deleted"] == 1
    assert later_media.exists(), "a category cannot run more than once per day"
    third = service.run_once(now_epoch=now + 86400)
    assert third["qq_media"]["deleted"] == 1
    assert not later_media.exists()
    persisted = json.loads((tmp_path / "data" / "retention" / "status.json").read_text(encoding="utf-8"))
    assert persisted["qq_media"]["deleted"] == 1
    assert persisted["sessions"]["error"] == "scan_failed:RuntimeError"


def test_data_retention_api_persists_shared_key_and_rejects_unknown_targets(tmp_path, monkeypatch):
    raw = {"other": {"kept": True}, "data_retention": {"jobs": {"enabled": False}}}
    saved = []
    monkeypatch.setattr(srv, "read_config_file", lambda: json.loads(json.dumps(saved[-1] if saved else raw)))
    monkeypatch.setattr(srv, "load_config", lambda: (saved[-1] if saved else raw))
    monkeypatch.setattr(srv, "save_config", lambda value: saved.append(value))
    service = type("Service", (), {"get_status": lambda _self: {}})()
    monkeypatch.setattr(srv, "_DATA_RETENTION_SERVICE", service)

    response = asyncio.run(srv.api_put_data_retention({"policies": {
        "sessions": {"enabled": True, "days": 5},
        "attachments": {"enabled": False, "days": 30},
        "qq_history": {"enabled": False, "days": 30},
        "qq_media": {"enabled": False, "days": 30},
        "pan_logs": {"enabled": False, "days": None},
    }}))
    assert response["configKey"] == "data_retention"
    assert saved[-1]["other"] == {"kept": True}
    assert saved[-1]["data_retention"]["jobs"] == {"enabled": False}
    assert saved[-1]["data_retention"]["sessions"] == {"enabled": True, "days": 5}
    response = asyncio.run(srv.api_get_data_retention())
    assert response["policies"]["sessions"] == {"enabled": True, "days": 5}
    assert response["jobsPolicy"] == {"enabled": False}
    response = asyncio.run(srv.api_put_data_retention({"policies": {
        "sessions": {"enabled": True, "days": None},
    }}))
    assert response["policies"]["sessions"] == {"enabled": True, "days": None}
    assert saved[-1]["data_retention"]["sessions"]["days"] is None
    asyncio.run(srv.api_put_data_retention({"policies": {
        "sessions": {"enabled": True, "days": 5},
    }, "jobs": {"retentionDays": 14, "activeOnly": True}}))
    assert saved[-1]["data_retention"]["jobs"] == {"retentionDays": 14, "activeOnly": True}
    with pytest.raises(srv.HTTPException) as error:
        asyncio.run(srv.api_put_data_retention({"policies": {"config": {"enabled": True, "days": 1}}}))
    assert error.value.status_code == 422
    with pytest.raises(srv.HTTPException) as error:
        asyncio.run(srv.api_put_data_retention({"policies": {}, "path": str(tmp_path)}))
    assert error.value.status_code == 422
    with pytest.raises(srv.HTTPException) as error:
        asyncio.run(srv.api_put_data_retention({
            "policies": {"sessions": {"enabled": True, "days": 5}},
            "jobs": {"fileGlob": "C:/data/**"},
        }))
    assert error.value.status_code == 422


def test_retention_status_reads_only_sanitized_owned_counters(tmp_path):
    data = tmp_path / "data"
    status_path = data / "retention" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({
        "sessions": {
            "scanned": 2, "deleted": 1, "skipped": 1,
            "skipReasons": {"live_worker": 1}, "lastScanAt": "2020-01-01T00:00:00",
            "credential": "never project this",
        },
        "unregistered": {"credential": "never project this"},
    }), encoding="utf-8")
    service = retention.DataRetentionService(
        sessions_root=data / "sessions", attachments_root=data / "attachments",
        qq_history_root=data / "qq_history", qq_media_root=data / "qq_media",
        status_path=status_path, policy_loader=lambda: {}, session_delete=lambda *_: {},
    )

    result = service.get_status()

    assert result["sessions"]["deleted"] == 1
    assert result["sessions"]["skipReasons"] == {"live_worker": 1}
    assert "credential" not in result["sessions"]
    assert "unregistered" not in result


def test_registered_sweeps_never_touch_config_auth_external_workdir_or_inbox(tmp_path):
    data = tmp_path / "data"
    sessions = data / "sessions"
    attachments = data / "attachments"
    history = data / "qq_history"
    media = data / "qq_media"
    for root in (sessions, attachments, history, media):
        root.mkdir(parents=True)
    protected = {
        tmp_path / "config.json": b'{"provider":"secret"}',
        tmp_path / "auth.json": b'{"token":"secret"}',
        tmp_path / "provider-home" / "state.json": b"provider state",
        tmp_path / "external-workdir" / "project.txt": b"external project",
        data / "qq_inbox" / "pending.json": b"unconsumed",
    }
    for path, payload in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    now = datetime(2026, 9, 27).timestamp()
    policies = {key: {"enabled": True, "days": 1} for key in retention.POLICY_IDS}
    service = retention.DataRetentionService(
        sessions_root=sessions,
        attachments_root=attachments,
        qq_history_root=history,
        qq_media_root=media,
        status_path=data / "retention" / "status.json",
        policy_loader=lambda: policies,
        session_delete=lambda _sid, _row: {"reason": "reference"},
    )
    service.run_once(now_epoch=now)
    for path, payload in protected.items():
        assert path.read_bytes() == payload

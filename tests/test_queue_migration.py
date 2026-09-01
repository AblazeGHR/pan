"""Compatibility and repeatability tests for persisted queue migration."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker


def test_legacy_rows_requeue_unfinished_and_drop_sent_idempotently():
    s = _sess.Session(id="ses-migrate", name="migration")
    old_task = {"text": "old user message", "source": "user"}
    old_report = {"result": "old report", "sessionId": "child"}
    unfinished = {
        "type": "task", "id": "unfinished", "text": "retry me",
        "source": "agent", "taskId": "retry-1", "deliveryState": "in_flight",
    }
    sent = {
        "type": "task", "id": "sent", "text": "already handed off",
        "source": "agent", "deliveryState": "sent_to_cli",
    }
    s.queue_pending = [old_task, old_report, unfinished, sent]

    assert worker._migrate_queue_delivery_state(s) is True
    assert [item.get("text") for item in s.queue_pending] == [
        "old user message", None, "retry me"
    ]
    assert s.queue_pending[0]["type"] == "task"
    assert s.queue_pending[0]["source"] == "user"
    assert s.queue_pending[1]["type"] == "report"
    assert s.queue_pending[2]["deliveryState"] == "queued"
    assert s.queue_pending[2]["deliveryAttempts"] == 1
    # A second run makes no further changes and does not manufacture new ids.
    ids = [item["id"] for item in s.queue_pending]
    assert worker._migrate_queue_delivery_state(s) is False
    assert [item["id"] for item in s.queue_pending] == ids


def test_new_session_fields_survive_old_and_new_loader_round_trip():
    s = _sess.Session(
        id="ses-round-trip", name="round trip",
        queue_delivery_ledger={"client-1": {"status": "queued"}},
        queue_revision=7,
    )
    loaded = _sess.Session._from_data(s.to_dict())
    assert loaded.queue_delivery_ledger == {"client-1": {"status": "queued"}}
    assert loaded.queue_revision == 7


def test_migration_script_creates_backup_and_is_repeatable(tmp_path):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    path = session_dir / "ses-script.json"
    original = {
        "id": "ses-script", "name": "script",
        "queue_pending": [{"text": "legacy"}], "history": [],
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    command = [
        sys.executable, "scripts/migrate_queue_delivery.py",
        "--session-dir", str(session_dir), "--apply",
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["queue_pending"][0]["type"] == "task"
    assert migrated["queue_pending"][0]["source"] == "user"
    backups = list((session_dir / "queue-migration-backups").rglob("ses-script.json"))
    assert backups and json.loads(backups[0].read_text(encoding="utf-8")) == original
    assert "1 session file(s)" in first.stdout

    second = subprocess.run(command, check=True, capture_output=True, text=True)
    assert "0 session file(s)" in second.stdout

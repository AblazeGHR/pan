"""Read-only migration drills for the unified Job/Inbox composition.

The fixtures are copied into temporary directories.  No product profile is
opened and every assertion checks both the projected view and source bytes.
"""
from __future__ import annotations

import hashlib
import json
import shutil

from packages.actions.store import Store
from packages.core.agent_inbox import Inbox
from packages.core.inbox_service import migrate_qq
from packages.core.background_jobs import job_public_view
from packages.jobs.action_templates import Templates
from packages.jobs.unified import project
from tests.test_automation_core import action


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_job_projection_preserves_unknown_data_and_never_enables(tmp_path):
    source = tmp_path / "legacy-job.json"
    raw = {
        "jobId": "legacy-1", "kind": "session-message", "enabled": True,
        "targetSessionId": "s1", "text": "body",
        "schedule": {"type": "interval", "intervalSeconds": 60},
        "vendorExtension": {"future": [1, 2, 3]},
        "unknownFlag": True,
    }
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    before = _sha(source)

    view = project(json.loads(source.read_text(encoding="utf-8")))

    assert view["config"]["enabled"] is False
    assert view["compatibility"]["unknownFields"] == {
        "vendorExtension": {"future": [1, 2, 3]}, "unknownFlag": True
    }
    assert _sha(source) == before
    assert json.loads(source.read_text(encoding="utf-8")) == raw


def test_action_template_and_job_read_projections_keep_unknown_fields(tmp_path):
    registry = tmp_path / "actions"
    state = {
        "revision": 1, "actions": {"pause": action()}, "groups": {},
        "retired_plugins": [], "jobTemplates": {
            "jtpl_legacy": {
                "id": "jtpl_legacy", "name": "legacy", "description": "",
                "enabled": False, "referenceGeneration": 0, "revision": 1,
                "config": {
                    "name": "legacy job", "description": "", "actionId": "pause",
                    "arguments": {}, "schedule": [], "trigger": {"kind": "immediate"},
                    "recovery": {"enabled": False, "monitoring": False,
                                  "maxAttempts": 3, "backoffSec": [5, 15, 60],
                                  "stableResetSec": 300},
                    "overlap": {"mode": "forbid", "parallelLimit": 3},
                    "notification": {"on": ["completed"], "target": "job"},
                    "completion": {"kind": "ordinary"}, "enabled": False,
                    "maxRuns": None, "misfirePolicy": "fire_now",
                },
                "futureTemplateField": {"kept": "as-is"},
            }
        },
    }
    registry.mkdir()
    path = registry / "registry.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    before = _sha(path)

    listed = Templates(Store(registry)).list()

    assert listed[0]["futureTemplateField"] == {"kept": "as-is"}
    assert "enabled" not in listed[0]
    assert listed[0]["usable"] == (listed[0]["integrity"]["status"] == "complete"
                                  and not listed[0]["integrity"]["approvalRequired"])
    assert listed[0]["config"]["enabled"] is False
    assert json.loads(path.read_text(encoding="utf-8")) == state
    assert json.loads(path.read_text(encoding="utf-8"))["jobTemplates"]["jtpl_legacy"]["enabled"] is False
    assert _sha(path) == before


def test_session_queue_projection_retains_unknown_and_hides_runtime_snapshot():
    raw = {
        "id": "q1", "kind": "notice", "deliveryState": "queued",
        "futureQueueField": {"source": "legacy"},
        "attemptSnapshot": {"secret": "runtime-only"},
        "targetSessionId": "s1",
    }
    view = job_public_view(raw)
    assert view["futureQueueField"] == {"source": "legacy"}
    assert "attemptSnapshot" not in view
    assert raw["futureQueueField"] == {"source": "legacy"}


def test_qq_migration_uses_temp_copy_keeps_source_and_unknown_content(tmp_path):
    legacy_root = tmp_path / "legacy-qq"
    legacy_root.mkdir()
    legacy = legacy_root / "peer.json"
    rows = [{"id": "old-1", "text": "hello", "futureEnvelope": {"v": 2}}]
    legacy.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    before = _sha(legacy)

    inbox_path = tmp_path / "copy" / "inbox.sqlite"
    box = Inbox(inbox_path, object())
    migrate_qq(box, legacy_root)
    lockers = box.list()
    messages = box.read(lockers[0]["id"])

    assert messages[0]["content"]["futureEnvelope"] == {"v": 2}
    assert _sha(legacy) == before
    assert json.loads(legacy.read_text(encoding="utf-8")) == rows

    # A second read from a physical copy proves the migration does not depend
    # on the original profile path or an in-place rewrite.
    copied_root = tmp_path / "legacy-copy"
    shutil.copytree(legacy_root, copied_root)
    copied_box = Inbox(tmp_path / "copy-2" / "inbox.sqlite", object())
    migrate_qq(copied_box, copied_root)
    copied_messages = copied_box.read(copied_box.list()[0]["id"])
    assert copied_messages[0]["content"] == messages[0]["content"]

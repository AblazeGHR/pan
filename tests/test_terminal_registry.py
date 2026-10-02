"""Pan Terminal P0：持久化 TerminalRegistry（原子写、跨进程锁、无秘密）。

覆盖契约报告 M7 与实施计划 §3.1/§13：每终端一个 JSON、原子替换、命名互斥、
失败记录不删、scope 字段、JSON 无秘密；增补注册表进程竞争、损坏文件、
非法 ID、环境变量覆盖。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal.contracts import (
    IllegalStateTransition,
    RegistryCorruptError,
    RuntimeState,
    TerminalExistsError,
    TerminalRecord,
    TerminalScope,
    UnknownTerminalError,
)
from packages.core.terminal.registry import TerminalRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent

_RECORD_KEYS = {
    "schema_version",
    "terminal_id",
    "owner",
    "status",
    "created_at",
    "updated_at",
    "rows",
    "cols",
    "pid",
    "process_created_at_filetime",
    "pipe",
    "detached",
    "detached_at",
    "scope",
    "exit",
    "lease_grace_seconds",
    "created_by",
}

_CHILD_SCRIPT = r"""
import sys
sys.path.insert(0, sys.argv[1])
from packages.core.terminal.contracts import TerminalRecord
from packages.core.terminal.registry import TerminalRegistry

root, tag = sys.argv[2], sys.argv[3]
registry = TerminalRegistry(root)
for index in range(3):
    tid = "term_proc%s_%02d" % (tag, index)
    registry.create(TerminalRecord(terminal_id=tid, created_by="proc-" + tag))
    for round_no in range(5):
        record = registry.get(tid)
        record.rows = round_no + 1
        registry.save(record)
print("OK-" + tag)
"""


def test_new_terminal_id_unique_prefixed_and_safe():
    ids = {TerminalRegistry.new_terminal_id() for _ in range(1000)}
    assert len(ids) == 1000
    sample = next(iter(ids))
    assert sample.startswith("term_")
    assert "/" not in sample and "\\" not in sample and ".." not in sample
    assert not sample.startswith(("ses_", "job_", "worker-", "cli-"))


def test_create_get_roundtrip_with_scope_and_fields(tmp_path):
    registry = TerminalRegistry(tmp_path)
    record = TerminalRecord(
        terminal_id="term_roundtrip000001",
        pid=4242,
        process_created_at_filetime=134354347461843768,
        pipe="\\\\.\\pipe\\pan-terminal-term_roundtrip000001",
        rows=24,
        cols=80,
        scope=TerminalScope(workspace_id="ws-1", session_id="ses-1"),
        lease_grace_seconds=2.0,
        created_by="mcp",
    )
    registry.create(record)
    assert record.created_at > 0
    assert record.updated_at > 0
    loaded = registry.get("term_roundtrip000001")
    assert loaded.pid == 4242
    assert loaded.process_created_at_filetime == 134354347461843768
    assert loaded.scope.workspace_id == "ws-1"
    assert loaded.scope.session_id == "ses-1"
    assert loaded.lease_grace_seconds == 2.0
    assert loaded.created_by == "mcp"
    assert loaded.status is RuntimeState.CREATED


def test_create_duplicate_raises(tmp_path):
    registry = TerminalRegistry(tmp_path)
    record = TerminalRecord(terminal_id="term_dup0000000000001")
    registry.create(record)
    with pytest.raises(TerminalExistsError):
        registry.create(TerminalRecord(terminal_id="term_dup0000000000001"))


def test_save_updates_and_persists(tmp_path):
    registry = TerminalRegistry(tmp_path)
    record = registry.create(TerminalRecord(terminal_id="term_save000000000001"))
    first_updated = record.updated_at
    record.rows = 30
    record.status = RuntimeState.RUNNING
    registry.save(record)
    assert record.updated_at >= first_updated
    reloaded = TerminalRegistry(tmp_path).get("term_save000000000001")
    assert reloaded.rows == 30
    assert reloaded.status is RuntimeState.RUNNING


def test_get_unknown_raises(tmp_path):
    registry = TerminalRegistry(tmp_path)
    with pytest.raises(UnknownTerminalError):
        registry.get("term_absent0000000001")
    assert registry.exists("term_absent0000000001") is False


def test_invalid_terminal_ids_rejected(tmp_path):
    registry = TerminalRegistry(tmp_path)
    for bad in ("", "../evil", "term_" + "a" * 65, "job_abc", "term_bad/slash"):
        with pytest.raises(ValueError):
            registry.get(bad)


def test_remove_only_allows_terminal_states(tmp_path):
    registry = TerminalRegistry(tmp_path)
    registry.create(
        TerminalRecord(terminal_id="term_rm00000000000001", status=RuntimeState.CLEANUP_FAILED)
    )
    with pytest.raises(IllegalStateTransition):
        registry.remove("term_rm00000000000001")
    record = registry.get("term_rm00000000000001")
    record.status = RuntimeState.EXITED
    registry.save(record)
    registry.remove("term_rm00000000000001")
    with pytest.raises(UnknownTerminalError):
        registry.get("term_rm00000000000001")


def test_failed_record_preserved_after_reload(tmp_path):
    registry = TerminalRegistry(tmp_path)
    registry.create(
        TerminalRecord(
            terminal_id="term_fail000000000001",
            status=RuntimeState.CLEANUP_FAILED,
            exit_reason="cleanup-failed",
        )
    )
    reloaded = TerminalRegistry(tmp_path)
    record = reloaded.get("term_fail000000000001")
    assert record.status is RuntimeState.CLEANUP_FAILED
    assert reloaded.exists("term_fail000000000001") is True
    with pytest.raises(IllegalStateTransition):
        reloaded.remove("term_fail000000000001")


def test_json_contains_no_secret_fields(tmp_path):
    registry = TerminalRegistry(tmp_path)
    registry.create(
        TerminalRecord(
            terminal_id="term_nocreds0000000001",
            pipe="\\\\.\\pipe\\pan-terminal-term_nocreds0000000001",
            created_by="mcp",
        )
    )
    path = tmp_path / "term_nocreds0000000001.json"
    text = path.read_text(encoding="utf-8")
    lower = text.lower()
    assert "token" not in lower
    assert "secret" not in lower
    assert "authorization" not in lower
    payload = json.loads(text)
    assert set(payload.keys()) == _RECORD_KEYS
    assert payload["terminal_id"] == "term_nocreds0000000001"
    assert set(payload["scope"].keys()) == {"workspace_id", "session_id"}
    assert set(payload["exit"].keys()) == {"code", "reason"}


def test_list_sorted_by_id(tmp_path):
    registry = TerminalRegistry(tmp_path)
    for tid in ("term_c000000000000001", "term_a000000000000001", "term_b000000000000001"):
        registry.create(TerminalRecord(terminal_id=tid))
    ids = [record.terminal_id for record in registry.list()]
    assert ids == sorted(ids)


def test_corrupt_record_raises_not_swallowed(tmp_path):
    registry = TerminalRegistry(tmp_path)
    (tmp_path / "term_bad0000000000001.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(RegistryCorruptError):
        registry.get("term_bad0000000000001")
    with pytest.raises(RegistryCorruptError):
        registry.list()


def test_env_override_root(tmp_path, monkeypatch):
    target = tmp_path / "via-env"
    monkeypatch.setenv("PAN_TERMINALS_DIR", str(target))
    registry = TerminalRegistry()
    assert registry.root == target
    registry.create(TerminalRecord(terminal_id="term_env000000000001"))
    assert (target / "term_env000000000001.json").exists()


def test_cross_process_contention(tmp_path):
    root = tmp_path / "terminals"
    tags = ["A", "B", "C", "D"]
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD_SCRIPT, str(REPO_ROOT), str(root), tag],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT),
        )
        for tag in tags
    ]
    for proc, tag in zip(procs, tags):
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode("utf-8", "replace")
        assert ("OK-" + tag) in out.decode("utf-8", "replace")
    registry = TerminalRegistry(root)
    records = registry.list()
    assert len(records) == len(tags) * 3
    for record in records:
        assert record.rows == 5
        assert (record.created_by or "").startswith("proc-")
    assert [p.name for p in root.glob("*.tmp")] == []

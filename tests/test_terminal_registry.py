"""Pan Terminal P0：持久化 TerminalRegistry（原子写、跨进程锁、无秘密）。

覆盖契约报告 M7 与实施计划 §3.1/§13：每终端一个 JSON、原子替换、命名互斥、
失败记录不删、scope 字段、JSON 无秘密；增补注册表进程竞争、损坏文件、
非法 ID、环境变量覆盖。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal import registry as registry_module
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


_UPDATE_CHILD_SCRIPT = r"""
import sys
sys.path.insert(0, sys.argv[1])
from packages.core.terminal.registry import TerminalRegistry

registry = TerminalRegistry(sys.argv[2])
tid = sys.argv[3]
for _ in range(10):
    registry.update(tid, lambda record: setattr(record, "rows", record.rows + 1))
print("OK")
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


# ---------------------------------------------------------------------------
# r2 回归：锁内 update（同 record 并发不丢更新）、lock_key、损坏统一、tmp 清理
# ---------------------------------------------------------------------------


def test_update_is_locked_read_modify_write(tmp_path):
    registry = TerminalRegistry(tmp_path)
    registry.create(TerminalRecord(terminal_id="term_upd0000000000001", rows=0))
    updated = registry.update(
        "term_upd0000000000001", lambda record: setattr(record, "rows", record.rows + 1)
    )
    assert updated.rows == 1
    updated = registry.update(
        "term_upd0000000000001", lambda record: setattr(record, "status", RuntimeState.RUNNING)
    )
    assert updated.status is RuntimeState.RUNNING
    reloaded = TerminalRegistry(tmp_path).get("term_upd0000000000001")
    assert reloaded.rows == 1
    assert reloaded.status is RuntimeState.RUNNING


def test_update_unknown_terminal_raises(tmp_path):
    registry = TerminalRegistry(tmp_path)
    with pytest.raises(UnknownTerminalError):
        registry.update("term_absent0000000001", lambda record: None)


def test_cross_process_update_no_lost_update(tmp_path):
    root = tmp_path / "terminals"
    registry = TerminalRegistry(root)
    registry.create(TerminalRecord(terminal_id="term_cas000000000001", rows=0))
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _UPDATE_CHILD_SCRIPT,
                str(REPO_ROOT),
                str(root),
                "term_cas000000000001",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT),
        )
        for _ in range(4)
    ]
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode("utf-8", "replace")
        assert "OK" in out.decode("utf-8", "replace")
    record = TerminalRegistry(root).get("term_cas000000000001")
    assert record.rows == 40  # 4 进程 × 10 次锁内自增：无丢失更新


def test_lock_key_case_insensitive_on_windows(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows 路径大小写等价性（命名 mutex 需同键）")
    upper = Path(str(tmp_path).upper())
    lower = Path(str(tmp_path).lower())
    assert registry_module._lock_key(upper) == registry_module._lock_key(lower)


def test_bad_utf8_record_raises_registry_corrupt(tmp_path):
    registry = TerminalRegistry(tmp_path)
    (tmp_path / "term_bad8byte000000001.json").write_bytes(b"\xff\xfe{\x00")
    with pytest.raises(RegistryCorruptError):
        registry.get("term_bad8byte000000001")
    with pytest.raises(RegistryCorruptError):
        registry.list()


def test_bad_schema_record_raises_registry_corrupt(tmp_path):
    registry = TerminalRegistry(tmp_path)
    payload = {"terminal_id": "term_badschema0000001", "status": "not-a-state"}
    (tmp_path / "term_badschema0000001.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(RegistryCorruptError):
        registry.get("term_badschema0000001")
    with pytest.raises(RegistryCorruptError):
        registry.list()


def test_atomic_write_cleans_tmp_on_failure(tmp_path, monkeypatch):
    registry = TerminalRegistry(tmp_path)
    monkeypatch.setattr(registry_module, "_REPLACE_RETRIES", 2)
    monkeypatch.setattr(registry_module, "_REPLACE_DELAY_SECONDS", 0.0)

    def blocked_replace(src, dst):
        raise PermissionError("locked (injected)")

    monkeypatch.setattr(registry_module.os, "replace", blocked_replace)
    with pytest.raises(PermissionError):
        registry.create(TerminalRecord(terminal_id="term_tmpfail000000001"))
    assert [p.name for p in tmp_path.glob("*.tmp")] == []  # finally 不残留 tmp

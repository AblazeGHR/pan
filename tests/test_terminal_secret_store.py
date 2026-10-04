"""Pan Terminal 秘密存储测试（DPAPI 用户作用域 + owner-only ACL + 原子持久化）。

分层：

1. **本进程**：加解密往返、密文不含明文哨兵、ACL 从创建时生效（DACL 枚举）、
   损坏/缺失/身份不匹配 fail-closed、64 位身份 JSON 精确性、reparse/越界名拒绝、
   删除约束、bootstrap 闭环、临时文件不残留。
2. **跨进程**：另一个自建 Python 进程读同一秘密文件并解密（只回传 SHA-256 前
   16 位与位数，不回传 token），验证“同用户 New Pan 能解密重连旧宿主”。

隔离与安全：只用 ``tmp_path``/``%TEMP%``、只 spawn 自己的进程、不监听端口、
不改任何账户权限（仅在本 TA 自建的临时文件上加宽 ACL 作为负例，随后随临时
目录一并删除）。未验证项：**其它 Windows 用户**被 ACL/DPAPI 拒绝需要第二账户
或第二安全上下文，本 TA 未创建账户、未改账户权限，因此只有结构性证据。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from packages.core.terminal import secret_store, win_pipe
from packages.core.terminal.contracts import ProcessIdentity, ProcessStatus

WINDOWS = os.name == "nt"
windows_only = pytest.mark.skipif(not WINDOWS, reason="DPAPI / Windows ACL only")
REPO_ROOT = Path(__file__).resolve().parents[1]
_SENTINEL_PREFIX = "5e7e0d1c0ffee"


def _sentinel_token() -> str:
    return (_SENTINEL_PREFIX + os.urandom(32).hex())[:64]


def _payload(terminal_id: str, token: str, identity: ProcessIdentity, **overrides) -> secret_store.SecretPayload:
    base = dict(
        terminal_id=terminal_id,
        pipe_name=win_pipe.pipe_name_for(terminal_id),
        token=token,
        runner_pid=identity.pid,
        runner_filetime=identity.created_at_filetime,
        created_at=1000.0,
        updated_at=1000.0,
    )
    base.update(overrides)
    return secret_store.SecretPayload(**base)


def _identity_for(pid: int) -> ProcessIdentity:
    handle = win_pipe.ProcessIdentityHandle.open(pid)
    assert handle is not None
    try:
        return handle.identity
    finally:
        handle.close()


@pytest.fixture
def store(tmp_path) -> secret_store.SecretStore:
    return secret_store.SecretStore(tmp_path / "data")


# ══════════════════════════════════════════════════════════════════════════
# DPAPI 往返 / 密文与明文
# ══════════════════════════════════════════════════════════════════════════


@windows_only
def test_dpapi_roundtrip_keeps_token_out_of_ciphertext(store):
    token = _sentinel_token()
    identity = win_pipe.current_process_identity()
    path = store.write_secret(_payload("term_sec01", token, identity))
    raw = path.read_bytes()
    assert token.encode("ascii") not in raw, "秘密文件里不得出现明文 token"
    assert b"runner" not in raw and b"created_at_filetime" not in raw, "明文 JSON 结构也不得落盘"
    assert len(raw) > 100

    payload = store.read_secret("term_sec01")
    assert payload.token == token
    assert payload.runner_identity() is not None
    assert payload.runner_identity().matches(identity) is True
    assert payload.runner_identity().pid == identity.pid
    assert payload.runner_filetime == identity.created_at_filetime
    assert payload.matches_identity(identity) is True
    assert payload.matches_identity(ProcessIdentity(pid=identity.pid, created_at_filetime=1)) is False
    assert "token" not in payload.as_dict()
    assert payload.as_dict()["token_present"] is True
    assert win_pipe.dacl_entries(str(path))[0]["sid"] == win_pipe.current_user_sid()


@windows_only
def test_write_is_atomic_and_leaves_no_tmp_files(store, tmp_path):
    identity = win_pipe.current_process_identity()
    first = store.write_secret(_payload("term_sec02", _sentinel_token(), identity))
    second_token = _sentinel_token()
    store.write_secret(_payload("term_sec02", second_token, identity, updated_at=2000.0))
    assert store.read_secret("term_sec02").token == second_token
    leftovers = [p.name for p in (tmp_path / "data" / "secrets").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"原子写不得残留 tmp: {leftovers}"
    assert first.exists()


# ══════════════════════════════════════════════════════════════════════════
# ACL：从创建时生效 + 其它 SID 的 allow ACE 被拒绝
# ══════════════════════════════════════════════════════════════════════════


@windows_only
def test_secrets_dir_and_files_are_owner_only_from_creation(store):
    identity = win_pipe.current_process_identity()
    secret_path = store.write_secret(_payload("term_sec03", _sentinel_token(), identity))
    hello_path = store.write_bootstrap_identity("term_sec03")
    sid = win_pipe.current_user_sid()
    for path in (store.secrets_dir, secret_path, hello_path):
        entries = win_pipe.dacl_entries(str(path))
        allowed = [e for e in entries if e["ace_type"] == 0]
        assert allowed, f"{path.name} 必须有 allow ACE"
        foreign = [e for e in allowed if e["sid"] != sid]
        assert foreign == [], f"{path.name} 不得授权其它 SID: {foreign}"
        assert win_pipe.owner_sid(str(path)) in (None, sid)
    # 目录 ACE 带继承标志（OI|CI），子对象因此继承同一 SID
    directory_ace = [e for e in win_pipe.dacl_entries(str(store.secrets_dir)) if e["ace_type"] == 0][0]
    assert directory_ace["ace_flags"] & (win_pipe.OBJECT_INHERIT_ACE | win_pipe.CONTAINER_INHERIT_ACE)
    store.verify_owner_only(secret_path)  # 不抛 = 通过


@windows_only
def test_foreign_ace_on_secret_file_is_rejected(store):
    """负例（结构性）：在本 TA 自建临时文件上加宽 ACL → 读取必须 fail-closed。

    这不是“其它用户实测被拒”（那需要第二账户/安全上下文，本 TA 未做），
    但能证明**存在其它 SID 的 allow ACE 时拒绝使用该秘密**。
    """
    identity = win_pipe.current_process_identity()
    path = store.write_secret(_payload("term_sec04", _sentinel_token(), identity))
    process = subprocess.run(
        ["icacls", str(path), "/grant", "*S-1-1-0:(R)"], capture_output=True
    )
    if process.returncode != 0:
        pytest.skip("icacls unavailable: " + process.stdout.decode("oem", "replace")[:120])
    assert any(entry["sid"] == "S-1-1-0" for entry in win_pipe.dacl_entries(str(path)))
    with pytest.raises(secret_store.SecretSecurityError):
        store.read_secret("term_sec04")
    with pytest.raises(secret_store.SecretSecurityError):
        store.verify_owner_only(path)


# ══════════════════════════════════════════════════════════════════════════
# fail-closed：缺失 / 损坏 / 身份不匹配
# ══════════════════════════════════════════════════════════════════════════


@windows_only
def test_missing_and_corrupt_secrets_fail_closed(store):
    with pytest.raises(secret_store.SecretNotFoundError):
        store.read_secret("term_sec05")
    assert store.exists("term_sec05") is False

    store.ensure_secrets_dir()
    path = store.secret_path("term_sec05")
    win_pipe.write_file_owner_only(str(path), b"not-a-dpapi-blob")
    with pytest.raises(secret_store.SecretProtectionError):
        store.read_secret("term_sec05")

    # DPAPI 合法但内容不是合法 JSON/schema → SecretCorruptError
    protector = secret_store.DpapiProtector(entropy=b"pan-terminal-secret|term_sec05")
    win_pipe.write_file_owner_only(str(path), protector.protect(b"{not json"))
    with pytest.raises(secret_store.SecretCorruptError):
        store.read_secret("term_sec05")

    identity = win_pipe.current_process_identity()
    good = _payload("term_sec05", _sentinel_token(), identity)
    for mutate, expected in (
        ({"schema_version": 99}, secret_store.SecretCorruptError),
        ({"token": "short"}, secret_store.SecretCorruptError),
        ({"pipe_name": r"\\evil\pipe\x"}, secret_store.SecretCorruptError),
        ({"terminal_id": "term_other"}, secret_store.SecretCorruptError),
    ):
        body = json.loads(good.to_json())
        body.update(mutate)
        win_pipe.write_file_owner_only(str(path), protector.protect(json.dumps(body).encode("utf-8")))
        with pytest.raises(expected):
            store.read_secret("term_sec05")

    # 空文件也必须拒绝
    win_pipe.write_file_owner_only(str(path), b"")
    with pytest.raises(secret_store.SecretCorruptError):
        store.read_secret("term_sec05")


@windows_only
def test_identity_mismatch_fails_closed_and_update_recovers(store):
    identity = win_pipe.current_process_identity()
    store.write_secret(_payload("term_sec06", _sentinel_token(), identity))
    wrong = ProcessIdentity(pid=identity.pid, created_at_filetime=identity.created_at_filetime + 1)
    with pytest.raises(secret_store.SecretIdentityMismatchError):
        store.verify_runner_identity("term_sec06", wrong)
    assert store.verify_runner_identity("term_sec06", identity).runner_pid == identity.pid

    # bootstrap hello 换成新身份后，服务可更新秘密身份；旧身份随即失效
    updated = store.update_runner_identity(
        "term_sec06", pid=identity.pid, filetime=identity.created_at_filetime + 2
    )
    assert updated.runner_filetime == identity.created_at_filetime + 2
    with pytest.raises(secret_store.SecretIdentityMismatchError):
        store.verify_runner_identity("term_sec06", identity)
    with pytest.raises(secret_store.SecretNotFoundError):
        store.update_runner_identity("term_missing", pid=1, filetime=1)


@windows_only
def test_write_refuses_incomplete_or_non_256bit_secrets(store):
    identity = win_pipe.current_process_identity()
    with pytest.raises(secret_store.SecretStoreError):
        store.write_secret(_payload("term_sec07", "abc", identity))
    with pytest.raises(secret_store.SecretStoreError):
        store.write_secret(_payload("term_sec07", _sentinel_token(), identity, runner_pid=None))
    with pytest.raises(secret_store.SecretStoreError):
        store.write_secret(_payload("term_sec07", _sentinel_token(), identity, runner_filetime=None))
    assert store.exists("term_sec07") is False


# ══════════════════════════════════════════════════════════════════════════
# 64 位身份 JSON 精确性
# ══════════════════════════════════════════════════════════════════════════


@windows_only
def test_64bit_identity_json_stays_exact(store):
    token = _sentinel_token()
    for filetime in (2**53 + 1, 2**63 - 1, 134000000000000001):
        terminal_id = f"term_exact{filetime % 100000}"
        payload = _payload(
            terminal_id, token, ProcessIdentity(pid=1234, created_at_filetime=filetime)
        )
        store.write_secret(payload)
        back = store.read_secret(terminal_id)
        assert back.runner_filetime == filetime, "64 位 FILETIME 必须精确往返"
        # 明文 JSON（解密后）必须是 decimal 字符串 + 0x 十六进制，且没有浮点
        protector = secret_store.DpapiProtector(entropy=f"pan-terminal-secret|{terminal_id}".encode())
        text = protector.unprotect(store.secret_path(terminal_id).read_bytes()).decode("utf-8")
        assert str(filetime) in text and hex(filetime) in text
        assert "e+" not in text and "E+" not in text
        parsed = json.loads(text)["runner"]
        assert isinstance(parsed["created_at_filetime"], str)
        assert int(parsed["created_at_filetime"]) == filetime
        assert int(parsed["created_at_filetime_hex"], 16) == filetime

    # 浮点 / 缺 hex / 进制不一致 → 拒绝
    base = json.loads(_payload("term_exact9", token, ProcessIdentity(pid=1, created_at_filetime=2)).to_json())
    for mutate in ("float", "missing-hex", "mismatch", "bool"):
        body = json.loads(json.dumps(base))
        if mutate == "float":
            body["runner"]["created_at_filetime"] = float(134000000000000001)
            body["runner"]["created_at_filetime_hex"] = hex(134000000000000001)
        elif mutate == "missing-hex":
            body["runner"].pop("created_at_filetime_hex")
        elif mutate == "mismatch":
            body["runner"]["created_at_filetime_hex"] = "0x1"
        else:
            body["runner"]["pid"] = True
        with pytest.raises(secret_store.SecretCorruptError):
            secret_store.SecretPayload.from_json(json.dumps(body), expect_terminal_id="term_exact9")


# ══════════════════════════════════════════════════════════════════════════
# 路径：越界名 / reparse
# ══════════════════════════════════════════════════════════════════════════


@windows_only
@pytest.mark.parametrize(
    "terminal_id",
    [
        "",
        "sess_abc",
        "term_",
        "term_a/b",
        "term_a\\b",
        "term_../etc",
        "term_a.b",
        "term_" + "x" * 80,
    ],
)
def test_out_of_bounds_terminal_ids_are_rejected(store, terminal_id):
    with pytest.raises(ValueError):
        store.secret_path(terminal_id)
    with pytest.raises(ValueError):
        store.bootstrap_path(terminal_id)
    assert store.exists(terminal_id) is False


@windows_only
def test_reparse_point_secret_path_is_rejected(store, tmp_path):
    store.ensure_secrets_dir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = store.secrets_dir / "term_reparse.secret"
    process = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True
    )
    if process.returncode != 0 or not win_pipe.is_reparse_point(str(link)):
        pytest.skip("junction creation unavailable: " + process.stdout.decode("oem", "replace")[:120])
    try:
        with pytest.raises(secret_store.SecretSecurityError):
            store.read_secret("term_reparse")
        identity = win_pipe.current_process_identity()
        with pytest.raises(secret_store.SecretSecurityError):
            store.write_secret(_payload("term_reparse", _sentinel_token(), identity))
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


# ══════════════════════════════════════════════════════════════════════════
# 删除约束
# ══════════════════════════════════════════════════════════════════════════


@windows_only
def test_delete_requires_verified_exit_or_explicit_caller_responsibility(store):
    identity = win_pipe.current_process_identity()
    store.write_secret(_payload("term_sec08", _sentinel_token(), identity))
    store.write_bootstrap_identity("term_sec08")
    # 断连/失联不构成删除理由
    with pytest.raises(secret_store.SecretDeletionRefused):
        store.delete_secret("term_sec08", reason="client-disconnected")
    with pytest.raises(secret_store.SecretDeletionRefused):
        store.delete_secret("term_sec08", reason="lease-expired", verified_exit=False, caller_responsible="   ")
    assert store.exists("term_sec08") is True
    assert store.bootstrap_path("term_sec08").exists()

    # 显式调用者责任（例如“显式关闭终端”路径）
    assert store.delete_secret(
        "term_sec08", reason="explicit-close", caller_responsible="caller terminated the runner tree"
    ) is True
    assert store.exists("term_sec08") is False
    assert store.bootstrap_path("term_sec08").exists() is False
    assert store.delete_secret("term_sec08", reason="retry", verified_exit=True) is False

    store.write_secret(_payload("term_sec09", _sentinel_token(), identity))
    assert store.delete_secret("term_sec09", reason="closed", verified_exit=True) is True
    with pytest.raises(ValueError):
        store.delete_secret("term_sec09", reason="")


# ══════════════════════════════════════════════════════════════════════════
# bootstrap 闭环
# ══════════════════════════════════════════════════════════════════════════


@windows_only
def test_bootstrap_identity_uses_kernel_raw_filetime_and_can_be_waited(store):
    terminal_id = "term_sec10"
    assert store.read_bootstrap_identity(terminal_id) is None
    path = store.write_bootstrap_identity(terminal_id)
    record = store.read_bootstrap_identity(terminal_id)
    assert record is not None
    identity = win_pipe.current_process_identity()
    assert record.pid == os.getpid() == identity.pid
    assert record.filetime == identity.created_at_filetime  # raw 100ns，非 Popen.pid 猜测
    assert record.identity().matches(identity) is True  # raw FILETIME 精确匹配
    assert store.wait_for_bootstrap_identity(terminal_id, timeout=2.0).pid == os.getpid()
    assert str(record.filetime) in path.read_text(encoding="utf-8")

    # 有界等待：没有秘密时超时抛错，且耗时有界
    started = time.monotonic()
    with pytest.raises(secret_store.SecretNotFoundError):
        store.wait_for_secret(terminal_id, timeout=0.2, poll=0.02)
    assert time.monotonic() - started < 2.0
    with pytest.raises(secret_store.SecretNotFoundError):
        store.wait_for_bootstrap_identity("term_sec11", timeout=0.2, poll=0.02)
    assert store.delete_bootstrap_identity(terminal_id) is True
    assert store.read_bootstrap_identity(terminal_id) is None


@windows_only
def test_registry_record_never_carries_ipc_credentials(store, tmp_path):
    from packages.core.terminal.registry import TerminalRecord, TerminalRegistry, RuntimeState

    terminal_id = "term_sec12"
    token = _sentinel_token()
    identity = win_pipe.current_process_identity()
    store.write_secret(_payload(terminal_id, token, identity))
    registry = TerminalRegistry(tmp_path / "data")
    registry.save(
        TerminalRecord(
            terminal_id=terminal_id,
            status=RuntimeState.RUNNING,
            pid=identity.pid,
            process_created_at_filetime=identity.created_at_filetime,
            pipe=win_pipe.pipe_name_for(terminal_id),
        )
    )
    text = (tmp_path / "data" / f"{terminal_id}.json").read_text(encoding="utf-8")
    assert token not in text
    assert "token" not in text and ".secret" not in text
    record = registry.get(terminal_id)
    assert record.pid == identity.pid
    assert record.process_created_at_filetime == identity.created_at_filetime  # 精确 64 位


# ══════════════════════════════════════════════════════════════════════════
# 跨进程 DPAPI 读取
# ══════════════════════════════════════════════════════════════════════════

READER_SOURCE = r'''
import hashlib, json, os, sys
CONFIG = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
sys.path.insert(0, CONFIG["repo"])
from packages.core.terminal import secret_store

REPORT = {"pid": os.getpid(), "ok": False}
try:
    store = secret_store.SecretStore(CONFIG["data_root"])
    payload = store.read_secret(CONFIG["terminal_id"])
    REPORT["ok"] = True
    REPORT["token_len"] = len(payload.token)
    REPORT["token_sha8"] = hashlib.sha256(payload.token.encode("ascii")).hexdigest()[:8]
    REPORT["token_sha16"] = hashlib.sha256(payload.token.encode("ascii")).hexdigest()[:16]
    REPORT["filetime_digits"] = len(str(payload.runner_filetime))
    REPORT["filetime_sha8"] = hashlib.sha256(str(payload.runner_filetime).encode()).hexdigest()[:8]
    REPORT["pipe_name"] = payload.pipe_name
    REPORT["identity_matches"] = payload.runner_identity() is not None
    REPORT["acl_checked"] = True
except Exception as exc:
    REPORT["error"] = type(exc).__name__
with open(CONFIG["report"], "w", encoding="utf-8") as handle:
    json.dump(REPORT, handle)
'''


BOOTSTRAP_SOURCE = r'''
import hashlib, json, os, sys
CONFIG = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
sys.path.insert(0, CONFIG["repo"])
from packages.core.terminal import secret_store, win_pipe

REPORT = {"pid": os.getpid(), "ok": False}
try:
    store = secret_store.SecretStore(CONFIG["data_root"])
    # ① 出生自证：自身 pid + raw FILETIME（来自 GetProcessTimes，不依赖父进程的 Popen.pid）
    store.write_bootstrap_identity(CONFIG["terminal_id"])
    # ② 等秘密出现（有界），argv 里只有路径，没有 token
    payload = store.wait_for_secret(CONFIG["terminal_id"], timeout=CONFIG.get("secret_timeout", 15.0))
    # ③ 身份自检：不匹配即 fail-closed（本进程退出非零）
    own = win_pipe.current_process_identity()
    payload = store.verify_runner_identity(CONFIG["terminal_id"], own)
    REPORT["ok"] = True
    REPORT["token_len"] = len(payload.token)
    REPORT["token_sha8"] = hashlib.sha256(payload.token.encode("ascii")).hexdigest()[:8]
    REPORT["token_sha16"] = hashlib.sha256(payload.token.encode("ascii")).hexdigest()[:16]
    REPORT["filetime_digits"] = len(str(payload.runner_filetime))
    REPORT["pipe_name"] = payload.pipe_name
    REPORT["filetime_sha8"] = hashlib.sha256(str(payload.runner_filetime).encode()).hexdigest()[:8]
except Exception as exc:
    REPORT["error"] = type(exc).__name__
    with open(CONFIG["report"], "w", encoding="utf-8") as handle:
        json.dump(REPORT, handle)
    raise
with open(CONFIG["report"], "w", encoding="utf-8") as handle:
    json.dump(REPORT, handle)
'''


@windows_only
def test_secret_is_decryptable_from_another_process(store, tmp_path):
    terminal_id = "term_sec13"
    token = _sentinel_token()
    identity = win_pipe.current_process_identity()
    store.write_secret(_payload(terminal_id, token, identity))
    store.write_bootstrap_identity(terminal_id)

    workdir = tmp_path / "reader"
    workdir.mkdir()
    script = workdir / "reader.py"
    script.write_text(READER_SOURCE, encoding="utf-8")
    config_path = workdir / "config.json"
    report_path = workdir / "report.json"
    config_path.write_text(
        json.dumps(
            {
                "repo": str(REPO_ROOT),
                "data_root": str(tmp_path / "data"),
                "terminal_id": terminal_id,
                "report": str(report_path),
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, str(script), str(config_path)],
        cwd=str(REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == 0, stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True, report
    # 同一个 token（用摘要对照，不回传明文）与同一个 raw FILETIME
    assert report["token_len"] == 64
    assert report["token_sha16"] == hashlib.sha256(token.encode("ascii")).hexdigest()[:16]
    assert report["token_sha8"] == hashlib.sha256(token.encode("ascii")).hexdigest()[:8]
    assert report["filetime_digits"] == len(str(identity.created_at_filetime))
    assert report["filetime_sha8"] == hashlib.sha256(str(identity.created_at_filetime).encode()).hexdigest()[:8]
    assert report["pipe_name"] == win_pipe.pipe_name_for(terminal_id)
    # 哨兵扫描：token 不得出现在子进程 stdout/stderr/argv/config
    for label, text in (
        ("stdout", stdout),
        ("stderr", stderr),
        ("argv", json.dumps([str(arg) for arg in process.args])),
        ("config", config_path.read_text(encoding="utf-8")),
        ("report", report_path.read_text(encoding="utf-8")),
    ):
        assert token not in text, f"token leaked into {label}"
    # 秘密文件本身也不含明文（跨进程解密成功 ≠ 明文落盘）
    assert token.encode("ascii") not in store.secret_path(terminal_id).read_bytes()


@windows_only
def test_bootstrap_and_secret_chain_with_real_child_process(tmp_path):
    """模拟 bootstrap 闭环：子进程自证身份 → 父进程写 DPAPI 秘密 → 子进程解密自检。"""
    terminal_id = "term_sec14"
    token = _sentinel_token()
    data_root = tmp_path / "data"
    parent_store = secret_store.SecretStore(data_root)
    workdir = tmp_path / "runner"
    workdir.mkdir()
    script = workdir / "bootstrap.py"
    script.write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
    config_path = workdir / "config.json"
    report_path = workdir / "report.json"
    config_path.write_text(
        json.dumps(
            {
                "repo": str(REPO_ROOT),
                "data_root": str(data_root),
                "terminal_id": terminal_id,
                "report": str(report_path),
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, str(script), str(config_path)],
        cwd=str(REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # F11：runner 身份取子进程自证（hello）+ 内核核验，不按 Popen.pid 猜（uv 蹦床下不同）
        hello = parent_store.wait_for_bootstrap_identity(terminal_id, timeout=15.0)
        probe = win_pipe.probe_process(int(hello.pid))
        assert probe.status is ProcessStatus.ALIVE and probe.identity is not None
        assert probe.identity.matches(hello.identity()) is True
        parent_store.write_secret(_payload(terminal_id, token, hello.identity()))
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["ok"] is True, report
        assert report["token_sha16"] == hashlib.sha256(token.encode("ascii")).hexdigest()[:16]
        assert token not in stdout and token not in stderr
        assert "token" not in config_path.read_text(encoding="utf-8")
    finally:
        # 先终止**真实 runner**（自证 + 内核核验），再终止 launcher（各自可证身份）
        try:
            hello_identity = parent_store.read_bootstrap_identity(terminal_id, verify=False)
        except Exception:  # noqa: BLE001 - 失败兜底
            hello_identity = None
        if hello_identity is not None:
            probe = win_pipe.probe_process(int(hello_identity.pid))
            if probe.status is ProcessStatus.ALIVE:
                win_pipe.terminate_verified_process(
                    int(hello_identity.pid), hello_identity.filetime
                )
        if process.poll() is None:
            launcher = win_pipe.ProcessIdentityHandle.open(int(process.pid))
            if launcher is not None:
                try:
                    if launcher.is_alive():
                        win_pipe.terminate_verified_process(int(process.pid), launcher.filetime)
                finally:
                    launcher.close()
            process.wait(timeout=10)


# ══════════════════════════════════════════════════════════════════════════
# 边界声明（防“看起来隔离了”）
# ══════════════════════════════════════════════════════════════════════════


@windows_only
def test_docs_declare_same_user_boundary_and_untested_negatives():
    module_docs = (
        secret_store.__doc__ or "",
        win_pipe.__doc__ or "",
        (REPO_ROOT / "docs" / "design" / "PAN_TERMINAL_IPC_INTERFACES_20261003.md").read_text(
            encoding="utf-8"
        ),
    )
    for text in module_docs:
        assert "同一" in text and "用户" in text, "必须声明同用户信任边界"
    design_doc = module_docs[2]
    assert "未实测" in design_doc or "未验证" in design_doc
    assert "S-1-1-0" not in design_doc  # 文档不写死 SID


@windows_only
def test_protector_reports_user_scope():
    protector = secret_store.DpapiProtector()
    described = protector.describe()
    assert described["scope"] == "user"
    assert described["ui_forbidden"] is True
    blob = protector.protect(b"payload")
    assert protector.unprotect(blob) == b"payload"
    assert b"payload" not in blob
    with pytest.raises(secret_store.SecretProtectionError):
        protector.unprotect(b"definitely-not-a-dpapi-blob")
    # entropy 绑定的密文不能在不带 entropy 时解开（跨用途隔离）
    bound = secret_store.DpapiProtector(entropy=b"pan-terminal-secret|term_x")
    with pytest.raises(secret_store.SecretProtectionError):
        secret_store.DpapiProtector().unprotect(bound.protect(b"payload"))


# ══════════════════════════════════════════════════════════════════════════
# r2 返工：独立复核 21 负例 → 安全正向回归（F7/F8/F9/F10）
#
# 由只读复核树 audit/terminal/implementation/ipc-review/tests 的负例转换而来：
# 原负例断言「缺陷存在」，这里断言「缺陷不存在」的安全正向期望。
# ACL 构造（真实 DACL 装入本 TA 自建临时文件）与复核探针同法，全部在 tmp_path 内，
# 不创建账户、不改账户权限、不触既有文件。
# ══════════════════════════════════════════════════════════════════════════

_ACL_REVISION_DS = 4
_ACCESS_ALLOWED_ACE_TYPE = 0
_ACCESS_DENIED_ACE_TYPE = 1
_ACCESS_ALLOWED_OBJECT_ACE_TYPE = 5
_ACCESS_ALLOWED_CALLBACK_ACE_TYPE = 9
_ACCESS_DENIED_CALLBACK_ACE_TYPE = 10
_FILE_ALL_ACCESS = 0x001F01FF
_FILE_GENERIC_READ = 0x00120089
_EVERYONE_SID = "S-1-1-0"

_adv_raw = ctypes.WinDLL("advapi32", use_last_error=True)
_k32_raw = ctypes.WinDLL("kernel32", use_last_error=True)
_adv_raw.ConvertStringSidToSidW.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p))
_adv_raw.ConvertStringSidToSidW.restype = ctypes.wintypes.BOOL
_adv_raw.GetLengthSid.argtypes = (ctypes.c_void_p,)
_adv_raw.GetLengthSid.restype = ctypes.wintypes.DWORD
_adv_raw.SetNamedSecurityInfoW.argtypes = (
    ctypes.c_wchar_p,
    ctypes.c_int,
    ctypes.wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)
_adv_raw.SetNamedSecurityInfoW.restype = ctypes.wintypes.DWORD
_k32_raw.LocalFree.argtypes = (ctypes.wintypes.HLOCAL,)
_k32_raw.LocalFree.restype = ctypes.wintypes.HLOCAL

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000


def _sid_bytes(sid_string: str) -> bytes:
    pointer = ctypes.c_void_p()
    if not _adv_raw.ConvertStringSidToSidW(sid_string, ctypes.byref(pointer)):
        raise OSError(f"ConvertStringSidToSidW failed for {sid_string}")
    try:
        return ctypes.string_at(pointer, int(_adv_raw.GetLengthSid(pointer)))
    finally:
        _k32_raw.LocalFree(ctypes.cast(pointer, ctypes.wintypes.HLOCAL))


def _ace_normal(ace_type: int, mask: int, sid: bytes) -> bytes:
    size = 4 + 4 + len(sid)
    return struct.pack("<BBHI", ace_type, 0, size, mask) + sid


def _ace_object(ace_type: int, mask: int, sid: bytes, *, object_type_guid: bool = False,
                inherited_type_guid: bool = False) -> bytes:
    """ACCESS_*_OBJECT_ACE：mask(4) + flags(4) + [GUID(s)] + SID。"""
    flags = (0x1 if object_type_guid else 0) | (0x2 if inherited_type_guid else 0)
    guids = b"\x11" * (16 if object_type_guid else 0) + b"\x22" * (16 if inherited_type_guid else 0)
    size = 4 + 4 + 4 + len(guids) + len(sid)
    return struct.pack("<BBHII", ace_type, 0, size, mask, flags) + guids + sid


def _ace_callback(ace_type: int, mask: int, sid: bytes, *, application_data: bytes = b"\xab\xcd") -> bytes:
    """ACCESS_*_CALLBACK_ACE：mask(4) + SID + application data。"""
    size = 4 + 4 + len(sid) + len(application_data)
    return struct.pack("<BBHI", ace_type, 0, size, mask) + sid + application_data


def _build_acl(aces: list[bytes]) -> bytes:
    body = b"".join(aces)
    return struct.pack("<BBHHH", _ACL_REVISION_DS, 0, 8 + len(body), len(aces), 0) + body


def _set_dacl(path: Path, acl: bytes | None) -> int:
    if acl is None:
        return int(
            _adv_raw.SetNamedSecurityInfoW(
                str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, None, None
            )
        )
    buffer = ctypes.create_string_buffer(acl, len(acl))
    return int(
        _adv_raw.SetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.cast(buffer, ctypes.c_void_p),
            None,
        )
    )


def _fresh_secret(store: secret_store.SecretStore, terminal_id: str) -> Path:
    identity = win_pipe.current_process_identity()
    return store.write_secret(_payload(terminal_id, _sentinel_token(), identity))


# ── F7：owner-only 严格白名单 ACE（object/callback/未知一律 fail-closed） ──


@windows_only
def test_f7_object_ace_for_foreign_sid_is_rejected_and_attributed(store):
    """object allow ACE（type 5）里的外部 SID 必须被解析出来并拒绝使用秘密。"""
    path = _fresh_secret(store, "term_f7_object")
    user_sid = win_pipe.current_user_sid()
    acl = _build_acl(
        [
            _ace_normal(_ACCESS_ALLOWED_ACE_TYPE, _FILE_ALL_ACCESS, _sid_bytes(user_sid)),
            _ace_object(_ACCESS_ALLOWED_OBJECT_ACE_TYPE, _FILE_GENERIC_READ, _sid_bytes(_EVERYONE_SID)),
        ]
    )
    assert _set_dacl(path, acl) == 0

    entries = win_pipe.dacl_entries(str(path))
    object_entries = [entry for entry in entries if entry["ace_type"] == _ACCESS_ALLOWED_OBJECT_ACE_TYPE]
    assert object_entries, "必须能读到 object ACE"
    assert object_entries[0]["sid"] == _EVERYONE_SID, "object ACE 的 SID 必须可归属（证据不失明）"
    assert object_entries[0]["mask"] == _FILE_GENERIC_READ

    with pytest.raises(secret_store.SecretSecurityError):
        store.verify_owner_only(path)
    with pytest.raises(secret_store.SecretSecurityError):
        store.read_secret("term_f7_object")


@windows_only
def test_f7_object_ace_with_guids_is_still_attributed_and_rejected(store):
    """带 object/inherited type GUID 的 object ACE 同样要定位 SID（不得越过 GUID 误读）。"""
    path = _fresh_secret(store, "term_f7_guid")
    user_sid = win_pipe.current_user_sid()
    acl = _build_acl(
        [
            _ace_normal(_ACCESS_ALLOWED_ACE_TYPE, _FILE_ALL_ACCESS, _sid_bytes(user_sid)),
            _ace_object(
                _ACCESS_ALLOWED_OBJECT_ACE_TYPE,
                _FILE_GENERIC_READ,
                _sid_bytes(_EVERYONE_SID),
                object_type_guid=True,
                inherited_type_guid=True,
            ),
        ]
    )
    assert _set_dacl(path, acl) == 0
    entries = [
        entry
        for entry in win_pipe.dacl_entries(str(path))
        if entry["ace_type"] == _ACCESS_ALLOWED_OBJECT_ACE_TYPE
    ]
    assert entries and entries[0]["sid"] == _EVERYONE_SID
    with pytest.raises(secret_store.SecretSecurityError):
        store.verify_owner_only(path)


@windows_only
def test_f7_callback_ace_for_foreign_sid_is_rejected(store, monkeypatch):
    """callback allow ACE（type 9/10）与 object callback（11/12）：解析 SID 并拒绝外部主体。

    本机 ``SetNamedSecurityInfoW`` 不接受自建 callback ACE（ERROR_INVALID_ACL，与复核
    探针一致），因此这里**注入** ``dacl_entries`` 的解析结果（标注为注入），
    验证白名单逻辑对外部主体 callback/未知类型一律 fail-closed；object ACE 有真实 DACL 证据。
    """
    path = _fresh_secret(store, "term_f7_callback")
    user_sid = win_pipe.current_user_sid()
    injected = [
        {"ace_type": _ACCESS_ALLOWED_ACE_TYPE, "ace_flags": 0, "sid": user_sid, "mask": _FILE_ALL_ACCESS},
        {"ace_type": _ACCESS_ALLOWED_CALLBACK_ACE_TYPE, "ace_flags": 0, "sid": _EVERYONE_SID, "mask": _FILE_GENERIC_READ},
        {"ace_type": _ACCESS_DENIED_CALLBACK_ACE_TYPE, "ace_flags": 0, "sid": _EVERYONE_SID, "mask": _FILE_GENERIC_READ},
        {"ace_type": 11, "ace_flags": 0, "sid": _EVERYONE_SID, "mask": _FILE_GENERIC_READ},
        {"ace_type": 12, "ace_flags": 0, "sid": None, "mask": None},
    ]
    monkeypatch.setattr(win_pipe, "dacl_entries", lambda path: [dict(entry) for entry in injected])
    with pytest.raises(secret_store.SecretSecurityError):
        store.verify_owner_only(path)
    with pytest.raises(secret_store.SecretSecurityError):
        store.read_secret("term_f7_callback")

    # 只保留当前用户的回调 ACE 时通过（白名单正向）
    injected[:] = [
        {"ace_type": _ACCESS_ALLOWED_ACE_TYPE, "ace_flags": 0, "sid": user_sid, "mask": _FILE_ALL_ACCESS},
        {"ace_type": _ACCESS_ALLOWED_CALLBACK_ACE_TYPE, "ace_flags": 0, "sid": user_sid, "mask": _FILE_GENERIC_READ},
    ]
    store.verify_owner_only(path)


@windows_only
def test_f7_unknown_and_non_allow_ace_types_are_rejected(store):
    """真实 DACL：未知类型（0x7F）与 AUDIT 类（0x02）一律 fail-closed（不跳过）。"""
    path = _fresh_secret(store, "term_f7_unknown")
    user_sid = win_pipe.current_user_sid()
    for ace_type in (0x7F, 0x02):
        acl = _build_acl(
            [
                _ace_normal(_ACCESS_ALLOWED_ACE_TYPE, _FILE_ALL_ACCESS, _sid_bytes(user_sid)),
                _ace_normal(ace_type, _FILE_GENERIC_READ, _sid_bytes(_EVERYONE_SID)),
            ]
        )
        assert _set_dacl(path, acl) == 0, f"ACE type {ace_type:#x} 必须能装入真实 DACL"
        entries = [entry for entry in win_pipe.dacl_entries(str(path)) if entry["ace_type"] == ace_type]
        assert entries, "必须读到该 ACE"
        with pytest.raises(secret_store.SecretSecurityError):
            store.verify_owner_only(path)
        with pytest.raises(secret_store.SecretSecurityError):
            store.read_secret("term_f7_unknown")


@windows_only
def test_f7_owner_grant_of_same_user_and_deny_aces_are_accepted(store):
    """白名单正向：同一用户的 object allow 与任何 deny ACE 都不影响使用。

    （callback ACE 无法用本机 ``SetNamedSecurityInfoW`` 装入真实 DACL——
    ERROR_INVALID_ACL；其白名单行为由 test_f7_callback_ace_* 的注入用例覆盖。）
    """
    path = _fresh_secret(store, "term_f7_allow")
    user_sid = win_pipe.current_user_sid()
    acl = _build_acl(
        [
            _ace_normal(_ACCESS_ALLOWED_ACE_TYPE, _FILE_ALL_ACCESS, _sid_bytes(user_sid)),
            _ace_object(_ACCESS_ALLOWED_OBJECT_ACE_TYPE, _FILE_GENERIC_READ, _sid_bytes(user_sid)),
            _ace_normal(_ACCESS_DENIED_ACE_TYPE, _FILE_GENERIC_READ, _sid_bytes(_EVERYONE_SID)),
        ]
    )
    assert _set_dacl(path, acl) == 0
    entries = win_pipe.dacl_entries(str(path))
    assert any(entry["ace_type"] == _ACCESS_ALLOWED_OBJECT_ACE_TYPE for entry in entries)
    store.verify_owner_only(path)  # 不抛：没有外部主体的 allow
    assert store.read_secret("term_f7_allow").token


# ── F8：owner 查询未知一律拒绝 ──


@windows_only
def test_f8_owner_query_failure_or_foreign_owner_is_rejected(store, monkeypatch):
    """owner 查不到（None）必须 fail-closed；查询抛错与外部 owner 同样拒绝。"""
    path = _fresh_secret(store, "term_f8_owner")

    monkeypatch.setattr(win_pipe, "owner_sid", lambda path: None)
    with pytest.raises(secret_store.SecretSecurityError):
        store.verify_owner_only(path)
    with pytest.raises(secret_store.SecretSecurityError):
        store.read_secret("term_f8_owner")

    def _boom(path):  # noqa: ANN001
        raise win_pipe.PipeSecurityError("query failed")

    monkeypatch.setattr(win_pipe, "owner_sid", _boom)
    with pytest.raises(secret_store.SecretSecurityError):
        store.verify_owner_only(path)

    monkeypatch.setattr(win_pipe, "owner_sid", lambda path: "S-1-5-18")
    with pytest.raises(secret_store.SecretSecurityError):
        store.verify_owner_only(path)


# ── F9：唯一 CREATE_NEW 临时文件 + 只清理自有 + 锁内 read-modify-write ──


@windows_only
def test_f9_tmp_names_are_unique_and_created_exclusively(store, tmp_path, monkeypatch):
    """同一 pid/同一毫秒的两个写者也必须用不同 tmp，并以 CREATE_NEW 独占创建。"""
    fixed = 1234567.890
    store.clock = lambda: fixed
    identities = win_pipe.current_process_identity()
    observed: list[str] = []
    real_create = win_pipe.create_file_exclusive_owner_only

    def recording_create(path, data, **kwargs):  # noqa: ANN001
        observed.append(str(path))
        return real_create(path, data, **kwargs)

    monkeypatch.setattr(win_pipe, "create_file_exclusive_owner_only", recording_create)
    for index in range(4):
        store.write_secret(_payload("term_f9_unique", _sentinel_token(), identities, updated_at=float(index)))
    assert len(observed) == 4
    assert len(set(observed)) == 4, f"tmp 名必须唯一（同 pid 同毫秒也不例外）: {observed}"
    assert all(name.endswith(".tmp") for name in observed)
    assert not any(Path(name).exists() for name in observed), "成功路径不得残留 tmp"

    # CREATE_NEW 语义：已存在的路径必须报错且**不覆盖**别人的文件
    existing = tmp_path / "occupied.bin"
    win_pipe.write_file_owner_only(str(existing), b"someone-else")
    with pytest.raises(FileExistsError):
        win_pipe.create_file_exclusive_owner_only(str(existing), b"mine")
    assert existing.read_bytes() == b"someone-else", "绝不允许覆盖/删除他写者的文件"


@windows_only
def test_f9_concurrent_writers_never_delete_foreign_tmp_or_publish_it(store, tmp_path, monkeypatch):
    """并发写（唯一 tmp + 锁）：不删他写者的文件、各自只发布自己的密文、无残留。

    - 先在 secrets 目录放一个"他写者"的 tmp 文件（哨兵）：写秘密不得删除/覆盖它；
    - 两个写者同 pid、同（固定）毫秒并发写：唯一 tmp 名 + 跨进程锁下都成功；
      每个写者 replace 之后目标文件必须是**它自己的** payload（用同 entropy 解密核对）；
    - 最终 payload ∈ {A, B}，且没有新的 tmp 残留。
    """
    fixed = 1234567.890
    store.clock = lambda: fixed
    terminal_id = "term_f9_race"
    token_a = "a" * 64
    token_b = "b" * 64
    payload_a = _payload(terminal_id, token_a, win_pipe.current_process_identity())
    payload_b = _payload(terminal_id, token_b, win_pipe.current_process_identity())

    store.ensure_secrets_dir()
    foreign_tmp = store.secrets_dir / f"{terminal_id}.secret.deadbeefdeadbeef.tmp"
    win_pipe.write_file_owner_only(str(foreign_tmp), b"not-ours")

    published: dict = {}
    real_replace = win_pipe.replace_file_atomic

    def replace_hook(source, destination, **kwargs):  # noqa: ANN001
        name = threading.current_thread().name
        result = real_replace(source, destination)
        published[name] = json.loads(_decrypt_secret_text(store, terminal_id))["token"]
        return result

    monkeypatch.setattr(win_pipe, "replace_file_atomic", replace_hook)

    outcome: dict = {}
    barrier = threading.Barrier(2)

    def writer(name: str, payload) -> None:  # noqa: ANN001
        try:
            barrier.wait(timeout=10)
            store.write_secret(payload)
            outcome[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - 用例断言失败类型
            outcome[name] = exc

    thread_a = threading.Thread(target=writer, args=("f9-writer-A", payload_a), name="f9-writer-A")
    thread_b = threading.Thread(target=writer, args=("f9-writer-B", payload_b), name="f9-writer-B")
    thread_a.start()
    thread_b.start()
    thread_a.join(30)
    thread_b.join(30)
    assert not thread_a.is_alive() and not thread_b.is_alive()

    assert outcome.get("f9-writer-A") == "ok", outcome
    assert outcome.get("f9-writer-B") == "ok", outcome
    # 每个写者发布的是**自己的**密文（不得静默发布对方的 payload）
    assert published["f9-writer-A"] == token_a, published
    assert published["f9-writer-B"] == token_b, published
    stored = store.read_secret(terminal_id).token
    assert stored in (token_a, token_b)
    # 他写者的文件既没被删也没被覆盖
    assert foreign_tmp.exists(), "不得删除/覆盖他写者的 tmp 文件"
    assert foreign_tmp.read_bytes() == b"not-ours"
    leftovers = sorted(
        p.name for p in store.secrets_dir.iterdir() if p.name.endswith(".tmp")
    )
    assert leftovers == [foreign_tmp.name], f"只应留下他写者的 tmp（我们自己的必须清干净）: {leftovers}"
    assert store.cleanup_failures == []


def _decrypt_secret_text(store: secret_store.SecretStore, terminal_id: str) -> str:
    """用与 store 相同的 entropy 解出明文 JSON（仅测试内使用，不落盘）。"""
    protector = secret_store.DpapiProtector(entropy=f"pan-terminal-secret|{terminal_id}".encode())
    return protector.unprotect(store.secret_path(terminal_id).read_bytes()).decode("utf-8")


@windows_only
def test_f9_concurrent_writes_and_identity_updates_preserve_fields(store):
    """真实并发：写入/身份更新全部正确提交，token/created_at/pipe 不丢、结果可解析。"""
    terminal_id = "term_f9_concurrent"
    identity = win_pipe.current_process_identity()
    token = _sentinel_token()
    store.write_secret(_payload(terminal_id, token, identity, created_at=4242.0))
    errors: list = []
    filetimes: list[int] = [identity.created_at_filetime + step for step in range(1, 7)]
    barrier = threading.Barrier(8)

    def updater(filetime: int) -> None:
        try:
            barrier.wait(timeout=10)
            store.update_runner_identity(terminal_id, pid=int(identity.pid), filetime=filetime)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader() -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(20):
                payload = store.read_secret(terminal_id)
                assert payload.token == token, "并发读不得看到丢字段/空 token 的中间态"
                assert payload.created_at == 4242.0
                assert payload.pipe_name == win_pipe.pipe_name_for(terminal_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=updater, args=(filetime,)) for filetime in filetimes]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive()
    assert errors == [], f"并发写/读不得失败或丢字段: {errors!r}"

    final = store.read_secret(terminal_id)
    assert final.token == token and final.created_at == 4242.0
    assert final.pipe_name == win_pipe.pipe_name_for(terminal_id)
    assert final.runner_filetime in filetimes, "最终身份必须是某次真实提交（不能是混合/丢字段状态）"
    assert final.runner_pid == int(identity.pid)


# ── F10：bootstrap 身份必须有强制核验入口 ──


@windows_only
def test_f10_wait_for_bootstrap_rejects_fabricated_identity(store):
    """伪造的 hello（pid 真实但 FILETIME 伪造）必须在核验入口被拒绝，绝不能返回。"""
    store.write_bootstrap_identity(
        "term_f10_fake", pid=os.getpid(), filetime=424242, now=time.time()
    )
    with pytest.raises(secret_store.SecretStoreError):
        store.wait_for_bootstrap_identity("term_f10_fake", timeout=1.0)
    # 显式诊断路径（明确标注不可信）仍可读到原值，但绝不作为服务身份使用
    raw = store.read_bootstrap_identity("term_f10_fake", verify=False)
    assert raw is not None and raw.filetime == 424242
    with pytest.raises(secret_store.SecretStoreError):
        store.read_bootstrap_identity("term_f10_fake")


@windows_only
def test_f10_wait_for_bootstrap_verifies_real_identity(store):
    """真实自证（本进程 pid + 内核 FILETIME）必须通过核验并返回已核验记录。"""
    terminal_id = "term_f10_real"
    store.write_bootstrap_identity(terminal_id)
    record = store.wait_for_bootstrap_identity(terminal_id, timeout=2.0)
    kernel = win_pipe.current_process_identity()
    assert record.pid == kernel.pid
    assert record.filetime == kernel.created_at_filetime
    assert store.read_bootstrap_identity(terminal_id).filetime == kernel.created_at_filetime


@windows_only
def test_f10_unverifiable_probe_fails_closed(store, monkeypatch):
    """探针 UNKNOWN / DEAD / 缺探针时一律 fail-closed（不得返回未核验身份）。"""
    terminal_id = "term_f10_probe"
    store.write_bootstrap_identity(terminal_id)
    from packages.core.terminal.contracts import ProcessProbe, ProcessStatus

    monkeypatch.setattr(
        win_pipe, "default_identity_probe",
        lambda pid: ProcessProbe(status=ProcessStatus.UNKNOWN, detail="cannot inspect"),
    )
    with pytest.raises(secret_store.SecretStoreError):
        store.wait_for_bootstrap_identity(terminal_id, timeout=1.0, probe=win_pipe.default_identity_probe)

    monkeypatch.setattr(
        win_pipe, "default_identity_probe",
        lambda pid: ProcessProbe(status=ProcessStatus.DEAD, detail="exited"),
    )
    with pytest.raises(secret_store.SecretStoreError):
        store.read_bootstrap_identity(terminal_id, probe=win_pipe.default_identity_probe)

    monkeypatch.setattr(win_pipe, "default_identity_probe", lambda pid: None)
    with pytest.raises(secret_store.SecretStoreError):
        store.wait_for_bootstrap_identity(terminal_id, timeout=1.0, probe=win_pipe.default_identity_probe)

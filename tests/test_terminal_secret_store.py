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

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from packages.core.terminal import secret_store, win_pipe
from packages.core.terminal.contracts import ProcessIdentity

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
        hello = parent_store.wait_for_bootstrap_identity(terminal_id, timeout=15.0)
        assert hello.pid == process.pid
        kernel = _identity_for(process.pid)
        assert hello.filetime == kernel.created_at_filetime
        parent_store.write_secret(_payload(terminal_id, token, hello.identity()))
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["ok"] is True, report
        assert report["token_sha16"] == hashlib.sha256(token.encode("ascii")).hexdigest()[:16]
        assert token not in stdout and token not in stderr
        assert "token" not in config_path.read_text(encoding="utf-8")
    finally:
        if process.poll() is None:  # pragma: no cover - 失败兜底（先核验身份再终止）
            kernel = _identity_for(process.pid)
            win_pipe.terminate_verified_process(process.pid, kernel.created_at_filetime)
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

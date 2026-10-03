"""TerminalService 控制层测试（确定性注入 + 真实 Windows 隔离组合）。

分两层，与既有套件口径一致：

- **确定性层**（注入原语，不起真实进程）：容量准入与并发 create、启动部分失败保
  owner、心跳不被慢业务拖死、控制权撤销/迟到输入、close 迟到结果与重试不重叠、
  shutdown 预算含锁等待、未知身份零触碰、记录写失败保 owner、秘密不提前删除、
  detach 拒绝零变化、gap/F5 不升级、reconcile 不冒充恢复、导入无副作用。
- **真实层**（Windows + 真 ConPTY/Job/DPAPI/命名管道/headless 引擎，隔离临时数据根）：
  默认创建与四门发布、真实 cwd、中文输入与 read/snapshot、独立心跳、断连同 PID、
  显式 close 整树 + sidecar 收尾、正常 shutdown、宿主崩溃后 lease 自停、新实例
  reconcile。

纪律：清理只对**自有**资源做同 handle 身份核验（raw FILETIME + Wait），不按名广杀；
不启动既有 Pan/完整 Web 服务，不使用 8768。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from packages.core.terminal import service as service_module
from packages.core.terminal import win_pipe
from packages.core.terminal.contracts import (
    NotControlLeaseError,
    ProcessStatus,
    RuntimeState,
    StaleLeaseError,
    UnknownTerminalError,
)
from packages.core.terminal.registry import TerminalRegistry
from packages.core.terminal.service import (
    CapacityExceeded,
    CleanupUnconfirmed,
    DetachRefused,
    ServiceContext,
    StartupFailed,
    TerminalNotAttached,
    TerminalService,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="service 生产路径依赖 Windows ConPTY / Job Object / DPAPI / 命名管道",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_DIR = REPO_ROOT / "packages/core/terminal/emulator_sidecar"


def _sidecar_deps_present() -> bool:
    return (SIDECAR_DIR / "node_modules/@xterm/headless/package.json").is_file()


requires_sidecar = pytest.mark.skipif(
    not _sidecar_deps_present(),
    reason="sidecar 依赖未安装（需在 emulator_sidecar/ 执行 npm ci）",
)


def _wait_until(predicate: Callable[[], Any], timeout: float, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


# ══════════════════════════════════════════════════════════════════════════
# 注入替身（只替换"谁来执行原语"；服务层判定纪律是生产对象）
# ══════════════════════════════════════════════════════════════════════════


class _FakeStore:
    """``SecretStore`` 替身：记录删除请求，**不**真的落盘。"""

    def __init__(self, root: Path, *, delete_error: Exception | None = None) -> None:
        self.root = Path(root)
        self.secrets_dir = self.root / "secrets"
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.deleted: list[tuple[str, str, bool, str | None]] = []
        self.existing: set[str] = set()
        self.delete_error = delete_error
        self._counter = 0

    def ensure_secrets_dir(self) -> Path:
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        return self.secrets_dir

    def secret_path(self, terminal_id: str) -> Path:
        return self.secrets_dir / f"{terminal_id}.secret"

    def wait_for_bootstrap_identity(self, terminal_id: str, *, timeout: float = 10.0, poll: float = 0.05):
        from packages.core.terminal.secret_store import BootstrapIdentity

        self._counter += 1
        self.existing.add(terminal_id)
        filetime = 133400000000000000 + self._counter
        return BootstrapIdentity(
            terminal_id=str(terminal_id),
            pid=40000 + self._counter,
            filetime=filetime,
            written_at=time.time(),
        )

    def write_secret(self, payload: Any) -> Path:
        self.existing.add(payload.terminal_id)
        return self.secret_path(payload.terminal_id)

    def exists(self, terminal_id: str) -> bool:
        return terminal_id in self.existing

    def delete_secret(
        self,
        terminal_id: str,
        *,
        reason: str,
        verified_exit: bool = False,
        caller_responsible: str | None = None,
    ) -> bool:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append((terminal_id, reason, bool(verified_exit), caller_responsible))
        self.existing.discard(terminal_id)
        return True


class _FakeProbe:
    """``ProcessProbe`` 替身（构造指定三态）。"""

    def __init__(self, status: ProcessStatus, filetime: int | None = None) -> None:
        self.status = status
        self.identity = None
        if filetime is not None:
            from packages.core.terminal.contracts import ProcessIdentity

            self.identity = ProcessIdentity(pid=1, created_at_filetime=filetime)


class _FakeClient:
    """``RunnerClient`` 替身：脚本化响应 + 调用计数。"""

    def __init__(
        self,
        terminal_id: str,
        *,
        client_id: str,
        data_root: Any = None,
        describe_state: str = "running",
        attach_error: Exception | None = None,
        close_status: str = "exited",
        detach_status: str = "detach-refused",
        snapshot_payload: dict[str, Any] | None = None,
        read_payload: dict[str, Any] | None = None,
        slow_seconds: float = 0.0,
    ) -> None:
        self.terminal_id = terminal_id
        self.client_id = client_id
        self.data_root = data_root
        self.describe_state = describe_state
        self.attach_error = attach_error
        self.close_status = close_status
        self.detach_status = detach_status
        self.snapshot_payload = dict(snapshot_payload or {})
        self.read_payload = dict(read_payload or {})
        self.slow_seconds = float(slow_seconds)
        self.attached = False
        self.released = False
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def attach(self) -> "_FakeClient":
        self._count("attach")
        if self.attach_error is not None:
            raise self.attach_error
        self.attached = True
        return self

    def release_connection(self) -> None:
        self._count("release")
        self.released = True
        self.attached = False

    def describe(self) -> dict[str, Any]:
        self._count("describe")
        return {
            "runner_state": self.describe_state,
            "first_retained_seq": int(self.read_payload.get("first_retained_seq") or 0),
            "durability": {"capable": False, "ambient_job": True},
        }

    def heartbeat(self, **kwargs: Any) -> dict[str, Any]:
        self._count("heartbeat")
        return {"status": "ok", "ok": True}

    def read(self, cursor: int = 0, *, max_bytes: int | None = None) -> dict[str, Any]:
        self._count("read")
        payload = {
            "data": b"hello",
            "seq": int(cursor),
            "size": 5,
            "next_cursor": int(cursor) + 5,
            "gap": None,
            "truncated": False,
            "total_bytes": int(cursor) + 5,
            "first_retained_seq": 0,
            "status": "running",
        }
        payload.update(self.read_payload)
        return payload

    def input(self, data: bytes, **kwargs: Any) -> dict[str, Any]:
        self._count("input")
        if self.slow_seconds:
            time.sleep(self.slow_seconds)
        return {"status": "ok", "ok": True, "size": len(data), "describe": {}}

    def resize(self, rows: int, cols: int) -> dict[str, Any]:
        self._count("resize")
        return {
            "status": "ok",
            "rows": int(rows),
            "cols": int(cols),
            "describe": {"emulator_resize": {"ok": True}},
        }

    def snapshot(self, *, timeout_ms: int = 5000) -> dict[str, Any]:
        self._count("snapshot")
        if self.slow_seconds:
            time.sleep(self.slow_seconds)
        payload = {
            "status": "running",
            "serialized_screen": "screen",
            "cursor": 100,
            "rows": 24,
            "cols": 80,
            "fidelity": "partial",
            "recovery": "degraded",
            "feed_lag": False,
            "note": "unverified-sequence",
            "engine": "xterm-headless+serialize",
            "cursors_valid": None,
            "reset_unconfirmed": None,
            "diagnostics": {"reasons": []},
        }
        payload.update(self.snapshot_payload)
        return payload

    def close(self, *, reason: str = "explicit-close") -> dict[str, Any]:
        self._count("close")
        return {"status": self.close_status, "ok": self.close_status == "exited", "describe": {}}

    def detach(self) -> dict[str, Any]:
        self._count("detach")
        return {
            "status": self.detach_status,
            "ok": self.detach_status == "detached",
            "durability": {"capable": False, "ambient_job": True},
        }


class _FakeProcess:
    """``Popen`` 替身：``poll()`` 由测试控制。"""

    def __init__(self, pid: int = 50000) -> None:
        self.pid = int(pid)
        self._returncode: int | None = None

    def exit(self, code: int = 0) -> None:
        self._returncode = int(code)

    def poll(self) -> int | None:
        return self._returncode


def _make_service(
    tmp_path: Path,
    *,
    clients: dict[str, Any] | None = None,
    process: _FakeProcess | None = None,
    store: _FakeStore | None = None,
    probe: Callable[[int], Any] | None = None,
    registry: TerminalRegistry | None = None,
    **kwargs: Any,
) -> TerminalService:
    """构造注入式服务（确定性层用；不派生真实进程）。"""
    root = tmp_path / "terminals"
    made_clients: list[_FakeClient] = []

    def spawn(terminal_id, *, secret_file, cwd, shell_argv, root):
        return process if process is not None else _FakeProcess()

    def client_factory(terminal_id, **client_kwargs):
        if clients is not None:
            return clients["primary"]
        client = _FakeClient(terminal_id, client_id=str(client_kwargs.get("client_id") or "c"))
        made_clients.append(client)
        return client

    service = TerminalService(
        root,
        registry=registry if registry is not None else TerminalRegistry(root),
        secret_store=store if store is not None else _FakeStore(root),
        spawn_launcher=spawn,
        client_factory=client_factory,
        identity_probe=probe,
        **kwargs,
    )
    service.test_clients = made_clients  # type: ignore[attr-defined]
    return service


def _converged_launcher_status(root: Path, terminal_id: str, pid: int, filetime: int) -> None:
    """写一份"引擎收尾已确证"的 launcher-status（与 launcher 契约同形）。"""
    directory = root / "launcher-status"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{terminal_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "terminal_id": terminal_id,
                "phase": "finished",
                "exit_code": 0,
                "reason": "exited",
                "launcher_identity": {
                    "pid": int(pid),
                    "process_created_at_filetime": str(int(filetime)),
                },
                "engine": {
                    "created": True,
                    "cleanup": {
                        "converged": True,
                        "attempts": 1,
                        "last_error_type": None,
                        "retained": [],
                        "in_flight": False,
                        "seconds": 0.1,
                        "outcome": "closed",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


# ══════════════════════════════════════════════════════════════════════════
# ① 导入无副作用
# ══════════════════════════════════════════════════════════════════════════


def test_import_has_no_side_effects_and_construction_creates_nothing(tmp_path):
    """导入不建目录/起线程；构造服务也不建数据目录。

    导入纯净性在**全新解释器**里验证（子进程）：同进程内 ``reload`` 会换掉模块里的
    类对象，污染其它用例的异常/类型身份比对。
    """
    import subprocess

    probe_root = tmp_path / "probe-root"
    script = (
        "import os, threading, sys\n"
        "before = threading.active_count()\n"
        "import packages.core.terminal.service as svc\n"
        "assert threading.active_count() == before, 'import created threads'\n"
        "assert not os.path.exists(os.environ['PAN_TERMINALS_DIR']), 'import created dirs'\n"
        "root = os.path.join(os.environ['PAN_TERMINALS_DIR'], 'nested', 'deeper')\n"
        "svc.TerminalService(root)\n"
        "assert not os.path.exists(root), 'construction created data dirs'\n"
        "print('clean')\n"
    )
    env = dict(os.environ)
    env["PAN_TERMINALS_DIR"] = str(probe_root)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "clean" in proc.stdout
    assert not probe_root.exists(), "导入/构造都不得创建数据根"


# ══════════════════════════════════════════════════════════════════════════
# ② 容量准入 / 并发 create / 重复 id
# ══════════════════════════════════════════════════════════════════════════


def test_capacity_admission_rejects_before_any_state_change(tmp_path):
    """超容量：抛 CapacityExceeded，**零记录、零进程**。"""
    service = _make_service(tmp_path, capacity=2)
    service.create()
    service.create()
    spawned: list[str] = []

    def spawn(terminal_id, *, secret_file, cwd, shell_argv, root):
        spawned.append(terminal_id)
        return _FakeProcess()

    service._spawn_launcher_impl = spawn
    with pytest.raises(CapacityExceeded):
        service.create()
    assert len(service.list()) == 2
    assert not spawned, "容量拒绝必须发生在派生之前"


def test_concurrent_create_never_exceeds_capacity(tmp_path):
    """并发 create：容量上限是硬约束（成功数 <= capacity，失败零副作用）。"""
    service = _make_service(tmp_path, capacity=3, heartbeat_interval=5.0)
    results: dict[str, str] = {}
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def worker(index: int) -> None:
        barrier.wait()
        try:
            view = service.create()
            with lock:
                results[f"t{index}"] = view["terminal_id"]
        except CapacityExceeded:
            with lock:
                results[f"t{index}"] = "rejected"
        except Exception as exc:  # noqa: BLE001
            with lock:
                results[f"t{index}"] = type(exc).__name__

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    created = [v for v in results.values() if v != "rejected"]
    assert len(created) <= 3, f"并发超出容量：{len(created)}"
    assert len(set(created)) == len(created), "terminal_id 必须唯一"
    assert len(service.list()) == len(created)
    for terminal_id in created:
        service.shutdown(budget=5.0)


def test_duplicate_terminal_id_does_not_overwrite_existing(tmp_path):
    """重复 id：不覆盖既有记录/秘密（换新 id，秘密保留）。"""
    service = _make_service(tmp_path, capacity=8, heartbeat_interval=5.0)
    first = service.create()
    fixed = service.create(terminal_id=first["terminal_id"])
    ids = {item["terminal_id"] for item in service.list()}
    assert first["terminal_id"] in ids
    assert fixed["terminal_id"] != first["terminal_id"]
    assert len(ids) == 2
    store = service._store()
    assert store.exists(first["terminal_id"]), "既有终端的秘密不得被覆盖/删除"
    service.shutdown(budget=5.0)


def test_create_publishes_running_only_after_gates(tmp_path):
    """四门确认前不发布 RUNNING；记录先于进程持久化。"""
    order: list[str] = []
    registry = TerminalRegistry(tmp_path / "terminals")
    original_create = registry.create
    original_update = registry.update

    def traced_create(record):
        order.append(f"record:{record.status.value}")
        return original_create(record)

    def traced_update(terminal_id, mutator):
        order.append("record:update")
        return original_update(terminal_id, mutator)

    registry.create = traced_create  # type: ignore[method-assign]
    registry.update = traced_update  # type: ignore[method-assign]
    service = _make_service(tmp_path, registry=registry, heartbeat_interval=5.0)
    view = service.create()
    assert order[0] == "record:starting", order
    assert view["status"] == RuntimeState.RUNNING.value
    # 记录里存的是 hello 自证身份（runner pid），不是 Popen.pid
    assert view["pid"] == 40001
    service.shutdown(budget=5.0)


def test_gates_not_confirmed_keeps_record_and_owner(tmp_path):
    """runner 未报 running：抛 StartupFailed，记录保留为可重试、秘密不删。"""
    clients = {
        "primary": _FakeClient("term_gates", client_id="c", describe_state="starting"),
    }
    service = _make_service(tmp_path, clients=clients, heartbeat_interval=5.0)
    with pytest.raises(StartupFailed):
        service.create()
    records = service.list()
    assert len(records) == 1
    assert records[0]["status"] in (
        RuntimeState.CLEANUP_FAILED.value,
        RuntimeState.STARTING.value,
    )
    assert records[0]["status"] not in (RuntimeState.EXITED.value, RuntimeState.LOST.value)
    store = service._store()
    assert store.deleted == [], "启动失败不得删除秘密"
    assert store.exists(records[0]["terminal_id"]), "秘密保留供重试"


# ══════════════════════════════════════════════════════════════════════════
# ③ 心跳：独立连接、不被慢业务拖死
# ══════════════════════════════════════════════════════════════════════════


def test_heartbeat_uses_its_own_connection_and_survives_slow_business(tmp_path):
    """慢 input 期间心跳继续（独立连接 + 稳定 client_id）。"""
    slow = _FakeClient("term_hb", client_id="pan-owner-term_hb", slow_seconds=0.6)
    service = _make_service(tmp_path, clients={"primary": slow}, heartbeat_interval=0.1)
    view = service.create()
    terminal_id = view["terminal_id"]
    holder = service.attach(terminal_id, "browser-1", role="control")
    assert holder.role == "control"
    beats_before = service.describe()["heartbeats"][terminal_id]["beats"]
    service.input(terminal_id, holder, b"slow-input")
    beats_after = service.describe()["heartbeats"][terminal_id]["beats"]
    assert beats_after > beats_before, "慢业务不得拖住独立心跳"
    hb = service.describe()["heartbeats"][terminal_id]
    assert hb["client_id"] == f"pan-owner-{terminal_id}", "client_id 必须稳定且可预测"
    assert hb["lost"] is False
    service.shutdown(budget=5.0)


def test_heartbeat_loss_is_reported_not_hidden(tmp_path):
    """心跳连续失败超过死期：如实标记 lost（不静默）。"""
    class _FailingClient(_FakeClient):
        def heartbeat(self, **kwargs):
            self._count("heartbeat")
            raise RuntimeError("injected-heartbeat-failure")

    service = _make_service(tmp_path, clients={"primary": _FailingClient("term_hl", client_id="c")},
                            heartbeat_interval=0.05, lease_grace=0.1)
    view = service.create()
    terminal_id = view["terminal_id"]
    hb = _wait_until(
        lambda: (service.describe()["heartbeats"][terminal_id]["lost"] or None), 5.0
    )
    assert hb is True, "心跳丢失必须如实上报"
    assert "injected" not in json.dumps(service.describe()), "诊断只带静态/类型信息"
    service.shutdown(budget=5.0)


# ══════════════════════════════════════════════════════════════════════════
# ④ 控制权：撤销后零新写 / observer 不可写 / 断开只撤销
# ══════════════════════════════════════════════════════════════════════════


def test_revoked_control_lease_writes_nothing(tmp_path):
    """撤销后零新写（同一临界区校验+调用）。"""
    client = _FakeClient("term_rev", client_id="c")
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    token = service.attach(terminal_id, "browser-1", role="control")
    assert service.input(terminal_id, token, b"before")["size"] == 6
    writes_before = client.calls.get("input", 0)

    service.release_attachment(token)
    with pytest.raises(StaleLeaseError):
        service.input(terminal_id, token, b"after")
    assert client.calls.get("input", 0) == writes_before, "撤销后不得产生新写"
    # 断开 attachment 不停止 runtime：读仍在
    assert service.read(terminal_id, 0)["size"] == 5
    service.shutdown(budget=5.0)


def test_observer_lease_cannot_input_or_resize(tmp_path):
    """observer 不可 input/resize（越权稳定为 NotControlLeaseError）。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    observer = service.attach(terminal_id, "browser-obs", role="observer")
    with pytest.raises(NotControlLeaseError):
        service.input(terminal_id, observer, b"nope")
    with pytest.raises(NotControlLeaseError):
        service.resize(terminal_id, observer, 30, 100)
    service.shutdown(budget=5.0)


def test_control_holder_changes_and_old_token_stale(tmp_path):
    """控制权转交后旧 token 失效，新 holder 生效（代际语义）。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    first = service.attach(terminal_id, "browser-1", role="control")
    second = service.attach(terminal_id, "browser-2", role="control")
    assert service.control_holder(terminal_id)["client_id"] == "browser-2"
    with pytest.raises(StaleLeaseError):
        service.input(terminal_id, first, b"stale")
    assert service.input(terminal_id, second, b"fresh")["size"] == 5
    service.shutdown(budget=5.0)


def test_input_resize_report_pty_and_engine_separately(tmp_path):
    """resize 分列 PTY / 引擎确认，且不宣称三方一致。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    token = service.attach(terminal_id, "browser-1", role="control")
    result = service.resize(terminal_id, token, 30, 100)
    assert result["pty"]["accepted"] is True
    assert result["engine"]["confirmed"] is True
    assert result["three_way_agreement"] is None, "不得宣称三方一致"
    service.shutdown(budget=5.0)


# ══════════════════════════════════════════════════════════════════════════
# ⑤ read gap / snapshot 降级：不补零、不跳 cursor、不升级
# ══════════════════════════════════════════════════════════════════════════


def test_read_gap_is_reported_without_zero_fill_or_cursor_jump(tmp_path):
    """gap：原样上报 + fresh-view 提示；不补零、不跳 cursor。"""
    client = _FakeClient(
        "term_gap",
        client_id="c",
        read_payload={"gap": [0, 4096], "next_cursor": 4096, "first_retained_seq": 4096},
    )
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    page = service.read(terminal_id, 0)
    assert page["gap"] == [0, 4096]
    assert page["fresh_view_required"] is True
    assert page["zero_fill"] is False
    assert page["next_cursor"] == 4096, "游标必须由协议给出，服务不得自行跳"
    assert page["data"] == b"hello"
    service.shutdown(budget=5.0)


def test_snapshot_keeps_f5_bool_or_null_and_never_upgrades(tmp_path):
    """F5 字段保持 bool-or-null；空 diagnostics.reasons 不升级为 full。"""
    client = _FakeClient(
        "term_snap",
        client_id="c",
        snapshot_payload={
            "fidelity": "partial",
            "recovery": "degraded",
            "cursors_valid": None,
            "reset_unconfirmed": True,
            "diagnostics": {"reasons": []},
            "note": "unverified-sequence",
        },
    )
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    snap = service.snapshot(terminal_id)
    assert snap["fidelity"] == "partial" and snap["recovery"] == "degraded"
    assert snap["cursors_valid"] is None
    assert snap["reset_unconfirmed"] is True
    assert snap["diagnostics"] == {"reasons": []}
    assert snap["fidelity"] != "full", "空 reasons 不得被当作 full"
    assert snap["auto_reset_applied"] is False
    assert snap["continuation_hint"] == "fresh-view-required"
    # 字符串真值不得被当成 bool
    client.snapshot_payload["cursors_valid"] = "true"
    snap2 = service.snapshot(terminal_id)
    assert snap2["cursors_valid"] is None
    service.shutdown(budget=5.0)


def test_snapshot_applied_evicted_gives_hint_without_reset(tmp_path):
    """applied cursor 被驱逐：显式提示，**不**自动 reset、不杀 PTY。"""
    client = _FakeClient(
        "term_evict",
        client_id="c",
        snapshot_payload={"cursor": 10},
        read_payload={"first_retained_seq": 4096},
    )
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    snap = service.snapshot(terminal_id)
    assert snap["applied_evicted"] is True
    assert snap["continuation_hint"] == "fresh-view-required"
    assert snap["auto_reset_applied"] is False
    assert "reset" not in client.calls, "不得调用引擎 reset_baseline"
    service.shutdown(budget=5.0)


# ══════════════════════════════════════════════════════════════════════════
# ⑥ close：三项证明、迟到结果、重试不重叠、秘密删除时机
# ══════════════════════════════════════════════════════════════════════════


def test_close_requires_all_three_proofs(tmp_path):
    """close：runner 确认 + 引擎收尾 + 经身份核验的进程退出，三者齐备才 exited。"""
    process = _FakeProcess()
    client = _FakeClient("term_close", client_id="c", close_status="exited")
    service = _make_service(tmp_path, clients={"primary": client}, process=process,
                            heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    record_pid = service.get(terminal_id)["pid"]
    record_ft = int(service.get(terminal_id)["process_created_at_filetime"])

    # ① 只有 runner 确认，缺引擎收尾与进程退出 -> 不标 exited
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    assert service.get(terminal_id)["status"] not in (
        RuntimeState.EXITED.value,
        RuntimeState.LOST.value,
    )
    assert service._store().deleted == [], "未证明不得删除秘密"

    # ② 引擎收尾已确证，但进程仍在 -> 仍不标 exited
    _converged_launcher_status(service.root, terminal_id, record_pid, record_ft)
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    assert service.get(terminal_id)["status"] not in (
        RuntimeState.EXITED.value,
        RuntimeState.LOST.value,
    )

    # ③ 三项齐备 -> exited + 删秘密（verified_exit=True，无 caller_responsible）
    process.exit(0)
    view = service.close(terminal_id)
    assert view["status"] == RuntimeState.EXITED.value
    deleted = service._store().deleted
    assert deleted and deleted[0][0] == terminal_id
    assert deleted[0][2] is True, "必须以 verified_exit 证明删除"
    assert deleted[0][3] is None, "不得用 caller-responsibility 绕过"


def test_close_uses_exit_mapping_and_records_closing_first(tmp_path):
    """CLOSING 映射既有合法枚举 EXITING（不新增枚举值、不标 exited）。"""
    client = _FakeClient("term_closing", client_id="c", close_status="closing")
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    record = service.get(terminal_id)
    assert record["status"] in (
        RuntimeState.EXITING.value,
        RuntimeState.CLEANUP_FAILED.value,
    )
    assert record["exit"]["reason"]


def test_close_late_result_is_consumed_without_second_stop(tmp_path):
    """迟到成功被消费：重试**不重复**发 stop（worker 复用、不叠加）。"""
    process = _FakeProcess()
    client = _FakeClient("term_late", client_id="c", close_status="exited")
    service = _make_service(tmp_path, clients={"primary": client}, process=process,
                            heartbeat_interval=5.0, stop_confirm=0.4)
    terminal_id = service.create()["terminal_id"]
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    stop_calls = client.calls.get("close", 0)
    # 补齐证明后重试：复用同一 worker 结果，stop 调用次数不增加
    record = service.get(terminal_id)
    _converged_launcher_status(
        service.root, terminal_id, record["pid"], int(record["process_created_at_filetime"])
    )
    process.exit(0)
    view = service.close(terminal_id)
    assert view["status"] == RuntimeState.EXITED.value
    assert client.calls.get("close", 0) == stop_calls, "重试不得叠加同一阻塞调用"


def test_secret_deleted_only_after_proof_even_when_record_write_fails(tmp_path):
    """记录写失败时：已证明终止**仍**删秘密（次序不倒置），且如实报告写失败。

    记录因写失败保持陈旧（``running``）—— 这是**如实的**：不得为了让视图好看而
    伪造 ``exited``。重启后 reconcile 会因"记录说存活但秘密已删"归入
    unattributable（安全且可恢复）。
    """
    process = _FakeProcess()
    client = _FakeClient("term_wf", client_id="c", close_status="exited")
    registry = TerminalRegistry(tmp_path / "terminals")
    service = _make_service(
        tmp_path, registry=registry, clients={"primary": client}, process=process,
        heartbeat_interval=5.0,
    )
    terminal_id = service.create()["terminal_id"]
    record = service.get(terminal_id)
    _converged_launcher_status(
        service.root, terminal_id, record["pid"], int(record["process_created_at_filetime"])
    )
    process.exit(0)

    original_update = registry.update
    calls = {"n": 0}

    def flaky_update(target_id, mutator):
        calls["n"] += 1
        # 第 1 次是 CLOSING 记录（必须能写），第 2 次是标记 exited —— 让它失败。
        if calls["n"] == 2:
            raise OSError("injected-record-write-failure")
        return original_update(target_id, mutator)

    registry.update = flaky_update  # type: ignore[method-assign]
    view = service.close(terminal_id)
    assert view["status"] == RuntimeState.EXITING.value, (
        "标记 exited 的写失败时，记录必须停在 CLOSING（不得伪造 exited）"
    )
    deleted = service._store().deleted
    assert deleted and deleted[0][0] == terminal_id, "已证明终止后仍应删秘密"
    assert deleted[0][2] is True, "必须以 verified_exit 证明删除"
    assert "exited-record-write-failed" in json.dumps(service.describe())
    assert "OSError" in json.dumps(service.describe())


def test_secret_delete_failure_is_reported_not_faked(tmp_path):
    """删秘密失败：记类型名，不假装成功。"""
    store = _FakeStore(tmp_path / "terminals", delete_error=OSError("injected-delete-failure"))
    process = _FakeProcess()
    client = _FakeClient("term_sd", client_id="c", close_status="exited")
    service = _make_service(tmp_path, store=store, clients={"primary": client},
                            process=process, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    record = service.get(terminal_id)
    _converged_launcher_status(
        service.root, terminal_id, record["pid"], int(record["process_created_at_filetime"])
    )
    process.exit(0)
    assert service.close(terminal_id)["status"] == RuntimeState.EXITED.value
    described = json.dumps(service.describe())
    assert "secret-delete-failed" in described and "OSError" in described


# ══════════════════════════════════════════════════════════════════════════
# ⑦ detach：拒绝零变化
# ══════════════════════════════════════════════════════════════════════════


def test_detach_refused_has_zero_state_change(tmp_path):
    """真实 ambient 拒绝：零状态变化（记录/心跳/秘密均不动）。"""
    client = _FakeClient("term_det", client_id="c", detach_status="detach-refused")
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    view = service.create()
    terminal_id = view["terminal_id"]
    before = service.get(terminal_id)
    beats_before = service.describe()["heartbeats"][terminal_id]["beats"]

    result = service.detach(terminal_id)
    assert result["detached"] is False
    assert result["status"] == "detach-refused"
    assert result["state_changed"] is False
    assert result["durability"] == {"capable": False, "ambient_job": True}

    after = service.get(terminal_id)
    assert after["status"] == before["status"] == RuntimeState.RUNNING.value
    assert after["detached"] is False and after["detached_at"] is None
    assert after["pid"] == before["pid"]
    assert service.describe()["heartbeats"][terminal_id]["beats"] >= beats_before
    assert service._store().deleted == [], "拒绝不得删秘密"
    assert service._store().exists(terminal_id)


def test_detach_injected_detached_marks_mechanism(tmp_path):
    """注入的已 detached 生命周期：可验证逻辑，但**明确机制是注入**。"""
    client = _FakeClient("term_det2", client_id="c", detach_status="detached")
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    result = service.detach(terminal_id)
    assert result["detached"] is True
    assert result["mechanism"] == "runner-reported"
    record = service.get(terminal_id)
    assert record["detached"] is True and record["owner"] == "detached"
    # detach 后保秘密（重连闭环靠它），且不再有心跳
    assert service._store().exists(terminal_id)
    assert service._store().deleted == []
    assert terminal_id not in service.describe()["heartbeats"]


# ══════════════════════════════════════════════════════════════════════════
# ⑧ reconcile：不冒充恢复、未知身份零触碰
# ══════════════════════════════════════════════════════════════════════════


def _forget_runtime_state(service: TerminalService) -> None:
    """丢弃内存态运行期对象（模拟"新 Pan 实例"：只剩持久记录可核对）。"""
    for state in list(service._states.values()):
        if state.heartbeat is not None:
            state.heartbeat.stop(timeout=2.0)
        if state.client is not None:
            state.client.release_connection()
    with service._global_lock:
        service._states.clear()


def test_reconcile_unknown_identity_zero_termination(tmp_path):
    """身份不可核验：零终止、保诊断、保留记录与秘密（跨进程视角）。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0,
                            probe=lambda _pid: _FakeProbe(ProcessStatus.UNKNOWN))
    terminal_id = service.create()["terminal_id"]
    _forget_runtime_state(service)
    result = service.reconcile()
    assert result["counts"]["unattributable"] == 1
    assert result["fresh_pid_absence_is_dead_evidence"] is False
    assert service.get(terminal_id)["status"] == RuntimeState.RUNNING.value
    assert service._store().exists(terminal_id), "不可核验不得删秘密"


def test_reconcile_fresh_pid_absence_is_not_dead_evidence(tmp_path):
    """PID 查不到（UNKNOWN）**不是**已死证据：不改终态、不删秘密。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0,
                            probe=lambda _pid: _FakeProbe(ProcessStatus.UNKNOWN, 999))
    terminal_id = service.create()["terminal_id"]
    _forget_runtime_state(service)
    buckets = service.reconcile()["buckets"]
    assert buckets["dead-confirmed"] == []
    assert buckets["unattributable"], "身份不可核验必须归入 unattributable"
    assert service.get(terminal_id)["status"] != RuntimeState.EXITED.value
    assert service._store().deleted == []


def test_reconcile_identity_mismatch_zero_touch(tmp_path):
    """FILETIME 不符：零触碰（不按 PID 单值动手）。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0,
                            probe=lambda _pid: _FakeProbe(ProcessStatus.ALIVE, 111111))
    terminal_id = service.create()["terminal_id"]
    _forget_runtime_state(service)
    result = service.reconcile()
    assert result["counts"]["unattributable"] == 1
    assert service.get(terminal_id)["status"] == RuntimeState.RUNNING.value
    assert service._store().deleted == []


def test_reconcile_never_creates_replacement_runner_or_revives_lease(tmp_path):
    """reconcile 不派生替代 runner、不复活旧 lease。"""
    spawned: list[str] = []
    service = _make_service(tmp_path, heartbeat_interval=5.0,
                            probe=lambda _pid: _FakeProbe(ProcessStatus.ALIVE, 40001))
    terminal_id = service.create()["terminal_id"]
    token = service.attach(terminal_id, "browser-1", role="control")
    service.release_attachment(token)

    def spawn(tid, *, secret_file, cwd, shell_argv, root):
        spawned.append(tid)
        return _FakeProcess()

    service._spawn_launcher_impl = spawn
    service.reconcile()
    assert not spawned, "reconcile 绝不派生替代进程"
    with pytest.raises(StaleLeaseError):
        service.input(terminal_id, token, b"zombie")
    service.shutdown(budget=5.0)


def test_reconcile_dead_confirmed_with_matching_identity(tmp_path):
    """我们派生的进程已退出（句柄证据）且 runner 身份精确匹配：承认 dead-confirmed。"""
    process = _FakeProcess()
    process.exit(0)
    probes: dict[int, Any] = {}

    def probe(pid: int):
        return probes.get(pid, _FakeProbe(ProcessStatus.ALIVE, 40001))

    service = _make_service(tmp_path, process=process, heartbeat_interval=5.0, probe=probe)
    terminal_id = service.create()["terminal_id"]
    record = service.get(terminal_id)
    probes[int(record["pid"])] = _FakeProbe(ProcessStatus.DEAD, int(record["process_created_at_filetime"]))
    result = service.reconcile()
    assert result["counts"]["dead-confirmed"] == 1
    assert service.get(terminal_id)["status"] == RuntimeState.EXITED.value
    assert service._store().deleted, "已证明终止才删秘密"


def test_reconcile_separates_unconfirmed_cleanup(tmp_path):
    """未收敛的清理单列（cleanup-unconfirmed），不冒充 exited/lost。"""
    client = _FakeClient("term_uc", client_id="c", close_status="cleanup-failed")
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    result = service.reconcile()
    assert result["counts"]["cleanup-unconfirmed"] == 1
    assert result["counts"]["dead-confirmed"] == 0
    assert service.get(terminal_id)["status"] not in (
        RuntimeState.EXITED.value,
        RuntimeState.LOST.value,
    )


# ══════════════════════════════════════════════════════════════════════════
# ⑨ shutdown：预算含锁等待、detached 保留、未收敛保留
# ══════════════════════════════════════════════════════════════════════════


def test_shutdown_budget_includes_lock_wait(tmp_path):
    """shutdown 总预算含锁等待：即使 close 卡住也有界返回。"""
    class _BlockingClient(_FakeClient):
        def close(self, *, reason: str = "explicit-close"):
            self._count("close")
            threading.Event().wait(30.0)  # 永久阻塞
            return {"status": "exited", "ok": True, "describe": {}}

    service = _make_service(tmp_path, clients={"primary": _BlockingClient("term_sd2", client_id="c")},
                            heartbeat_interval=5.0, stop_confirm=0.3, shutdown_budget=0.6)
    service.create()
    started = time.monotonic()
    result = service.shutdown()
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"shutdown 必须有界（实测 {elapsed:.2f}s）"
    assert result["budget_exhausted"] in (True, False)
    assert result["unconfirmed"], "未收敛必须如实列入 unconfirmed"
    assert result["secrets_retained"] is True


def test_shutdown_keeps_detached_runtime_and_secret(tmp_path):
    """detached 终端不随服务停止（保 PTY/PID/秘密/记录）。"""
    client = _FakeClient("term_kd", client_id="c", detach_status="detached")
    service = _make_service(tmp_path, clients={"primary": client}, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    assert service.detach(terminal_id)["detached"] is True
    result = service.shutdown()
    assert result["kept_detached"] == [terminal_id]
    record = service.get(terminal_id)
    assert record["detached"] is True and record["owner"] == "detached"
    assert record["status"] != RuntimeState.EXITED.value
    assert service._store().exists(terminal_id)
    assert service._store().deleted == []


def test_shutdown_reports_exited_with_proof(tmp_path):
    """正常 shutdown：三项证明齐备的终端如实收敛为 exited。"""
    process = _FakeProcess()
    client = _FakeClient("term_sd3", client_id="c", close_status="exited")
    service = _make_service(tmp_path, clients={"primary": client}, process=process,
                            heartbeat_interval=5.0, stop_confirm=2.0)
    view = service.create()
    terminal_id = view["terminal_id"]
    record = service.get(terminal_id)
    _converged_launcher_status(
        service.root, terminal_id, record["pid"], int(record["process_created_at_filetime"])
    )
    # 进程退出在 close 之前发生（模拟 runner 已收尾）
    process.exit(0)
    result = service.shutdown()
    assert result["exited"] == [terminal_id]
    assert result["unconfirmed"] == []
    assert service.get(terminal_id)["status"] == RuntimeState.EXITED.value


# ══════════════════════════════════════════════════════════════════════════
# ⑩ 公共面：脱敏、上下文、未知终端
# ══════════════════════════════════════════════════════════════════════════


def test_public_views_expose_no_pipe_or_secret_material(tmp_path):
    """公共返回值不含 token/pipe/秘密内容。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0)
    view = service.create()
    blob = json.dumps(view) + json.dumps(service.list()) + json.dumps(service.describe())
    assert "token" not in blob.lower()
    assert "pan-terminal-" not in blob, "不得暴露管道名"
    assert str(service._store().secret_path(view["terminal_id"])) not in blob
    assert view["authorization"] == "scope-is-metadata-not-permission"
    service.shutdown(budget=5.0)


def test_created_by_comes_from_context_not_session_id(tmp_path):
    """created_by 来自调用上下文，不从目标 session_id 推断。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0)
    view = service.create(
        session_id="sess-123",
        workspace_id="ws-9",
        context=ServiceContext(created_by="mcp", role="control"),
    )
    assert view["created_by"] == "mcp"
    assert view["scope"] == {"workspace_id": "ws-9", "session_id": "sess-123"}
    assert view["scope"] != view["created_by"]
    service.shutdown(budget=5.0)


def test_errors_are_static_reasons_and_type_names_only(tmp_path):
    """错误只带静态 reason + 异常类型名（无自由文本/路径/秘密）。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0)
    # 记录不存在 -> registry 的 UnknownTerminalError 原样冒泡（调用方需区分）
    with pytest.raises(UnknownTerminalError):
        service.get("term_missing")
    with pytest.raises(UnknownTerminalError):
        service.read("term_missing", 0)
    # 记录存在但本实例未持有 -> 明确"未连接"（不猜、不泄漏内部细节）
    terminal_id = service.create()["terminal_id"]
    _forget_runtime_state(service)
    with pytest.raises(TerminalNotAttached) as info:
        service.read(terminal_id, 0)
    assert info.value.reason == "not-attached"
    assert info.value.error_type is None
    assert "Traceback" not in str(info.value)


def test_filetime_exposed_as_decimal_string(tmp_path):
    """raw64 FILETIME 以十进制字符串对外（避免 JS 精度丢失）。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0)
    view = service.create()
    assert isinstance(view["process_created_at_filetime"], str)
    assert view["process_created_at_filetime"].isdigit()
    service.shutdown(budget=5.0)


def test_default_shell_is_cmd_q_d_and_size_defaults(tmp_path):
    """默认 cmd.exe /q /d、24x80（不静默改默认）。"""
    captured: dict[str, Any] = {}

    def spawn(terminal_id, *, secret_file, cwd, shell_argv, root):
        captured["shell_argv"] = shell_argv
        return _FakeProcess()

    service = _make_service(tmp_path, heartbeat_interval=5.0)
    service._spawn_launcher_impl = spawn
    view = service.create()
    assert captured["shell_argv"] is None, "缺省不改 shell（runner 用 cmd.exe /q /d）"
    assert (view["rows"], view["cols"]) == (24, 80)
    service.shutdown(budget=5.0)


def test_launcher_argv_contains_no_token_and_carries_cwd(tmp_path):
    """派生 argv 无 token，带可选 cwd/shell；缺省不带 cwd（保持默认行为）。"""
    service = _make_service(tmp_path, heartbeat_interval=5.0)
    secret = service.root / "secrets" / "term_argv.secret"
    argv = service._launcher_argv(
        "term_argv", secret, rows=24, cols=80, cwd="C:\\work", shell_argv=("pwsh.exe", "-NoLogo")
    )
    joined = " ".join(argv)
    assert "token" not in joined.lower()
    assert "--terminal-id" in argv and "--secret-file" in argv
    assert "packages.core.terminal.launcher" in joined
    assert argv[argv.index("--cwd") + 1] == "C:\\work"
    assert json.dumps(["pwsh.exe", "-NoLogo"]) in argv

    default_argv = service._launcher_argv("term_argv", secret, rows=24, cols=80)
    assert "--cwd" not in default_argv, "缺省不得改cwd（launcher/runner 保持默认）"
    assert "--shell-argv" not in default_argv


# ══════════════════════════════════════════════════════════════════════════
# ⑪ 真实 Windows 隔离组合（生产 launcher + 真 ConPTY/DPAPI/管道/引擎）
# ══════════════════════════════════════════════════════════════════════════


def _read_until(
    service: TerminalService,
    terminal_id: str,
    needle: bytes | str,
    *,
    timeout: float = 20.0,
    ignore_case: bool = False,
) -> bool:
    """按游标增量读取直到命中 ``needle``（真实 PTY 输出；不依赖单次 read 命中）。"""
    if isinstance(needle, str):
        needle = needle.encode("utf-8", errors="replace")
    cursor = 0
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        try:
            page = service.read(terminal_id, cursor)
        except Exception:  # noqa: BLE001 - 启动窗口内的读失败重试
            time.sleep(0.1)
            continue
        data = page.get("data") or b""
        haystack = data.lower() if ignore_case else data
        target = needle.lower() if ignore_case else needle
        if target in haystack:
            return True
        next_cursor = int(page.get("next_cursor") or cursor)
        # gap 后按协议给的游标继续（服务不补零、不跳；这里也不自行推进语义）
        cursor = next_cursor if next_cursor > cursor else cursor + max(len(data), 1)
        time.sleep(0.1)
    return False


def _real_service(tmp_path: Path, **kwargs: Any) -> TerminalService:
    """真实生产路径的服务（registry/secret/launcher/IPC 全部真实）。"""
    root = tmp_path / "service-terminals"
    return TerminalService(root, **kwargs)


def _cleanup_terminal(service: TerminalService, terminal_id: str) -> dict[str, Any]:
    """只对**自有**资源做同 handle 核验清理（raw FILETIME + Wait）。"""
    trace: dict[str, Any] = {}
    try:
        record = service.registry.get(terminal_id)
    except Exception:  # noqa: BLE001
        return {"record": "absent"}
    pid, filetime = record.pid, record.process_created_at_filetime
    if pid and not _process_dead(int(pid), filetime):
        trace["terminate"] = win_pipe.terminate_verified_process(int(pid), filetime)
    return trace


def _process_dead(pid: int, filetime: int | None = None) -> bool:
    probe = win_pipe.probe_process(int(pid))
    if probe.status is ProcessStatus.DEAD:
        return True
    if probe.status is ProcessStatus.ALIVE:
        if filetime is not None and probe.identity is not None:
            return int(probe.identity.created_at_filetime or -1) != int(filetime)
        return False
    return False


@requires_sidecar
def test_real_service_create_cwd_chinese_input_read_snapshot_and_close(tmp_path):
    """真实：默认创建/真实 cwd/中文输入 + read/snapshot + 显式 close 整树收尾。"""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    service = _real_service(tmp_path, log_stderr=False)
    terminal_id = None
    try:
        view = service.create(rows=24, cols=80, cwd=str(workdir))
        terminal_id = view["terminal_id"]
        assert view["status"] == RuntimeState.RUNNING.value
        assert view["pid"], "记录应带 hello 自证的 runner pid"

        # 真实 cwd：终端的初始目录必须就是我们指定的临时目录（cmd 提示符回显 %CD%）
        token = service.attach(terminal_id, "browser-1", role="control")
        expected_cd = str(workdir)
        found_cd = _wait_until(
            lambda: _read_until(
                service, terminal_id, expected_cd, timeout=25.0, ignore_case=True
            )
            or None,
            30.0,
        )
        assert found_cd, f"终端初始目录应为 {expected_cd}（真实 cwd 未生效）"

        marker = f"中文标记-{os.urandom(3).hex()}"
        service.input(terminal_id, token, f"echo {marker}\r".encode("utf-8"))
        seen = _wait_until(
            lambda: _read_until(service, terminal_id, marker.encode("utf-8"), timeout=20.0) or None,
            30.0,
        )
        assert seen, "中文输出应可经 read 取回"

        # 协议 A 快照：字段齐全且不升级
        snap = service.snapshot(terminal_id, timeout_ms=8000)
        assert snap["status"] in ("running", "degraded", "starting")
        assert snap["serialized_screen"] is not None and "cursor" in snap
        for field in ("cursors_valid", "reset_unconfirmed"):
            assert snap[field] is None or isinstance(snap[field], bool)
        assert snap["fidelity"] in ("unavailable", "partial", "full")

        # 显式 close：三项证明齐备才 exited
        record = service.get(terminal_id)
        _wait_until(lambda: _converged_if_launcher_done(service, terminal_id), 60.0)
        result = _wait_until(
            lambda: _try_close(service, terminal_id) or None, 60.0
        )
        assert result is not None, "close 应在预算内返回确定结果"
        assert service.get(terminal_id)["status"] == RuntimeState.EXITED.value, result
        assert service._store().exists(terminal_id) is False, "已证明终止后应删秘密"
    finally:
        if terminal_id:
            _cleanup_terminal(service, terminal_id)


def _converged_if_launcher_done(service: TerminalService, terminal_id: str) -> bool:
    path = service.root / "launcher-status" / f"{terminal_id}.json"
    return path.is_file()


def _try_close(service: TerminalService, terminal_id: str) -> str | None:
    try:
        service.close(terminal_id)
        return "exited"
    except CleanupUnconfirmed:
        return None


@requires_sidecar
def test_real_service_independent_heartbeat_and_same_pid_after_disconnect(tmp_path):
    """真实：独立心跳在慢业务期间存活；断连后 runner 同 PID 继续运行。"""
    service = _real_service(tmp_path, log_stderr=False, heartbeat_interval=0.35)
    terminal_id = None
    try:
        terminal_id = service.create()["terminal_id"]
        token = service.attach(terminal_id, "browser-1", role="control")
        before = service.describe()["heartbeats"][terminal_id]["beats"]
        # 慢业务（大量输入）不应拖住心跳
        for index in range(6):
            service.input(terminal_id, token, f"echo line-{index}\r".encode("utf-8"))
        after = service.describe()["heartbeats"][terminal_id]["beats"]
        assert after > before, "独立心跳必须继续推进"

        pid_before = service.get(terminal_id)["pid"]
        # 断开业务连接（模拟浏览器断连）：只释放连接，不停 runtime
        service._release_for(terminal_id)
        time.sleep(0.5)
        probe = win_pipe.probe_process(int(pid_before))
        assert probe.status is ProcessStatus.ALIVE, "断连不得杀死 runner"
        assert int(probe.identity.created_at_filetime) == int(
            service.get(terminal_id)["process_created_at_filetime"]
        )
        # 秘密仍在（重连闭环）
        assert service._store().exists(terminal_id)
    finally:
        if terminal_id:
            service.shutdown(budget=20.0)
            _cleanup_terminal(service, terminal_id)


@requires_sidecar
def test_real_service_detach_refused_zero_state_change(tmp_path):
    """真实 ambient 约束：detach 拒绝且零状态变化（不做环境逃脱）。"""
    service = _real_service(tmp_path, log_stderr=False)
    terminal_id = None
    try:
        terminal_id = service.create()["terminal_id"]
        before = service.get(terminal_id)
        result = service.detach(terminal_id)
        assert result["detached"] is False
        assert result["status"] in ("detach-refused", "rejected")
        assert result["state_changed"] is False
        after = service.get(terminal_id)
        assert after["status"] == before["status"]
        assert after["pid"] == before["pid"]
        assert after["detached"] is False
        assert service._store().exists(terminal_id)
        assert win_pipe.probe_process(int(before["pid"])).status is ProcessStatus.ALIVE
    finally:
        if terminal_id:
            service.shutdown(budget=20.0)
            _cleanup_terminal(service, terminal_id)


@requires_sidecar
def test_real_service_new_instance_reconcile_after_host_crash(tmp_path):
    """真实：服务宿主崩溃后（心跳停）lease 自停；新实例 reconcile 收敛。"""
    first = _real_service(tmp_path, log_stderr=False)
    terminal_id = first.create()["terminal_id"]
    record = first.get(terminal_id)
    # 模拟宿主崩溃：不再心跳、释放连接（**不**停进程、不删秘密）
    for state in list(first._states.values()):
        state.heartbeat.stop(timeout=2.0) if state.heartbeat else None
        state.client.release_connection() if state.client else None
    try:
        dead = _wait_until(lambda: _process_dead(int(record["pid"]), int(record["process_created_at_filetime"])) or None, 30.0)
        assert dead, "心跳停止后 runner 应按 lease 自停"

        # 新实例：reconcile 必须把已死身份与未收敛清理分列，且不冒充恢复
        second = _real_service(tmp_path, log_stderr=False)
        result = second.reconcile()
        assert result["fresh_pid_absence_is_dead_evidence"] is False
        assert result["counts"]["dead-confirmed"] + result["counts"]["unattributable"] >= 1
        status_after = second.get(terminal_id)["status"]
        assert status_after in (
            RuntimeState.EXITED.value,
            RuntimeState.CLEANUP_FAILED.value,
            RuntimeState.STARTING.value,
        )
    finally:
        _cleanup_terminal(first, terminal_id)

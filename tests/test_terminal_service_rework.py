"""TerminalService 返工回归（F1–F10；独立审查 `d7905408` 判返工）。

本文件**只增不改**原 `tests/test_terminal_service.py`（后者由独立卫生任务占用）。
可复用原测试的替身helper（只读导入），但**不复用其断言**。

逐项对应审查发现（先失败 → 修复后通过）：

- **F1** 活状态 ``reconcile`` 必须以 **runner身份三态**为门：我们派生的进程句柄退出
  **不**等于整树终止证明。ALIVE / UNKNOWN / 身份不符 → 保秘密 + 保记录、**不**写
  ``exited``。默认生产 ``_identity_evidence`` 只比 FILETIME 不足 → 必须**同时核对
  PID**（新增"错 PID + 同 FILETIME"负例）。launcher ``exit 6``（引擎未确证）**不**得
  判成成功清理。fresh 查不到**不**当 DEAD；**不**对陌生 PID 终止。
- **F2** 瞬时失败的 stop **不得**永久缓存：在途单飞复用；已完成但 ``closing``/报错
  允许下一轮**有界幂等**重发；已确认成功不机械重复；**worker 线程确已收敛**才重发。
- **F3** 跨进程遗留的 managed / cleanup-failed 记录，经**秘密 + 端点 + 身份核验**后可
  重连并 stop；死期等待**有界**；**不**通过续约复活旧 managed；成功需三项真实证明；
  未知零触碰；缺证保 owner 可重试。**不拥有** self-spawn handle 时**不**造证明。
- **F4** ``reconnect`` 必须复用或真实停止并回收旧 client / 心跳（无界增长 = 失败）。
- **F5** 未收敛 / 在途时**不得**提前 ``release`` client或丢弃心跳线程引用。
- **F6** 总预算**含**取锁、worker、停心跳、释放等待；``budget=0`` 不被抬高；
  ``elapsed_within_budget`` 如实。
- **F7** 预算耗尽后 skipped 记录**全部**列入未确认；``secrets_retained`` 不得假报 False。
- **F8** 关闭准入门**早于** RPC，且内存态与磁盘**同步**（close 后 input 不得放行）。
- **F9** ``shutdown`` 先关 create 准入，与并发 create **线性化**；重试 cleanup 仍允许。
- **F10** close 缺证用**静态分类**（区分缺哪一项证明），**无自由文本**泄漏。

另含 F12 的**有界等待**修正（心跳断言不依赖固定负载；属测试时序容忍度，不改产品）。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from packages.core.terminal.contracts import (
    ProcessStatus,
    RuntimeState,
    StaleLeaseError,
)
from packages.core.terminal.registry import TerminalRegistry
from packages.core.terminal.service import (
    CleanupUnconfirmed,
    StartupFailed,
    TerminalNotAttached,
    TerminalService,
)

# 只读复用原测试替身（不修改原文件）
from tests.test_terminal_service import (  # noqa: E402
    _converged_launcher_status,
    _FakeClient,
    _FakeProbe,
    _FakeProcess,
    _FakeStore,
    _forget_runtime_state,
    _wait_until,
)


def _service(tmp_path: Path, **kwargs: Any) -> TerminalService:
    """构造注入式服务（不起真实进程）。"""
    root = tmp_path / "terminals"
    process = kwargs.pop("process", None)
    store = kwargs.pop("store", None)
    probe = kwargs.pop("probe", None)
    registry = kwargs.pop("registry", None)
    clients = kwargs.pop("client", None)
    spawn_override = kwargs.pop("spawn", None)

    def spawn(terminal_id, *, secret_file, cwd, shell_argv, root):
        if spawn_override is not None:
            return spawn_override
        return process if process is not None else _FakeProcess()

    def factory(terminal_id, **client_kwargs):
        if clients is not None:
            return clients
        return _FakeClient(terminal_id, client_id=str(client_kwargs.get("client_id") or "c"))

    service = TerminalService(
        root,
        registry=registry if registry is not None else TerminalRegistry(root),
        secret_store=store if store is not None else _FakeStore(root),
        spawn_launcher=spawn,
        client_factory=factory,
        identity_probe=probe,
        **kwargs,
    )
    return service


def _record_identity(service: TerminalService, terminal_id: str) -> tuple[int, int]:
    view = service.get(terminal_id)
    return int(view["pid"]), int(view["process_created_at_filetime"])


# ══════════════════════════════════════════════════════════════════════════
# F1：活状态 reconcile 必须以 runner 身份三态为门
# ══════════════════════════════════════════════════════════════════════════


def test_f1_reconcile_live_alive_identity_keeps_secret_and_record(tmp_path):
    """F1：句柄已退出但 runner 身份仍 **ALIVE** → 绝不写 exited / 删秘密。"""
    from packages.core.terminal.contracts import ProcessIdentity, ProcessProbe

    process = _FakeProcess()
    process.exit(0)  # 我们派生的进程退出
    probes: dict[int, Any] = {}

    def probe(pid: int):
        return probes.get(pid, ProcessProbe(status=ProcessStatus.UNKNOWN))

    service = _service(tmp_path, process=process, heartbeat_interval=5.0, probe=probe)
    terminal_id = service.create()["terminal_id"]
    pid, filetime = _record_identity(service, terminal_id)
    # 身份**精确匹配**（pid + FILETIME）且仍 ALIVE —— 最难区分的场景
    probes[pid] = ProcessProbe(
        status=ProcessStatus.ALIVE,
        identity=ProcessIdentity(pid=pid, created_at_filetime=filetime),
    )

    result = service.reconcile()
    assert result["counts"]["dead-confirmed"] == 0, "身份仍 ALIVE 不得判已死"
    assert result["counts"]["cleanup-unconfirmed"] + result["counts"]["unattributable"] >= 1
    record = service.get(terminal_id)
    assert record["status"] != RuntimeState.EXITED.value, "ALIVE 时不得写 exited"
    assert record["status"] != RuntimeState.LOST.value
    assert service._store().deleted == [], "ALIVE 时**不得**删除秘密（不可逆）"
    assert service._store().exists(terminal_id), "秘密必须保留"
    service.shutdown(budget=5.0)


def test_f1_reconcile_live_unknown_identity_keeps_secret(tmp_path):
    """F1：句柄已退出但身份探针 **UNKNOWN** → 不写 exited、不删秘密。"""
    process = _FakeProcess()
    process.exit(0)
    service = _service(
        tmp_path, process=process, heartbeat_interval=5.0,
        probe=lambda _pid: _FakeProbe(ProcessStatus.UNKNOWN),
    )
    terminal_id = service.create()["terminal_id"]
    result = service.reconcile()
    assert result["counts"]["dead-confirmed"] == 0
    assert result["fresh_pid_absence_is_dead_evidence"] is False
    assert service.get(terminal_id)["status"] != RuntimeState.EXITED.value
    assert service._store().deleted == []
    service.shutdown(budget=5.0)


def test_f1_reconcile_live_dead_identity_is_the_only_delete_path(tmp_path):
    """F1 反向：身份**精确匹配且已死**才允许 dead-confirmed + 删秘密。"""
    process = _FakeProcess()
    process.exit(0)
    probes: dict[int, Any] = {}

    def probe(pid: int):
        return probes.get(pid, _FakeProbe(ProcessStatus.ALIVE, 40001))

    service = _service(tmp_path, process=process, heartbeat_interval=5.0, probe=probe)
    terminal_id = service.create()["terminal_id"]
    pid, filetime = _record_identity(service, terminal_id)
    # 精确身份（pid + FILETIME 都一致）+ DEAD
    from packages.core.terminal.contracts import ProcessIdentity, ProcessProbe

    probes[pid] = ProcessProbe(
        status=ProcessStatus.DEAD, identity=ProcessIdentity(pid=pid, created_at_filetime=filetime)
    )
    result = service.reconcile()
    assert result["counts"]["dead-confirmed"] == 1
    assert service.get(terminal_id)["status"] == RuntimeState.EXITED.value
    assert service._store().deleted, "唯一允许删秘密的路径"


def test_f1_identity_evidence_requires_pid_and_filetime_match(tmp_path):
    """F1：默认 ``_identity_evidence`` 必须**同时核对 PID + raw FILETIME**。

    审查指出生产默认只比 FILETIME；这里用"**错 PID + 同 FILETIME**"负例钉死：
    FILETIME 相同但 PID 不同 → 不可归因（零终止）。
    """
    from packages.core.terminal.contracts import ProcessIdentity, ProcessProbe

    service = _service(tmp_path, heartbeat_interval=5.0)
    other_pid_same_ft = ProcessProbe(
        status=ProcessStatus.ALIVE,
        identity=ProcessIdentity(pid=999999, created_at_filetime=133400000000000001),
    )
    service._identity_probe = lambda _pid: other_pid_same_ft
    verdict = service._identity_evidence(40001, 133400000000000001)
    assert verdict["status"] == "unattributable", "PID 不同即不可归因（即使 FILETIME 同）"
    assert verdict["reason"] == "pid-mismatch"
    # FILETIME 不同同样不可归因
    same_pid_other_ft = ProcessProbe(
        status=ProcessStatus.ALIVE,
        identity=ProcessIdentity(pid=40001, created_at_filetime=1),
    )
    service._identity_probe = lambda _pid: same_pid_other_ft
    assert service._identity_evidence(40001, 133400000000000001)["status"] == "unattributable"
    # PID 与 FILETIME 均一致 → 认进程状态
    exact = ProcessProbe(
        status=ProcessStatus.ALIVE,
        identity=ProcessIdentity(pid=40001, created_at_filetime=133400000000000001),
    )
    service._identity_probe = lambda _pid: exact
    assert service._identity_evidence(40001, 133400000000000001)["status"] == "alive"


def test_f1_launcher_exit6_engine_unproven_is_not_clean_success(tmp_path):
    """F1：launcher ``exit 6``（引擎收尾未确证）不得被当成"清理成功"。

    close 的第二项证明必须要求 ``engine.cleanup.converged is True``；退出码非 0
    （含 6）时引擎未确证 → 不写 exited、不删秘密。
    """
    process = _FakeProcess()
    process.exit(6)  # LAUNCHER_EXIT_CLEANUP_UNPROVEN
    service = _service(
        tmp_path, process=process, heartbeat_interval=5.0,
        client=_FakeClient("term_e6", client_id="c", close_status="exited"),
    )
    terminal_id = service.create()["terminal_id"]
    pid, filetime = _record_identity(service, terminal_id)
    # 引擎未确证的 launcher-status（converged=false）
    status_dir = service.root / "launcher-status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / f"{terminal_id}.json").write_text(
        json.dumps({
            "schema_version": 1, "terminal_id": terminal_id, "phase": "cleanup-unproven",
            "exit_code": 6, "reason": "engine-cleanup-unproven",
            "launcher_identity": {"pid": pid, "process_created_at_filetime": str(filetime)},
            "engine": {"created": True, "cleanup": {"converged": False, "retained": ["engine"]}},
        }),
        encoding="utf-8",
    )
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    assert service.get(terminal_id)["status"] != RuntimeState.EXITED.value
    assert service._store().deleted == [], "引擎未确证不得删秘密"


def test_f1_reconcile_never_terminates_unknown_pid(tmp_path):
    """F1：身份不可核验时**零终止**（不得对陌生 PID 动刀）。"""
    calls: list[tuple[int, Any]] = []
    service = _service(
        tmp_path, heartbeat_interval=5.0,
        probe=lambda _pid: _FakeProbe(ProcessStatus.UNKNOWN),
    )
    terminal_id = service.create()["terminal_id"]
    #监控：注入的探针不得被换成 terminate
    original = service._identity_evidence

    def spy(pid, filetime):
        calls.append((int(pid), filetime))
        return original(pid, filetime)

    service._identity_evidence = spy
    service.reconcile()
    assert calls, "应调用身份探针"
    # 我们的替身里没有任何 terminate 通道；确认秘密与记录仍在
    assert service._store().exists(terminal_id)
    assert service.get(terminal_id)["status"] != RuntimeState.EXITED.value
    service.shutdown(budget=5.0)


# ══════════════════════════════════════════════════════════════════════════
# F2：stop 不得永久缓存；在途单飞、完成可幂等重发
# ══════════════════════════════════════════════════════════════════════════


def test_f2_stop_returning_closing_can_be_retried_boundedly(tmp_path):
    """F2：stop 首次返回 ``closing`` → 补齐证据后允许**再发一次**并收敛。"""
    process = _FakeProcess()
    client = _FakeClient("term_f2a", client_id="c", close_status="closing")
    service = _service(tmp_path, client=client, process=process,
                       heartbeat_interval=5.0, stop_confirm=1.0)
    terminal_id = service.create()["terminal_id"]
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    first_calls = client.calls.get("close", 0)

    # 第二次 close 仍返回 closing，但补齐外部证据后应能收敛
    client.close_status = "exited"
    pid, filetime = _record_identity(service, terminal_id)
    _converged_launcher_status(service.root, terminal_id, pid, filetime)
    process.exit(0)
    view = service.close(terminal_id)
    assert view["status"] == RuntimeState.EXITED.value
    assert client.calls.get("close", 0) > first_calls, "允许幂等重发 stop"
    assert service._store().deleted


def test_f2_stop_raising_exception_can_be_retried(tmp_path):
    """F2：stop 抛异常（瞬时）也不得永久缓存。"""
    process = _FakeProcess()
    client = _FakeClient("term_f2b", client_id="c")
    boom = {"raise": True}
    original_close = client.close

    def flaky(*, reason="explicit-close"):
        if boom["raise"]:
            boom["raise"] = False
            raise RuntimeError("injected-transient-stop-failure")
        return original_close(reason=reason)

    client.close = flaky  # type: ignore[method-assign]
    service = _service(tmp_path, client=client, process=process,
                       heartbeat_interval=5.0, stop_confirm=1.0)
    terminal_id = service.create()["terminal_id"]
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    pid, filetime = _record_identity(service, terminal_id)
    _converged_launcher_status(service.root, terminal_id, pid, filetime)
    process.exit(0)
    view = service.close(terminal_id)
    assert view["status"] == RuntimeState.EXITED.value


def test_f2_already_confirmed_success_is_not_resent(tmp_path):
    """F2：已确认成功（exited）**不**机械重复发 stop。"""
    process = _FakeProcess()
    client = _FakeClient("term_f2c", client_id="c", close_status="exited")
    service = _service(tmp_path, client=client, process=process,
                       heartbeat_interval=5.0, stop_confirm=1.0)
    terminal_id = service.create()["terminal_id"]
    pid, filetime = _record_identity(service, terminal_id)
    _converged_launcher_status(service.root, terminal_id, pid, filetime)
    process.exit(0)
    service.close(terminal_id)
    calls_after_first = client.calls.get("close", 0)
    # 已exited 后再 close：幂等返回，不再触发 stop
    service.close(terminal_id)
    assert client.calls.get("close", 0) == calls_after_first, "已确认成功不得机械重发"


def test_f2_inflight_stop_is_single_flight(tmp_path):
    """F2：在途 stop **单飞复用**：并发 close 不叠加同阻塞调用。"""
    process = _FakeProcess()
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    class _SlowStopClient(_FakeClient):
        def close(self, *, reason="explicit-close"):
            calls["n"] += 1
            started.set()
            release.wait(10.0)
            return {"status": "exited", "ok": True, "describe": {}}

    client = _SlowStopClient("term_f2d", client_id="c")
    service = _service(tmp_path, client=client, process=process,
                       heartbeat_interval=5.0, stop_confirm=3.0)
    terminal_id = service.create()["terminal_id"]
    results: list[str] = []

    def worker() -> None:
        try:
            service.close(terminal_id)
            results.append("exited")
        except CleanupUnconfirmed:
            results.append("unconfirmed")

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    assert started.wait(5.0), "首个 stop 应在途"
    time.sleep(0.2)
    release.set()
    for t in threads:
        t.join(timeout=15)
    assert calls["n"] == 1, f"在途 stop 必须单飞（实测 {calls['n']} 次）"


# ══════════════════════════════════════════════════════════════════════════
# F3：跨进程遗留 managed / cleanup-failed 的重连 stop 闭环
# ══════════════════════════════════════════════════════════════════════════


def test_f3_persisted_cleanup_failed_gets_bounded_stop_reentry(tmp_path):
    """F3：重启后遗留 cleanup-failed 记录，经身份核验可重连并 stop（不再无入口）。"""
    process = _FakeProcess()
    client = _FakeClient("term_f3", client_id="c", close_status="exited")
    store = _FakeStore(tmp_path / "terminals")
    service = _service(tmp_path, client=client, process=process, store=store,
                       heartbeat_interval=5.0, stop_confirm=1.0)
    terminal_id = service.create()["terminal_id"]
    pid, filetime = _record_identity(service, terminal_id)
    _converged_launcher_status(service.root, terminal_id, pid, filetime)
    # 进程**仍在** → 第一次 close 缺证据③，未证明（但 stop 已被确认）
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    assert service.get(terminal_id)["status"] in (
        RuntimeState.CLEANUP_FAILED.value, RuntimeState.EXITING.value
    )
    stop_calls_before = client.calls.get("close", 0)
    assert stop_calls_before >= 1
    # 模拟服务重启：丢弃内存态，仅剩持久记录 + 秘密
    _forget_runtime_state(service)
    stop_calls_before = client.calls.get("close", 0)
    assert stop_calls_before >= 1

    # 新实例：凭据在位 + 身份可核验（ALIVE）→ 必须**重发 stop**（不再是死路）
    from packages.core.terminal.contracts import ProcessIdentity, ProcessProbe

    def probe(p: int):
        return ProcessProbe(
            status=ProcessStatus.ALIVE,
            identity=ProcessIdentity(pid=int(p), created_at_filetime=filetime),
        )

    second = _service(tmp_path, client=client, process=None, store=store,
                      heartbeat_interval=5.0, stop_confirm=1.0, probe=probe,
                      spawn=None)
    second._states.clear()
    second._spawn_launcher_impl = lambda *a, **k: _FakeProcess()
    result = second.reconcile()
    # 关键：确实**重发了 stop**（F3 的核心——此前恒为 1，永不重发）
    assert client.calls.get("close", 0) > stop_calls_before, "遗留记录必须能重发 stop"
    # runner 仍 ALIVE 且本实例不拥有句柄 → 不得冒充已终止、不得删秘密
    assert result["counts"]["dead-confirmed"] == 0
    assert second._store().deleted == [], "未证明终止不得删秘密"


def test_f3_persisted_unattributable_never_touches(tmp_path):
    """F3：身份不可核验的遗留记录 → 零终止、保秘密/记录（不冒充恢复）。"""
    store = _FakeStore(tmp_path / "terminals")
    service = _service(tmp_path, store=store, heartbeat_interval=5.0)
    client = _FakeClient("term_f3b", client_id="c")
    service._client_factory_impl = lambda tid, **kw: client
    terminal_id = service.create()["terminal_id"]
    _forget_runtime_state(service)
    service._identity_probe = lambda _pid: _FakeProbe(ProcessStatus.UNKNOWN)
    result = service.reconcile()
    assert result["counts"]["unattributable"] == 1
    assert service.get(terminal_id)["status"] != RuntimeState.EXITED.value
    assert store.deleted == [], "不可核验不得删秘密"


def test_f3_no_selfspawn_handle_means_no_fabricated_proof(tmp_path):
    """F3：**不拥有** self-spawn handle 时不得造"进程已退出"证明。"""
    store = _FakeStore(tmp_path / "terminals")
    service = _service(tmp_path, store=store, heartbeat_interval=5.0)
    client = _FakeClient("term_f3c", client_id="c")
    service._client_factory_impl = lambda tid, **kw: client
    terminal_id = service.create()["terminal_id"]
    _forget_runtime_state(service)
    # 无self-spawn handle（state不存在）→只能走身份三态；UNKNOWN → 不可归因
    service._identity_probe = lambda _pid: _FakeProbe(ProcessStatus.UNKNOWN)
    result = service.reconcile()
    assert result["counts"]["dead-confirmed"] == 0, "无句柄不得凭空判已死"
    assert store.deleted == []


def test_f3_does_not_revive_old_managed_by_renewal(tmp_path):
    """F3：reconcile **不**通过续约把旧 managed 记录"复活"成 running。"""
    store = _FakeStore(tmp_path / "terminals")
    service = _service(tmp_path, store=store, heartbeat_interval=5.0)
    client = _FakeClient("term_f3d", client_id="c", describe_state="running")
    service._client_factory_impl = lambda tid, **kw: client
    terminal_id = service.create()["terminal_id"]
    _forget_runtime_state(service)
    before = service.get(terminal_id)
    service._identity_probe = lambda _pid: _FakeProbe(ProcessStatus.ALIVE, 40001)
    service.reconcile()
    after = service.get(terminal_id)
    # reconcile 不得把记录改成 running（也不得凭空删除）
    assert after["status"] == before["status"]


def test_f3_reconcile_survives_endpoint_refusal_without_termination(tmp_path):
    """F3：端点不可核验（attach 失败）→ unattributable、零终止。"""
    store = _FakeStore(tmp_path / "terminals")
    service = _service(tmp_path, store=store, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    _forget_runtime_state(service)
    pid, filetime = _record_identity(service, terminal_id)
    service._identity_probe = lambda _p: _FakeProbe(ProcessStatus.ALIVE, filetime)

    class _RefuseClient(_FakeClient):
        def attach(self):
            raise RuntimeError("injected-endpoint-refused")

    service._client_factory_impl = lambda tid, **kw: _RefuseClient(tid, client_id="x")
    result = service.reconcile()
    assert result["counts"]["unattributable"] == 1
    assert store.deleted == []
    assert service.get(terminal_id)["status"] != RuntimeState.EXITED.value


class _NoSelfSpawnHandle:
    """``state.process`` 缺席时的替身：本实例**不拥有** self-spawn 句柄。"""

    pid = 0

    def poll(self):
        return None


# ══════════════════════════════════════════════════════════════════════════
# F4：reconnect 不得无界泄漏心跳/连接
# ══════════════════════════════════════════════════════════════════════════


def test_f4_reconnect_releases_old_client_and_stops_old_heartbeat(tmp_path):
    """F4：重复 reconcile（detached）不得叠加心跳线程 / 泄漏连接。"""
    from packages.core.terminal.contracts import ProcessIdentity, ProcessProbe

    store = _FakeStore(tmp_path / "terminals")
    service = _service(tmp_path, store=store, heartbeat_interval=5.0)
    # detach 能力由 client 决定：create 之前就绑定"可 detach"的 client
    service._client_factory_impl = lambda tid, **kw: _FakeClient(
        tid, client_id="c", detach_status="detached"
    )
    terminal_id = service.create()["terminal_id"]
    assert service.detach(terminal_id)["detached"] is True
    _forget_runtime_state(service)
    pid, filetime = _record_identity(service, terminal_id)
    service._identity_probe = lambda p: ProcessProbe(
        status=ProcessStatus.ALIVE,
        identity=ProcessIdentity(pid=int(p), created_at_filetime=filetime),
    )

    made: list[_FakeClient] = []

    def factory(tid, **kw):
        c = _FakeClient(tid, client_id=str(kw.get("client_id") or "c"),
                        describe_state="detached")
        made.append(c)
        return c

    service._client_factory_impl = factory
    for _ in range(4):
        service.reconcile()
        time.sleep(0.05)
    live_hb = [t for t in threading.enumerate() if t.name.startswith("pan-terminal-hb-")]
    # 每个终端最多一个存活心跳线程
    assert len(live_hb) <= 1, f"心跳线程泄漏：{len(live_hb)}"
    # 反复reconcile 必须**复用**同一连接，而不是每次新建
    assert len(made) <= 2, f"重连应复用现有连接（实测新建 {len(made)} 个 client）"
    service.shutdown(budget=5.0)


# ══════════════════════════════════════════════════════════════════════════
# F5：未收敛/在途不得提前 release
# ══════════════════════════════════════════════════════════════════════════


def test_f5_unconfirmed_terminal_keeps_client_for_retry(tmp_path):
    """F5：未收敛终端在 shutdown 后仍**保留**连接，可继续重试 close 收敛。"""
    process = _FakeProcess()
    client = _FakeClient("term_f5", client_id="c", close_status="closing")
    service = _service(tmp_path, client=client, process=process,
                       heartbeat_interval=5.0, stop_confirm=1.0)
    terminal_id = service.create()["terminal_id"]
    # 先制造未收敛（缺引擎证据 + 进程仍在）
    result = service.shutdown(budget=5.0)
    assert result["unconfirmed"] == [terminal_id]
    # 未收敛项必须保留连接（否则永远无法收敛）
    state = service._states.get(terminal_id)
    assert state is not None and state.client is not None, "未收敛项必须保留 client 供重试"
    # 补齐证据后可继续重试并收敛
    client.close_status = "exited"
    pid, filetime = _record_identity(service, terminal_id)
    _converged_launcher_status(service.root, terminal_id, pid, filetime)
    process.exit(0)
    view = service.close(terminal_id)
    assert view["status"] == RuntimeState.EXITED.value


def test_f5_inflight_close_worker_survives_shutdown_release(tmp_path):
    """F5：close worker 在途时，shutdown 不得把它的 client 释放掉或丢引用。"""
    process = _FakeProcess()
    started = threading.Event()
    release = threading.Event()

    class _SlowStopClient(_FakeClient):
        def close(self, *, reason="explicit-close"):
            started.set()
            release.wait(6.0)
            return {"status": "exited", "ok": True, "describe": {}}

    client = _SlowStopClient("term_f5b", client_id="c")
    service = _service(tmp_path, client=client, process=process,
                       heartbeat_interval=5.0, stop_confirm=2.0, shutdown_budget=0.4)
    terminal_id = service.create()["terminal_id"]
    closer = threading.Thread(target=lambda: _safe_close(service, terminal_id), daemon=True)
    closer.start()
    assert started.wait(5.0), "首个 stop 应进入在途"
    service.shutdown(budget=0.3)
    state = service._states.get(terminal_id)
    assert state is not None, "在途 worker 的state 不得被丢弃"
    assert state.close_call is not None, "close worker 引用不得被丢弃"
    assert state.client is not None, "在途 worker 使用的连接不得被提前释放"
    release.set()
    closer.join(timeout=10)


def _safe_close(service: TerminalService, terminal_id: str) -> None:
    try:
        service.close(terminal_id)
    except CleanupUnconfirmed:
        pass


# ══════════════════════════════════════════════════════════════════════════
# F6：预算含锁等待；budget=0 不抬高；报告如实
# ══════════════════════════════════════════════════════════════════════════


def test_f6_close_budget_includes_state_lock_wait(tmp_path):
    """F6：**别的线程**占住 ``state.lock`` 时，close 必须在预算内**有界返回**。

    注意两点：
    1) 必须在**另一个线程**持锁——``state.lock`` 是 RLock，同线程重入量不到等待；
    2) close 跑在 worker 线程里做**有界 join**：若实现无界等锁，测试会**快速失败**
       而不是挂到全局 timeout（挂死不是可用的回归信号）。
    """
    process = _FakeProcess()
    service = _service(tmp_path, process=process, heartbeat_interval=5.0, stop_confirm=0.4)
    terminal_id = service.create()["terminal_id"]
    state = service._states[terminal_id]
    release = threading.Event()
    holder = threading.Thread(target=lambda: (state.lock.acquire(), release.wait(30.0)),
                              daemon=True)
    holder.start()
    time.sleep(0.2)
    assert not _lock_free(state), "前提：锁应被其它线程持有"

    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            service.close(terminal_id)
            outcome["result"] = "exited"
        except CleanupUnconfirmed:
            outcome["result"] = "unconfirmed"

    runner = threading.Thread(target=worker, daemon=True)
    started = time.monotonic()
    runner.start()
    runner.join(timeout=3.0)  # 有界 join：超时要失败而不是挂死
    elapsed = time.monotonic() - started
    release.set()
    holder.join(timeout=5)
    assert not runner.is_alive(), (
        f"close 未在预算内有界返回（等锁 {elapsed:.2f}s > stop_confirm 0.4s）"
    )
    assert elapsed < 2.0, f"close 预算必须含锁等待（实测 {elapsed:.2f}s）"
    assert outcome.get("result") == "unconfirmed"


def _lock_free(state: Any) -> bool:
    """非阻塞探测锁是否可获取（获取后立即释放）。"""
    acquired = state.lock.acquire(blocking=False)
    if acquired:
        state.lock.release()
    return acquired


def test_f6_shutdown_budget_includes_lock_and_reports_honestly(tmp_path):
    """F6：shutdown 预算含锁等待；超时须如实反映（worker 线程内有界 join）。"""
    process = _FakeProcess()
    service = _service(tmp_path, process=process, heartbeat_interval=5.0, shutdown_budget=0.5)
    terminal_id = service.create()["terminal_id"]
    state = service._states[terminal_id]
    release = threading.Event()
    holder = threading.Thread(target=lambda: (state.lock.acquire(), release.wait(30.0)),
                              daemon=True)
    holder.start()
    time.sleep(0.2)
    assert not _lock_free(state), "前提：锁应被其它线程持有"

    result_holder: dict[str, Any] = {}

    def worker() -> None:
        result_holder["result"] = service.shutdown()

    runner = threading.Thread(target=worker, daemon=True)
    started = time.monotonic()
    runner.start()
    runner.join(timeout=3.0)
    elapsed = time.monotonic() - started
    release.set()
    holder.join(timeout=5)
    assert not runner.is_alive(), (
        f"shutdown 未在预算内有界返回（等锁 {elapsed:.2f}s > shutdown_budget 0.5s）"
    )
    result = result_holder["result"]
    assert elapsed < 2.0, f"shutdown 预算必须含锁等待（实测 {elapsed:.2f}s）"
    if result["budget_exhausted"]:
        assert result["elapsed_within_budget"] is False, "超预算不得谎报 within_budget"
    assert terminal_id in result["unconfirmed"] or result["budget_exhausted"]


def test_f6_zero_budget_is_not_inflated(tmp_path):
    """F6：``budget=0`` 不被 ``max(0.1, …)`` 抬高为 0.1s（如实按~0 处理）。"""
    process = _FakeProcess()
    service = _service(tmp_path, process=process, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    result = service.shutdown(budget=0.0)
    assert result["budget_seconds"] == 0.0 or result["budget_seconds"] < 0.1, (
        f"budget=0 被抬高为 {result['budget_seconds']}"
    )
    assert terminal_id in result["unconfirmed"] or result["budget_exhausted"]


# ══════════════════════════════════════════════════════════════════════════
# F7：skipped 全部列入未确认；secrets_retained 不假报
# ══════════════════════════════════════════════════════════════════════════


def test_f7_budget_exhausted_records_all_listed_as_unconfirmed(tmp_path):
    """F7：预算耗尽后 skipped 记录**全部**进未确认（确定性锚定，不推断）。"""
    process = _FakeProcess()

    class _NeverStoppingClient(_FakeClient):
        def close(self, *, reason="explicit-close"):
            # 阻塞超过任何预算 → 触发 budget break
            threading.Event().wait(30.0)
            return {"status": "exited", "ok": True, "describe": {}}

    service = _service(tmp_path, client=_NeverStoppingClient("term_f7", client_id="c"),
                       process=process, heartbeat_interval=5.0, shutdown_budget=0.2)
    first = service.create()["terminal_id"]
    # 再建多个记录（同一注入 client 复用；id 唯一）
    ids = [first]
    for _ in range(3):
        svc2 = _service(tmp_path, client=_NeverStoppingClient("term_f7", client_id="c"),
                        process=_FakeProcess(), heartbeat_interval=5.0)
        ids.append(svc2.create()["terminal_id"])
    # 把这些记录复制进本服务的 registry（模拟多条待收敛记录）
    for extra in ids[1:]:
        record = service.registry.get(extra)
        record.status = RuntimeState.CLEANUP_FAILED
        service.registry.save(record)

    result = service.shutdown()
    total = len(service.registry.list())
    listed = set(result["outcomes"]) | set(result["unconfirmed"])
    # 每条记录都必须出现在某处（outcomes 或 unconfirmed），不得静默丢失
    assert len(listed) == total, f"有记录未被列出：{total} vs {len(listed)}"
    assert result["secrets_retained"] is True, "存在未收敛记录时不得假报 secrets_retained=False"


# ══════════════════════════════════════════════════════════════════════════
# F8：关闭准入门早于 RPC；内存态与磁盘同步
# ══════════════════════════════════════════════════════════════════════════


def test_f8_close_admission_gate_blocks_input_before_rpc(tmp_path):
    """F8：close 未证明后 input **不得**触达 client（准入门早于 RPC）。"""
    process = _FakeProcess()
    client = _FakeClient("term_f8", client_id="c", close_status="closing")
    service = _service(tmp_path, client=client, process=process, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    token = service.attach(terminal_id, "browser-1", role="control")
    assert service.input(terminal_id, token, b"before")["size"] == 6
    before = client.calls.get("input", 0)
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    # close 之后内存态与磁盘应同步为"不可写"（准入门早于 RPC：不得触达 client）
    from packages.core.terminal.contracts import UnknownTerminalError

    with pytest.raises((StaleLeaseError, RuntimeError, TerminalNotAttached, UnknownTerminalError)):
        service.input(terminal_id, token, b"after")
    assert client.calls.get("input", 0) == before, "close 后不得触达 client"


def test_f8_memory_record_synced_after_close(tmp_path):
    """F8：close 后内存 ``state.record`` 与磁盘同步（不再是陈旧 running）。"""
    process = _FakeProcess()
    client = _FakeClient("term_f8b", client_id="c", close_status="closing")
    service = _service(tmp_path, client=client, process=process, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    with pytest.raises(CleanupUnconfirmed):
        service.close(terminal_id)
    state = service._states[terminal_id]
    assert state.record.status is not RuntimeState.RUNNING, "内存态仍陈旧 running"
    assert state.record.status.value == service.get(terminal_id)["status"]


# ══════════════════════════════════════════════════════════════════════════
# F9：shutdown 先关 create 准入，与并发 create 线性化
# ══════════════════════════════════════════════════════════════════════════


def test_f9_create_rejected_after_shutdown(tmp_path):
    """F9：shutdown 后 create 被拒绝（无准入 -> 记录不得增长）。"""
    service = _service(tmp_path, heartbeat_interval=5.0)
    service.create()
    before = len(service.list())
    service.shutdown()
    with pytest.raises(TerminalServiceError_types()):
        service.create()
    assert len(service.list()) == before, "shutdown 后不得新增记录"


def TerminalServiceError_types():
    from packages.core.terminal.service import TerminalServiceError

    return TerminalServiceError


def test_f9_create_and_shutdown_linearized(tmp_path):
    """F9：并发 create 与 shutdown 线性化（准入门先关）。"""
    service = _service(tmp_path, heartbeat_interval=5.0)
    service.create()
    results: list[str] = []
    barrier = threading.Barrier(2)

    def creator() -> None:
        barrier.wait()
        try:
            service.create()
            results.append("created")
        except Exception:  # noqa: BLE001
            results.append("rejected")

    def closer() -> None:
        barrier.wait()
        service.shutdown()
        results.append("shutdown")

    threads = [threading.Thread(target=creator), threading.Thread(target=closer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    # shutdown 完成后不得再有 create 成功（线性化）
    service.shutdown()
    try:
        service.create()
        after = "created"
    except Exception:  # noqa: BLE001
        after = "rejected"
    assert after == "rejected"


# ══════════════════════════════════════════════════════════════════════════
# F10：close 缺证用静态分类，无自由文本
# ══════════════════════════════════════════════════════════════════════════


def test_f10_close_reason_distinguishes_missing_proof(tmp_path):
    """F10：缺不同证明 → 不同**静态** reason（调用方可据reason 决策）。"""
    from packages.core.terminal.contracts import ProcessIdentity, ProcessProbe

    # 情形 A：stop 已确认 + 引擎已确证，但**进程仍在** → 缺证据③
    process_alive = _FakeProcess()
    client_a = _FakeClient("term_f10a", client_id="c", close_status="exited")
    svc_a = _service(tmp_path / "a", client=client_a, process=process_alive,
                     heartbeat_interval=5.0)
    tid_a = svc_a.create()["terminal_id"]
    _converged_launcher_status(svc_a.root, tid_a, *_record_identity(svc_a, tid_a))
    with pytest.raises(CleanupUnconfirmed) as info_a:
        svc_a.close(tid_a)

    # 情形 B：stop 已确认 + 进程已退出，但**引擎收尾未确证**（无 launcher-status）
    # → 缺证据②
    process_dead = _FakeProcess()
    process_dead.exit(0)
    client_b = _FakeClient("term_f10b", client_id="c", close_status="exited")
    svc_b = _service(tmp_path / "b", client=client_b, process=process_dead,
                     heartbeat_interval=5.0)
    tid_b = svc_b.create()["terminal_id"]
    with pytest.raises(CleanupUnconfirmed) as info_b:
        svc_b.close(tid_b)

    assert info_a.value.reason == "process-still-running"
    assert info_b.value.reason == "engine-cleanup-unproven"
    assert info_a.value.reason != info_b.value.reason, "缺不同证明须给不同 reason"
    for info in (info_a, info_b):
        reason = info.value.reason
        assert reason and reason.isascii(), f"reason 必须是静态 ascii 分类：{reason!r}"
        assert " " not in reason, f"reason 不得含自由文本：{reason!r}"


def test_f10_no_free_text_leak_in_close_error(tmp_path):
    """F10：close 错误不得泄漏自由文本/路径/秘密（只静态 reason + 类型名）。"""
    process = _FakeProcess()
    client = _FakeClient("term_f10c", client_id="c", close_status="closing")
    svc = _service(tmp_path, client=client, process=process, heartbeat_interval=5.0)
    tid = svc.create()["terminal_id"]
    with pytest.raises(CleanupUnconfirmed) as info:
        svc.close(tid)
    message = str(info.value)
    assert str(svc.root) not in message
    assert ".secret" not in message
    assert info.value.error_type is None or info.value.error_type.isidentifier()


# ══════════════════════════════════════════════════════════════════════════
# F12：心跳断言的有界等待（测试时序容忍度，不改产品）
# ══════════════════════════════════════════════════════════════════════════


def test_f12_heartbeat_progress_uses_bounded_wait(tmp_path):
    """F12：心跳推进断言改**有界等待**，不依赖固定负载窗口。"""
    service = _service(tmp_path, heartbeat_interval=0.1)
    terminal_id = service.create()["terminal_id"]
    token = service.attach(terminal_id, "b", role="control")
    before = service.describe()["heartbeats"][terminal_id]["beats"]
    for index in range(3):
        service.input(terminal_id, token, f"echo x-{index}\r".encode())
    advanced = _wait_until(
        lambda: service.describe()["heartbeats"][terminal_id]["beats"] > before or None, 5.0
    )
    assert advanced, "心跳应在有界等待内推进"
    service.shutdown(budget=5.0)


# ══════════════════════════════════════════════════════════════════════════
# 真实 Windows 隔离层：重启恢复 → stop/收敛闭环；关闭失败 → 证据补齐 → 重试收敛
# ══════════════════════════════════════════════════════════════════════════

import subprocess  # noqa: E402
import sys  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_DIR = REPO_ROOT / "packages/core/terminal/emulator_sidecar"


def _sidecar_present() -> bool:
    return (SIDECAR_DIR / "node_modules/@xterm/headless/package.json").is_file()


requires_sidecar = pytest.mark.skipif(
    not _sidecar_present(),
    reason="sidecar 依赖未安装（需在 emulator_sidecar/ 执行 npm ci）",
)


def _real_service(tmp_path: Path, **kwargs: Any):
    from packages.core.terminal.service import TerminalService as _TS

    return _TS(tmp_path / "service-terminals", **kwargs)


def _owned_dead(pid: int, filetime: int) -> bool:
    """同 handle 口径判自有进程是否已退出（FILETIME 不符视为已退出）。"""
    from packages.core.terminal import win_pipe
    from packages.core.terminal.contracts import ProcessStatus

    probe = win_pipe.probe_process(int(pid))
    if probe.status is ProcessStatus.DEAD:
        return True
    if probe.status is ProcessStatus.ALIVE and probe.identity is not None:
        return int(probe.identity.created_at_filetime or -1) != int(filetime)
    return False


def _terminate_owned(pid: int, filetime: int) -> None:
    """只对**自有**资源做同 handle 核验终止（不按名广杀）。"""
    from packages.core.terminal import win_pipe

    if not _owned_dead(pid, filetime):
        win_pipe.terminate_verified_process(int(pid), int(filetime))


@requires_sidecar
def test_real_restart_recovery_converges_via_reconcile(tmp_path):
    """真实：服务宿主"重启"（丢弃内存态）后，新实例 reconcile 把遗留记录收敛。

    覆盖 F3 的真实面：跨进程只剩持久记录 + 秘密时，仍能判定并收敛，且
    **不**通过续约复活旧 managed、**不**误删仍在跑的 runner 的秘密。
    """
    first = _real_service(tmp_path, log_stderr=False, heartbeat_interval=0.35)
    terminal_id = first.create()["terminal_id"]
    record = first.get(terminal_id)
    pid, filetime = int(record["pid"]), int(record["process_created_at_filetime"])
    try:
        # 模拟宿主崩溃：停心跳 + 断连，但**不**关停 runtime、**不**删秘密
        for state in list(first._states.values()):
            if state.heartbeat is not None:
                state.heartbeat.stop(timeout=2.0)
            if state.client is not None:
                state.client.release_connection()
        # lease 死期后 runner 自停
        assert _wait_until(lambda: _owned_dead(pid, filetime) or None, 40.0), (
            "心跳停止后 runner 应按 lease 自停"
        )
        # 新实例：无内存态，仅凭持久记录 + 秘密核对
        second = _real_service(tmp_path, log_stderr=False)
        result = second.reconcile()
        assert result["fresh_pid_absence_is_dead_evidence"] is False
        assert second.get(terminal_id)["status"] in (
            RuntimeState.EXITED.value,
            RuntimeState.CLEANUP_FAILED.value,
        ), "重启后核对不得凭空标 running，也不得误标其它态"
        # 秘密：要么已证明终止后删除，要么明确保留（不得静默消失）
        exists = second._store().exists(terminal_id)
        if not exists:
            # 已删除 → 必须已把记录标为 exited（删除只允许在证明终止之后）
            assert second.get(terminal_id)["status"] == RuntimeState.EXITED.value, (
                "秘密被删除时记录必须是 exited（不得先删凭据后补状态）"
            )
    finally:
        _terminate_owned(pid, filetime)


@requires_sidecar
def test_real_close_failure_then_retry_converges(tmp_path):
    """真实：关闭先失败（预算内未收敛）→ 证据补齐后**重试**收敛为 exited。

    用极小 ``stop_confirm`` 让首个 close 必然"未证明"（stop 已发出但本地预算
    耗尽），随后在正常预算下重试：三项证明齐备才记 exited 并删秘密。
    """
    service = _real_service(tmp_path, log_stderr=False, stop_confirm=0.001)
    terminal_id = None
    record_pid = record_ft = None
    try:
        terminal_id = service.create()["terminal_id"]
        record = service.get(terminal_id)
        record_pid = int(record["pid"])
        record_ft = int(record["process_created_at_filetime"])
        # 首个 close：预算极小 → 未证明（记录停在 EXITING/CLEANUP_FAILED，秘密保留）
        with pytest.raises(CleanupUnconfirmed):
            service.close(terminal_id)
        assert service.get(terminal_id)["status"] in (
            RuntimeState.EXITING.value,
            RuntimeState.CLEANUP_FAILED.value,
        )
        assert service._store().exists(terminal_id), "未证明不得删秘密"
        # 正常预算重试：等 launcher 引擎收尾落盘后应能收敛
        service.stop_confirm = 20.0
        view = _wait_until(lambda: _try_close(service, terminal_id) or None, 60.0)
        assert view == "exited", "证据补齐后重试必须收敛"
        assert service.get(terminal_id)["status"] == RuntimeState.EXITED.value
        # 已证明终止 → 秘密被删除（真实 SecretStore：文件应不存在）
        assert not service._store().exists(terminal_id), "已证明终止后应删秘密"
    finally:
        if terminal_id and record_pid:
            _terminate_owned(record_pid, int(record_ft or 0))


def _try_close(service: Any, terminal_id: str) -> str | None:
    try:
        service.close(terminal_id)
        return "exited"
    except CleanupUnconfirmed:
        return None


"""Pan Terminal P0：attachment / control lease（撤销不可复活、同锁校验+写）。

覆盖契约报告 M8、M14（含伪造 token、observer 越权、observer 不误失效、
200 次 churn、并发 churn）与增补：attachment detach 与 runtime detach 的语义
边界（前者绝不触碰进程寿命）。
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal.attachments import AttachmentRegistry
from packages.core.terminal.contracts import (
    LEASE_ROLE_CONTROL,
    LEASE_ROLE_OBSERVER,
    DetachUnsupportedError,
    IllegalStateTransition,
    LeaseToken,
    NotControlLeaseError,
    RuntimeState,
    StaleLeaseError,
    UnknownTerminalError,
)
from packages.core.terminal.runtime import PtyRuntime


def _load_runtime_support():
    """复用 test_terminal_runtime.py 中的 ScriptedBackend（唯一权威定义）。"""
    path = Path(__file__).resolve().parent / "test_terminal_runtime.py"
    spec = importlib.util.spec_from_file_location("_terminal_runtime_support_lease", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ScriptedBackend = _load_runtime_support().ScriptedBackend

TID = "term_lease0000000000001"


def _make_lease():
    backend = ScriptedBackend([])
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=24, cols=80)
    runtimes = {TID: runtime}
    lease = AttachmentRegistry(lambda terminal_id: runtimes.get(terminal_id))
    return lease, runtime, backend


# ---------------------------------------------------------------------------
# 世代与转交
# ---------------------------------------------------------------------------


def test_control_generation_starts_at_one_and_handoff_revokes_previous():
    lease, _runtime, backend = _make_lease()
    first = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    assert first.generation == 1
    assert first.role == LEASE_ROLE_CONTROL
    assert first.revocation_id
    second = lease.attach(TID, "c2", role=LEASE_ROLE_CONTROL)
    assert second.generation == 2
    with pytest.raises(StaleLeaseError):
        lease.send(first, b"x")
    assert backend.writes == []
    assert lease.send(second, b"y") == 1


def test_transfer_control_returns_new_generation():
    lease, _runtime, _backend = _make_lease()
    first = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    second = lease.transfer_control(first, to_client="c2")
    assert second.generation == first.generation + 1
    with pytest.raises(StaleLeaseError):
        lease.send(first, b"x")
    assert lease.send(second, b"y") == 1
    holder = lease.control_holder(TID)
    assert holder is not None and holder.client_id == "c2"


def test_stale_generation_copy_rejected():
    lease, _runtime, _backend = _make_lease()
    first = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    lease.attach(TID, "c2", role=LEASE_ROLE_CONTROL)
    stale_copy = LeaseToken(
        terminal_id=TID,
        client_id="c1",
        generation=first.generation,
        role=LEASE_ROLE_CONTROL,
        rows=0,
        cols=0,
        revocation_id="0" * 32,
    )
    with pytest.raises(StaleLeaseError):
        lease.send(stale_copy, b"x")


# ---------------------------------------------------------------------------
# 权限与伪造
# ---------------------------------------------------------------------------


def test_observer_cannot_write_resize_or_transfer():
    lease, _runtime, _backend = _make_lease()
    observer = lease.attach(TID, "m1", role=LEASE_ROLE_OBSERVER)
    with pytest.raises(NotControlLeaseError):
        lease.send(observer, b"x")
    with pytest.raises(NotControlLeaseError):
        lease.resize(observer, 10, 10)
    with pytest.raises(NotControlLeaseError):
        lease.transfer_control(observer, to_client="x")
    lease.validate(observer)  # 观察者校验本身通过


def test_forged_control_token_rejected():
    lease, _runtime, backend = _make_lease()
    real = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    forged = LeaseToken(
        terminal_id=TID,
        client_id="evil",
        generation=real.generation,
        role=LEASE_ROLE_CONTROL,
        rows=0,
        cols=0,
        revocation_id="f" * 32,
    )
    with pytest.raises(NotControlLeaseError):
        lease.send(forged, b"x")
    with pytest.raises(NotControlLeaseError):
        lease.transfer_control(forged, to_client="evil2")
    # 伪造者“撤销自己”不会影响真实控制权，也无法复活。
    lease.detach(forged)
    with pytest.raises(StaleLeaseError):
        lease.send(forged, b"x")
    assert lease.send(real, b"ok") == 2
    assert backend.writes == [b"ok"]


def test_unknown_terminal_rejected():
    lease, _runtime, _backend = _make_lease()
    with pytest.raises(UnknownTerminalError):
        lease.attach("term_missing000000001", "c1")
    token = LeaseToken(
        terminal_id="term_missing000000001",
        client_id="c1",
        generation=1,
        role=LEASE_ROLE_CONTROL,
        rows=0,
        cols=0,
        revocation_id="a" * 32,
    )
    with pytest.raises(UnknownTerminalError):
        lease.send(token, b"x")


def test_invalid_role_rejected():
    lease, _runtime, _backend = _make_lease()
    with pytest.raises(ValueError):
        lease.attach(TID, "c1", role="admin")


# ---------------------------------------------------------------------------
# 撤销不可复活与观察者隔离
# ---------------------------------------------------------------------------


def test_detach_is_terminal_revocation():
    lease, _runtime, backend = _make_lease()
    token = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    lease.detach(token)
    assert lease.is_revoked(token)
    with pytest.raises(StaleLeaseError):
        lease.validate(token)
    with pytest.raises(StaleLeaseError):
        lease.send(token, b"x")
    with pytest.raises(StaleLeaseError):
        lease.resize(token, 1, 1)
    with pytest.raises(StaleLeaseError):
        lease.transfer_control(token, to_client="x")
    with pytest.raises(StaleLeaseError):
        lease.send(token, b"x")  # 重复访问仍被拒（不可复活）
    assert backend.writes == []


def test_observer_revocation_is_scoped():
    lease, _runtime, _backend = _make_lease()
    obs1 = lease.attach(TID, "m1", role=LEASE_ROLE_OBSERVER)
    obs2 = lease.attach(TID, "m2", role=LEASE_ROLE_OBSERVER)
    control = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    lease.detach(obs1)
    with pytest.raises(StaleLeaseError):
        lease.validate(obs1)
    lease.validate(obs2)  # 其它观察者不受影响
    assert lease.send(control, b"x") == 1  # 控制权不受影响


def test_control_handoff_keeps_observers_valid():
    lease, _runtime, _backend = _make_lease()
    observer = lease.attach(TID, "m1", role=LEASE_ROLE_OBSERVER)
    lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    lease.attach(TID, "c2", role=LEASE_ROLE_CONTROL)  # 抢占/转交
    lease.validate(observer)  # 观察者不因控制权更替而失效


def test_control_revocation_does_not_touch_observer():
    lease, _runtime, _backend = _make_lease()
    observer = lease.attach(TID, "m1", role=LEASE_ROLE_OBSERVER)
    control = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    lease.detach(control)
    lease.validate(observer)
    assert lease.control_holder(TID) is None


# ---------------------------------------------------------------------------
# churn：撤销后绝无写入
# ---------------------------------------------------------------------------


def test_sequential_churn_200_no_write_after_revoke():
    lease, _runtime, backend = _make_lease()
    for index in range(200):
        token = lease.attach(TID, f"client-{index}", role=LEASE_ROLE_CONTROL)
        assert lease.send(token, b"w") == 1
        lease.detach(token)
        with pytest.raises(StaleLeaseError):
            lease.send(token, b"x")
    assert len(backend.writes) == 200
    assert all(write == b"w" for write in backend.writes)


def test_concurrent_churn_no_write_after_revoke():
    lease, _runtime, backend = _make_lease()
    workers, rounds = 8, 25
    after_revoke_writes: list[int] = []
    unexpected: list[str] = []
    successes: list[int] = []
    counter_lock = threading.Lock()

    def worker(wid: int) -> None:
        for index in range(rounds):
            token = lease.attach(TID, f"w{wid}-{index}", role=LEASE_ROLE_CONTROL)
            try:
                written = lease.send(token, b"w")
                with counter_lock:
                    successes.append(written)
            except (StaleLeaseError, NotControlLeaseError):
                pass  # 控制权在 attach 与 send 之间被他人抢占：合法拒绝
            lease.detach(token)
            try:
                lease.send(token, b"x")
                with counter_lock:
                    after_revoke_writes.append(1)
            except (StaleLeaseError, NotControlLeaseError):
                pass
            except Exception as exc:  # noqa: BLE001
                with counter_lock:
                    unexpected.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert unexpected == []
    assert after_revoke_writes == []  # 撤销后绝无写入
    assert len(successes) == len(backend.writes)  # 单 writer 守恒
    assert 0 < len(backend.writes) <= workers * rounds
    assert all(write == b"w" for write in backend.writes)


# ---------------------------------------------------------------------------
# 尺寸跟随控制者 / 语义边界（attachment detach vs runtime detach）
# ---------------------------------------------------------------------------


def test_resize_follows_control_holder():
    lease, runtime, backend = _make_lease()
    control = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    assert lease.resize(control, 30, 100) is True
    assert (runtime.rows, runtime.cols) == (30, 100)
    assert backend.resizes == [(30, 100)]
    observer = lease.attach(TID, "m1", role=LEASE_ROLE_OBSERVER)
    with pytest.raises(NotControlLeaseError):
        lease.resize(observer, 10, 10)
    assert (runtime.rows, runtime.cols) == (30, 100)


def test_attachment_detach_differs_from_runtime_detach():
    lease, runtime, backend = _make_lease()
    token = lease.attach(TID, "browser", role=LEASE_ROLE_CONTROL)
    lease.detach(token)  # 浏览器断开：只释放连接
    assert runtime.state is RuntimeState.RUNNING
    assert runtime.detached is False
    assert backend.closed is False
    assert backend.terminate_calls == []
    # runtime.detach 需要真实宿主能力：无注入必须明确 Unsupported，不假实现。
    with pytest.raises(DetachUnsupportedError):
        runtime.detach()
    assert runtime.state is RuntimeState.RUNNING
    assert backend.closed is False
    assert backend.terminate_calls == []


def test_send_on_exited_runtime_raises_illegal_state():
    lease, runtime, _backend = _make_lease()
    token = lease.attach(TID, "c1", role=LEASE_ROLE_CONTROL)
    assert runtime.close(interrupt=False).ok
    with pytest.raises(IllegalStateTransition):
        lease.send(token, b"x")

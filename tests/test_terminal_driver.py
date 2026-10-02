"""Pan Terminal P0：AutomationContext 边界与 pyte 观察者（自动化专用）。

覆盖契约报告 M9-M11：driver 只能经 control lease 写终端、观察者取快照、
公共核心零 adapter 菜单字面量；pyte 观察者（``fidelity=partial``）的模式还原、
resize、wait_for matched/timed_out/quiet_ms 与如实声明的限制。
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
    Fidelity,
    NotControlLeaseError,
    ScreenSnapshot,
    WaitOutcome,
)
from packages.core.terminal.driver import AutomationContext
from packages.core.terminal.observer import ALT_SCREEN_DEC_MODES, PyteScreenObserver
from packages.core.terminal.runtime import PtyRuntime

try:
    import pyte  # noqa: F401

    HAS_PYTE = True
except ImportError:  # pragma: no cover - 隔离环境取决于 --with pyte
    HAS_PYTE = False

pyte_required = pytest.mark.skipif(not HAS_PYTE, reason="pyte not installed")


def _load_runtime_support():
    path = Path(__file__).resolve().parent / "test_terminal_runtime.py"
    spec = importlib.util.spec_from_file_location("_terminal_runtime_support_driver", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ScriptedBackend = _load_runtime_support().ScriptedBackend

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "packages" / "core" / "terminal"
TID = "term_driver000000000001"


class _StubObserver:
    """最小观察者 stub（不依赖 pyte）。"""

    def __init__(self, lines=("hello",)) -> None:
        self.lines = tuple(lines)
        self.text_calls = 0
        self.wait_calls: list[tuple[float, float]] = []
        self.feeded: list[bytes] = []

    def feed(self, data: bytes) -> None:
        self.feeded.append(data)

    def snapshot(self) -> ScreenSnapshot:
        return ScreenSnapshot(
            rows=len(self.lines),
            cols=20,
            lines=self.lines,
            cursor=(0, 0),
            alternate_screen=None,
            private_modes=frozenset(),
            raw_mode_bits=frozenset(),
            saved_cursor=False,
            scrollback_lines=0,
            engine="stub",
            fidelity=Fidelity.PARTIAL,
        )

    def resize(self, rows: int, cols: int) -> None:
        pass

    def text(self) -> str:
        self.text_calls += 1
        return "\n".join(self.lines)

    def wait_for(self, predicate, timeout: float, *, quiet_ms: float = 0.0) -> WaitOutcome:
        self.wait_calls.append((timeout, quiet_ms))
        return WaitOutcome(True, False, self.snapshot(), self.text(), 0)


class _SnapshotOnlyObserver(_StubObserver):
    """无 ``text`` 方法的观察者：``screen_text`` 必须回退到 snapshot。"""

    text = None


def _make_context(observer=None):
    backend = ScriptedBackend([])
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=24, cols=80)
    runtimes = {TID: runtime}
    lease = AttachmentRegistry(lambda terminal_id: runtimes.get(terminal_id))
    token = lease.attach(TID, "driver", role=LEASE_ROLE_CONTROL)
    context = AutomationContext(TID, runtime, observer or _StubObserver(), token, lease)
    return context, runtime, backend, lease


# ---------------------------------------------------------------------------
# AutomationContext 边界（M9/M11）
# ---------------------------------------------------------------------------


def test_send_text_utf8_via_control_lease():
    context, _runtime, backend, _lease = _make_context()
    written = context.send_text("中文")
    assert written == len("中文".encode("utf-8"))
    assert backend.writes == ["中文".encode("utf-8")]


def test_send_keys_ascii():
    context, _runtime, backend, _lease = _make_context()
    context.send_keys("\x1b[A", "\t")
    assert backend.writes == [b"\x1b[A\t"]


def test_observer_token_cannot_drive_context():
    context, runtime, backend, lease = _make_context()
    observer_token = lease.attach(TID, "viewer", role=LEASE_ROLE_OBSERVER)
    bad_context = AutomationContext(TID, runtime, context.observer, observer_token, lease)
    with pytest.raises(NotControlLeaseError):
        bad_context.send_text("x")
    assert backend.writes == []


def test_screen_text_prefers_text_method():
    observer = _StubObserver(lines=("line-a", "line-b"))
    context, _runtime, _backend, _lease = _make_context(observer)
    assert context.screen_text() == "line-a\nline-b"
    assert observer.text_calls == 1


def test_screen_text_falls_back_to_snapshot():
    observer = _SnapshotOnlyObserver(lines=("only-line",))
    context, _runtime, _backend, _lease = _make_context(observer)
    assert context.screen_text() == "only-line"


def test_wait_for_proxies_to_observer():
    observer = _StubObserver()
    context, _runtime, _backend, _lease = _make_context(observer)
    outcome = context.wait_for(lambda text: "hello" in text, 1.5, quiet_ms=25.0)
    assert outcome.matched is True
    assert observer.wait_calls == [(1.5, 25.0)]


def test_core_has_no_adapter_menu_literals():
    files = sorted(CORE_DIR.glob("*.py"))
    assert len(files) == 9
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        assert "restore and fork the conversation" not in text
        assert "never mind" not in text
        assert "cbc" not in text
        assert "codex" not in text


# ---------------------------------------------------------------------------
# pyte 观察者（M10，自动化专用；fidelity=partial）
# ---------------------------------------------------------------------------


@pyte_required
def test_pyte_observer_alt_screen_modes_and_fidelity():
    observer = PyteScreenObserver(rows=24, cols=80)
    observer.feed(b"\x1b[?1049h")
    snapshot = observer.snapshot()
    assert snapshot.alternate_screen is True
    assert 1049 in snapshot.private_modes
    assert (1049 << 5) in snapshot.raw_mode_bits
    assert snapshot.fidelity is Fidelity.PARTIAL
    assert "pyte" in snapshot.engine
    assert snapshot.scrollback_lines == 0


@pyte_required
def test_pyte_observer_text_cursor_and_resize():
    observer = PyteScreenObserver(rows=5, cols=20)
    observer.feed(b"hello")
    assert observer.text() == "hello"
    assert observer.snapshot().cursor == (0, 5)
    observer.resize(rows=10, cols=30)
    snapshot = observer.snapshot()
    assert snapshot.rows == 10
    assert snapshot.cols == 30
    assert observer.observed_bytes == 5


@pyte_required
def test_pyte_observer_wait_for_matched_and_timeout():
    observer = PyteScreenObserver(rows=5, cols=20)
    observer.feed(b"prompt> ")
    matched = observer.wait_for(lambda text: "prompt>" in text, timeout=0.5)
    assert matched.matched is True
    assert matched.timed_out is False
    timed_out = observer.wait_for(lambda text: "absent" in text, timeout=0.05)
    assert timed_out.matched is False
    assert timed_out.timed_out is True


@pyte_required
def test_pyte_observer_wait_for_quiet_ms():
    observer = PyteScreenObserver(rows=5, cols=20)
    timer = threading.Timer(0.06, lambda: observer.feed(b"ready"))
    timer.start()
    try:
        outcome = observer.wait_for(lambda text: "ready" in text, timeout=1.0, quiet_ms=40.0)
    finally:
        timer.join()
    assert outcome.matched is True
    assert outcome.timed_out is False


@pyte_required
def test_pyte_observer_alt_screen_exit_limitation_is_declared():
    observer = PyteScreenObserver(rows=5, cols=20)
    observer.feed(b"\x1b[?1049h")
    observer.feed(b"TUI-CONTENT")
    observer.feed(b"\x1b[?1049l")
    snapshot = observer.snapshot()
    assert snapshot.alternate_screen is False
    # pyte 无备用屏缓冲：TUI 内容不会随退出备用屏而“恢复主屏”，必须声明 partial。
    assert "TUI-CONTENT" in observer.text()


@pyte_required
def test_pyte_observer_incremental_utf8_across_feed_chunks():
    """r2 回归：跨块增量 UTF-8 解码，不得按块 replace 破坏中文。"""
    observer = PyteScreenObserver(rows=5, cols=20)
    data = "中文".encode("utf-8")
    observer.feed(data[:2])
    observer.feed(data[2:4])
    observer.feed(data[4:])
    text = observer.text()
    assert "中文" in text
    assert "\ufffd" not in text  # 无替换字符残留


@pyte_required
def test_pyte_observer_incremental_utf8_replacement_on_invalid_bytes():
    observer = PyteScreenObserver(rows=5, cols=20)
    observer.feed(b"ok")
    observer.feed(b"\xff")  # 非法字节：以替换字符呈现（不静默丢弃）
    text = observer.text()
    assert "ok" in text


def test_pyte_observer_static_contract():
    assert ALT_SCREEN_DEC_MODES == (47, 1047, 1049)
    assert PyteScreenObserver.SUPPORTS_ALTERNATE_SCREEN_BUFFER is False
    assert PyteScreenObserver.SUPPORTS_SCROLLBACK is False
    private, plain = PyteScreenObserver.decode_modes([(1049 << 5), 4, 33568])
    assert private == frozenset({1049})
    assert plain == frozenset({4})

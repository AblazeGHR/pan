"""Tests for oneshot consumer usage/credit accounting (P0 fix).

Background: ``_consumer_oneshot`` (worker.py) inlines stdout event parsing but
never called ``adapter.enrich_after_result`` — the only place raw_usage/cost/
credit get back-filled. Stream mode (``_read_stdout``) does call it, so:

- cbc (oneshot-capable) recorded no credit on the oneshot path;
- claude (oneshot-only) recorded no usage/cost at all.

This suite verifies:
- ``_consumer_oneshot`` calls ``enrich_after_result`` after setting last_result
  and accumulates raw_usage / total_usage exactly like ``_read_stdout``;
- enrich returning None / raising must not break the oneshot flow;
- claude's cost (authoritative only on stdout result events) bridges into
  raw_usage via ``_PENDING_RESULT_USAGE``, populated by ``extract_result_text``
  during oneshot result-event parsing.

Uses mock subprocess + temp SESSION_DIR (no real CLI needed).
"""

import asyncio
import json
import sys
from pathlib import Path

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter
from packages.core.adapters.claude import adapter as claude_adapter
from packages.core.adapters.claude.adapter import ClaudeAdapter


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_oneshot_usage", adapter_name="cbc", model="test-model"):
    s = _sess.Session(id=sid, name="test", adapter=adapter_name, model=model)
    _sess._cache[sid] = s
    return s


def _setup_worker(session_id, adapter):
    w = worker.Worker(
        worker_id="worker-test",
        session_id=session_id,
        adapter=adapter,
        status="idle",
        process=None,
        pending_signal=asyncio.Queue(),
        _replaying=False,
    )
    worker.workers[w.worker_id] = w
    return w


def _line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


class _FakeStdout:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class FakeMcpProc:
    """Fake one-shot subprocess: serves `output` bytes then exits 0."""

    def __init__(self, output: bytes):
        self._chunks = [output[i:i + 512] for i in range(0, len(output), 512)] or [b""]
        self.stdout = _FakeStdout(self._chunks)
        self.returncode = 0
        self.pid = 4242

    async def wait(self):
        return 0

    def kill(self):
        self.returncode = -1


def _patch_spawn(monkeypatch, proc: FakeMcpProc):
    async def fake_spawn(*args, **kwargs):
        return proc
    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", fake_spawn)


def _oneshot_output(cli_sid: str = "cli-oneshot-1") -> bytes:
    """init + assistant + result 事件流（cbc/claude 同构）。"""
    return b"".join([
        _line({"type": "system", "subtype": "init", "session_id": cli_sid, "model": "test-model"}),
        _line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}),
        _line({"type": "result", "result": "hi", "is_error": False}),
    ])


# ── tests ──

def test_oneshot_calls_enrich_and_accumulates_usage(monkeypatch, tmp_path):
    """oneshot 设置 last_result 后调用 enrich_after_result 并累加 usage/credit，
    且用量随 session 落盘（重载后仍在）。"""
    _cleanup()
    sid = "ses_oneshot_acc"
    s = _setup_session(sid=sid)
    s.workdir = str(tmp_path)
    adapter = CbcAdapter()
    w = _setup_worker(s.id, adapter)

    entries = [{
        "model": "test-model",
        "rawUsage": {
            "prompt_tokens": 100,
            "prompt_cache_hit_tokens": 10,
            "prompt_cache_miss_tokens": 90,
            "completion_tokens": 50,
            "credit": 0.25,
        },
    }]
    # 只 mock enrich 的取值（模拟 cbc JSONL 读取），其余走真实逻辑
    monkeypatch.setattr(adapter, "enrich_after_result", lambda s: entries)
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _patch_spawn(monkeypatch, FakeMcpProc(_oneshot_output()))

    asyncio.run(worker._consumer_oneshot(w, "hello", "agent", s))

    assert w.status == "idle", f"expected idle, got {w.status}"
    # last_result 语义不变（只补用量记账）
    assert s.last_result["result"] == "hi"
    assert s.last_result["status"] == "done"
    assert s.last_result["taskSeq"] is None
    # 用量/credit 已累加
    assert s.raw_usage["test-model"]["request_count"] == 1
    assert s.raw_usage["test-model"]["rawUsage"]["credit"] == 0.25
    assert s.total_usage["prompt_tokens"] == 100
    assert s.total_usage["cache_hit_tokens"] == 10
    assert s.total_usage["cache_miss_tokens"] == 90
    assert s.total_usage["completion_tokens"] == 50
    assert s.total_usage["credit"] == 0.25
    # 已落盘：清缓存重载后用量仍在
    _sess._cache.clear()
    reloaded = _sess.get(sid)
    assert reloaded is not None
    assert reloaded.raw_usage["test-model"]["rawUsage"]["credit"] == 0.25
    assert reloaded.total_usage["credit"] == 0.25
    print("PASS: oneshot enrich accumulates usage/credit and persists")
    _cleanup()


def test_oneshot_enrich_none_tolerated(monkeypatch, tmp_path):
    """enrich_after_result 返回 None（无新增）→ 不落账、主流程不受影响。"""
    _cleanup()
    s = _setup_session(sid="ses_oneshot_none")
    s.workdir = str(tmp_path)
    adapter = CbcAdapter()
    w = _setup_worker(s.id, adapter)

    monkeypatch.setattr(adapter, "enrich_after_result", lambda s: None)
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _patch_spawn(monkeypatch, FakeMcpProc(_oneshot_output()))

    asyncio.run(worker._consumer_oneshot(w, "hello", "agent", s))

    assert w.status == "idle"
    assert s.last_result["result"] == "hi"
    assert s.raw_usage is None, f"expected no usage, got {s.raw_usage}"
    assert s.total_usage is None
    print("PASS: oneshot enrich None tolerated")
    _cleanup()


def test_oneshot_enrich_exception_tolerated(monkeypatch, tmp_path):
    """enrich_after_result 抛异常 → try/except 保护，不拖垮主流程。"""
    _cleanup()
    s = _setup_session(sid="ses_oneshot_boom")
    s.workdir = str(tmp_path)
    adapter = CbcAdapter()
    w = _setup_worker(s.id, adapter)

    def boom(s):
        raise RuntimeError("adapter enrich exploded")

    monkeypatch.setattr(adapter, "enrich_after_result", boom)
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _patch_spawn(monkeypatch, FakeMcpProc(_oneshot_output()))

    asyncio.run(worker._consumer_oneshot(w, "hello", "agent", s))

    assert w.status == "idle"
    assert s.last_result["result"] == "hi"
    assert s.raw_usage is None
    assert s.total_usage is None
    print("PASS: oneshot enrich exception tolerated")
    _cleanup()


def test_oneshot_claude_bridges_result_usage_cache(monkeypatch, tmp_path):
    """claude（仅 oneshot）：result 事件的 usage+cost 经 _PENDING_RESULT_USAGE
    桥接进 raw_usage——验证 extract_result_text → enrich_after_result 全链路。"""
    _cleanup()
    claude_adapter._PENDING_RESULT_USAGE.clear()
    sid = "ses_claude_bridge"
    # model 留空：init 事件会捕获 model（claude result 的 modelUsage 提供 model）
    s = _setup_session(sid=sid, adapter_name="claude", model="")
    s.workdir = str(tmp_path)
    adapter = ClaudeAdapter()
    w = _setup_worker(s.id, adapter)

    result_ev = {
        "type": "result",
        "is_error": False,
        "result": "final",
        "session_id": "clu-bridge-1",
        "usage": {
            "input_tokens": 200,
            "output_tokens": 80,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 5,
        },
        "total_cost_usd": 0.0234,
        "modelUsage": {"claude-opus-4-8": {"input_tokens": 200}},
        "timestamp": "2026-08-26T15:14:41.550Z",
    }
    output = b"".join([
        _line({"type": "system", "subtype": "init", "session_id": "clu-bridge-1", "model": "claude-opus-4-8"}),
        _line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "final"}]}}),
        _line(result_ev),
    ])
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _patch_spawn(monkeypatch, FakeMcpProc(output))

    asyncio.run(worker._consumer_oneshot(w, "hello", "agent", s))

    assert w.status == "idle"
    assert s.cli_session_id == "clu-bridge-1"
    assert s.last_result["result"] == "final"
    ru = s.raw_usage["claude-opus-4-8"]["rawUsage"]
    assert ru["prompt_tokens"] == 200
    assert ru["completion_tokens"] == 80
    assert ru["cache_read_tokens"] == 20
    assert ru["cache_write_tokens"] == 5
    assert ru["cost"] == 0.0234, f"cost not bridged: {ru}"
    assert s.total_usage["prompt_tokens"] == 200
    assert s.total_usage["completion_tokens"] == 80
    assert s.total_usage["credit"] == 0.0234
    # 缓存读取即弹出（一次性）
    assert "clu-bridge-1" not in claude_adapter._PENDING_RESULT_USAGE
    print("PASS: claude oneshot result usage cache bridges into raw_usage")
    _cleanup()

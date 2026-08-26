"""Tests for worker output_mode (execution mode selection).

Covers resolve_execution_mode (merges adapter.execution_modes +
session.output_mode) and the API validation helper in server.py
(_apply_output_mode).

For cbc (execution_modes = ["stream","oneshot"]):
- No MCP, output_mode unset -> stream (default)
- MCP, output_mode unset -> stream (default)
- MCP + output_mode="oneshot" -> oneshot
- MCP + output_mode="stream" -> stream (+MCP)
- output_mode="oneshot" (no MCP) -> oneshot  (NEW: no longer requires MCP)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker
from packages.core import session as _sess
from packages.core.adapters import resolve_execution_mode, get_adapter


class _FakeAdapter:
    """Minimal adapter stub carrying only execution_modes (for clamp tests)."""

    def __init__(self, modes):
        self.name = "fake"
        self.execution_modes = modes


def _cleanup():
    worker.workers.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _session(**adapter_config):
    s = _sess.Session(id="ses_test", name="test", model="test-model")
    s.adapter_config = dict(adapter_config)
    return s


# ── _mcp_configured ──


def test_mcp_configured_true_when_servers_nonempty():
    s = _session(mcp_servers=[{"name": "pan"}])
    assert worker._mcp_configured(s) is True


def test_mcp_configured_false_when_no_servers():
    s = _session(mcp_servers=[])
    assert worker._mcp_configured(s) is False


def test_mcp_configured_false_when_missing():
    s = _session()
    assert worker._mcp_configured(s) is False


def test_mcp_configured_ignores_mcp_enabled():
    """mcp_enabled 已废弃：即使 false，servers 非空仍视为启用。"""
    s = _session(mcp_enabled=False, mcp_servers=[{"name": "pan"}])
    assert worker._mcp_configured(s) is True


# ── resolve_execution_mode (merges adapter.execution_modes + output_mode) ──


def test_no_servers_unset_goes_stream():
    s = _session()
    assert resolve_execution_mode(get_adapter("cbc"), s) == "stream"


def test_mcp_without_output_mode_goes_stream():
    """MCP configured, output_mode unset -> stream (default since 2026-08-17)."""
    s = _session(mcp_servers=[{"name": "pan"}])
    assert resolve_execution_mode(get_adapter("cbc"), s) == "stream"


def test_mcp_with_explicit_oneshot_goes_oneshot():
    s = _session(mcp_servers=[{"name": "pan"}], output_mode="oneshot")
    assert resolve_execution_mode(get_adapter("cbc"), s) == "oneshot"


def test_mcp_with_stream_output_mode_goes_stream():
    """stream + MCP channel."""
    s = _session(mcp_servers=[{"name": "pan"}], output_mode="stream")
    assert resolve_execution_mode(get_adapter("cbc"), s) == "stream"


def test_stream_without_mcp_stays_stream():
    s = _session(output_mode="stream")
    assert resolve_execution_mode(get_adapter("cbc"), s) == "stream"


def test_oneshot_without_mcp_goes_oneshot():
    """NEW semantics: output_mode="oneshot" no longer requires MCP configured."""
    s = _session(output_mode="oneshot")
    assert resolve_execution_mode(get_adapter("cbc"), s) == "oneshot"


def test_invalid_output_mode_clamps_to_stream():
    s = _session(mcp_servers=[{"name": "pan"}], output_mode="bogus")
    assert resolve_execution_mode(get_adapter("cbc"), s) == "stream"


def test_oneshot_only_adapter_clamps_stream_to_oneshot():
    s = _session(output_mode="stream")
    # one-shot-only adapter: stream unsupported -> clamp to its only mode
    assert resolve_execution_mode(_FakeAdapter(["oneshot"]), s) == "oneshot"


def test_oneshot_only_adapter_explicit_oneshot():
    s = _session(output_mode="oneshot")
    assert resolve_execution_mode(_FakeAdapter(["oneshot"]), s) == "oneshot"


# ── _apply_output_mode (server.py helper) ──


def _apply(mode):
    from packages.web import server as srv
    s = _sess.Session(id="ses_test", name="test", model="test-model")
    srv._apply_output_mode(s, mode)
    return s.adapter_config.get("output_mode")


def test_apply_output_mode_stream():
    assert _apply("stream") == "stream"


def test_apply_output_mode_oneshot():
    assert _apply("oneshot") == "oneshot"


def test_apply_output_mode_invalid_raises():
    from packages.web import server as srv
    s = _sess.Session(id="ses_test", name="test", model="test-model")
    try:
        srv._apply_output_mode(s, "bogus")
    except ValueError as e:
        assert "outputMode" in str(e)
    else:
        raise AssertionError("expected ValueError for invalid outputMode")


def test_apply_output_mode_clear():
    s = _sess.Session(id="ses_test", name="test", model="test-model")
    from packages.web import server as srv
    srv._apply_output_mode(s, "stream")
    srv._apply_output_mode(s, None)
    assert "output_mode" not in s.adapter_config


def test_apply_output_mode_auto_clears():
    assert _apply("auto") is None


def test_apply_output_mode_oneshot_only_rejects_stream():
    """one-shot-only adapter must reject a stream request (validation 400)."""
    from packages.web import server as srv
    s = _sess.Session(
        id="ses_test", name="test", model="test-model", adapter="fake"
    )
    fake = _FakeAdapter(["oneshot"])
    orig = srv.get_adapter
    srv.get_adapter = lambda name: fake
    try:
        try:
            srv._apply_output_mode(s, "stream")
        except ValueError as e:
            assert "stream" in str(e) or "execution" in str(e).lower()
        else:
            raise AssertionError("expected ValueError for stream on oneshot-only adapter")
    finally:
        srv.get_adapter = orig

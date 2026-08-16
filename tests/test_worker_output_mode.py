"""Tests for worker output_mode (three execution channels).

Covers the mode-selection matrix in worker.py (_mcp_configured /
_use_oneshot_mcp) and the API validation helper in server.py (_apply_output_mode).

Channels:
- No MCP  -> stream (long-running, no MCP)           [existing]
- MCP + output_mode="stream"   -> stream + MCP        [new, cbc >= 2.137.0]
- MCP + output_mode unset/"oneshot" -> one-shot MCP   [existing]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker
from packages.core import session as _sess


def _cleanup():
    worker.workers.clear()
    worker._result_waiters.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _session(**adapter_config):
    s = _sess.Session(id="ses_test", name="test", model="test-model")
    s.adapter_config = dict(adapter_config)
    return s


# ── _mcp_configured ──


def test_mcp_configured_false_when_disabled():
    s = _session(mcp_enabled=False, mcp_servers=[{"name": "pan"}])
    assert worker._mcp_configured(s) is False


def test_mcp_configured_false_when_no_servers():
    s = _session(mcp_enabled=True, mcp_servers=[])
    assert worker._mcp_configured(s) is False


def test_mcp_configured_false_when_missing():
    s = _session()
    assert worker._mcp_configured(s) is False


def test_mcp_configured_true():
    s = _session(mcp_enabled=True, mcp_servers=[{"name": "pan"}])
    assert worker._mcp_configured(s) is True


# ── _use_oneshot_mcp (decision matrix) ──


def test_no_mcp_goes_stream():
    s = _session(mcp_enabled=False, mcp_servers=[{"name": "pan"}])
    assert worker._use_oneshot_mcp(s) is False


def test_mcp_without_output_mode_goes_oneshot():
    """Existing behaviour: MCP configured, output_mode unset -> one-shot."""
    s = _session(mcp_enabled=True, mcp_servers=[{"name": "pan"}])
    assert worker._use_oneshot_mcp(s) is True


def test_mcp_with_explicit_oneshot_goes_oneshot():
    s = _session(mcp_enabled=True, mcp_servers=[{"name": "pan"}], output_mode="oneshot")
    assert worker._use_oneshot_mcp(s) is True


def test_mcp_with_stream_output_mode_goes_stream():
    """New channel: stream + MCP."""
    s = _session(mcp_enabled=True, mcp_servers=[{"name": "pan"}], output_mode="stream")
    assert worker._use_oneshot_mcp(s) is False


def test_stream_without_mcp_stays_stream():
    s = _session(output_mode="stream")
    assert worker._use_oneshot_mcp(s) is False


def test_oneshot_without_mcp_stays_stream():
    """output_mode="oneshot" without MCP is meaningless -> stream."""
    s = _session(output_mode="oneshot")
    assert worker._use_oneshot_mcp(s) is False


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

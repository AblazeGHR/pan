"""Tests for stream-spawn --system-prompt injection decision.

Root-cause regression for the SMA(NoAdapter)+kimi hang: worker.py used to
append ``--system-prompt`` to the spawn argv for ALL stream+MCP sessions.
cbc's CLI natively supports the flag, but kimi's wrapper argparse did not —
the wrapper exited (code 2) on startup and the session never replied.
Injection is now gated by the adapter capability
``supports_spawn_system_prompt`` (getattr, default False), with a
first-message fallback for adapters lacking it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import get_adapter


def _session(**adapter_config):
    s = _sess.Session(id="ses_test", name="test", adapter="kimi")
    s.adapter_config = dict(adapter_config)
    return s


def _session_with_prompt(**adapter_config):
    s = _session(**adapter_config)
    s.system_prompt = "You are SMA."
    return s


class _NoCapabilityAdapter:
    """Adapter stub WITHOUT supports_spawn_system_prompt (getattr default)."""

    name = "fake"


# ── capability declarations ──


def test_cbc_declares_capability():
    assert get_adapter("cbc").supports_spawn_system_prompt is True


def test_kimi_declares_capability():
    assert get_adapter("kimi").supports_spawn_system_prompt is True


# ── _spawn_system_prompt_args decision ──


def test_mcp_plus_prompt_new_session_injects():
    s = _session_with_prompt(mcp_servers=[{"name": "pan"}])
    args = worker._spawn_system_prompt_args(get_adapter("cbc"), s, mcp_on=True)
    assert args == ["--system-prompt", "You are SMA."]


def test_kimi_mcp_plus_prompt_injects():
    s = _session_with_prompt(mcp_servers=[{"name": "pan"}])
    args = worker._spawn_system_prompt_args(get_adapter("kimi"), s, mcp_on=True)
    assert args == ["--system-prompt", "You are SMA."]


def test_adapter_without_capability_returns_none():
    """No capability declared -> None -> worker falls back to first-message
    injection (never pass the flag to a wrapper that would die on it)."""
    s = _session_with_prompt(mcp_servers=[{"name": "pan"}])
    assert worker._spawn_system_prompt_args(_NoCapabilityAdapter(), s, mcp_on=True) is None


def test_no_mcp_returns_none():
    s = _session_with_prompt()
    assert worker._spawn_system_prompt_args(get_adapter("cbc"), s, mcp_on=False) is None


def test_empty_system_prompt_returns_none():
    s = _session(mcp_servers=[{"name": "pan"}])  # system_prompt stays ""
    assert worker._spawn_system_prompt_args(get_adapter("cbc"), s, mcp_on=True) is None


def test_existing_cli_session_id_returns_none():
    """Resume/fork sessions already carry the prompt in CLI context."""
    s = _session_with_prompt(mcp_servers=[{"name": "pan"}],
                             cli_session_id="session_abc")
    assert worker._spawn_system_prompt_args(get_adapter("cbc"), s, mcp_on=True) is None


if __name__ == "__main__":
    for fn in [
        test_cbc_declares_capability,
        test_kimi_declares_capability,
        test_mcp_plus_prompt_new_session_injects,
        test_kimi_mcp_plus_prompt_injects,
        test_adapter_without_capability_returns_none,
        test_no_mcp_returns_none,
        test_empty_system_prompt_returns_none,
        test_existing_cli_session_id_returns_none,
    ]:
        fn()
        print(f"PASS: {fn.__name__}")
    print("\n=== ALL SPAWN SYSTEM-PROMPT TESTS PASSED ===")

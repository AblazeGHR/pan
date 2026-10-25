"""Tests for the Kimi sessions provider (packages/core/adapters/kimi/sessions.py).

Focus: kimi 2.3.1 (2026-10-24) layout realities verified against the real CLI:
- session index has TWO sources — legacy ``session_index.jsonl`` and the new
  ``sessions/.index-cache/scan.json`` (fresh isolated homes only get the
  latter); Pan merges both plus a directory-scan fallback;
- native ``kimi fork`` (2.3.1+) is the primary fork path, with the directory
  copy kept as fallback for older builds;
- provider functions accept an optional ``kimi_home`` kwarg (方案 C isolated
  home) and ``provider_kwargs`` filters it per provider signature.

Real-CLI integration lives in tests/test_kimi_real_integration.py (env-gated).
"""

import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters import provider_kwargs
from packages.core.adapters.cbc import sessions as cbc_sessions
from packages.core.adapters.kimi import sessions as ks


# ── synthetic kimi home builders ────────────────────────────────────────────


def _wire_event(etype: str, **extra) -> dict:
    ev = {"type": etype, "agentId": "main", "time": 1792857801901}
    ev.update(extra)
    return ev


def _make_session(home: Path, ws: str, sid: str, cwd: str, *,
                  title: str | None = None, turns: int = 1) -> Path:
    """Create a synthetic kimi session dir with wire.jsonl + state.json."""
    sdir = home / "sessions" / ws / sid
    (sdir / "agents" / "main").mkdir(parents=True, exist_ok=True)
    lines = [_wire_event("metadata", protocol_version=1)]
    for i in range(turns):
        text = f"turn-{i} question"
        lines.append(_wire_event(
            "context.append_message",
            message={"role": "user", "content": [{"type": "text", "text": text}],
                     "origin": {"kind": "user"}}))
        lines.append(_wire_event(
            "context.append_loop_event",
            event={"type": "content.part", "part": {"type": "text", "text": f"turn-{i} answer"}}))
        lines.append(_wire_event(
            "usage.record", model="moonshot-cn/kimi-k2.6",
            usage={"inputOther": 10, "output": 5, "inputCacheRead": 0, "inputCacheCreation": 0},
            time=1792857801901 + i * 1000))
    (sdir / "agents" / "main" / "wire.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in lines), encoding="utf-8")
    state = {"id": sid, "cwd": cwd, "createdAt": 1792857801901, "updatedAt": 1792857901901}
    if title is not None:
        state["title"] = title
    (sdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return sdir


def _legacy_index(home: Path, entries: list[dict]) -> None:
    with open(home / "session_index.jsonl", "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _scan_index(home: Path, sessions: dict[str, dict]) -> None:
    """Write kimi 2.3.1's sessions/.index-cache/scan.json."""
    cache = home / "sessions" / ".index-cache"
    cache.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "sessions": {
        sid: {"ws": info["ws"], "meta": "state.json", "mtimeMs": 1.0, "size": 1,
              "summary": {"id": sid, "workspaceId": info["ws"], "cwd": info["cwd"],
                          "createdAt": 1, "updatedAt": 2, "archived": False, "custom": {}}}
        for sid, info in sessions.items()
    }}
    (cache / "scan.json").write_text(json.dumps(payload), encoding="utf-8")


# ── _load_session_index: three sources, one merged view ─────────────────────


def test_load_index_legacy_jsonl(tmp_path):
    home = tmp_path / "home"
    (home / "sessions" / "wd_a").mkdir(parents=True)
    _make_session(home, "wd_a", "session_a", r"C:\work\a")
    _legacy_index(home, [{
        "sessionId": "session_a",
        "sessionDir": str(home / "sessions" / "wd_a" / "session_a"),
        "workDir": r"C:\work\a",
    }])
    index = ks._load_session_index(str(home))
    assert [e["sessionId"] for e in index] == ["session_a"]
    assert index[0]["workDir"] == r"C:\work\a"


def test_load_index_scan_json_only(tmp_path):
    """2.3.1 regression: fresh isolated homes have NO session_index.jsonl —
    the synchronous index is sessions/.index-cache/scan.json."""
    home = tmp_path / "home"
    _make_session(home, "wd_a", "session_a", r"C:\work\a")
    _scan_index(home, {"session_a": {"ws": "wd_a", "cwd": r"C:\work\a"}})
    assert not (home / "session_index.jsonl").exists()

    index = ks._load_session_index(str(home))
    assert [e["sessionId"] for e in index] == ["session_a"]
    assert index[0]["sessionDir"] == str(home / "sessions" / "wd_a" / "session_a")
    assert index[0]["workDir"] == r"C:\work\a"


def test_load_index_directory_scan_fallback(tmp_path):
    """No index files at all — fall back to scanning sessions/wd_*/session_*."""
    home = tmp_path / "home"
    _make_session(home, "wd_a", "session_a", r"C:\work\a")
    index = ks._load_session_index(str(home))
    assert [e["sessionId"] for e in index] == ["session_a"]


def test_load_index_merges_and_dedups(tmp_path):
    home = tmp_path / "home"
    _make_session(home, "wd_a", "session_a", r"C:\work\a")
    _make_session(home, "wd_b", "session_b", r"C:\work\b")
    _legacy_index(home, [{
        "sessionId": "session_a",
        "sessionDir": str(home / "sessions" / "wd_a" / "session_a"),
        "workDir": r"C:\work\a",
    }])
    _scan_index(home, {
        "session_a": {"ws": "wd_a", "cwd": r"C:\work\a"},   # duplicate of jsonl
        "session_b": {"ws": "wd_b", "cwd": r"C:\work\b"},   # scan-only
    })
    ids = [e["sessionId"] for e in ks._load_session_index(str(home))]
    assert sorted(ids) == ["session_a", "session_b"]
    assert len(ids) == len(set(ids))


# ── provider functions carry kimi_home ──────────────────────────────────────


def test_provider_functions_accept_kimi_home(tmp_path):
    home = tmp_path / "home"
    _make_session(home, "wd_a", "session_a", r"C:\work\a", turns=2)
    _scan_index(home, {"session_a": {"ws": "wd_a", "cwd": r"C:\work\a"}})

    history = ks.parse_history("session_a", None, kimi_home=str(home))
    assert [h["role"] for h in history] == ["user", "assistant"] * 2
    usage = ks.get_raw_usage("session_a", None, kimi_home=str(home))
    assert len(usage) == 2
    assert usage[0]["model"] == "moonshot-cn/kimi-k2.6"
    assert usage[0]["rawUsage"]["prompt_tokens"] == 10
    assert usage[0]["rawUsage"]["completion_tokens"] == 5


def test_fork_session_with_kimi_home_uses_copy_fallback(tmp_path, monkeypatch):
    """Native fork unavailable (monkeypatched to None) → copy fallback must
    register the fork in the SAME home's index (not the real ~/.kimi-code)."""
    home = tmp_path / "home"
    _make_session(home, "wd_a", "session_a", r"C:\work\a", title="orig")
    _scan_index(home, {"session_a": {"ws": "wd_a", "cwd": r"C:\work\a"}})
    monkeypatch.setattr(ks, "_native_fork_session", lambda *a, **k: None)

    new_id = ks.fork_session("session_a", "pan-branch", None, kimi_home=str(home))
    assert new_id.startswith("session_") and new_id != "session_a"

    # registered in the isolated home's index, not the real one
    index = ks._load_session_index(str(home))
    assert new_id in [e["sessionId"] for e in index]
    assert not (Path.home() / ".kimi-code" / "session_index.jsonl").read_text(
        encoding="utf-8").count(new_id)

    # title + history carried over
    assert ks.get_session_title(new_id, None, kimi_home=str(home)) == "pan-branch"
    assert len(ks.parse_history(new_id, None, kimi_home=str(home))) == 2


def test_fork_kimi_session_missing_parent(tmp_path):
    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        ks.fork_kimi_session("session_missing", "x", None, kimi_home=str(home))


# ── native fork (kimi 2.3.1 `kimi fork`) ────────────────────────────────────


def _fake_run(monkeypatch, stdout: str, returncode: int = 0):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["env"] = kwargs.get("env")
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(ks.subprocess, "run", fake_run)
    return calls


def test_native_fork_parses_new_session_id(tmp_path, monkeypatch):
    calls = _fake_run(monkeypatch, 'Forked to session_6981e692-ad3f-4437-b257-41a4d09d2ece ("Fork: x") in 445ms\n')
    monkeypatch.setattr(ks, "_kimi_executable", lambda: "kimi.exe")
    new_id = ks._native_fork_session(
        "session_parent", "pan-branch", r"C:\work\a", None)
    assert new_id == "session_6981e692-ad3f-4437-b257-41a4d09d2ece"
    assert calls["cmd"][:3] == ["kimi.exe", "fork", "session_parent"]
    assert "-y" in calls["cmd"]
    assert calls["cmd"][calls["cmd"].index("--cwd") + 1] == str(Path(r"C:\work\a"))


def test_native_fork_injects_kimi_code_home(tmp_path, monkeypatch):
    calls = _fake_run(monkeypatch, "Forked to session_6981e692-ad3f-4437-b257-41a4d09d2ece (x)\n")
    monkeypatch.setattr(ks, "_kimi_executable", lambda: "kimi.exe")
    ks._native_fork_session("session_parent", None, r"C:\work\a", "D:/data/kimi-homes/ses_x")
    assert calls["env"]["KIMI_CODE_HOME"] == "D:/data/kimi-homes/ses_x"


def test_native_fork_failure_returns_none(tmp_path, monkeypatch):
    _fake_run(monkeypatch, "error: unknown command 'fork'\n", returncode=1)
    monkeypatch.setattr(ks, "_kimi_executable", lambda: "kimi.exe")
    assert ks._native_fork_session("session_parent", None, r"C:\work\a", None) is None


def test_native_fork_unparseable_output_returns_none(tmp_path, monkeypatch):
    _fake_run(monkeypatch, "some unexpected output\n")
    monkeypatch.setattr(ks, "_kimi_executable", lambda: "kimi.exe")
    assert ks._native_fork_session("session_parent", None, r"C:\work\a", None) is None


def test_native_fork_missing_executable_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "_kimi_executable", lambda: str(tmp_path / "nope.exe"))
    assert ks._native_fork_session("session_parent", None, r"C:\work\a", None) is None


def test_fork_kimi_session_prefers_native(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "_native_fork_session",
                        lambda *a, **k: "session_native_1")
    assert ks.fork_kimi_session("session_parent", "n", None, None) == "session_native_1"


# ── provider_kwargs signature filtering ─────────────────────────────────────


def test_provider_kwargs_filters_by_signature():
    assert provider_kwargs(ks.fork_session, kimi_home="/h") == {"kimi_home": "/h"}
    assert provider_kwargs(ks.parse_history, kimi_home="/h") == {"kimi_home": "/h"}
    assert provider_kwargs(ks.get_raw_usage, kimi_home="/h") == {"kimi_home": "/h"}
    assert provider_kwargs(ks.write_custom_title, kimi_home="/h") == {"kimi_home": "/h"}
    # providers without the kwarg must not receive it (pre-2026-10-24 bug:
    # server passed kimi_home= to every provider → TypeError on cbc rename)
    assert provider_kwargs(cbc_sessions.fork_session, kimi_home="/h") == {}
    assert provider_kwargs(cbc_sessions.write_custom_title, kimi_home="/h") == {}


# ── titles on 2.3.1 state.json ──────────────────────────────────────────────


def test_iso_or_raw_normalizes_mixed_timestamps():
    """2.3.1 state.json 时间戳是 epoch ms 整数，旧版是 ISO 字符串——API 输出
    必须统一为 ISO（真实 home 实测 629 字符串 + 26 整数）。"""
    assert ks._iso_or_raw(1792857940093) == "2026-10-24T16:05:40.093000+00:00"
    assert ks._iso_or_raw("1792857940093") == "2026-10-24T16:05:40.093000+00:00"
    assert ks._iso_or_raw("2026-07-19T09:16:26.191Z") == "2026-07-19T09:16:26.191Z"
    assert ks._iso_or_raw("") == ""
    assert ks._iso_or_raw(None) == ""
    assert ks._iso_or_raw(True) == ""


def test_list_sessions_normalizes_timestamps(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _make_session(home, "wd_a", "session_a", r"C:\work\a")
    state_path = home / "sessions" / "wd_a" / "session_a" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["createdAt"] = 1792857801901   # 2.3.1 epoch ms int
    state["updatedAt"] = 1792857901901
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _scan_index(home, {"session_a": {"ws": "wd_a", "cwd": r"C:\work\a"}})

    # list_kimi_sessions 读「真实 home」索引——把模块级 _KIMI_DIR 指向 tmp home
    monkeypatch.setattr(ks, "_KIMI_DIR", home)
    sessions = ks.list_kimi_sessions()
    match = [s for s in sessions if s["session_id"] == "session_a"]
    assert match, "session not listed"
    assert match[0]["createdAt"] == "2026-10-24T16:03:21.901000+00:00"
    assert match[0]["updatedAt"] == "2026-10-24T16:05:01.901000+00:00"


def test_write_and_read_custom_title(tmp_path):
    home = tmp_path / "home"
    _make_session(home, "wd_a", "session_a", r"C:\work\a")
    _scan_index(home, {"session_a": {"ws": "wd_a", "cwd": r"C:\work\a"}})
    assert ks.get_session_title("session_a", None, kimi_home=str(home)) == ""
    ks.write_custom_title("session_a", "pan-renamed", None, kimi_home=str(home))
    assert ks.get_session_title("session_a", None, kimi_home=str(home)) == "pan-renamed"
    state = json.loads((home / "sessions" / "wd_a" / "session_a" / "state.json")
                       .read_text(encoding="utf-8"))
    assert state["title"] == "pan-renamed"
    assert state["isCustomTitle"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

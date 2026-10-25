"""Real kimi CLI integration tests — OPT-IN, never run by default.

Gates (non-PAN prefix so ``scripts/run_tests_isolated.py`` forwards them):
- ``TEST_REAL_KIMI=1``：read-only checks against the real kimi CLI
  (``kimi session list`` vs Pan's merged index parser). Safe, no writes.
- ``TEST_REAL_KIMI_E2E=1``：full Pan wrapper → real ``kimi -p`` roundtrip.
  Requires a working (funded) Moonshot account; the contract test still
  exercises the whole argv chain when the account is suspended (the result
  event then carries the provider error), the success roundtrip skips with
  an explicit blocker instead of faking a green.

Never run against another user's profile: these tests use the real
``~/.kimi-code`` read-only, or (E2E) append new sessions there — they never
delete or rewrite existing sessions.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters.kimi import sessions as ks

REAL_LIST = os.environ.get("TEST_REAL_KIMI") == "1"
REAL_E2E = os.environ.get("TEST_REAL_KIMI_E2E") == "1"

KIMI = Path.home() / ".kimi-code" / "bin" / "kimi.exe"

_CREDENTIAL_RE = re.compile(r"(ak-[A-Za-z0-9]{6})[A-Za-z0-9-]*|(org-[A-Za-z0-9]{6})[A-Za-z0-9-]*")


def _redact(text: str) -> str:
    """kimi 的错误信息会带 account/ak 前缀——日志与 skip 原因里必须打码。"""
    def _sub(match: re.Match) -> str:
        prefix = match.group(1) or match.group(2)
        return f"{prefix}…(redacted)"
    return _CREDENTIAL_RE.sub(_sub, text)


def _kimi_exe() -> str:
    if KIMI.is_file():
        return str(KIMI)
    found = subprocess.run(["where" if os.name == "nt" else "which", "kimi"],
                           capture_output=True, text=True)
    if found.returncode == 0 and found.stdout.strip():
        return found.stdout.strip().splitlines()[0]
    pytest.skip("kimi CLI not found")


@pytest.mark.skipif(not REAL_LIST, reason="set TEST_REAL_KIMI=1 to run real-CLI checks")
def test_real_cli_version_is_2x():
    proc = subprocess.run([_kimi_exe(), "--version"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().startswith("2."), proc.stdout


@pytest.mark.skipif(not REAL_LIST, reason="set TEST_REAL_KIMI=1 to run real-CLI checks")
def test_real_session_list_is_covered_by_pan_parser():
    """kimi 自己的 session 列表必须被 Pan 的合并索引全覆盖（2.3.1 scan.json 来源）。

    Pan 的 list_sessions() 合并 jsonl + scan.json + 目录扫描，是 kimi 列表的
    超集（还会包含 jsonl 独有/目录残留的条目），故断言方向为「kimi 列出 ⊆ Pan 列出」。
    """
    proc = subprocess.run(
        [_kimi_exe(), "session", "list", "--all", "--json"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    native = json.loads(proc.stdout)
    pan_sessions = {s["session_id"] for s in ks.list_sessions()}
    missing = [s["id"] for s in native if s["id"] not in pan_sessions]
    assert not missing, f"kimi sessions missing from Pan index: {missing[:5]}"
    print(f"native={len(native)} pan={len(pan_sessions)} — all native sessions covered")


@pytest.mark.skipif(not REAL_LIST, reason="set TEST_REAL_KIMI=1 to run real-CLI checks")
def test_real_history_and_usage_parse():
    """对真实 home 里最近一个有历史的会话跑 parse_history/get_raw_usage。"""
    sessions = sorted(ks.list_sessions(),
                      key=lambda s: str(s.get("updatedAt") or s.get("createdAt") or ""),
                      reverse=True)
    checked = 0
    for s in sessions:
        if checked >= 3:
            break
        history = ks.parse_history(s["session_id"])
        if not history:
            continue
        checked += 1
        assert any(h["role"] == "user" for h in history) or any(
            h["role"] == "assistant" for h in history)
        usage = ks.get_raw_usage(s["session_id"])
        assert isinstance(usage, list)
    assert checked > 0, "no parseable real session found"


# ── full wrapper E2E (needs a funded account for the success path) ──────────


def _drive_wrapper(tmp_path: Path, text: str, timeout: float = 180.0):
    """Spawn the real Pan wrapper against the real kimi CLI; return (events, proc)."""
    from packages.core.adapters.kimi import wrapper as wrapper_mod  # noqa: F401
    from packages.jobs.window import command_window_options

    prompt_file = tmp_path / "system-prompt.txt"
    prompt_file.write_text("You are a Pan integration probe. Answer briefly.", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-u",
         str(Path(ks.__file__).resolve().parent / "wrapper.py"),
         "--kimi-path", _kimi_exe(),
         "--system-prompt-file", str(prompt_file)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(tmp_path), text=True, encoding="utf-8", bufsize=1,
        **command_window_options(),
    )
    proc.stdin.write(json.dumps({"text": text}) + "\n")
    proc.stdin.flush()

    events: list[dict] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if events[-1].get("role") == "result":
            break
    proc.stdin.close()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    return events, proc


@pytest.mark.skipif(not REAL_E2E, reason="set TEST_REAL_KIMI_E2E=1 to run the real wrapper roundtrip")
def test_real_wrapper_contract(tmp_path):
    """wrapper → 真实 kimi 进程链契约：必有一条 result 事件；成功时带
    session_id（meta/session.resume_hint），失败时错误信息带 kimi stderr
    （证明 argv 被接受、stderr 泵工作、result 合成正常）。"""
    events, proc = _drive_wrapper(tmp_path, "Reply with exactly: PAN_REAL_OK")
    results = [e for e in events if e.get("role") == "result"]
    assert len(results) == 1, f"expected exactly one result event, got: {events}"
    result = results[0]
    assert isinstance(result.get("result"), str)
    if result.get("is_error"):
        # Provider/account failure still proves the process chain; report the blocker.
        print(f"BLOCKED by provider: {_redact(result['result'][:300])}")
        pytest.skip("kimi provider unavailable (process chain OK): "
                    + _redact(result["result"][:200]))
    session_ids = [e.get("session_id") for e in events
                   if e.get("role") == "meta" and e.get("type") == "session.resume_hint"]
    assert session_ids and session_ids[0], f"no session_id in events: {events}"
    assert result["result"], "empty result text on success"
    print(f"PASS: first turn OK, session_id={session_ids[0]}")


@pytest.mark.skipif(not REAL_E2E, reason="set TEST_REAL_KIMI_E2E=1 to run the real wrapper roundtrip")
def test_real_wrapper_resume_roundtrip(tmp_path):
    """二轮 resume：同一 wrapper 进程二条消息，-S 续接，上下文连续。"""
    events, proc = _drive_wrapper(tmp_path, "Remember the phrase PAN_REAL_OK. Reply with just: ACK1")
    results = [e for e in events if e.get("role") == "result"]
    if not results or results[0].get("is_error"):
        pytest.skip("first turn blocked by provider; resume roundtrip not exercised")
    session_id = next((e.get("session_id") for e in events
                       if e.get("role") == "meta" and e.get("type") == "session.resume_hint"), None)
    assert session_id

    proc.stdin.write(json.dumps({
        "text": "What phrase did I ask you to remember? Reply with just that phrase."
    }) + "\n")
    proc.stdin.flush()
    events2: list[dict] = []
    deadline = time.time() + 180
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            events2.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if events2[-1].get("role") == "result":
            break
    proc.stdin.close()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()

    results2 = [e for e in events2 if e.get("role") == "result"]
    assert len(results2) == 1, f"expected one result event, got: {events2}"
    if results2[0].get("is_error"):
        pytest.skip("resume turn blocked by provider: "
                    + _redact(results2[0]["result"][:200]))
    assert "PAN_REAL_OK" in (results2[0].get("result") or ""), (
        f"resume lost context: {results2[0].get('result')!r}")
    print(f"PASS: resume roundtrip OK, session_id={session_id}")

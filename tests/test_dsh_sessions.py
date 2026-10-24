"""Tests for the DSH (DeepSeek Harness) SessionsProvider.

Builds an owned ``DSH_HOME`` in ``tmp_path`` with the real multi-frame zstd
layout (header in its own frame, one event per frame) so the reader, usage
aggregation, title fallback and fork rewrite are exercised without touching the
user's ``~/.dsh``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters.dsh import sessions as S

requires_zstd = pytest.mark.skipif(
    not S.zstd_available(), reason="zstandard (or Python >= 3.14 compression.zstd) is required"
)

WORKDIR = r"D:\proj\alpha"


def _header(session_id: str, cwd: str = WORKDIR, created_at: int = 1_792_856_941_811) -> dict:
    return {"type": "session", "version": 4, "id": session_id,
            "createdAt": created_at, "cwd": cwd, "isSeeded": False,
            "delegationDepth": 0}


def _user(text: str, seq: int, source: str = "user", msg_id: str = "") -> dict:
    return {"type": "user/message", "seq": seq, "time": 1_792_856_941_900 + seq * 10,
            "data": {"content": [{"type": "text", "text": text}],
                     "source": {"kind": source}, "role": "user",
                     "id": msg_id or f"msg-{seq}"}, "surfaceOp": "append"}


def _assistant(text: str, seq: int, reasoning: str = "", tool=None,
               usage: dict | None = None) -> dict:
    content = []
    if reasoning:
        content.append({"type": "reasoning", "text": reasoning})
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool-call", "id": "call-1", "name": tool,
                        "arguments": json.dumps({"path": "a.txt"})})
    message = {"role": "assistant", "content": content}
    data = {"turn": 1, "step": 1, "message": message}
    if usage is not None:
        data["usage"] = usage
    return {"type": "assistant/message", "seq": seq,
            "time": 1_792_856_941_900 + seq * 10, "data": data}


def _write_session(home: Path, events: list[dict], session_id: str,
                   project: str, multi_frame: bool = True) -> Path:
    session_dir = home / "sessions" / project / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False) for event in events]
    store = session_dir / "session.v4.jsonl.zstd"
    if multi_frame:
        import zstandard
        compressor = zstandard.ZstdCompressor()
        store.write_bytes(b"".join(compressor.compress((line + "\n").encode("utf-8"))
                                   for line in lines))
    else:
        import zstandard
        store.write_bytes(zstandard.ZstdCompressor().compress(
            ("\n".join(lines) + "\n").encode("utf-8")))
    return session_dir


def _write_projcache(home: Path, session_id: str, *, title: str = "",
                     cwd: str = WORKDIR, created_at: int = 1_792_856_941_811,
                     totals: dict | None = None) -> None:
    rows = {
        "title": {"ver": 1, "seq": 9, "val": title},
        "tokenUsage": {"ver": 2, "seq": 9, "val": {
            "totals": totals or {"uncachedInputTokens": 0, "outputTokens": 0,
                                 "cacheReadTokens": 0, "cacheWriteTokens": 0},
            "last": None}},
    }
    record = {"identity": {"formatVersion": 4, "createdAt": created_at, "cwd": cwd},
              "rows": rows}
    path = home / "storages" / "session_projcache" / "sessions" / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 7, "record": record}), encoding="utf-8")


@pytest.fixture()
def dsh_home(tmp_path, monkeypatch):
    home = tmp_path / "dsh_home"
    home.mkdir()
    monkeypatch.setenv("DSH_HOME", str(home))
    return home


# ── projectKey（与 DSH 的 projectKey 逐字符对齐，样本取自真实目录） ──


def test_project_key_matches_real_dsh_directories():
    assert S.project_key("D:\\") == "--D---"
    assert S.project_key("D:\\tmp\\dshprobe") == "--D-tmp-dshprobe--"
    assert S.project_key("D:\\tmp\\dsh probe.x_y") == "--D-tmp-dsh~0020probe.x_y--"
    assert S.project_key("") == S._NO_CWD_KEY
    assert S.project_key("C:/Users/a/b") == S.project_key("C:\\Users\\a\\b")


def test_dsh_home_respects_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "custom"))
    assert S.dsh_home() == tmp_path / "custom"
    assert S.sessions_root() == tmp_path / "custom" / "sessions"
    monkeypatch.delenv("DSH_HOME")
    assert S.dsh_home() == Path.home() / ".dsh"


# ── 列表 ──


@requires_zstd
def test_list_sessions_reads_projcache_and_log(dsh_home):
    sid = "session-11111111-1111-1111-1111-111111111111"
    events = [_header(sid), _user("first prompt", 1), _assistant("done", 2)]
    _write_session(dsh_home, events, sid, S.project_key(WORKDIR))
    _write_projcache(dsh_home, sid, title="cached title")

    rows = S.list_sessions(WORKDIR)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == sid
    assert row["title"] == "cached title"
    assert row["workDir"] == WORKDIR
    assert row["message_count"] == 2
    assert row["createdAt"].startswith("2026-") or row["createdAt"]
    assert row["updatedAt"]


@requires_zstd
def test_list_sessions_falls_back_to_log_metadata(dsh_home):
    sid = "session-22222222-2222-2222-2222-222222222222"
    events = [
        _header(sid),
        {"type": "user/message", "seq": 1, "time": 1_792_856_941_901,
         "data": {"content": [{"type": "text", "text": "hello"}],
                  "source": {"kind": "runtime-context"}, "role": "user", "id": "m1"}},
        _user("real question", 2),
        {"type": "session/title", "seq": 3, "time": 1_792_856_941_930,
         "data": {"title": "log title"}},
        {"type": "request/context", "seq": 4, "time": 1_792_856_941_931,
         "data": {"provider": "deepseek-official", "model": "deepseek-v4-pro"}},
    ]
    _write_session(dsh_home, events, sid, S.project_key(WORKDIR))

    row = S.list_sessions(WORKDIR)[0]
    assert row["title"] == "log title"
    assert row["model"] == "deepseek-v4-pro"
    # runtime-context 不算一条消息，只算真实用户消息 + 助手消息
    assert row["message_count"] == 1


@requires_zstd
def test_list_sessions_filters_by_cwd(dsh_home):
    sid = "session-33333333-3333-3333-3333-333333333333"
    _write_session(dsh_home, [_header(sid)], sid, S.project_key(WORKDIR))
    assert S.list_sessions(WORKDIR)[0]["session_id"] == sid
    assert S.list_sessions(r"D:\other") == []
    # 无 cwd = 全量
    assert len(S.list_sessions()) == 1


def test_list_sessions_without_home_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "empty"))
    assert S.list_sessions() == []


# ── 历史 ──


@requires_zstd
def test_parse_history_maps_committed_messages_only(dsh_home):
    sid = "session-44444444-4444-4444-4444-444444444444"
    events = [
        _header(sid),
        {"type": "system/message", "seq": 0, "time": 1_792_856_941_850,
         "data": {"message": {"role": "system",
                              "content": [{"type": "text", "text": "be nice"}]}}},
        _user("请读文件", 1, msg_id="u-1"),
        # 运行时上下文也伪装成 user/message，必须被跳过
        _user("<system-reminder>skills</system-reminder>", 2, source="runtime-context"),
        # 被丢弃的 attempt 不进历史
        {"type": "assistant/attempt", "seq": 3, "time": 1_792_856_941_930,
         "data": {"turn": 1, "step": 1, "stream": []}},
        _assistant("结果如下", 4, reasoning="先看目录", tool="read_file"),
        {"type": "tool/call", "seq": 5, "time": 1_792_856_941_950,
         "data": {"turn": 1, "step": 1, "callId": "call-1", "name": "read_file",
                  "arguments": "{}"}},
        {"type": "tool/result", "seq": 6, "time": 1_792_856_941_960,
         "data": {"turn": 1, "step": 1, "message": {
             "role": "tool", "toolCallId": "call-1",
             "content": [{"type": "text", "text": "file body"}]}}},
    ]
    _write_session(dsh_home, events, sid, S.project_key(WORKDIR))

    history = S.parse_history(sid, WORKDIR)
    assert [block["role"] for block in history] == ["user", "thinking", "assistant", "tool"]
    assert history[0]["content"] == "请读文件"
    assert history[0]["nativeItemId"] == "u-1"
    assert history[1]["content"] == "先看目录"
    assert history[2]["content"] == "结果如下"
    assert history[3]["content"] == 'read_file({"path":"a.txt"})'
    # tool/result 不重复计入历史
    assert all("file body" not in str(block.get("content")) for block in history)


@requires_zstd
def test_parse_history_tolerates_single_frame_writer(dsh_home):
    sid = "session-55555555-5555-5555-5555-555555555555"
    _write_session(dsh_home, [_header(sid), _user("one frame", 1)], sid,
                   S.project_key(WORKDIR), multi_frame=False)
    assert S.parse_history(sid) == [
        {"role": "user", "content": "one frame", "nativeItemId": "msg-1"}]


def test_parse_history_missing_session(dsh_home):
    assert S.parse_history("session-missing") == []
    assert S.session_exists("session-missing") is False


# ── usage ──


@requires_zstd
def test_get_raw_usage_sums_assistant_messages(dsh_home):
    sid = "session-66666666-6666-6666-6666-666666666666"
    events = [
        _header(sid),
        {"type": "request/context", "seq": 1, "time": 1, "data": {"model": "deepseek-flash"}},
        _assistant("a", 2, usage={"inputTokens": 100, "outputTokens": 10,
                                  "cacheReadTokens": 40, "cacheWriteTokens": 5,
                                  "reasoningTokens": 2, "totalTokens": 110}),
        _assistant("b", 3, usage={"inputTokens": 50, "outputTokens": 5,
                                  "cacheReadTokens": 0, "totalTokens": 55}),
    ]
    _write_session(dsh_home, events, sid, S.project_key(WORKDIR))

    entries = S.get_raw_usage(sid, WORKDIR)
    assert len(entries) == 1
    raw = entries[0]["rawUsage"]
    # inputTokens 含缓存 → prompt = 150 - 40 - 5
    assert raw["prompt_tokens"] == 105
    assert raw["completion_tokens"] == 15
    assert raw["cache_read_tokens"] == 40
    assert raw["cache_write_tokens"] == 5
    assert raw["reasoning_tokens"] == 2
    assert raw["total_tokens"] == 165
    assert entries[0]["model"] == "deepseek-flash"


@requires_zstd
def test_get_raw_usage_falls_back_to_projcache(dsh_home):
    sid = "session-77777777-7777-7777-7777-777777777777"
    _write_session(dsh_home, [_header(sid), _assistant("no usage", 1)], sid,
                   S.project_key(WORKDIR))
    _write_projcache(dsh_home, sid, totals={
        "uncachedInputTokens": 20, "outputTokens": 4,
        "cacheReadTokens": 8, "cacheWriteTokens": 0})
    raw = S.get_raw_usage(sid, WORKDIR)[0]["rawUsage"]
    assert raw["prompt_tokens"] == 20
    assert raw["completion_tokens"] == 4
    assert raw["cache_read_tokens"] == 8


@requires_zstd
def test_get_raw_usage_skips_empty_sessions(dsh_home):
    sid = "session-88888888-8888-8888-8888-888888888888"
    _write_session(dsh_home, [_header(sid)], sid, S.project_key(WORKDIR))
    _write_projcache(dsh_home, sid)
    assert S.get_raw_usage(sid, WORKDIR) == []


# ── 标题 / 快照 ──


@requires_zstd
def test_get_session_title_priority(dsh_home):
    sid = "session-99999999-9999-9999-9999-999999999999"
    _write_session(dsh_home, [_header(sid), _user("first   real prompt", 1)],
                   sid, S.project_key(WORKDIR))
    assert S.get_session_title(sid, WORKDIR) == "first real prompt"

    _write_projcache(dsh_home, sid, title="cached")
    assert S.get_session_title(sid, WORKDIR) == "cached"


def test_write_custom_title_is_documented_noop(dsh_home):
    assert S.write_custom_title("session-x", "new name") is None


@requires_zstd
def test_reimport_snapshot_tracks_log_bytes(dsh_home):
    sid = "session-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    session_dir = _write_session(dsh_home, [_header(sid)], sid, S.project_key(WORKDIR))
    first = S.get_reimport_snapshot(sid, WORKDIR)
    assert first and len(first) == 64
    store = session_dir / "session.v4.jsonl.zstd"
    store.write_bytes(store.read_bytes() + b"extra")
    assert S.get_reimport_snapshot(sid, WORKDIR) != first
    assert S.get_reimport_snapshot("session-missing") is None


# ── fork ──


@requires_zstd
def test_fork_session_copies_history_and_rewrites_header(dsh_home):
    parent = "session-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    _write_session(dsh_home, [_header(parent), _user("keep me", 1),
                              _assistant("kept", 2)], parent, S.project_key(WORKDIR))
    _write_projcache(dsh_home, parent, title="parent title")

    child = S.fork_session(parent, "branch", WORKDIR)
    assert child.startswith("session-")
    assert child != parent

    child_dir = dsh_home / "sessions" / S.project_key(WORKDIR) / child
    assert (child_dir / "session.v4.jsonl.zstd").is_file()
    # 第一帧必须恰好是一行 header（DSH 的校验前提）
    imported = S._decompress((child_dir / "session.v4.jsonl.zstd").read_bytes())
    first_line = imported.split(b"\n")[0]
    header = json.loads(first_line)
    assert header["id"] == child
    assert header["forkedFrom"] == parent
    assert header["cwd"] == WORKDIR

    # 历史与父会话一致，且新 id 可被 provider 独立读取
    assert S.parse_history(child, WORKDIR) == S.parse_history(parent, WORKDIR)
    assert S.session_exists(child, WORKDIR) is True
    # 投影缓存按新 id 复制，列表/标题立即可用
    assert S.get_session_title(child, WORKDIR) == "parent title"


@requires_zstd
def test_fork_session_missing_parent(dsh_home):
    with pytest.raises(FileNotFoundError):
        S.fork_session("session-missing", "branch", WORKDIR)


@requires_zstd
def test_fork_session_rejects_corrupt_log(dsh_home):
    sid = "session-cccccccc-cccc-cccc-cccc-cccccccccccc"
    session_dir = dsh_home / "sessions" / S.project_key(WORKDIR) / sid
    session_dir.mkdir(parents=True)
    (session_dir / "session.v4.jsonl.zstd").write_bytes(b"not zstd")
    with pytest.raises(ValueError):
        S.fork_session(sid, "branch", WORKDIR)
    # 失败不得留下半成品目录
    leftovers = [p.name for p in (dsh_home / "sessions" / S.project_key(WORKDIR)).iterdir()
                 if p.name.startswith("session-") and p.name != sid]
    assert leftovers == []

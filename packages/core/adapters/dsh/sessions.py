"""DSH (DeepSeek Harness) session store reader/writer.

DSH 44.7.0 的原生存储（源码证据：``@deepseek-ai/dsh-session-persistence-jsonl``
与 ``@deepseek-ai/dsh-home-paths``）：

- 会话日志：``$DSH_HOME/sessions/<projectKey(cwd)>/<sessionId>/session.v4.jsonl.zstd``
  —— **多帧 zstd**：第一帧恰好是 header 行（``{"type":"session",...,"id":...}``），
  之后每个事件一帧，追加写。header 帧边界是 DSH 自己的校验项（改写时会报
  "first frame is not exactly one header line"）。
- 投影缓存：``$DSH_HOME/storages/session_projcache/sessions/<sessionId>.json``
  —— 普通 JSON，含 ``identity.cwd/createdAt``、``rows.title.val``、
  ``rows.tokenUsage.val.totals``。会按事件数/时间间隔刷新，因此**可能滞后**；
  只用于列表/标题的快速读取，权威数据仍来自会话日志。
- ``$DSH_HOME`` 默认 ``~/.dsh``；环境变量 ``DSH_HOME`` 可覆盖（实测生效）。

事件（写进日志的持久化词表，与 ``--json`` 投影词表不同）：

- ``session``（header）、``permission/preset``、``sandbox/mode``、``approval/policy``
- ``agent/inbox/spliced``、``turn/start``、``step/start``、``step/end``、``turn/end``
- ``system/message``（系统提示词快照）、``user/message``（含 runtime-context /
  skill reminder，``data.source.kind`` 区分）、``assistant/attempt``（可能是被丢弃
  的重试）、``assistant/message``（**提交版**，content 块 text/reasoning/tool-call）
- ``tool/call``、``tool/result``、``session/title``、``request/header``、
  ``request/context``

历史只取 *提交* 的 ``assistant/message``（被 retry 丢弃的 attempt 不进历史，与
DSH 自己的 ``--json`` 投影一致）；tool 调用以 assistant 消息里的 tool-call 块
呈现，``tool/result`` 不重复计入。

依赖：zstd 解压需要 ``zstandard``（本机 0.25.0）或 Python ≥ 3.14 的
``compression.zstd``。两者都缺失时，读取接口返回空列表并记录一次 warning——
adapter 的实时流不受影响（它只依赖 CLI 的 NDJSON）。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

_log = logging.getLogger(__name__)

_SESSION_LOG_NAME = "session.v4.jsonl.zstd"
_NO_CWD_KEY = "_no-cwd"
_MAX_FRAME_BYTES = 64 * 1024 * 1024

try:  # Python ≥ 3.14 自带 zstd
    from compression import zstd as _std_zstd  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    _std_zstd = None

try:
    import zstandard as _zstandard
except Exception:  # noqa: BLE001
    _zstandard = None


def zstd_available() -> bool:
    return _std_zstd is not None or _zstandard is not None


# ── 路径与命名 ──


def dsh_home() -> Path:
    """``$DSH_HOME``（默认 ``~/.dsh``）；每次调用重新读环境变量。"""
    configured = os.environ.get("DSH_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".dsh"


def sessions_root() -> Path:
    return dsh_home() / "sessions"


def projcache_root() -> Path:
    return dsh_home() / "storages" / "session_projcache" / "sessions"


def project_key(cwd: str | None) -> str:
    """DSH 的 projectKey 算法（逐 code unit 精确移植）。

    分隔符（``/ \\ :``）连续run 折叠成一个 ``-``；``[A-Za-z0-9._-]`` 之外的字符
    （含 ``~``）编码成 ``~XXXX``；结果包在 ``--…--`` 里，去掉前导 ``-``，
    截断到 251 字符；空 cwd 用 ``_no-cwd``。
    """
    if not cwd:
        return _NO_CWD_KEY
    readable = ""
    separator_run = False
    for ch in cwd:
        if ch in ("/", "\\", ":"):
            if not separator_run:
                readable += "-"
            separator_run = True
        elif ch != "~" and re.match(r"[A-Za-z0-9._-]", ch):
            readable += ch
            separator_run = False
        else:
            readable += "~" + format(ord(ch), "04X")
            separator_run = False
    readable = readable.lstrip("-") or "root"
    return f"--{readable[:251]}--"


def _norm_path(value: str | None) -> str:
    text = str(value or "")
    if not text:
        return ""
    if os.name == "nt":
        text = text.replace("/", "\\")
        text = re.sub(r"^\\+\?\\+", "", text)
    try:
        text = os.path.normpath(text)
    except (OSError, ValueError):
        pass
    return text.rstrip("\\/").casefold() if os.name == "nt" else text.rstrip("/")


def _cwd_matches(session_cwd: str | None, project_cwd: str | None) -> bool:
    left, right = _norm_path(session_cwd), _norm_path(project_cwd)
    if not right:
        return True
    return left == right


def _session_dir(session_id: str, cwd: str | None = None) -> Path | None:
    """定位 session 目录：先按 cwd 的 projectKey，再全量兜底。"""
    if not session_id:
        return None
    root = sessions_root()
    if cwd:
        candidate = root / project_key(cwd) / session_id
        if (candidate / _SESSION_LOG_NAME).is_file():
            return candidate
    try:
        for project in root.iterdir():
            if not project.is_dir():
                continue
            candidate = project / session_id
            if (candidate / _SESSION_LOG_NAME).is_file():
                return candidate
    except OSError:
        return None
    return None


def session_exists(session_id: str, cwd: str | None = None) -> bool:
    """SessionsProvider 可选能力：import guard 用的存在性检查。"""
    return _session_dir(session_id, cwd) is not None


# ── zstd 读取 ──


def _decompress(data: bytes) -> bytes:
    if _zstandard is not None:
        with _zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
            return reader.read()
    if _std_zstd is not None:
        return _std_zstd.decompress(data)
    raise RuntimeError("zstandard is required to read DSH session logs")


def _iter_events(path: Path) -> list[dict]:
    """读一个会话日志的全部事件（容忍尾部半帧/损坏帧）。"""
    if not zstd_available():
        _log.warning("dsh: no zstd backend; cannot read %s", path)
        return []
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        text = _decompress(raw).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 并发写入时尾部可能是半帧
        text = _decompress_frames(raw).decode("utf-8", errors="replace")
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _decompress_frames(data: bytes) -> bytes:
    """逐帧解压，遇到损坏/未写完的帧就停止（返回已解出的前缀）。"""
    if _zstandard is None:
        return b""
    dctx = _zstandard.ZstdDecompressor()
    chunks: list[bytes] = []
    rest = data
    while rest:
        obj = dctx.decompressobj()
        try:
            chunks.append(obj.decompress(rest, max_output_size=_MAX_FRAME_BYTES))
        except Exception:  # noqa: BLE001
            break
        rest = obj.unused_data or b""
    return b"".join(chunks)


def _iso(ts_ms) -> str:
    if not isinstance(ts_ms, (int, float)) or isinstance(ts_ms, bool):
        return ""
    try:
        return datetime.fromtimestamp(ts_ms / 1000).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


# ── 事件 → Pan 历史块 ──


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def _tool_block(name: str, arguments) -> dict:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"input": arguments}
    if arguments is None:
        arguments = {}
    try:
        body = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        body = str(arguments)
    return {"role": "tool", "content": f"{name or 'tool'}({body})"}


def _assistant_blocks(message: dict) -> list[dict]:
    blocks: list[dict] = []
    for part in message.get("content") or []:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            text = str(part.get("text") or "")
            if text:
                blocks.append({"role": "assistant", "content": text})
        elif ptype in ("reasoning", "thinking"):
            text = str(part.get("text") or "")
            if text:
                blocks.append({"role": "thinking", "content": text})
        elif ptype in ("tool-call", "tool_call", "tool_use"):
            blocks.append(_tool_block(str(part.get("name") or ""), part.get("arguments")
                                      if "arguments" in part else part.get("input")))
    return blocks


def _usage_of(event: dict) -> dict | None:
    data = event.get("data") or {}
    usage = data.get("usage")
    if isinstance(usage, dict):
        return usage
    stream = data.get("stream")
    if isinstance(stream, list):
        for chunk in reversed(stream):
            inner = chunk.get("chunk") if isinstance(chunk, dict) else None
            if isinstance(inner, dict) and isinstance(inner.get("usage"), dict):
                return inner["usage"]
    return None


# ── 会话列表 ──


def _projcache(session_id: str) -> dict:
    path = projcache_root() / f"{session_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _projcache_rows(cache: dict) -> dict:
    record = cache.get("record")
    rows = record.get("rows") if isinstance(record, dict) else None
    return rows if isinstance(rows, dict) else {}


def _projcache_value(cache: dict, key: str):
    row = _projcache_rows(cache).get(key)
    return row.get("val") if isinstance(row, dict) else None


def _count_messages(events: list[dict]) -> int:
    count = 0
    for event in events:
        etype = event.get("type")
        if etype == "user/message":
            source = (event.get("data") or {}).get("source") or {}
            if isinstance(source, dict) and source.get("kind") == "user":
                count += 1
        elif etype == "assistant/message":
            count += 1
    return count


def _session_meta(session_dir: Path, events: list[dict] | None = None) -> dict | None:
    """会话元数据：日志是权威来源，projcache 只做读不到日志时的兜底。

    标题取**最后一条** ``session/title``（DSH 自己的 ``foldSessionTitle`` 也是
    ``findLast``）；因此 Pan 追加的自定义标题会立刻出现在列表里，不必等 DSH 重写
    投影缓存。
    """
    session_id = session_dir.name
    log = session_dir / _SESSION_LOG_NAME
    if events is None:
        events = _iter_events(log)
    if not events:
        # 读过不日志（缺 zstd 后端/日志损坏/尚在写入）：宁可不列这一行，也不
        # 在导入列表里给出没有标题和工作目录的空壳。
        return None
    cache = _projcache(session_id)
    identity = {}
    record = cache.get("record")
    if isinstance(record, dict) and isinstance(record.get("identity"), dict):
        identity = record["identity"]

    created = identity.get("createdAt")
    cwd = identity.get("cwd")
    title = ""
    model = ""
    for event in events:
        etype = event.get("type")
        if etype == "session":
            cwd = cwd or event.get("cwd")
            created = created or event.get("createdAt")
        elif etype == "session/title":
            value = (event.get("data") or {}).get("title")
            if isinstance(value, str) and value.strip():
                title = value  # 最后一条胜出（用户标题会 pin 住自动标题）
        elif etype in ("request/context", "request/header"):
            data = event.get("data") or {}
            config = (data.get("header") or {}).get("config") if isinstance(
                data.get("header"), dict) else None
            model = model or str((config or {}).get("model")
                                 or data.get("model") or "")
    if not title:
        cached = _projcache_value(cache, "title")
        title = cached if isinstance(cached, str) else ""
    if not model:
        rows = _projcache_rows(cache)
        row = rows.get("model") if isinstance(rows.get("model"), dict) else {}
        model = str(row.get("val") or "")
    try:
        updated = log.stat().st_mtime
    except OSError:
        updated = None
    return {
        "session_id": session_id,
        "parent_id": "",
        "title": str(title or ""),
        "workDir": str(cwd or ""),
        "createdAt": _iso(created),
        "updatedAt": _iso(updated * 1000) if updated else "",
        "message_count": _count_messages(events),
        "model": model,
    }


def list_sessions(cwd: str | None = None) -> list[dict]:
    """列原生 DSH 会话（可按 cwd 过滤）。

    ``cwd`` 为空时返回所有项目目录下的会话；给出 cwd 时按 projectKey 精确匹配
    目录名（DSH 自己的分组语义），并用日志/缓存里的真实 cwd 复核。
    """
    root = sessions_root()
    rows: list[dict] = []
    try:
        projects = [root / project_key(cwd)] if cwd else list(root.iterdir())
    except OSError:
        return []
    for project in projects:
        if not project.is_dir():
            continue
        try:
            session_dirs = [child for child in project.iterdir() if child.is_dir()]
        except OSError:
            continue
        for session_dir in session_dirs:
            if not (session_dir / _SESSION_LOG_NAME).is_file():
                continue
            meta = _session_meta(session_dir)
            if meta is None:
                continue
            if cwd and not _cwd_matches(meta.get("workDir"), cwd):
                continue
            rows.append(meta)
    rows.sort(key=lambda row: row.get("updatedAt") or "", reverse=True)
    return rows


# ── 历史解析 ──


def parse_history(session_id: str, cwd: str | None = None) -> list[dict]:
    """解析会话历史（只取提交过的消息；不含运行时上下文快照）。"""
    session_dir = _session_dir(session_id, cwd)
    if session_dir is None:
        return []
    history: list[dict] = []
    for event in _iter_events(session_dir / _SESSION_LOG_NAME):
        etype = event.get("type")
        data = event.get("data") or {}
        if not isinstance(data, dict):
            continue
        if etype == "user/message":
            source = data.get("source") or {}
            # 运行时上下文 / skill reminder 也是 user/message，但不是人说的话。
            if not isinstance(source, dict) or source.get("kind") != "user":
                continue
            text = _text_of(data.get("content"))
            if not text:
                continue
            block = {"role": "user", "content": text}
            if data.get("id"):
                block["nativeItemId"] = str(data["id"])
            history.append(block)
        elif etype == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            blocks = _assistant_blocks(message)
            native = message.get("id") or data.get("id") or event.get("seq")
            for block in blocks:
                if native is not None and "nativeItemId" not in block:
                    block["nativeItemId"] = str(native)
                history.append(block)
    return history


# ── usage ──


def get_raw_usage(session_id: str, cwd: str | None = None) -> list[dict]:
    """聚合会话级 token usage（单元素列表，与 codex/opencode provider 同构）。

    优先用会话日志里的 ``assistant/message.data.usage`` 求和（权威、进程退出前
    落盘）；日志缺 usage 时回退投影缓存的 ``tokenUsage.totals``（可能滞后）。
    """
    session_dir = _session_dir(session_id, cwd)
    totals = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0,
              "cacheWriteTokens": 0, "reasoningTokens": 0, "totalTokens": 0}
    model = ""
    timestamp = ""
    found = False
    native = None
    if session_dir is not None:
        for event in _iter_events(session_dir / _SESSION_LOG_NAME):
            etype = event.get("type")
            if etype == "request/context":
                model = str((event.get("data") or {}).get("model") or model)
            if etype != "assistant/message":
                continue
            usage = _usage_of(event)
            if not isinstance(usage, dict):
                continue
            found = True
            for key in totals:
                value = usage.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                totals[key] += int(value)
            timestamp = _iso(event.get("time")) or timestamp
    if not found:
        cache = _projcache(session_id)
        snapshot = _projcache_value(cache, "tokenUsage") or {}
        native = snapshot.get("totals") if isinstance(snapshot, dict) else None
        if isinstance(native, dict):
            found = True
            # 投影缓存里已经是 uncachedInputTokens（不含缓存），直接当 prompt 用。
            totals["inputTokens"] = int(native.get("uncachedInputTokens") or 0)
            totals["outputTokens"] = int(native.get("outputTokens") or 0)
            totals["cacheReadTokens"] = int(native.get("cacheReadTokens") or 0)
            totals["cacheWriteTokens"] = int(native.get("cacheWriteTokens") or 0)
        if not model:
            record = cache.get("record") if isinstance(cache.get("record"), dict) else {}
            rows = record.get("rows") if isinstance(record.get("rows"), dict) else {}
            row = rows.get("model") if isinstance(rows.get("model"), dict) else {}
            model = str(row.get("val") or "")
    if not found:
        return []
    cache_read = int(totals["cacheReadTokens"])
    cache_write = int(totals["cacheWriteTokens"])
    if native is not None and not any(
            totals[key] for key in ("reasoningTokens", "totalTokens")):
        # 投影缓存给出的就是 uncached 输入，无需再减缓存。
        prompt = int(totals["inputTokens"])
    else:
        prompt = max(0, int(totals["inputTokens"]) - cache_read - cache_write)
    completion = int(totals["outputTokens"])
    total = int(totals["totalTokens"]) or (prompt + cache_read + cache_write + completion)
    if not any((prompt, completion, cache_read, cache_write, total,
                int(totals["reasoningTokens"]))):
        # 只有空投影/失败 turn 时不要上报一条全 0 的 usage（会污染账本）。
        return []
    return [{
        "model": model,
        "rawUsage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "reasoning_tokens": int(totals["reasoningTokens"]),
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "total_tokens": total,
        },
        "timestamp": timestamp,
    }]


def get_reimport_snapshot(session_id: str, cwd: str | None = None) -> str | None:
    """import/reimport 一致性票据：整个日志文件的 sha256（含非正文事件）。"""
    session_dir = _session_dir(session_id, cwd)
    if session_dir is None:
        return None
    try:
        return hashlib.sha256((session_dir / _SESSION_LOG_NAME).read_bytes()).hexdigest()
    except OSError:
        return None


# ── 标题 ──

# DSH `session-title` 的默认 maxTitleBytes（headless profile 未覆盖）。
_TITLE_MAX_BYTES = 80

# 与 dsh-session-title 的 cleanTitleText 对齐：剥掉 OSC/CSI/ESC/控制字符与方向性
# 不可见字符，再把空白折叠成单行。
_OSC_SEQUENCE = re.compile(r"(?:\x1b\]|\x9d)(?:(?!\x07|\x1b\\)[\s\S])*(?:\x07|\x1b\\|$)")
_CSI_SEQUENCE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_ESC_SEQUENCE = re.compile(r"\x1b[@-_]")
_CONTROL_CHARACTER = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
_DIRECTIONAL_CONTROL = re.compile(
    "[\u200b\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]")


def normalize_title(title: str) -> str:
    """把任意标题归一成 DSH 接受的单行 UTF-8 预算内文本。"""
    text = str(title or "")
    for pattern in (_OSC_SEQUENCE, _CSI_SEQUENCE, _ESC_SEQUENCE,
                    _CONTROL_CHARACTER, _DIRECTIONAL_CONTROL):
        text = pattern.sub("", text)
    text = " ".join(text.split()).strip()
    encoded = text.encode("utf-8")
    if len(encoded) <= _TITLE_MAX_BYTES:
        return text
    return encoded[:_TITLE_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()


def _last_event_meta(events: list[dict]) -> tuple[int, int]:
    """返回 ``(last_seq, last_time_ms)``，用于派生新事件的 seq/time。"""
    last_seq = -1
    last_time = 0
    for event in events:
        seq = event.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq > last_seq:
            last_seq = seq
        stamp = event.get("time")
        if isinstance(stamp, int) and not isinstance(stamp, bool) and stamp > last_time:
            last_time = stamp
    return last_seq, last_time


def _tail_meta(lines: list[str]) -> tuple[int, int]:
    """从原始 JSONL 行尾部找最新的 ``(seq, time)``（fork 时派生新事件用）。"""
    last_seq = -1
    last_time = 0
    for raw in reversed(lines):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        seq = event.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq > last_seq:
            last_seq = seq
        stamp = event.get("time")
        if isinstance(stamp, int) and not isinstance(stamp, bool) and stamp > last_time:
            last_time = stamp
        if last_seq >= 0:
            break
    return last_seq, last_time


def _append_events(path: Path, events: list[dict]) -> bool:
    """按 DSH 的「一行一帧」方式追加事件（只追加，不重写既有字节）。

    先做一次「读到的字节数没变」检查，缩小与 DSH 自身追加写并发的窗口；帧本身
    是一次 append 写，不会截断既有内容。
    """
    if not events:
        return False
    payload = _compress_frames([json.dumps(event, ensure_ascii=False) for event in events])
    try:
        size_before = path.stat().st_size
        with open(path, "ab") as handle:
            if handle.tell() != size_before:
                return False
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return False
    return True


def get_session_title(session_id: str, cwd: str | None = None) -> str:
    """标题：日志里最后一条 ``session/title`` > 投影缓存 > 首条用户消息。

    日志是权威来源（DSH 的 ``foldSessionTitle`` 也是取最后一条，Pan 追加的
    自定义标题会覆盖自动标题），投影缓存只在读不到日志时兜底。
    """
    session_dir = _session_dir(session_id, cwd)
    first_user = ""
    logged_title = ""
    if session_dir is not None:
        for event in _iter_events(session_dir / _SESSION_LOG_NAME):
            etype = event.get("type")
            data = event.get("data") or {}
            if etype == "session/title":
                title = data.get("title")
                if isinstance(title, str) and title.strip():
                    # 最后一条胜出：自动标题在前，用户重命名在后。
                    logged_title = title.strip()
            elif etype == "user/message" and not first_user:
                source = data.get("source") or {}
                if isinstance(source, dict) and source.get("kind") == "user":
                    first_user = _text_of(data.get("content"))
    if logged_title:
        return logged_title
    cache = _projcache(session_id)
    title = _projcache_value(cache, "title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return " ".join(first_user.split())[:80]


def write_custom_title(session_id: str, title: str, cwd: str | None = None) -> None:
    """把 Pan 的会话名写成 DSH 原生标题（best-effort）。

    做法与 DSH 自己的 ``SessionTitleService.rename()`` 完全一致：追加一条
    ``session/title`` 事件，``source.kind = "user"`` 且 ``messageSeqs = []``。
    这会把标题「钉住」（pin）：DSH 的自动标题生成会让位，后续用户消息也不再重新
    生成。实测（2026-10-24，DSH 44.7.0）：

    - 追加后 ``dsh headless --session-id`` 正常 resume（turn:2），没有报日志损坏；
    - DSH 重写投影缓存后 ``storages/session_projcache/sessions/<sid>.json`` 的
      ``title`` 变成新值 —— 说明它接受这条事件并据此重投影，而不是覆盖它。

    仍然保留 best-effort 语义：找不到会话、归一化后为空、zstd 不可用、或写入
    失败时静默返回（Pan 侧的重命名已经生效，不该因为原生标题失败而报错）。
    并发风险：DSH 会话日志是单写者追加文件，若此时恰好有 DSH 进程在同一会话里
    写事件，理论上可能交错；``_append_events`` 只做「原文件大小未变」的廉价检查，
    不做跨进程锁（DSH 本身也不加锁）。因此调用点应在会话空闲时使用。
    """
    normalized = normalize_title(title)
    if not normalized:
        return
    session_dir = _session_dir(session_id, cwd)
    if session_dir is None or not zstd_available():
        return
    path = session_dir / _SESSION_LOG_NAME
    events = _iter_events(path)
    if not events:
        return
    # 已经是同标题就不追加（DSH rename 语义幂等化）。
    for event in reversed(events):
        if event.get("type") == "session/title":
            if (event.get("data") or {}).get("title") == normalized:
                return
            break
    last_seq, last_time = _last_event_meta(events)
    now_ms = int(time.time() * 1000)
    event = {
        "type": "session/title",
        "seq": max(last_seq + 1, 0),
        "time": max(now_ms, last_time),
        "data": {"title": normalized, "messageSeqs": [], "source": {"kind": "user"}},
    }
    _append_events(path, [event])


# ── fork ──


def _compress_frames(lines: list[str]) -> bytes:
    payload = [line.encode("utf-8") + b"\n" for line in lines]
    if _zstandard is not None:
        compressor = _zstandard.ZstdCompressor()
        return b"".join(compressor.compress(chunk) for chunk in payload)
    if _std_zstd is not None:
        return b"".join(_std_zstd.compress(chunk) for chunk in payload)
    raise RuntimeError("zstandard is required to fork a DSH session")


def fork_session(parent_id: str, name: str, cwd: str | None = None) -> str:
    """复制原生会话：新 ``session-<uuid>`` 目录 + header 改写 + 逐帧重写。

    实测依据：DSH 只校验"第一帧恰好是一行 header"，因此按"一行一帧"重写既有
    日志即可被 ``--session-id`` 采用（本机验证：复制后 turn 号继续递增，说明
    历史被完整读取）。新会话会额外追加一条 ``session/title``（user 来源），把
    DSH 原生标题钉成 Pan 的分支名（与 ``write_custom_title`` 同一机制）。
    """
    parent_dir = _session_dir(parent_id, cwd)
    if parent_dir is None:
        raise FileNotFoundError(f"DSH session not found: {parent_id}")
    if not zstd_available():
        raise RuntimeError("zstandard is required to fork a DSH session")
    try:
        text = _decompress((parent_dir / _SESSION_LOG_NAME).read_bytes())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid DSH session log for {parent_id}: {exc}") from exc
    lines = [line for line in text.decode("utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"empty DSH session log for {parent_id}")
    header = json.loads(lines[0])
    if header.get("type") != "session":
        raise ValueError(f"DSH session log for {parent_id} has no session header")
    new_id = f"session-{uuid.uuid4()}"
    header["id"] = new_id
    header["forkedFrom"] = parent_id
    lines[0] = json.dumps(header, ensure_ascii=False)

    title = normalize_title(name)
    if title:
        last_seq, last_time = _tail_meta(lines)
        lines.append(json.dumps({
            "type": "session/title",
            "seq": max(last_seq + 1, 0),
            "time": max(int(time.time() * 1000), last_time),
            "data": {"title": title, "messageSeqs": [], "source": {"kind": "user"}},
        }, ensure_ascii=False))

    new_dir = parent_dir.parent / new_id
    new_dir.mkdir(parents=True, exist_ok=True)
    (new_dir / _SESSION_LOG_NAME).write_bytes(_compress_frames(lines))

    # 投影缓存按 session id 命名；复制一份让列表/标题立即可读（缺失也无害）。
    source_cache = projcache_root() / f"{parent_id}.json"
    target_cache = projcache_root() / f"{new_id}.json"
    try:
        if source_cache.is_file() and not target_cache.exists():
            target_cache.parent.mkdir(parents=True, exist_ok=True)
            target_cache.write_bytes(source_cache.read_bytes())
    except OSError:
        pass
    return new_id


# ── SessionsProvider 统一接口（adapter-architecture P0-2） ──
# 与 cbc/kimi/opencode/claude/codex 的 sessions 模块对齐协议命名。

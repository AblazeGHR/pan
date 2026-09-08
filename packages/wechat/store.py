"""微信通道的全部落盘 —— 单一出入口。

目录布局（默认在项目根 data/ 下，测试可用 PAN_WECHAT_DATA_DIR 或
set_data_root() 重定向）::

    data/wechat_history/<target_id>.json   对话记录（user + assistant 双侧）
    data/wechat_inbox/<target_id>.json     待处理队列（selective 模式）
    data/wechat_outbox/<target_id>.json    发送失败缓冲（无 context_token 时）
    data/wechat/bot_token.json             登录凭证
    data/wechat/context_tokens.json        每个用户最近的 context_token
    data/wechat/login_qrcode.txt           当前登录二维码 URL（供人取用）

全部写操作走 :func:`_atomic_write_json`（tmp + os.replace），避免进程在
write_text 中途被杀留下截断的 JSON —— 这是 QQ 版 ``plugin.py`` 直接
``write_text`` 的缺陷，微信版不再重犯。

target_id 一律经 :func:`sanitize` 消毒，杜绝目录穿越。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# 落盘条数上限（超出保留最新部分）
HISTORY_MAX_ENTRIES = 500
INBOX_MAX_ENTRIES = 500
#: outbox 是「发不出去的缓冲」，不宜堆积过多
OUTBOX_MAX_ENTRIES = 20
#: outbox 条目存活时间（秒）——超时丢弃，避免 stale 消息隔天突然发出去
OUTBOX_TTL_SEC = 24 * 3600

#: context_token 软有效期：超期仍会尝试使用（失败才算失效），仅用于状态展示
CONTEXT_TOKEN_TTL_SEC = 24 * 3600

_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-]")


# ── 根目录 ──

_DATA_ROOT: Path | None = None


def data_root() -> Path:
    """数据根目录：PAN_WECHAT_DATA_DIR > set_data_root() > 项目根 data/。"""
    global _DATA_ROOT
    if _DATA_ROOT is None:
        env = os.environ.get("PAN_WECHAT_DATA_DIR")
        _DATA_ROOT = Path(env) if env else Path(__file__).resolve().parents[2] / "data"
    return _DATA_ROOT


def set_data_root(path: str | Path) -> None:
    """重定向数据根（测试用）。传 None 之外的值即固定，不再读环境变量。"""
    global _DATA_ROOT
    _DATA_ROOT = Path(path)


def sanitize(target_id: str | int) -> str:
    """把 target_id 消毒成安全文件名片段（防目录穿越）。"""
    return _SAFE_RE.sub("_", str(target_id))[:128] or "_"


# ── 原子写 ──


def _atomic_write_json(path: Path, payload) -> None:
    """写 JSON：先写 .tmp 再 os.replace，保证读到的永远是完整文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    os.replace(tmp, path)


def _load_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) else default


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── 路径 ──


def history_path(target_id: str | int) -> Path:
    return data_root() / "wechat_history" / f"{sanitize(target_id)}.json"


def inbox_path(target_id: str | int) -> Path:
    return data_root() / "wechat_inbox" / f"{sanitize(target_id)}.json"


def outbox_path(target_id: str | int) -> Path:
    return data_root() / "wechat_outbox" / f"{sanitize(target_id)}.json"


def credential_path() -> Path:
    return data_root() / "wechat" / "bot_token.json"


def context_tokens_path() -> Path:
    return data_root() / "wechat" / "context_tokens.json"


def login_qrcode_path() -> Path:
    return data_root() / "wechat" / "login_qrcode.txt"


# ── history ──


def load_history(target_id: str | int) -> list[dict]:
    return _load_json(history_path(target_id), [])


def append_history(
    target_id: str | int, role: str, text: str, extra: dict | None = None
) -> dict:
    """追加一条对话记录（role: user / assistant），返回写入的 entry。"""
    entry = {"role": role, "text": text, "time": _now()}
    if extra:
        entry.update(extra)
    messages = load_history(target_id)
    messages.append(entry)
    if len(messages) > HISTORY_MAX_ENTRIES:
        messages = messages[-HISTORY_MAX_ENTRIES:]
    _atomic_write_json(history_path(target_id), messages)
    return entry


# ── inbox（selective 模式待处理队列，FIFO）──


def load_inbox(target_id: str | int) -> list[dict]:
    return _load_json(inbox_path(target_id), [])


def append_inbox(
    target_id: str | int, text: str, scope: str = "user", nickname: str = ""
) -> dict:
    """追加一条待处理消息，返回写入的 entry（含 id，供 consume 后核对）。"""
    messages = load_inbox(target_id)
    entry = {
        "id": f"{int(time.time() * 1000)}-{len(messages)}",
        "target_id": str(target_id),
        "scope": scope,
        "role": "user",
        "text": text,
        "time": _now(),
    }
    if nickname:
        entry["nickname"] = nickname
    messages.append(entry)
    if len(messages) > INBOX_MAX_ENTRIES:
        messages = messages[-INBOX_MAX_ENTRIES:]
    _atomic_write_json(inbox_path(target_id), messages)
    return entry


def take_inbox(target_id: str | int, limit: int) -> list[dict]:
    """取最前 limit 条（FIFO），但**不删除**——删除由 consume 显式触发。"""
    return load_inbox(target_id)[:limit]


def consume_inbox(target_id: str | int, limit: int) -> list[dict]:
    """取最前 limit 条并回写剩余（消费即删）。"""
    messages = load_inbox(target_id)
    take, rest = messages[:limit], messages[limit:]
    _atomic_write_json(inbox_path(target_id), rest)
    return take


def clear_inbox(target_id: str | int) -> bool:
    """清空某会话的 inbox，返回是否曾存在文件。"""
    path = inbox_path(target_id)
    existed = path.exists()
    try:
        path.unlink()
    except OSError:
        return existed
    return existed


# ── outbox（无 context_token 时的发送缓冲）──


def load_outbox(target_id: str | int) -> list[dict]:
    """读出缓冲条目并**顺带丢弃过期项**（TTL 24h），避免隔天补发陈旧内容。"""
    messages = _load_json(outbox_path(target_id), [])
    fresh = [
        m for m in messages
        if isinstance(m, dict)
        and time.time() - float(m.get("ts") or 0) < OUTBOX_TTL_SEC
    ]
    if len(fresh) != len(messages):
        _atomic_write_json(outbox_path(target_id), fresh)
    return fresh


def append_outbox(target_id: str | int, text: str) -> int:
    """缓冲一条发不出去的消息，返回当前积压条数。"""
    messages = load_outbox(target_id)
    messages.append({"text": text, "time": _now(), "ts": time.time(), "tries": 0})
    if len(messages) > OUTBOX_MAX_ENTRIES:
        # 丢弃最旧的，保留最新一批（用户刚说的才是相关的）
        messages = messages[-OUTBOX_MAX_ENTRIES:]
    _atomic_write_json(outbox_path(target_id), messages)
    return len(messages)


def clear_outbox(target_id: str | int) -> int:
    """清空缓冲，返回被丢弃的条数。"""
    count = len(load_outbox(target_id))
    try:
        outbox_path(target_id).unlink()
    except OSError:
        pass
    return count


# ── context token（iLink 核心：回复必须回传它）──


class ContextTokenStore:
    """每个用户最近的 context_token，落盘持久化（跨重启保留）。

    iLink 不允许主动推送：bot 只能在用户发来消息后，用该消息带来的
    ``context_token`` 回复。这里缓存「每个用户最近一次上行消息的 token」，
    让 selective 模式下的异步回复也能发出去。

    软 TTL：超期**仍会返回**（照常尝试发送），只是 :meth:`is_stale` 为真，
    供状态展示与「很可能发不出去」的预判使用。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or context_tokens_path()

    def _all(self) -> dict:
        data = _load_json(self._path, {})
        return data if isinstance(data, dict) else {}

    def get(self, user_id: str | int) -> str:
        entry = self._all().get(str(user_id))
        return str(entry.get("token", "")) if isinstance(entry, dict) else ""

    def set(self, user_id: str | int, token: str) -> None:
        """覆盖该用户的最新 token（每次收到上行消息时调用）。"""
        if not token:
            return
        data = self._all()
        data[str(user_id)] = {"token": str(token), "ts": time.time()}
        _atomic_write_json(self._path, data)

    def is_stale(self, user_id: str | int) -> bool:
        entry = self._all().get(str(user_id))
        if not isinstance(entry, dict):
            return True
        return time.time() - float(entry.get("ts") or 0) >= CONTEXT_TOKEN_TTL_SEC

    def all_ids(self) -> list[str]:
        return list(self._all().keys())


# ── 登录凭证 ──


def save_credential(payload: dict) -> None:
    """落盘登录凭证（bot_token / baseurl / bot_id）。"""
    _atomic_write_json(credential_path(), payload)


def load_credential() -> dict:
    data = _load_json(credential_path(), {})
    return data if isinstance(data, dict) else {}


def clear_credential() -> None:
    try:
        credential_path().unlink()
    except OSError:
        pass


def save_login_qrcode(url: str) -> None:
    """把当前二维码 URL 落盘（终端不可见时供人取用）。"""
    path = login_qrcode_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(url), encoding="utf-8")


def clear_login_qrcode() -> None:
    try:
        login_qrcode_path().unlink()
    except OSError:
        pass

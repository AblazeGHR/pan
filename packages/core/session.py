"""Session store — persistent, UUID-keyed, independent of Worker lifecycle.

概念模型（agent-naming 确立）：Session = Agent —— 即逻辑编排对象：持久身份
（收件箱 queue_pending、agentLevel、managedBy 链）都在这里；Worker（worker.py）
只是本 Session 名下临时的 CLI 进程实例，可随时重建。外部编排语义（MCP 的
agent_* 工具、/api/send）以 Session 为寻址目标。

Each session is stored as data/sessions/<id>.json.
The ID format is ses_<16-hex-chars> (e.g. ses_a1b2c3d4e5f67890).
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

SESSION_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sessions"

# 增量持久化（方案 4）：history 落盘到独立的 <id>.history.jsonl 追加文件，
# 不再每次全量重写主文件。主文件只含元数据 + 尾部 history（供人工查看/旧读者
# 兼容），jsonl 存在时加载一律以 jsonl 为 history 权威来源。
_MAIN_HISTORY_TAIL = 20          # 主文件内保留的尾部 history 条数（常量开销）
_SAVE_LOCK = threading.Lock()    # 跨线程写锁：save_async(to_thread) 并发时防双写
_newline_terminated_jsonl: set[str] = set()  # 进程内已知以 \n 结尾的 jsonl 路径（热路径跳过探测）


def _path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def _history_path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.history.jsonl"


def _encode_line(item: dict) -> bytes:
    return (json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            + "\n").encode("utf-8")


def _write_jsonl(path: Path, items: list[dict]):
    """整文件重写（截断 + 全量写入），迁移 / force_full / 首次创建时用。"""
    with open(path, "wb") as f:
        for it in items:
            f.write(_encode_line(it))
        f.flush()
    _newline_terminated_jsonl.add(str(path))  # 全量写必然以 \n 结尾


def _append_jsonl(path: Path, items: list[dict]):
    """追加写，写后 flush——热路径只追加，O(new entries)。

    崩溃恢复：文件不以换行结尾（上次 append 中断的半行）时先补一个换行，
    避免后续新记录粘在损坏半行上一起丢。ab 模式不支持读（无法探测末字节）。

    性能：Windows 上"读末字节探测 + 写"两次 open 开销极大（实测 5-7ms），
    而纯 ab 追加仅 0.3ms。故用进程内集合 _newline_terminated_jsonl 缓存
    "文件已知以换行结尾"——本进程每次写入都带换行，正常路径无需重复探测。
    冷路径（首次写 / 崩溃重启后 / 文件被外部改写）才探测一次并修复半行。
    """
    if not items:
        return
    key = str(path)
    if key in _newline_terminated_jsonl:
        with open(path, "ab") as f:
            for it in items:
                f.write(_encode_line(it))
            f.flush()
        return
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            need_newline = False
            if size > 0:
                f.seek(size - 1)
                need_newline = f.read(1) != b"\n"
    except FileNotFoundError:
        _write_jsonl(path, items)  # 首次创建：无既有半行风险
        _newline_terminated_jsonl.add(key)
        return
    with open(path, "ab") as f:
        if need_newline:
            f.write(b"\n")
        for it in items:
            f.write(_encode_line(it))
        f.flush()
    _newline_terminated_jsonl.add(key)


def _read_jsonl(path: Path) -> list[dict]:
    """读 jsonl 追加文件。损坏行（崩溃半行）跳过、其后的有效行继续读取，
    最大程度恢复已提交记录。"""
    out: list[dict] = []
    try:
        with open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line.decode("utf-8")))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # 损坏行 = 上次 append 崩溃留下的半行 → 文件不再以 \n 结尾，
                    # 使 _append_jsonl 的"以换行结尾"缓存失效（下次写会重新探测补换行）。
                    _newline_terminated_jsonl.discard(str(path))
                    continue
    except OSError:
        pass
    return out


def _new_id() -> str:
    return "ses_" + secrets.token_hex(8)


# 存量清理：旧版投递对账把 `[delivered: task/report:<id>:<12位指纹>]` 作为独立
# 文本行注入消息正文（fix/delivery-mark 起改为 history 条目的 delivered_keys
# 元数据，见 packages/core/worker.py）。加载时剥离，避免旧消息继续在 UI / 上下
# 文中显示前缀。整行精确匹配（含 12 位十六进制指纹）才剥离——真实用户消息恰为
# 该格式独立行的概率可忽略，不误删。
_DELIVERY_MARK_LINE_RE = re.compile(
    r"^\[delivered: (?:task|report):[^\]\n]*:[0-9a-f]{12}\]$")


def _strip_delivery_marks(history: list[dict]) -> list[dict]:
    """剥离 history 条目 content 中的旧版 `[delivered: ...]` 标记行（就地修改）。"""
    for h in history:
        content = h.get("content")
        if not isinstance(content, str) or "[delivered:" not in content:
            continue
        lines = [ln for ln in content.split("\n")
                 if not _DELIVERY_MARK_LINE_RE.match(ln)]
        h["content"] = "\n".join(lines).lstrip("\n")
    return history


# The three capability flags are stored nested under ``pan_access``. Old JSON
# wrote them as top-level fields; migration lives in _from_data / __post_init__.
_PAN_ACCESS_KEYS = ("restrict_to_managed", "can_claim_unmanaged", "auto_claim_created")


@dataclass(init=False)
class Session:
    id: str
    name: str
    adapter: str = "cbc"   # CLI adapter name, default "cbc"
    model: str | None = None
    permission_mode: str | None = None
    pan_access: dict = field(default_factory=dict)  # capability flags, nested (restrict_to_managed/can_claim_unmanaged/auto_claim_created)
    adapter_config: dict = field(default_factory=dict)  # adapter-specific settings
    character_id: str | None = None   # bound character ID (for memory + assets)
    session_template: str | None = None  # session_template name this session was configured with (None = built-in default)
    system_prompt: str | None = None  # injected at Worker spawn
    game_id: str | None = None        # RuleWhisper game identifier for MCP tool calls
    raw_usage: dict | None = None
    total_usage: dict | None = None
    workdir: str = ""
    history: list[dict] = field(default_factory=list)
    last_result: dict | None = None
    created_at: str = ""
    updated_at: str = ""
    managed: list[str] = field(default_factory=list)  # session ids this session manages
    managed_by: str | None = None  # session id of the session managing this one
    queue_pending: list = field(default_factory=list)  # persisted message queue (for report consumption)
    report_subscriptions: set[str] = field(default_factory=set)  # managed sessions whose completion reports this session subscribes to
    qq_subscriptions: set[str] = field(default_factory=set)  # QQ conversations this session subscribes to ("user:<qq>"/"group:<group_id>")

    # ── adapter_config convenience accessors ──

    @property
    def cli_session_id(self) -> str | None:
        """Adapter-native session ID (for --resume, --continue, etc.)."""
        return self.adapter_config.get("cli_session_id")

    @cli_session_id.setter
    def cli_session_id(self, value: str | None):
        if value:
            self.adapter_config["cli_session_id"] = value
        else:
            self.adapter_config.pop("cli_session_id", None)

    def __init__(self, id: str, name: str, adapter: str = "cbc",
                 model: str | None = None, permission_mode: str | None = None,
                 pan_access: dict | None = None,
                 restrict_to_managed: bool | None = None,
                 can_claim_unmanaged: bool | None = None,
                 auto_claim_created: bool | None = None,
                 adapter_config: dict | None = None,
                 character_id: str | None = None,
                 session_template: str | None = None,
                 system_prompt: str | None = None,
                 game_id: str | None = None,
                 raw_usage: dict | None = None,
                 total_usage: dict | None = None,
                 workdir: str = "",
                 history: list[dict] | None = None,
                 last_result: dict | None = None,
                 created_at: str = "",
                 updated_at: str = "",
                 managed: list[str] | None = None,
                 managed_by: str | None = None,
                 queue_pending: list | None = None,
                 report_subscriptions=None,
                 qq_subscriptions=None):
        """Manual init so legacy top-level capability kwargs still construct.

        ``pan_access`` is the single source of truth for the three capability
        flags; the old flat kwargs (``restrict_to_managed`` etc.) are merged in
        for backward compatibility and win over a pre-built ``pan_access``.
        """
        self.id = id
        self.name = name
        self.adapter = adapter
        self.model = model
        self.permission_mode = permission_mode
        pa = dict(pan_access) if pan_access else {}
        if restrict_to_managed is not None:
            pa["restrict_to_managed"] = restrict_to_managed
        if can_claim_unmanaged is not None:
            pa["can_claim_unmanaged"] = can_claim_unmanaged
        if auto_claim_created is not None:
            pa["auto_claim_created"] = auto_claim_created
        self.pan_access = pa
        self.adapter_config = adapter_config if adapter_config is not None else {}
        self.character_id = character_id
        self.session_template = session_template
        self.system_prompt = system_prompt
        self.game_id = game_id
        self.raw_usage = raw_usage
        self.total_usage = total_usage
        self.workdir = workdir
        self.history = history if history is not None else []
        self.last_result = last_result
        self.created_at = created_at
        self.updated_at = updated_at
        self.managed = managed if managed is not None else []
        self.managed_by = managed_by
        self.queue_pending = queue_pending if queue_pending is not None else []
        self.report_subscriptions = report_subscriptions if report_subscriptions is not None else set()
        self.qq_subscriptions = qq_subscriptions if qq_subscriptions is not None else set()
        self.__post_init__()

    # ── pan_access convenience accessors (capability flags) ──

    @property
    def restrict_to_managed(self) -> bool:
        """Operations on other sessions are gated by `managed`."""
        return bool(self.pan_access.get("restrict_to_managed", False))

    @restrict_to_managed.setter
    def restrict_to_managed(self, value: bool):
        self.pan_access["restrict_to_managed"] = bool(value)

    @property
    def can_claim_unmanaged(self) -> bool:
        """May claim an unclaimed session into `managed`."""
        return bool(self.pan_access.get("can_claim_unmanaged", False))

    @can_claim_unmanaged.setter
    def can_claim_unmanaged(self, value: bool):
        self.pan_access["can_claim_unmanaged"] = bool(value)

    @property
    def auto_claim_created(self) -> bool:
        """Sessions this session creates are auto-claimed."""
        return bool(self.pan_access.get("auto_claim_created", False))

    @auto_claim_created.setter
    def auto_claim_created(self, value: bool):
        self.pan_access["auto_claim_created"] = bool(value)

    def adapter_field(self, key: str, default=None):
        """Read a value from adapter_config."""
        return self.adapter_config.get(key, default)

    def set_adapter_field(self, key: str, value):
        """Set a value in adapter_config in-place."""
        if value is not None and value != "" and value is not False:
            self.adapter_config[key] = value
        else:
            self.adapter_config.pop(key, None)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        # 落盘 JSON 里 set 序列化为 list → 读回时还原
        if isinstance(self.report_subscriptions, (list, tuple)):
            self.report_subscriptions = set(self.report_subscriptions)
        if isinstance(self.qq_subscriptions, (list, tuple)):
            self.qq_subscriptions = set(self.qq_subscriptions)
        # pan_access: normalize to a dict with all three capability keys,
        # defaulting to False. Migrate legacy top-level instance attrs (old
        # JSON / old constructor paths) into the nested dict.
        pa = self.pan_access if isinstance(self.pan_access, dict) else {}
        for key in _PAN_ACCESS_KEYS:
            legacy = self.__dict__.pop(key, None)
            if legacy is not None:
                pa[key] = legacy
            pa.setdefault(key, False)
        self.pan_access = pa
        # migrate any legacy top-level fields that ended up on the instance
        # (from Session(**data) with old JSON having cbc_session_id, etc.)
        _migrate_legacy_fields(self)

    @classmethod
    def _from_data(cls, data: dict) -> Session:
        """Construct Session from legacy or new JSON data.

        Pops legacy adapter-specific fields from data and puts
        them into adapter_config before constructing the instance.
        Old top-level capability fields are migrated into nested pan_access
        (and the old keys removed) so pre-refactor JSON keeps loading.
        """
        ac = data.pop("adapter_config", {}) or {}
        for old_key, new_key in [
            ("cbc_session_id", "cli_session_id"),
            ("always_thinking_enabled", "always_thinking_enabled"),
            ("effort", "effort"),
            ("max_thinking_tokens", "max_thinking_tokens"),
        ]:
            val = data.pop(old_key, None)
            if val is not None and val != "" and val is not False:
                ac[new_key] = val
        # Migrate legacy top-level capability fields into nested pan_access.
        pa = dict(data.pop("pan_access", {}) or {})
        for key in _PAN_ACCESS_KEYS:
            if key in data:
                pa[key] = data.pop(key)
        data["pan_access"] = pa
        data["adapter_config"] = ac
        return cls(**data)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "adapter": self.adapter,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "pan_access": dict(self.pan_access),
            "adapter_config": self.adapter_config,
            "character_id": self.character_id,
            "session_template": self.session_template,
            "system_prompt": self.system_prompt,
            "game_id": self.game_id,
            "raw_usage": self.raw_usage,
            "total_usage": self.total_usage,
            "workdir": self.workdir,
            "history": self.history,
            "last_result": self.last_result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "managed": self.managed,
            "managed_by": self.managed_by,
            "queue_pending": self.queue_pending,
            "report_subscriptions": sorted(self.report_subscriptions),
            "qq_subscriptions": sorted(self.qq_subscriptions),
        }


# ── in-memory cache ──
_cache: dict[str, Session] = {}


# ── CRUD ──

def create(name: str, model: str | None = None,
           permission_mode: str | None = None,
           adapter: str = "cbc",
           adapter_config: dict | None = None,
           raw_usage: dict | None = None,
           total_usage: dict | None = None,
           workdir: str = "",
           history: list[dict] | None = None,
           character_id: str | None = None,
           session_template: str | None = None,
           system_prompt: str | None = None,
           game_id: str | None = None,
           pan_access: dict | None = None,
           restrict_to_managed: bool = False,
           can_claim_unmanaged: bool = False,
           auto_claim_created: bool = False,
           # backward-compat kwargs (migrated to adapter_config)
           cli_session_id: str | None = None,
           always_thinking_enabled: bool = False,
           effort: str = "",
           max_thinking_tokens: int | None = None) -> Session:
    # build adapter_config
    ac = dict(adapter_config) if adapter_config else {}
    if cli_session_id and "cli_session_id" not in ac:
        ac["cli_session_id"] = cli_session_id
    if always_thinking_enabled and "always_thinking_enabled" not in ac:
        ac["always_thinking_enabled"] = True
    if effort and "effort" not in ac:
        ac["effort"] = effort
    if max_thinking_tokens and "max_thinking_tokens" not in ac:
        ac["max_thinking_tokens"] = max_thinking_tokens

    # pan_access: explicit nested dict wins; legacy flat kwargs fill gaps.
    pa = dict(pan_access) if pan_access else {}
    pa.setdefault("restrict_to_managed", restrict_to_managed)
    pa.setdefault("can_claim_unmanaged", can_claim_unmanaged)
    pa.setdefault("auto_claim_created", auto_claim_created)

    s = Session(
        id=_new_id(),
        name=name,
        adapter=adapter,
        model=model,
        permission_mode=permission_mode,
        pan_access=pa,
        adapter_config=ac,
        character_id=character_id,
        session_template=session_template,
        system_prompt=system_prompt,
        game_id=game_id,
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=workdir,
        history=history or [],
    )
    save(s)
    _cache[s.id] = s
    return s


def _meta_signature(s: Session) -> str:
    """元数据（不含 history / updated_at）的稳定签名。

    用于判断主文件是否需要重写：history append 不改变元数据 → 跳过主文件写。
    """
    meta = s.to_dict()
    meta.pop("history", None)
    meta.pop("updated_at", None)
    return repr(meta)


def _from_data_with_history(sid: str, data: dict) -> Session:
    """从主文件 data 构造 Session，并用 <id>.history.jsonl 合并 history。

    兼容新旧两种格式：
    - 旧格式（history 内嵌主文件）：无 jsonl → history 取 data["history"]；
    - 新格式（增量）：jsonl 存在 → history 以 jsonl 为准（可能比主文件新）。
    同时设置进程内增量游标 s._hist_persisted（已在 jsonl 中的条数）。
    """
    s = Session._from_data(data)
    _migrate_legacy_fields(s)
    _migrate_session_usage(s)
    hist_path = _history_path(sid)
    if hist_path.exists():
        s.history = _strip_delivery_marks(_read_jsonl(hist_path))
    else:
        _strip_delivery_marks(s.history)
    s._hist_persisted = len(s.history)
    s._last_meta_sig = _meta_signature(s)
    return s


def get(session_id: str) -> Session | None:
    if session_id in _cache:
        return _cache[session_id]
    path = _path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        s = _from_data_with_history(session_id, data)
        _cache[session_id] = s
        return s
    except (json.JSONDecodeError, OSError):
        return None


def agent_level(session_id: str) -> int:
    """Compute a session's agent level along its managedBy chain.

    Level 1 = no manager (managedBy is None). Each resolvable hop upward
    (session.managed_by → manager) adds 1, so a session managed by a level-1
    session is level 2, and so on.

    Edge cases:
    - Dangling managedBy (manager session deleted): the chain stops there and
      the session keeps the level reached so far (a broken link is treated as
      the top of the chain).
    - Cycles: a visited-set guards against managedBy loops (which claim()
      prevents but old data might contain) — the walk stops when a manager id
      repeats.
    - Unknown session_id → 1.

    Cost: O(depth) cache lookups per call (get() is an in-memory dict hit).
    """
    seen: set[str] = {session_id}
    level = 1
    cur = get(session_id)
    while cur is not None:
        mb = cur.managed_by
        if not mb or mb in seen:
            break
        manager = get(mb)
        if manager is None:
            break  # dangling reference → treat as chain top
        seen.add(mb)
        level += 1
        cur = manager
    return level


def _save_sync(s: Session, force_full: bool = False):
    """落盘 Session。热路径只做增量：

    - history 追加（自 s._hist_persisted 起的未落盘条目）到 <id>.history.jsonl；
    - 主文件仅在元数据变化（或首次 / 迁移 / force_full）时重写——纯 history
      append 不碰主文件，彻底消除 O(history) 全量序列化。

    force_full=True（首次创建 / 迁移 / history 整体替换）时整重写 jsonl。
    进程内内存 history 是权威，落盘是镜像；_hist_persisted 记录已镜像条数。
    """
    s.updated_at = datetime.now().isoformat()  # 内存总是新鲜（API 读内存）
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    meta_sig = _meta_signature(s)
    hist_path = _history_path(s.id)
    with _SAVE_LOCK:
        start = getattr(s, "_hist_persisted", 0)
        if not isinstance(start, int) or start < 0:
            start = 0
        # 需要整重写的三种情况：显式 force、history 被整体替换（游标超出当前
        # 长度，说明 jsonl 里有作废条目）、jsonl 尚不存在（首次/旧格式迁移）。
        if force_full or start > len(s.history) or not hist_path.exists():
            _write_jsonl(hist_path, s.history)
        else:
            _append_jsonl(hist_path, s.history[start:])
        s._hist_persisted = len(s.history)

        # 元数据变化 → 重写主文件（元数据 + 尾部 history，常量序列化开销）。
        # 写临时文件 + os.replace 原子替换：主文件写一半崩溃也不会损坏
        # （history 真源在 jsonl，主文件只是元数据镜像 + 存在标记）。
        if force_full or meta_sig != getattr(s, "_last_meta_sig", None):
            d = s.to_dict()
            d["history"] = s.history[-_MAIN_HISTORY_TAIL:]
            main_path = _path(s.id)
            tmp_path = main_path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(d, ensure_ascii=False, indent=2),
                encoding="utf-8")
            os.replace(tmp_path, main_path)
            s._last_meta_sig = meta_sig
        _cache[s.id] = s


def save(s: Session):
    """Sync save (for low-frequency server API calls)."""
    _save_sync(s)


def save_full(s: Session):
    """全量重写（主文件 + 完整 history jsonl）。

    用于 history 被整体替换而非追加的路径（reimport 覆盖 / 导入），
    避免增量游标把新 history 的头部误当作已落盘而跳过。
    """
    _save_sync(s, force_full=True)


async def save_async(s: Session):
    """Async save (for high-frequency worker stdout/consumer calls)."""
    await asyncio.to_thread(_save_sync, s)


def delete(session_id: str):
    path = _path(session_id)
    if path.exists():
        path.unlink()
    hist_path = _history_path(session_id)
    if hist_path.exists():
        hist_path.unlink()
    _cache.pop(session_id, None)
    _newline_terminated_jsonl.discard(str(hist_path))  # 文件已删，缓存作废


def claim(manager_id: str, session_id: str) -> str | None:
    """Set a bidirectional managed relationship (立项 4.2).

    Establishes: manager.managed += [session_id], session.managed_by = manager_id.

    Claim 建立 managed 关系时默认自动 report_subscribe：manager.report_subscriptions
    自动加入 session_id，使该 manager 收到目标 session 的完成报告（done/error）。
    仅当确实新增了 managed 条目或首次订阅时才 save(manager)（set.add 幂等）。

    Refuses (returns an error string) if the target session is already managed
    by a different session. No-op success when the relationship already holds.
    Refuses self-claim (manager_id == session_id): a session cannot manage or
    subscribe to itself.

    Returns None on success, or an error message string on refusal.
    """
    if manager_id == session_id:
        return f"Cannot claim itself ({session_id})"
    manager = get(manager_id)
    if manager is None:
        return f"Manager session {manager_id} not found"
    target = get(session_id)
    if target is None:
        return f"Session {session_id} not found"
    if target.managed_by and target.managed_by != manager_id:
        return f"Session {session_id} is managed by {target.managed_by}, not {manager_id}"
    changed = False
    if session_id not in manager.managed:
        manager.managed.append(session_id)
        changed = True
    if session_id not in manager.report_subscriptions:
        manager.report_subscriptions.add(session_id)
        changed = True
    if changed:
        save(manager)
    if target.managed_by != manager_id:
        target.managed_by = manager_id
        save(target)
    return None


def release(session_id: str) -> str | None:
    """Remove the managed relationship pointing at session_id.

    Called when a session is deleted: the managing session's `managed` list is
    cleaned up so it doesn't reference a deleted session (立项 #3), and every
    other session's `report_subscriptions` is purged of session_id so no
    session keeps subscribing to a deleted session's completion reports
    (B1 残留清理).

    Returns None on success, or an error message string.
    """
    # 订阅残留清理：任何其它 session 的 report_subscriptions 不得引用被删 id
    for s in list_all():
        if s.id == session_id:
            continue
        if session_id in s.report_subscriptions:
            s.report_subscriptions.discard(session_id)
            save(s)
    target = get(session_id)
    if target is None:
        return None  # nothing else to clean up
    manager_id = target.managed_by
    if not manager_id:
        return None
    manager = get(manager_id)
    if manager is not None and session_id in manager.managed:
        manager.managed.remove(session_id)
        save(manager)
    target.managed_by = None
    save(target)
    return None


def unclaim(manager_id: str, session_id: str) -> str | None:
    """Remove the managed relationship (manager_id → session_id).

    Only the current manager may unclaim. Also purges the manager's
    ``report_subscriptions`` for session_id (解除管理即退订完成报告).
    Refuses self-unclaim (manager_id == session_id, defensive).

    Returns None on success, or an error message string.
    """
    if manager_id == session_id:
        return f"Cannot unclaim itself ({session_id})"
    manager = get(manager_id)
    if manager is None:
        return f"Manager session {manager_id} not found"
    target = get(session_id)
    if target is None:
        return f"Session {session_id} not found"
    if target.managed_by != manager_id:
        return f"Session {session_id} is not managed by {manager_id}"
    manager.report_subscriptions.discard(session_id)
    if session_id in manager.managed:
        manager.managed.remove(session_id)
        save(manager)
    target.managed_by = None
    save(target)
    return None


def handoff_session(
    session_id: str,
    handoff_prompt: str,
    *,
    copy_settings: bool = True,
    adapter: str | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
) -> tuple[Session, Session] | str:
    """替身交接（session_handoff v1）：创建孪生 session B 接替 session A。

    用途：精简上下文（B 全新会话，不继承 A 的 history / cli_session_id），或
    切换 adapter（普通 session 不能中途切 adapter）。

    行为：
    1. **关系网接替（自动、必然）**：B.managed = A.managed，A 的子会话
       managed_by 改 B；A 的 report_subscriptions / QQ postbox 绑定（qq_subscriptions）
       转移给 B；A 若曾被某 manager 管理，B 接替 A 在该 manager 下的位置。
    2. **B 自动 manage A**：B.managed 追加 A，A.managed_by = B（A 归档为 B 的
       被管理会话，B 订阅 A 的完成报告）。
    3. **可选设置复制（copy_settings）**：true 时 1:1 复制 A 的设置（adapter、
       adapter_config、model、permission_mode、session_template、pan_access、
       mcp_servers 等，**明确不含 system_prompt**；cli_session_id 清空——B 是
       全新会话）；false 时 B 用默认设置（此时调用方应显式传 adapter）。
    4. **B.system_prompt = handoff_prompt（A 新写）与 A 原 system_prompt 拼接**，
       用「交接上下文 / 原 system prompt」两个分节引导。
    5. **重命名**：A → `(archive) <原名>`，B → `<原名>`。
    6. **解除 A 的原关系网**：A.managed / report_subscriptions / qq_subscriptions
       清空（A.managed_by 保留 = B，见第 2 条）。

    Returns (A, B) on success, or an error message string.
    """
    a = get(session_id)
    if a is None:
        return f"Session {session_id} not found"
    if not handoff_prompt or not handoff_prompt.strip():
        return "handoff_prompt is required — session A 的 agent 必须编写交接简报"

    orig_name = a.name
    archive_name = f"(archive) {orig_name}"

    # ── 1. 创建 B：可选 1:1 复制 A 的设置（不含 system_prompt）──
    if copy_settings:
        new_adapter = adapter or a.adapter
        new_model = model or a.model
        new_permission_mode = permission_mode or a.permission_mode
        new_adapter_config = copy.deepcopy(a.adapter_config)
        new_adapter_config.pop("cli_session_id", None)  # B 是全新会话，不继承 A 的 CLI 上下文
        new_pan_access = copy.deepcopy(dict(a.pan_access))
        new_character_id = a.character_id
        new_template = a.session_template
        new_game_id = a.game_id
    else:
        new_adapter = adapter or "cbc"
        new_model = model
        new_permission_mode = permission_mode
        new_adapter_config = {}
        new_pan_access = {}
        new_character_id = None
        new_template = None
        new_game_id = None

    # B 的 system_prompt = 交接 prompt（A 新写） + A 原 system_prompt 拼接
    b_prompt = handoff_prompt.strip()
    if a.system_prompt and a.system_prompt.strip():
        b_prompt = (
            "【交接上下文（由被交接 session A 的 agent 编写）】\n"
            f"{b_prompt}\n\n"
            "【原 session 的 system prompt】\n"
            f"{a.system_prompt.strip()}"
        )

    b = create(
        name=orig_name,
        adapter=new_adapter,
        model=new_model,
        permission_mode=new_permission_mode,
        adapter_config=new_adapter_config,
        character_id=new_character_id,
        session_template=new_template,
        system_prompt=b_prompt,
        game_id=new_game_id,
        pan_access=new_pan_access,
        workdir=a.workdir,
    )

    # ── 2. 关系网接替 ──
    # 2a. A 的子会话 → 改由 B 管理
    for child_id in list(a.managed):
        child = get(child_id)
        if child is not None:
            child.managed_by = b.id
            save(child)
    b.managed = list(a.managed)

    # 2b. B 自动 manage A（A 归档为 B 的被管理会话；B 订阅 A 的报告）
    b.managed.append(a.id)
    b.report_subscriptions = set(a.report_subscriptions)
    b.report_subscriptions.add(a.id)

    # 2c. A 的原父 manager → B 接替 A 的位置（A 曾被他人管理时）
    parent_id = a.managed_by
    if parent_id:
        parent = get(parent_id)
        if parent is not None:
            if a.id in parent.managed:
                parent.managed[parent.managed.index(a.id)] = b.id
            if a.id in parent.report_subscriptions:
                parent.report_subscriptions.discard(a.id)
                parent.report_subscriptions.add(b.id)
            save(parent)
        b.managed_by = parent_id

    # 2d. 其它会话对 A 的 report 订阅 → 改指向 B（一般即原父 manager，兜底全量扫）
    for s in list_all():
        if s.id in (a.id, b.id):
            continue
        if a.id in s.report_subscriptions:
            s.report_subscriptions.discard(a.id)
            s.report_subscriptions.add(b.id)
            save(s)

    # 2e. QQ postbox 绑定 → B
    b.qq_subscriptions = set(a.qq_subscriptions)

    # ── 3. 解除 A 的原关系网（A.managed_by 保留 = B，见 2b）──
    a.managed = []
    a.managed_by = b.id
    a.report_subscriptions = set()
    a.qq_subscriptions = set()
    a.name = archive_name
    save(a)
    save(b)
    return a, b


_all_loaded: bool = False


def list_all() -> list[Session]:
    global _all_loaded
    if not _all_loaded:
        if SESSION_DIR.exists():
            for f in sorted(SESSION_DIR.iterdir()):
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        sid = data.get("id")
                        # 不覆盖已缓存的 Session（worker 可能在 _read_stdout
                        # 里 append 了 history 但还没 save，磁盘版本更旧）
                        if sid and sid not in _cache:
                            s = _from_data_with_history(sid, data)
                            _cache[sid] = s
                    except (json.JSONDecodeError, OSError):
                        pass
        _all_loaded = True
    # after initial load, cache is always current (create/save/delete sync it)
    return sorted(_cache.values(), key=lambda s: s.created_at)


# ── migration helpers ──

def _migrate_legacy_fields(s: Session):
    """Migrate old top-level adapter-specific fields into adapter_config."""
    changed = False
    # if old-style fields exist as attributes, move them to adapter_config
    legacy_map = {
        "cbc_session_id": "cli_session_id",
        "always_thinking_enabled": "always_thinking_enabled",
        "effort": "effort",
        "max_thinking_tokens": "max_thinking_tokens",
    }
    for old_key, new_key in legacy_map.items():
        value = getattr(s, old_key, None)
        if value and new_key not in s.adapter_config:
            s.adapter_config[new_key] = value
            changed = True
    if changed:
        _save_sync(s)


def _deep_sum_raw_usage(a: dict, b: dict) -> dict:
    """递归累加两个 rawUsage dict 中所有数值字段。"""
    result = dict(a)
    for k, v in b.items():
        if k not in result:
            result[k] = v
        elif isinstance(v, dict) and isinstance(result[k], dict):
            result[k] = _deep_sum_raw_usage(result[k], v)
        elif isinstance(v, (int, float)) and isinstance(result[k], (int, float)):
            result[k] += v
    return result


def accumulate_raw_usage(existing: dict | None, entries: list[dict]) -> dict:
    """将 raw_usage 条目按 model 累加，返回 {model: {model, request_count, rawUsage}}。

    existing: 已有的累加结果（dict keyed by model），None 表示无
    entries:  待合并的条目列表，每项 {"model": str, "rawUsage": dict, ...}
    """
    result: dict = dict(existing) if existing else {}
    for entry in entries:
        model = entry.get("model", "unknown")
        ru = entry.get("rawUsage")
        if not ru:
            continue
        if model in result:
            result[model]["rawUsage"] = _deep_sum_raw_usage(result[model]["rawUsage"], ru)
            result[model]["request_count"] += 1
        else:
            result[model] = {
                "model": model,
                "request_count": 1,
                "rawUsage": ru,
            }
    return result


def compute_total_usage(raw_usage: dict | None) -> dict | None:
    """从按模型累加的 raw_usage 汇总累计消耗。

    返回 {"prompt_tokens": int, "cache_hit_tokens": int, "cache_miss_tokens": int,
          "completion_tokens": int, "credit": float}
    或 None（raw_usage 为空时）。

    credit 同时兼容 "credit"（cbc/kimi）与 "cost"（claude/opencode）两种
    rawUsage 键名——各 adapter 对「金额」字段命名不统一，聚合处收敛到 credit。
    """
    if not raw_usage:
        return None
    total = {"prompt_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0,
             "completion_tokens": 0, "credit": 0.0}
    for entry in raw_usage.values():
        ru = entry.get("rawUsage", {})
        total["prompt_tokens"] += ru.get("prompt_tokens", 0)
        total["cache_hit_tokens"] += ru.get("prompt_cache_hit_tokens", 0)
        total["cache_miss_tokens"] += ru.get("prompt_cache_miss_tokens", 0)
        total["completion_tokens"] += ru.get("completion_tokens", 0)
        total["credit"] += ru.get("credit", 0) + ru.get("cost", 0)
    return total


def _migrate_session_usage(s: Session):
    """Migrate legacy list-format raw_usage to dict + compute total_usage."""
    if isinstance(s.raw_usage, list):
        s.raw_usage = accumulate_raw_usage(None, s.raw_usage)
    if s.total_usage is None and isinstance(s.raw_usage, dict):
        s.total_usage = compute_total_usage(s.raw_usage)


def clear_cache():
    _cache.clear()

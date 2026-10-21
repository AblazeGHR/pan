"""Worker — runtime cbc/kimi/... CLI process management.

概念模型（agent-naming 确立）：
    Agent  = Session —— 逻辑编排对象（持久身份：收件箱 queue_pending /
             agentLevel / managedBy 链，见 session.py）。投递/编排语义
             （send/assign/report）都绑在它上面。
    Worker = CLI 进程实例 —— 本模块管理的物理执行体：某个 Agent 名下的
             临时 cbc/kimi/... 子进程。进程是顺带的：kill 即消失，可随时
             重建（watchdog / pendingSpawn 自动补员）。

Worker is ephemeral: kill it, the Worker is gone.
All persistent data lives in Session (session.py) — 即 Agent 的持久身份。
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from packages.jobs.window import command_window_options
from packages.plugins.lifecycle import startup as _admit_plugin_startup
from packages.plugins.lifecycle import native_admission as _plugin_native_admission
import copy
import hashlib
import inspect
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psutil

from . import session as _sess
from . import auto_report as _auto_report
from . import notifications as _notifications
from . import codex_quota_store as _codex_quota_store
from . import legal_offline as _offline
from . import queue_locks as _message_locks
from . import session_error_log as _error_log

READONLY_SESSION_ERROR = (
    "该 session 当前为只读，必须先取消对该 session 的 readonly 设置后才能发送信息、任务或回报"
)
from .adapters import (
    get_adapter,
    get_sessions_provider,
    CliAdapter,
    resolve_execution_mode,
)
from .adapters.base import SYSTEM_PROMPT_ARG_MAX_CHARS
from .config import load_config
from .cli_diagnostics import format_cli_spawn_error
from .attachment_projection import project_message_parts, public_message_parts

_log = logging.getLogger(__name__)

# Keep provider prompt bodies out of Windows CreateProcess command lines.  The
# conservative boundary also leaves room for the rest of an adapter's argv;
# short prompts retain the existing native flag path for compatibility.
# ── Worker 生命周期配置（启动时读取一次，缓存）──

_WORKER_TIMEOUT_SEC: float = 300.0       # 静默超时：queued 无输出 / MCP 读取超时超过该值 → kill
_WORKER_TASK_TIMEOUT_SEC: float = 1800.0  # stream running 任务运行时长上限：超此值判定卡死
_WORKER_IDLE_SEC: float = 300.0          # 空闲回收：idle 超时 → kill
_WATCHDOG_TICK_SEC: float = 30.0         # watchdog 检查间隔

# 可被 server 启动时覆盖（测试也可直接赋值）
_DEFAULTS_INITIALIZED = False


def load_worker_config():
    """从 config.json 读取 worker 生命周期配置并缓存。

    由 server 启动时调用一次（lifespan）；直接使用 core 的场景也会在
    首次 create_worker 前惰性初始化。

    - timeout_sec：静默超时（queued 无输出 / MCP 读取超时），默认 300。
    - task_timeout_sec：stream running 任务运行时长上限，默认 1800。
      与 timeout_sec 语义分离：长思考/大文件读取会长时间无 stdout 输出，
      若用「无输出时长」判定 running 会误杀；此处用「任务运行时长」。
    - idle_sec：空闲回收，默认 300。
    """
    global _WORKER_TIMEOUT_SEC, _WORKER_TASK_TIMEOUT_SEC, _WORKER_IDLE_SEC, _DEFAULTS_INITIALIZED
    cfg = load_config().get("worker", {})
    _WORKER_TIMEOUT_SEC = float(cfg.get("timeout_sec", 300))
    _WORKER_TASK_TIMEOUT_SEC = float(cfg.get("task_timeout_sec", 1800))
    _WORKER_IDLE_SEC = float(cfg.get("idle_sec", 300))
    _DEFAULTS_INITIALIZED = True


def reload_worker_config() -> dict:
    """热重载 worker 生命周期配置（POST /api/config/reload 调用）。

    重新执行 load_worker_config() 从盘上读 config.json 的 worker 字段，
    刷新模块级缓存变量，使运行中的 server 不重启即应用新值。

    返回新旧值对比（before/after），供端点向前端展示变化。
    """
    before = {
        "timeout_sec": _WORKER_TIMEOUT_SEC,
        "task_timeout_sec": _WORKER_TASK_TIMEOUT_SEC,
        "idle_sec": _WORKER_IDLE_SEC,
    }
    load_worker_config()
    after = {
        "timeout_sec": _WORKER_TIMEOUT_SEC,
        "task_timeout_sec": _WORKER_TASK_TIMEOUT_SEC,
        "idle_sec": _WORKER_IDLE_SEC,
    }
    return {"before": before, "after": after}


# ── Memory injection 开关（config.json -> memory.enabled）──
# 默认关闭：config.json 缺省 memory 段时不做 embedding 记忆注入，
# 避免首次加载 bge 模型 + huggingface 网络重试阻塞 worker 任务。
# 需要记忆注入的角色，在 config.json 显式配 "memory": { "enabled": true }。

_MEMORY_ENABLED = False


def load_memory_config():
    """从 config.json 的 memory.enabled 读取记忆注入开关。

    config.json:
        "memory": { "enabled": false }

    缺省该键（config 无 memory 段）→ 视为关闭（默认 false）。
    """
    global _MEMORY_ENABLED
    _MEMORY_ENABLED = bool(load_config().get("memory", {}).get("enabled", False))


def reload_memory_config() -> dict:
    """热重载 memory.enabled 开关（POST /api/config/reload 调用）。

    重新执行 load_memory_config() 从盘上读 config.json 的 memory 段，
    刷新模块级 _MEMORY_ENABLED，使运行中的 server 不重启即应用新值。

    返回新旧值对比（before/after），供端点向前端展示变化。
    """
    before = {"enabled": _MEMORY_ENABLED}
    load_memory_config()
    after = {"enabled": _MEMORY_ENABLED}
    return {"before": before, "after": after}


# ── Memory injection helper ──


async def _maybe_inject_memory(s, text: str) -> str:
    """If session has a character_id, search memory and inject context.

    Runs in a thread to avoid blocking the event loop (embedding is CPU-bound).
    On failure, returns the original text unchanged.
    """
    if not _MEMORY_ENABLED:
        return text
    if not s.character_id:
        return text

    try:
        from .memory_context import search_and_format, inject_context

        # Resolve memory db dir from the project root (repo-relative), NOT
        # Path.cwd() — the server uses an absolute DATA_DIR and the two must
        # point at the same DB (#22).
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
        db_dir = str(_PROJECT_ROOT / "data" / "memory")

        # Bounded wait: first call loads the embedding model (bge-base-zh), which
        # can take minutes when huggingface is unreachable (network retries).
        # A worker task must not sit in queued indefinitely while model loads —
        # degrade to raw text after a short budget.
        ctx = await asyncio.wait_for(
            asyncio.to_thread(
                search_and_format,
                text,
                character_id=s.character_id,
                db_dir=db_dir,
            ),
            timeout=15.0,
        )
        if ctx.snippet_count > 0:
            return inject_context(text, ctx)
    except asyncio.TimeoutError:
        _log.warning("Memory injection timed out for %s, using raw text", s.character_id)
    except Exception:
        _log.warning("Memory injection failed for %s, using raw text", s.character_id)
    return text


@dataclass
class Worker:
    """One CLI process instance (physical executor) belonging to an Agent.

    Agent = Session（编排对象，本类的 session_id 指向它）；Worker 只是该
    Agent 当前活着的 CLI 子进程，随时可被 kill / 回收 / 重建。不要把
    Worker 当持久实体——持久状态一律在 Session（queue_pending / history /
    cliSessionId）。

    字段说明：
    """
    worker_id: str
    session_id: str           # Session UUID (ses_<hex>)
    adapter: CliAdapter       # CLI tool adapter instance
    status: str = "idle"      # idle | running | held | done | error | cancelled | queued | zombie
    process: asyncio.subprocess.Process | None = None
    system_prompt_file: str | None = None  # fresh-spawn transport; removed with the worker
    _mcp_proc: asyncio.subprocess.Process | None = None  # in-flight one-shot MCP process
    _stdout_task: asyncio.Task | None = None
    _consume_task: asyncio.Task | None = None
    _watchdog_task: asyncio.Task | None = None
    # 唤醒信号通道（内存，语义按立项 4.3/4.7/L4 收窄为"只唤醒、不承载正文"）：
    # 消息真源是落盘 Session.queue_pending，本队列只放无正文唤醒信号。
    # 旧版 report_signal/task_signal 仍被兼容接收，但新路径只发 queue_signal。
    pending_signal: asyncio.Queue | None = None
    # Native Codex interactive requests are kept here as a short-lived
    # replay cache.  The process remains the source of truth for the actual
    # JSON-RPC request; this snapshot only lets a reconnected dashboard
    # restore its prompt while that same worker is still alive.
    pending_interactions: dict[str, dict] = field(default_factory=dict)
    # Last native Codex thread status for reconnecting dashboards.  Like the
    # interaction cache, this is only valid while this worker process lives.
    native_status: dict | None = None
    # Separate from status/held and the user's durable queue_paused setting.
    # Switching/unknown ownership blocks only new handoffs and Pan controls.
    native_control_mode: str = "pan"
    native_control_enabled: bool = False  # Latched at spawn, not the desired Session config.
    native_passthrough_enabled: bool | None = None  # None means runtime policy unconfirmed.
    native_control_generation: int = 0
    native_output: bytearray = field(default_factory=bytearray)
    native_output_total: int = 0
    native_control_error: str | None = None
    _offline_ready_token: str | None = None
    _offline_predecessors: set[str] | None = None
    _native_reservation_busy: bool = False
    _native_admission_id: str | None = None
    _native_admission_ids: set[str] = field(default_factory=set)
    native_kernel_ready: bool = False
    _native_external_turn_active: bool = False
    native_input_uncertain: bool = False
    _native_mode_receipts: dict[str, asyncio.Future] = field(default_factory=dict)
    _native_mode_request_id: str | None = None  # Latest issued handoff, including a timed-out one.
    _native_mode_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Latest native Codex token usage notification for reconnecting dashboards.
    # The provider remains the source of truth for persisted/session totals;
    # this is only the live thread/turn snapshot from app-server.
    native_usage: dict | None = None
    # Latest account-level Codex rate-limit snapshot. Unlike token usage this
    # survives a completed turn, but it is still process-local and must be
    # cleared when the app-server is respawned.
    native_rate_limits: dict | None = None
    # Local receive time for the latest rate-limit event.  The provider event
    # does not carry a trustworthy original-update timestamp in this path.
    native_rate_limits_received_at: str | None = None
    # Compatibility alias retained for existing callers/API fields.
    native_rate_limits_updated_at: str | None = None
    # The selected Codex auth profile is account-level metadata, not a
    # persisted Worker identity. It is used only to route live updates to the
    # matching global quota store.
    codex_profile_key: str | None = None
    # Current turn's native plan/diff snapshots for dashboard reconnect replay.
    native_plan: dict | None = None
    native_diff: dict | None = None
    _task_done: asyncio.Event | None = None  # stream 任务完成信号（_consumer_stream 等待，防多消息同时在 cbc 管道飞行）
    _replaying: bool = False  # 遗留：cbc --resume 的 stdout 重放标志（worker-resume-replay 结论：stdin 有 prompt 时 cbc 不重放，恒为 False；_read_stdout 的 replay 分支保留作 EOF 型重放的死代码兜底）
    takeover_pid: int | None = None  # PID of takeover PowerShell terminal
    takeover_job: object | None = None  # Windows Codex descendants, including orphans
    _takeover_stop_task: asyncio.Task | None = None
    pending_restart: bool = False  # 进程相关配置变更后待重启（idle 时自动 respawn）
    _queue_unit_active: bool = False  # includes terminal publication and queue cleanup
    _terminal_finishing: bool = False
    # ── 活性探测（watchdog 用）──
    last_activity: float = 0.0  # time.monotonic；stdout 有事件 / 新任务入队时刷新
    _task_started_at: float = 0.0  # time.monotonic；stream 任务开始处理（status→running）时记录，watchdog 据此判定「任务运行时长」超时
    # ── 任务序号（result 与 task 配对用）──
    # 序号计数器在 session.task_seq 上（跨 worker respawn 持久）；send_task
    # 入队时从 session 读、自增后随 item.seq 一起落盘。
    # Runtime execution order includes report/Job batches; task seq stays queue identity.
    _execution_seq: int = 0
    _current_seq: int | None = None  # 正在处理的 item 序号（_consumer 取出时记录）
    _current_task_id: str | None = None  # 正在处理的 item 的 taskId（幂等用）
    _current_task_idempotent: bool = False  # formal assign vs inherited send context
    _current_task_assistant_ids: set[str] = field(default_factory=set)
    _auto_report_admission: dict | None = None
    _current_source_session_id: str | None = None  # 当前 item 的来源 Session（审计元数据）
    # 当前正在执行的持久 queue item。直到 provider hand-off 成功前，item
    # 仍保留在 Session.queue_pending；这里仅保留运行期引用用于结果配对。
    _current_queue_item: dict | None = None
    # A provider can repeat a terminal event during reconnect/replay.  This
    # runtime guard prevents a second result/report for the same active turn;
    # the durable usage job below provides the restart-side idempotency cursor.
    _current_task_last_assistant_text: str | None = None
    # Run identity (task seq) that produced ``_current_task_last_assistant_text``.
    # Error reports may only attach the text when it provably belongs to the run
    # being reported, so a stale capture from a previous turn can never leak into
    # a queued item or a later run.
    _current_task_last_assistant_seq: int | None = None
    # Per-run scratch file for non-JSON stdout/stderr.  It is attached to
    # terminal/crash error samples as ``rawDiagnostics`` (never as errorText)
    # and is reset at each task hand-off.  Kept on disk so a long diagnostic
    # stream cannot grow the event-loop heap.
    _stream_raw_spill: object | None = None
    _stream_raw_line_count: int = 0
    _host_exit_illegal: bool = False
    _terminal_handled: bool = False
    # 已完成的协议序列化快照（仅兼容旧测试/嵌入方，队列真源仍在 Session）。
    _current_serialized: bytes | None = None
    # 兼容旧 consumer 的运行期位置字段；新 consumer 不依赖它决定持久语义。
    _claimed_queue_index: int | None = None
    # 当前报告批次的持久 item。报告与 task 共用 queue_pending，连续报告由一次
    # CLI hand-off 组成一个 delivery unit。
    _current_report_items: list[dict] = field(default_factory=list)
    # True only after the provider-specific hand-off boundary has committed:
    # stream write+drain, or successful one-shot process creation.
    _current_handoff_acked: bool = False
    _zombie_reported: bool = False  # 异常死亡 zombie 报告是否已推送（防 watchdog/EOF 双路径重复）
    # ── 流式块防抖落盘（A1）──
    _hist_dirty: bool = False          # 有未落盘的 history 块（append 只标记，不逐块 save）
    _hist_block_count: int = 0         # 本次批量累计的块数（达上限提前落盘）
    _hist_force_flush: bool = False    # result/退出路径强制立即落盘标记
    _hist_wake_count: int = 0          # 未消费的「提前落盘」唤醒计数（权威信号，防 ev.set 被 clear 冲掉）
    _hist_flush_event: asyncio.Event | None = None  # 防抖唤醒（仅作阻塞唤醒；计数为准）
    _hist_save_task: asyncio.Task | None = None     # 防抖落盘任务（单写者）
    # A restart reuses the public worker id, so stale background tasks must be
    # fully stopped before a new generation starts.  This counter makes that
    # ownership boundary explicit for delayed interrupt fallbacks.
    generation: int = 0
    offline_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    _interrupt_guard_task: asyncio.Task | None = None
    _steer_receipts: dict[str, asyncio.Future] = field(default_factory=dict)
    _steer_contexts: dict[str, dict] = field(default_factory=dict)
    _steer_run_id: str | None = None
    _direct_steer_pending: bool = False


workers: dict[str, Worker] = {}
_delayed_restarts: dict[str, dict] = {}
_delayed_restart_revision = 0
# Fast session -> current runtime lookup.  Keep the worker-id registry as the
# compatibility/source-of-truth container; this index only avoids scanning it
# for the hot session control/status paths.
_workers_by_session: dict[str, Worker] = {}
_worker_generations: dict[str, int] = {}


def _register_worker(w: Worker) -> None:
    _workers_by_session[w.session_id] = w


def _unregister_worker(w: Worker) -> None:
    _close_raw_capture(w)
    if _workers_by_session.get(w.session_id) is w:
        _workers_by_session.pop(w.session_id, None)


def _next_worker_generation(session_id: str) -> int:
    generation = _worker_generations.get(session_id, -1) + 1
    _worker_generations[session_id] = generation
    return generation


def _bump_worker_generation(w: Worker) -> None:
    _cancel_pending_restart(w)
    w.generation += 1
    w.offline_token = uuid.uuid4().hex
    _worker_generations[w.session_id] = max(
        _worker_generations.get(w.session_id, -1), w.generation,
    )

# A durable item may be appended while its worker is dead (or while the old
# generation is being torn down).  Keep one best-effort recovery task per
# session so the request path can wake recovery immediately without spawning
# duplicate workers.  The global watchdog remains the retry/fallback path when
# a spawn fails or the service was not able to schedule this task.
_recovery_tasks: dict[str, asyncio.Task] = {}
# Set once the service shutdown begins.  Recovery spawn is fire-and-forget:
# cancelling a task mid real create_subprocess_exec leaves the Windows
# Proactor waiter unresolved forever, so shutdown drains in-flight recovery
# tasks with a bounded wait instead, and no new recovery may be scheduled
# after the flag is raised.
_shutdown_started: bool = False
# Sessions whose worker died during an active turn.  Keep retry intent beyond
# the first best-effort spawn so a transient CLI launch failure is retried by
# the service watchdog; successful/normal idle paths remove the marker.
_recovery_required: set[str] = set()
# One timer per session is enough to wake a queued item after persisted
# backoff.  This is deliberately separate from pending_signal: the timer only
# emits a generic wake-up and never stores queue payload.
_queue_retry_tasks: dict[str, asyncio.Task] = {}
_queue_retry_wakes: dict[str, asyncio.Event] = {}
_queue_locks: dict[str, asyncio.Lock] = {}
_unread_done_locks: dict[str, asyncio.Lock] = {}
_usage_enrichment_locks: dict[str, asyncio.Lock] = {}
_usage_enrichment_tasks: dict[str, asyncio.Task] = {}
_usage_enrichment_adapters: dict[str, CliAdapter] = {}
_QUEUE_EDIT_LOCK_TTL_SECONDS = 5 * 60

# Usage lookup is provider-specific synchronous I/O (and cbc/kimi include a
# deliberate post-result wait).  Keep it off the asyncio loop, retry forever
# with bounded backoff, and persist the retry metadata so a service restart can
# resume it without inventing another queue/event protocol.
_ENRICH_RETRY_BASE_SEC = 1.0
_ENRICH_RETRY_MAX_SEC = 30.0


def queue_lock(session_id: str) -> asyncio.Lock:
    """Serialize queue API mutations and Worker reservation decisions per Session."""
    return _queue_locks.setdefault(session_id, asyncio.Lock())


def unread_done_lock(session_id: str) -> asyncio.Lock:
    """Serialize done generation commits with durable read-cursor acknowledgements."""
    return _unread_done_locks.setdefault(session_id, asyncio.Lock())


def _queue_edit_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _active_queue_edit_lock(s, item_id: str, now: float | None = None) -> dict | None:
    """Return an unexpired edit lease, discarding an expired one in memory."""
    locks = getattr(s, "queue_edit_locks", None)
    if not isinstance(locks, dict):
        s.queue_edit_locks = locks = {}
    lease = locks.get(item_id)
    if not isinstance(lease, dict):
        return None
    expires_at = lease.get("expiresAt")
    if (not isinstance(expires_at, (int, float))
            or expires_at <= (time.time() if now is None else now)):
        locks.pop(item_id, None)
        return None
    token_hash = lease.get("tokenHash")
    if not isinstance(token_hash, str) or len(token_hash) != 64:
        locks.pop(item_id, None)
        return None
    return lease


def acquire_queue_edit_lock(s, item_id: str, token: str,
                            now: float | None = None) -> tuple[dict | None, bool]:
    """Acquire or renew an item edit lease; return (lease, conflict)."""
    locks = getattr(s, "queue_edit_locks", None)
    if not isinstance(locks, dict):
        s.queue_edit_locks = locks = {}
    current = _active_queue_edit_lock(s, item_id, now)
    token_hash = _queue_edit_token_digest(token)
    if current is not None and current.get("tokenHash") != token_hash:
        return None, True
    expires_at = (time.time() if now is None else now) + _QUEUE_EDIT_LOCK_TTL_SECONDS
    lease = {"tokenHash": token_hash, "expiresAt": expires_at}
    locks[item_id] = lease
    return lease, False


def release_queue_edit_lock(s, item_id: str, token: str) -> bool:
    """Release only the matching lease; late clients cannot release a newer edit."""
    lease = _active_queue_edit_lock(s, item_id)
    locks = getattr(s, "queue_edit_locks", {})
    if lease is None:
        return False
    if lease.get("tokenHash") != _queue_edit_token_digest(token):
        return False
    locks.pop(item_id, None)
    return True


def queue_item_edit_locked(s, item_id: str) -> bool:
    return _active_queue_edit_lock(s, item_id) is not None


def _usage_enrichment_lock(session_id: str) -> asyncio.Lock:
    """Serialize usage post-processing for one Session, across Workers."""
    return _usage_enrichment_locks.setdefault(session_id, asyncio.Lock())

_broadcast: callable = None

# ── task 幂等注册表: taskId → {"status", "workerId", "result"}
# assign 生成 taskId 入队时登记，result 时标记完成；Meta-Agent 重发带同一
# taskId → 检测已存在则返回状态，不重复入队（防超时后双跑）
_task_status: dict[str, dict] = {}

# Claude Code's non-interactive permission-prompt MCP tool calls back into Pan
# while the CLI is blocked waiting for a decision.  Keep the waiter on the
# Worker boundary so both stream and one-shot workers use the same dashboard
# control path.  The future is deliberately process-local: a respawn cannot
# safely answer a permission request owned by the old Claude process.
_CLAUDE_PERMISSION_TIMEOUT_SEC: float = 300.0
_claude_permission_requests: dict[str, tuple[str, asyncio.Future]] = {}

# task 幂等注册表条目 TTL：条目超过该时长（无论 pending 还是已完成）在下次
# assign 访问注册表时被惰性清除，防止全局 dict 长期运行无界增长（H2 泄漏）。
_TASK_STATUS_TTL_SEC: float = 86400.0  # 24h


def _prune_task_status() -> None:
    """惰性清除 _task_status 中超过 TTL 的过期条目。

    注册表在 assign 幂等检查处读取，故在 assign 入口调用即可兜住泄漏。
    ts 缺失的旧条目视为永不超时，避免破坏升级前写入的数据。
    """
    cutoff = time.monotonic() - _TASK_STATUS_TTL_SEC
    expired = [
        tid for tid, entry in _task_status.items()
        if entry.get("ts", float("inf")) < cutoff
    ]
    for tid in expired:
        _task_status.pop(tid, None)
    if expired:
        _log.info("_task_status: pruned %d expired task(s)", len(expired))


def _mark_worker_tasks_error(worker_id: str, reason: str) -> int:
    """kill_worker / worker 退出路径：把该 worker 名下 status==pending 的 taskId 标 error。

    只有已经完成 provider hand-off、从 Session.queue_pending 移除的任务才
    在 worker 被杀时标记 error；仍处于 queued/reserved/writing 的任务保留
    pending，交给队列恢复路径重新投递。
    """
    now = time.monotonic()
    old_worker = workers.get(worker_id)
    pending_task_ids: set[str] = set()
    if old_worker is not None:
        session = _session(old_worker)
        if session is not None:
            pending_task_ids = {
                item.get("taskId")
                for item in session.queue_pending
                if _is_formal_task_item(item)
                and item.get("taskId")
            }
    marked = 0
    for tid, entry in list(_task_status.items()):
        if entry.get("workerId") == worker_id and entry.get("status") == "pending":
            if tid in pending_task_ids:
                entry["workerId"] = None
                continue
            entry.update({"status": "error", "result": reason, "ts": now})
            marked += 1
    if marked:
        _log.info("[Worker %s] _task_status: marked %d pending task(s) error (%s)",
                  worker_id, marked, reason)
    return marked


def _kill_pid_tree(pid: int) -> None:
    """同步：用 psutil 杀掉指定 PID 及其所有子进程树。"""
    try:
        parent = psutil.Process(pid)
        processes = [parent, *parent.children(recursive=True)]
        for child in processes[1:]:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        # kill() only sends the termination signal.  Wait for every process in
        # the tree so a caller that immediately starts another client for the
        # same Codex thread cannot race the old app-server writer.
        _, alive = psutil.wait_procs(processes, timeout=5)
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    except Exception as e:
        _log.error("psutil kill tree failed for PID=%s: %s", pid, e)


def set_broadcaster(fn: callable):
    global _broadcast
    _broadcast = fn


async def _bcast(data: dict):
    if _broadcast is not None:
        r = _broadcast(data)
        if hasattr(r, "__await__"):
            await r


# ── helpers (internal) ──


async def _next_worker_id() -> str:
    used: set[int] = set()
    for wid in workers:
        try:
            used.add(int(wid.rsplit("-", 1)[-1]))
        except (ValueError, IndexError):
            pass
    n = 1
    while n in used:
        n += 1
    return f"worker-{n}"


def _session(w: Worker) -> _sess.Session | None:
    return _sess.get(w.session_id)


_LEGAL_WORKER_STATES = frozenset({
    "queued", "running", "done", "error", "cancelled", "idle",
    "restarting", "held", "offline",
})
_legal_state_locks: dict[str, asyncio.Lock] = {}


async def _commit_offline(s, mutate, *, force: bool = False) -> bool:
    """Caller owns queue_lock. Stage/rollback inside the ordered writer ticket.

    Earlier/later metadata writers must never save an uncommitted candidate.
    Cancellation waits for that ticket's observable outcome before propagating.
    """
    changed = False
    def commit():
        nonlocal changed
        if _sess.get(s.id, load_history=False) is not s:
            raise RuntimeError("Session owner changed before legality commit")
        old = _offline.snapshot(s)
        candidate = copy.copy(s)
        for key, value in copy.deepcopy(old).items():
            setattr(candidate, key, value)
        _offline.attach_foreground(candidate)
        result = mutate(candidate)
        if candidate.offline_run and candidate.offline_run.get("id") in candidate.execution_operations:
            candidate.execution_operations[candidate.offline_run["id"]] = candidate.offline_run
        _offline.aggregate(candidate)
        values = _offline.snapshot(candidate)
        changed = old != values
        if changed or force:
            _sess._save_body(s, offline_override=values)
        return result
    await _sess._persist_async_outcome(s.id, commit)
    return changed


def _remember_offline_lock(s, item):
    _remember_queue_item(s, item, _delivery_state(item))


async def _offline_snapshot_event(s) -> None:
    # Offline legality changes are committed together with queue state.  The
    # queue snapshot is also the immediate event emitted by EOF/illegal
    # detection, so carry the authoritative Session legality projection here;
    # otherwise the web store refreshes queue rows but keeps the old dot colour
    # until a later full Session refresh.
    await _bcast({"type": "queue.snapshot", "sessionId": s.id,
                  "queueRevision": s.queue_revision,
                  "session": {
                      "legalOffline": s.legal_offline,
                      "illegalOffline": _offline.is_illegal(s),
                      "legalityDiagnostics": _offline.diagnostics(s),
                  }})


async def _prepare_offline_recovery(s) -> None:
    """Before any new provider can start, classify the previous unfinished run."""
    async with queue_lock(s.id):
        if getattr(s, "_obsolete_legality_fields_pending", False):
            await _commit_offline(s, lambda candidate: None, force=True)
            s._obsolete_legality_fields_pending = False
        owner = s.offline_owner
        tracked_owner = find_worker_by_session(s.id)
        if owner and not (tracked_owner and _offline.same_owner(owner, _offline.owner(tracked_owner))
                          and not _runtime_stopped(tracked_owner)):
            status = await asyncio.to_thread(_offline.previous_owner_status, owner)
            if status != "dead":
                raise RuntimeError(f"Previous runtime owner is {status}; refusing a second writer")
        run = s.offline_run
        if (s.legal_offline and run and run.get("serviceId") != _offline.SERVICE_ID
                and run.get("processes")):
            status = await asyncio.to_thread(_offline.previous_owner_status, run)
            if status != "dead":
                raise RuntimeError(f"Previous idle owner is {status}; refusing a second writer")
        unresolved = _offline.outstanding(s)
        identities = {(op["token"], op["generation"]): op for op in unresolved}
        dead = []
        for identity in identities.values():
            if (tracked_owner and _offline.same_owner(identity, _offline.owner(tracked_owner))
                    and not _runtime_stopped(tracked_owner)):
                continue
            status = await asyncio.to_thread(_offline.previous_owner_status, identity)
            if status != "dead":
                raise RuntimeError(f"Previous execution owner is {status}; refusing a second writer")
            dead.append(identity)
        def classify(candidate):
            _migrate_queue_delivery_state(candidate, restore_ledger=True)
            for identity in dead:
                _offline.detect(candidate, "unfinished-run", identity=identity, eligible=lambda item: (
                    _delivery_state(item) == _DELIVERY_QUEUED and not _queue_item_locked(item)),
                    remember=lambda item: _remember_offline_lock(candidate, item))
        changed = await _commit_offline(s, classify) if dead else False
    if changed:
        await _offline_snapshot_event(s)


async def initialize_legal_offline(sessions) -> None:
    # Each Session is independently retryable at create_worker, including a
    # failed startup write. No consumer exists before that same preparation.
    for s in sessions:
        try:
            await _prepare_offline_recovery(s)
        except Exception:
            _log.exception("Legality initialization deferred for Session %s", s.id)


async def _claim_offline_owner(w: Worker) -> None:
    """Register the process without claiming readiness or recovering any lock."""
    s = _session(w)
    if s is None:
        return  # Cleanup of a deleted Session must not recreate its metadata.
    identity = _offline.owner(w)
    async with queue_lock(w.session_id):
        def claim(candidate):
            if (workers.get(w.worker_id) is not w or w.offline_token != identity["token"]
                    or w.generation != identity["generation"]):
                raise RuntimeError("Runtime changed before owner registration")
            candidate.offline_owner = dict(identity)
        await _commit_offline(s, claim)


async def _activate_offline_worker(w: Worker, *, ready_proof: bool = False,
                                   expected_owner: dict | None = None) -> None:
    if w._host_exit_illegal:
        return
    if expected_owner is not None and (w.offline_token != expected_owner["token"]
            or w.generation != expected_owner["generation"]):
        return
    if not ready_proof or w._offline_ready_token == w.offline_token:
        return
    s = _session(w)
    if s is None:
        raise RuntimeError("Session disappeared before Worker activation")
    identity = _offline.owner(w)
    async with queue_lock(s.id):
        if workers.get(w.worker_id) is not w or w.offline_token != identity["token"]:
            raise RuntimeError("Worker changed before activation")
        def activate(candidate):
            if (w._host_exit_illegal or workers.get(w.worker_id) is not w or w.offline_token != identity["token"]
                    or w.generation != identity["generation"]):
                raise RuntimeError("Worker changed while activation waited for persistence")
            return _offline.activate(candidate, identity, predecessors=w._offline_predecessors,
                                     remember=lambda item: _remember_offline_lock(candidate, item))
        changed = await _commit_offline(s, activate)
    if w.offline_token != identity["token"] or w.generation != identity["generation"]:
        return
    w._offline_ready_token = identity["token"]
    if changed:
        await _offline_snapshot_event(s)
        if w.pending_signal is not None:
            w.pending_signal.put_nowait({"type": "queue_signal"})


async def _begin_offline_run(w: Worker, *, locked: bool = False) -> dict:
    identity = _offline.owner(w)
    s = _session(w)
    if _shutdown_started or w._host_exit_illegal:
        raise RuntimeError("Host is shutting down")
    if s is None:
        raise RuntimeError("Session disappeared before execution")
    async def commit():
        def begin(candidate):
            if (_shutdown_started or w._host_exit_illegal or workers.get(w.worker_id) is not w or w.offline_token != identity["token"]
                    or w.generation != identity["generation"] or w.status == "restarting"):
                raise RuntimeError("Stale execution owner")
            _offline.begin(candidate, identity)
            candidate.offline_run["autoReportAdmission"] = copy.deepcopy(w._auto_report_admission)
        await _commit_offline(s, begin)
    if locked:
        await commit()
    else:
        async with queue_lock(s.id):
            await commit()
    return identity


async def _finish_offline_stop(w: Worker, identity: dict, reason: str) -> None:
    s = _session(w)
    if w._host_exit_illegal:
        await _detect_offline_exit(w, identity, "host/signal")
        return
    if s is not None and _offline.outstanding(s, identity):
        if not _runtime_stopped(w):
            raise RuntimeError("Runtime stop is unconfirmed; legality retained")
        async with queue_lock(s.id):
            await _commit_offline(s, lambda candidate: _offline.retire_owner(candidate, identity, reason=reason,
                proof={"kind": "runtime-stopped", "owner": identity}))


async def _detect_offline_exit(w: Worker, identity: dict, reason: str) -> None:
    s = _session(w)
    if s is None:
        return
    async with queue_lock(s.id):
        if (workers.get(w.worker_id) is not w or w.offline_token != identity["token"]
                or w.generation != identity["generation"]
                or not _offline.outstanding(s, identity)):
            return
        def classify(candidate):
            if (workers.get(w.worker_id) is not w or w.offline_token != identity["token"]
                    or w.generation != identity["generation"]
                    or not _offline.outstanding(s, identity)):
                return
            _migrate_queue_delivery_state(candidate, restore_ledger=True)
            _offline.detect(candidate, reason, identity=identity, eligible=lambda item: (
                _delivery_state(item) == _DELIVERY_QUEUED and not _queue_item_locked(item)),
                remember=lambda item: _remember_offline_lock(candidate, item))
        changed = await _commit_offline(s, classify)
    if changed:
        await _offline_snapshot_event(s)


async def _record_legal_worker_state(w: Worker, state: str, source: str, *, persist: bool = True) -> bool:
    """Deprecated internal hook: operation admission/terminal owns legality now.

    Retained for embedders until removal; never writes legacy metadata.
    """
    if state not in _LEGAL_WORKER_STATES:
        raise ValueError(f"unsupported legal worker state: {state}")
    return _session(w) is not None


async def clear_session_illegal_for_startup(session_id: str, *, reconcile: bool = False) -> dict:
    """Retire only diagnosed incidents; never release a live/unknown owner."""
    lock = await _session_spawn_lock(session_id)
    async with lock:
        s = _sess.get(session_id)
        if s is None:
            return {"sessionId": session_id, "status": "error", "error": f"Session {session_id} not found"}
        async with queue_lock(session_id):
            # Do not let this policy classify or retire a new running operation.
            tracked = find_worker_by_session(session_id)
            if tracked is not None and not _runtime_stopped(tracked):
                raise RuntimeError("Worker runtime is not stopped")
            incidents = [op for op in _offline.outstanding(s) if reconcile or op.get("state") == "illegal"]
            for identity in [s.offline_owner, *incidents]:
                if identity and await asyncio.to_thread(_offline.previous_owner_status, identity) != "dead":
                    raise RuntimeError("Execution owner alive or unknown; refusing Illegal retirement")
            def clear(candidate):
                if reconcile:
                    _migrate_queue_delivery_state(candidate, restore_ledger=True)
                    for identity in incidents:
                        _offline.detect(candidate, "manual/mark-safe-offline", identity=identity,
                            eligible=lambda item: _delivery_state(item) == _DELIVERY_QUEUED and not _queue_item_locked(item),
                            remember=lambda item: _remember_offline_lock(candidate, item))
                return _offline.clear_illegal(candidate, remember=lambda item: _remember_offline_lock(candidate, item))
            changed = await _commit_offline(s, clear)
        result = {"sessionId": session_id, "status": "updated", "legalityDiagnostics": _offline.diagnostics(s),
                  "legalOffline": s.legal_offline, "illegalOffline": _offline.is_illegal(s)}
    # create_worker takes the same spawn lock: release it before requesting
    # immediate recovery. Persistence must succeed before any consumer wakes.
    try:
        await _offline_snapshot_event(s)
    finally:
        if changed and _has_dispatchable_items(s):
            await _wake_worker(session_id, auto_spawn=False)
    return result


async def set_session_legal_worker_state_running(session_id: str, *, source: str = "manual/mark-illegal") -> dict:
    """Explicitly mark an offline Session Illegal, using the existing incident ledger."""
    lock = await _session_spawn_lock(session_id)
    async with lock:
        s = _sess.get(session_id)
        if s is None:
            return {"sessionId": session_id, "status": "error", "error": f"Session {session_id} not found"}
        async with queue_lock(session_id):
            tracked = find_worker_by_session(session_id)
            if tracked is not None and not _runtime_stopped(tracked):
                raise RuntimeError("Worker runtime is not stopped")
            for identity in [s.offline_owner, *_offline.outstanding(s)]:
                if identity and await asyncio.to_thread(_offline.previous_owner_status, identity) != "dead":
                    raise RuntimeError("Execution owner alive or unknown; refusing manual state change")
            def mark(candidate):
                if _offline.is_illegal(candidate):
                    return
                _migrate_queue_delivery_state(candidate, restore_ledger=True)
                if not _offline.outstanding(candidate):
                    # Administrative state change, not an invented provider execution.
                    identity = {"token": uuid.uuid4().hex, "generation": 0, "executionSeq": 0,
                                "serviceId": _offline.SERVICE_ID, "processes": []}
                    _offline.admit(candidate, identity, kind="manual-recovery")
                for identity in list(_offline.outstanding(candidate)):
                    _offline.detect(candidate, source, identity=identity,
                        eligible=lambda item: _delivery_state(item) == _DELIVERY_QUEUED and not _queue_item_locked(item),
                        remember=lambda item: _remember_offline_lock(candidate, item))
            await _commit_offline(s, mark)
        await _offline_snapshot_event(s)
        return {"sessionId": session_id, "status": "updated", "legalOffline": s.legal_offline,
                "illegalOffline": _offline.is_illegal(s), "legalityDiagnostics": _offline.diagnostics(s)}


def _terminal_enrichment_key(
    task_id: str | None, task_seq: int | None, worker_id: str,
    generation: int, *, task_idempotent: bool = True,
) -> str:
    """Build the durable idempotency key for one terminal turn."""
    # An inherited agent_send/agent_send_force row deliberately carries the
    # current formal task id for report pairing.  It is not the formal assign
    # idempotency key: each queue row has its own sequence and must get its own
    # terminal identity, otherwise a follow-up can be swallowed as a durable
    # duplicate of the preceding assign.
    if task_id and task_idempotent:
        return f"task:{task_id}"
    if task_seq is not None:
        return f"seq:{task_seq}"
    # Report batches and compatibility callers can lack both fields.  Their
    # runtime _terminal_handled guard still suppresses duplicate events while
    # this key makes a crash after the base save recoverable.
    return f"worker:{worker_id}:generation:{generation}:{uuid.uuid4().hex}"


def _queue_usage_enrichment(
    s, adapter: CliAdapter, *, task_id: str | None, task_seq: int | None,
    worker_id: str, generation: int, task_idempotent: bool = True,
) -> str | None:
    """Append one durable post-terminal usage job, without changing queues."""
    if not callable(getattr(adapter, "enrich_after_result", None)):
        return None
    # Every built-in provider lookup is keyed by its native Session id.  A
    # compatibility/direct stream caller without that id has no recoverable
    # usage source, so do not create a no-op durable job (or an extra save).
    if not s.cli_session_id:
        return None
    key = _terminal_enrichment_key(
        task_id, task_seq, worker_id, generation,
        task_idempotent=task_idempotent,
    )
    pending = getattr(s, "usage_enrichment_pending", None)
    if not isinstance(pending, list):
        pending = s.usage_enrichment_pending = []
    if any(isinstance(job, dict) and job.get("key") == key for job in pending):
        _usage_enrichment_adapters.setdefault(key, adapter)
        return key
    pending.append({
        "key": key,
        "adapter": getattr(adapter, "name", None) or s.adapter,
        "taskId": task_id,
        "taskSeq": task_seq,
        "workerId": worker_id,
        "generation": generation,
        "state": "pending",
        "attempts": 0,
        "nextAttemptAt": 0.0,
        "createdAt": time.time(),
    })
    # Keep the exact Worker adapter available for the current process (useful
    # for registered extensions and tests); a restart falls back to the
    # persisted adapter name below.
    _usage_enrichment_adapters[key] = adapter
    return key


def _has_pending_usage_enrichment(s) -> bool:
    return bool(
        s is not None
        and isinstance(getattr(s, "usage_enrichment_pending", None), list)
        and s.usage_enrichment_pending
    )


def _enrichment_job(s, key: str) -> dict | None:
    for job in getattr(s, "usage_enrichment_pending", []) or []:
        if isinstance(job, dict) and job.get("key") == key:
            return job
    return None


@dataclass
class _UsageEnrichmentSnapshot:
    """Minimal provider input/output state for background usage lookup.

    Adapter enrichment only needs the provider session id, workdir, model,
    accumulated usage, and adapter cursor/config.  Keeping this as a small
    object is intentional: ``copy.deepcopy(Session)`` would also copy the
    complete history, queue, and attachment-related state while the provider
    is running in a background thread.
    """

    session_id: str
    cli_session_id: str | None
    workdir: str
    model: str | None
    model_before: str | None
    raw_usage: dict | None
    adapter_config: dict
    adapter_config_before: dict

    def set_adapter_field(self, key: str, value):
        """Match ``Session.set_adapter_field`` for provider cursors."""
        if value is not None and value != "" and value is not False:
            self.adapter_config[key] = value
        else:
            self.adapter_config.pop(key, None)


def _make_usage_enrichment_snapshot(s) -> _UsageEnrichmentSnapshot:
    """Copy only fields that provider usage readers can inspect or advance."""
    adapter_config = copy.deepcopy(s.adapter_config)
    return _UsageEnrichmentSnapshot(
        session_id=s.id,
        cli_session_id=s.cli_session_id,
        workdir=s.workdir,
        model=s.model,
        model_before=s.model,
        raw_usage=copy.deepcopy(s.raw_usage),
        adapter_config=adapter_config,
        adapter_config_before=copy.deepcopy(adapter_config),
    )


_USAGE_FIELD_MISSING = object()


def _merge_usage_enrichment_state(
    s: _sess.Session, snapshot: _UsageEnrichmentSnapshot,
) -> tuple[dict, str | None]:
    """Merge provider-side model/cursor changes, returning what was applied.

    A field is only written when the live Session still holds the value the
    snapshot was taken from, so a concurrent update always wins.  The returned
    mapping records *exactly* which fields this call wrote (and the value it
    wrote), which is what a later rollback must be allowed to undo.  Anything the
    merge skipped must never be rolled back -- it belongs to whoever changed it.
    """
    # ``model`` lives on the Session itself, not in ``adapter_config``, so it is
    # tracked separately rather than sharing the cursor keyspace (no adapter
    # writes ``adapter_config["model"]``, but overloading the dict would make that
    # an invariant the rollback silently depends on).
    applied: dict = {}
    applied_model: str | None = None
    if snapshot.model != snapshot.model_before and s.model == snapshot.model_before:
        s.model = snapshot.model
        applied_model = snapshot.model

    missing = _USAGE_FIELD_MISSING
    before_config = snapshot.adapter_config_before
    current_config = s.adapter_config
    for key in set(before_config) | set(snapshot.adapter_config):
        before = before_config.get(key, missing)
        after = snapshot.adapter_config.get(key, missing)
        if before == after:
            continue
        current = current_config.get(key, missing)
        # Compare-and-merge: a concurrent live update owns the field and must
        # win over the detached provider snapshot.
        if current != before:
            continue
        if after is missing:
            current_config.pop(key, None)
        else:
            current_config[key] = copy.deepcopy(after)
        applied[key] = after
    return applied, applied_model


def _restore_usage_enrichment_state(
    s: _sess.Session, snapshot: _UsageEnrichmentSnapshot, applied: dict,
    applied_model: str | None = None,
) -> None:
    """Undo exactly the fields :func:`_merge_usage_enrichment_state` wrote.

    Usage and the provider cursor are one commit unit: cbc derives "new" entries
    from ``raw_usage.request_count`` while codex/kimi/opencode persist
    ``*_prev_usage`` / ``*_last_usage_ts`` cursors. Rolling usage back while
    leaving a cursor advanced makes the next lookup return nothing, so the job
    would be consumed with the usage lost.

    Only keys present in ``applied`` are eligible, and each is restored only
    while it still holds the value this attempt wrote.  A field the merge skipped
    (because a concurrent writer had already moved it) is never touched here, so
    a rollback can never take away another writer's value.
    """
    missing = _USAGE_FIELD_MISSING
    if applied_model is not None and s.model == applied_model:
        s.model = snapshot.model_before

    current_config = s.adapter_config
    before_config = snapshot.adapter_config_before
    for key, written in applied.items():
        # Only undo our own write, and only if nobody changed it since.
        if current_config.get(key, missing) != written:
            continue
        before = before_config.get(key, missing)
        if before is missing:
            current_config.pop(key, None)
        else:
            current_config[key] = copy.deepcopy(before)


def _restore_usage_fields(s, raw_before, total_before, applied_raw) -> bool:
    """Compare-and-restore ``raw_usage``/``total_usage`` after a failed commit.

    Returns True only when this attempt's own values are still in place and were
    therefore rolled back; False when a concurrent writer already replaced the
    usage, in which case that writer keeps ownership of both fields.

    Only our exact candidate object may be restored. Production usage writers
    are gated; this ownership check also protects independent field updates.
    """
    if s.raw_usage is not applied_raw:
        return False
    s.raw_usage = raw_before
    s.total_usage = total_before
    return True


def _commit_usage_enrichment(
    s: _sess.Session, snapshot: _UsageEnrichmentSnapshot,
    entries: list[dict] | None, job: dict,
) -> None:
    """Publish/save/rollback in ONE persistence ticket, without an await.

    Other Session saves share this ticket queue. A failed writer restores its
    accounting before the next ticket can serialize it, so terminal/history
    saves cannot accidentally persist a failed usage candidate.
    """
    raw_before = copy.deepcopy(s.raw_usage)
    total_before = copy.deepcopy(s.total_usage)
    candidate_raw = s.raw_usage
    applied: dict = {}
    applied_model: str | None = None
    removed = False
    try:
        if entries:
            candidate_raw = _sess.accumulate_raw_usage(
                copy.deepcopy(s.raw_usage), entries)
            candidate_total = _sess.compute_total_usage(candidate_raw)
            s.raw_usage = candidate_raw
            s.total_usage = candidate_total
        applied, applied_model = _merge_usage_enrichment_state(s, snapshot)
        # Preserve the list terminal processing appends to on the event loop.
        # Replacing it from this writer thread could strand an append holding
        # the previous list reference. Remove only this job, in place.
        pending = s.usage_enrichment_pending
        for candidate in list(pending):
            if isinstance(candidate, dict) and candidate.get("key") == job.get("key"):
                pending.remove(candidate)
        removed = True
        _sess._save_body(s)
    except BaseException:
        if entries:
            _restore_usage_fields(s, raw_before, total_before, candidate_raw)
        _restore_usage_enrichment_state(s, snapshot, applied, applied_model)
        if removed and _enrichment_job(s, job.get("key")) is None:
            s.usage_enrichment_pending.insert(0, job)
        raise


async def _run_usage_enrichment(session_id: str) -> None:
    """Run durable usage jobs serially for one Session.

    Adapter lookup and the whole synchronous enrich call run in a worker
    thread.  The Session lock serializes post-processing jobs; the provider
    receives a minimal snapshot so a later terminal path can still
    persist/broadcast without waiting for provider I/O or racing a provider
    cursor write.
    """
    lock = _usage_enrichment_lock(session_id)
    async with lock:
        async def retry_job(s, job, exc) -> bool:
            current = _enrichment_job(s, job.get("key"))
            if current is None:
                # Another recovery path completed this exact durable job.
                return False
            try:
                attempts = max(0, int(current.get("attempts", 0))) + 1
            except (TypeError, ValueError):
                attempts = 1
            delay = min(
                _ENRICH_RETRY_MAX_SEC,
                _ENRICH_RETRY_BASE_SEC * (2 ** min(attempts - 1, 5)),
            )
            current.update({
                "state": "retrying",
                "attempts": attempts,
                "lastError": f"{type(exc).__name__}: {exc}",
                "nextAttemptAt": time.time() + delay,
            })
            try:
                await _sess.save_async(s)
            except Exception:
                _log.exception(
                    "[Session %s] failed to persist usage retry state key=%s",
                    session_id, current.get("key"),
                )
            _log.warning(
                "[Session %s] usage enrichment failed key=%s attempt=%d; "
                "retrying in %.1fs: %s",
                session_id, current.get("key"), attempts, delay, exc,
            )
            await asyncio.sleep(delay)
            return True

        while True:
            s = _get_session_shallow(session_id)
            if s is None:
                return
            pending = getattr(s, "usage_enrichment_pending", None)
            if not isinstance(pending, list) or not pending:
                return
            job = pending[0]
            if not isinstance(job, dict) or not job.get("key"):
                pending.pop(0)
                try:
                    await _sess.save_async(s)
                except Exception:
                    _log.exception(
                        "[Session %s] failed to discard malformed usage enrichment job",
                        session_id,
                    )
                    return
                continue

            next_attempt = job.get("nextAttemptAt", 0.0)
            if isinstance(next_attempt, (int, float)) and next_attempt > time.time():
                await asyncio.sleep(min(next_attempt - time.time(), _ENRICH_RETRY_MAX_SEC))
                continue

            # Snapshot only after any reimport writer has retired. Provider
            # I/O remains outside the gate, so terminal progress is unblocked.
            async with _sess.usage_commit_lock(s):
                lookup_revision = _sess.usage_revision(s)
                enrichment_session = _make_usage_enrichment_snapshot(s)
            try:
                adapter = _usage_enrichment_adapters.get(job.get("key"))
                if adapter is None:
                    adapter = get_adapter(job.get("adapter") or s.adapter)
                enrichment = await asyncio.to_thread(
                    adapter.enrich_after_result, enrichment_session,
                )
                if inspect.isawaitable(enrichment):
                    enrichment = await enrichment
            except asyncio.CancelledError:
                # The live Session was not touched by provider execution, so
                # the durable pending job remains for the next generation.
                raise
            except Exception as exc:
                if await retry_job(s, job, exc):
                    continue
                return

            async with _sess.usage_commit_lock(s):
                # Reimport completed during lookup. Its native cursor and usage
                # are authoritative; retain the job and query again on that base.
                if _sess.usage_revision(s) != lookup_revision:
                    continue
                try:
                    await _sess._persist_async_outcome(
                        s.id, lambda: _commit_usage_enrichment(
                            s, enrichment_session, enrichment, job),
                    )
                except asyncio.CancelledError:
                    # The synchronous commit rolls back on writer failure before
                    # retiring its ticket. Successful writes remain published.
                    raise
                except Exception as exc:
                    failed = exc
                else:
                    failed = None
            if failed is not None:
                if await retry_job(s, job, failed):
                    continue
                return

            _usage_enrichment_adapters.pop(job.get("key"), None)
            _log.info(
                "[Session %s] usage enrichment complete key=%s attempts=%s",
                session_id, job.get("key"), job.get("attempts", 0),
            )


def _schedule_usage_enrichment(session_id: str) -> asyncio.Task | None:
    """Start at most one Session-scoped usage worker."""
    if _shutdown_started:
        return None
    existing = _usage_enrichment_tasks.get(session_id)
    if existing is not None and not existing.done():
        return existing
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(
        _run_usage_enrichment(session_id),
        name=f"pan-usage-enrich-{session_id}",
    )
    _usage_enrichment_tasks[session_id] = task

    def _finish(done: asyncio.Task) -> None:
        if _usage_enrichment_tasks.get(session_id) is done:
            _usage_enrichment_tasks.pop(session_id, None)
        if done.cancelled():
            return
        try:
            done.result()
        except Exception:
            _log.exception(
                "[Pan] usage enrichment worker crashed for session=%s",
                session_id,
            )

    task.add_done_callback(_finish)
    return task


def recover_pending_usage_enrichment() -> int:
    """Schedule persisted usage jobs after startup/recovery scans."""
    scheduled = 0
    for s in _list_sessions_shallow():
        if _has_pending_usage_enrichment(s):
            if _schedule_usage_enrichment(s.id) is not None:
                scheduled += 1
    return scheduled


# ── session error samples ──
# The sample log is diagnostic only: these helpers never change status,
# retries, queue state or legality.  Every call is synchronous and swallows
# its own write failure inside session_error_log.

_TOOL_ITEM_TYPES = frozenset({
    "commandexecution", "mcptoolcall", "functioncall", "dynamictoolcall",
    "filechange", "patchapply", "websearch",
})

# A stream line over the parser cap is only read back in full when its JSON
# envelope can be an error.  Recognition is order-independent and top-level
# only: scanning tracks string/depth state instead of regex-matching nested
# "type" text, so {"error": ..., "type": "result", ...} is handled.
_TOP_LEVEL_ERROR_TYPES = frozenset({
    "error", "codex.turn_error", "codex.item_error", "pan.native_error",
})
_TOP_LEVEL_INTEREST = frozenset({
    "type", "is_error", "cancelled", "is_cancelled",
})


def _decode_json_token(buf: bytearray):
    try:
        return json.loads(b'"' + bytes(buf) + b'"')
    except (ValueError, TypeError):
        return bytes(buf).decode("utf-8", errors="replace")


def _decode_json_scalar(buf: bytearray):
    try:
        return json.loads(bytes(buf).strip().decode("utf-8"))
    except (ValueError, TypeError):
        return None


def _decide_oversized_fields(fields: dict):
    """Return True/False when the top-level fields already decide, else None."""
    if "type" not in fields:
        return None
    value = fields.get("type")
    if value not in _TOP_LEVEL_ERROR_TYPES and value != "result":
        return False
    if value != "result":
        return True
    if (fields.get("is_error") is True
            or fields.get("cancelled") is True
            or fields.get("is_cancelled") is True):
        return True
    return None


def _classify_oversized_json(prefix: bytes, spill) -> bool:
    """Order-independent, top-level-only error-envelope detection.

    Streams the prefix and the spill file; keeps only the current top-level
    key/scalar.  Nested objects/arrays are skipped by depth, so a nested
    ``"type":"result"`` inside assistant/tool text cannot match.  ``type`` may
    appear after 256 KiB because the spill is scanned too.
    """
    def chunks():
        yield prefix
        spill.seek(0)
        while True:
            chunk = spill.read(65536)
            if not chunk:
                return
            yield chunk

    depth = 0
    in_string = False
    escape = False
    buf = bytearray()
    state = "start"  # start | key | colon | value | after_value
    current_key = None
    fields: dict = {}

    for chunk in chunks():
        for byte in chunk:
            if in_string:
                collect = depth == 1 and (
                    state == "key"
                    or (state == "value" and current_key in _TOP_LEVEL_INTEREST)
                )
                if escape:
                    if collect:
                        buf.append(byte)
                    escape = False
                elif byte == 0x5C:  # backslash
                    if collect:
                        buf.append(byte)
                    escape = True
                elif byte == 0x22:  # quote
                    in_string = False
                    if depth == 1:
                        if state == "key":
                            current_key = _decode_json_token(buf)
                            buf.clear()
                            state = "colon"
                        elif state == "value":
                            if current_key in _TOP_LEVEL_INTEREST:
                                fields[current_key] = _decode_json_token(buf)
                                buf.clear()
                                decision = _decide_oversized_fields(fields)
                                if decision is not None:
                                    return decision
                            state = "after_value"
                else:
                    if collect:
                        buf.append(byte)
                continue

            if depth == 0:
                if byte == 0x7B:  # {
                    depth = 1
                    state = "key"
                elif byte not in b" \t\r\n":
                    return False
                continue

            if byte == 0x22:  # string starts
                in_string = True
                escape = False
                buf.clear()
                continue

            if depth == 1:
                if byte in (0x7B, 0x5B):  # nested { or [
                    depth += 1
                    if depth == 2:
                        state = "after_value"
                    continue
                if byte == 0x7D:  # }
                    if (state == "value" and buf
                            and current_key in _TOP_LEVEL_INTEREST):
                        fields[current_key] = _decode_json_scalar(buf)
                    depth -= 1
                    if depth == 0:
                        break
                    continue
                if byte == 0x5D:  # ]
                    depth -= 1
                    continue
                if byte == 0x3A:  # :
                    if state == "colon":
                        state = "value"
                    continue
                if byte == 0x2C:  # ,
                    if (state == "value" and buf
                            and current_key in _TOP_LEVEL_INTEREST):
                        fields[current_key] = _decode_json_scalar(buf)
                        buf.clear()
                        decision = _decide_oversized_fields(fields)
                        if decision is not None:
                            return decision
                    state = "key"
                    continue
                if byte in b" \t\r\n":
                    continue
                if state == "value":
                    if current_key in _TOP_LEVEL_INTEREST:
                        buf.append(byte)
                continue

            # depth > 1: skip nested structure without buffering content
            if byte in (0x7B, 0x5B):
                depth += 1
            elif byte in (0x7D, 0x5D):
                depth -= 1
        if depth == 0:
            break

    return bool(_decide_oversized_fields(fields))


def _raw_capture(w: Worker):
    handle = getattr(w, "_stream_raw_spill", None)
    if handle is None:
        handle = tempfile.TemporaryFile()
        w._stream_raw_spill = handle
        w._stream_raw_line_count = 0
    return handle


def _capture_raw_line(w: Worker, line: bytes) -> None:
    """Attribute one unparsed stream line as raw diagnostic text, not error."""
    handle = _raw_capture(w)
    handle.write(line)
    if not line.endswith(b"\n"):
        handle.write(b"\n")
    w._stream_raw_line_count = int(getattr(w, "_stream_raw_line_count", 0) or 0) + 1


def _capture_raw_oversized(w: Worker, prefix: bytes, spill) -> None:
    """Copy an oversized unparsed line to the diagnostic spill without RAM."""
    handle = _raw_capture(w)
    handle.write(prefix)
    spill.seek(0)
    while True:
        chunk = spill.read(65536)
        if not chunk:
            break
        handle.write(chunk)
    handle.write(b"\n")
    w._stream_raw_line_count = int(getattr(w, "_stream_raw_line_count", 0) or 0) + 1


def _raw_capture_source(w: Worker):
    handle = getattr(w, "_stream_raw_spill", None)
    count = int(getattr(w, "_stream_raw_line_count", 0) or 0)
    if handle is None or count <= 0:
        return None
    return (handle, count)


def _reset_raw_capture(w: Worker) -> None:
    handle = getattr(w, "_stream_raw_spill", None)
    if handle is not None:
        try:
            handle.seek(0)
            handle.truncate(0)
        except OSError:
            pass
    w._stream_raw_line_count = 0


def _close_raw_capture(w: Worker) -> None:
    handle = getattr(w, "_stream_raw_spill", None)
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass
    w._stream_raw_spill = None
    w._stream_raw_line_count = 0


def _looks_like_structured_text(text: str) -> bool:
    """True only for a complete JSON object/array, not for arbitrary text.

    A log line such as ``[second stderr line]`` starts with ``[`` but is not
    JSON; it must be attributed as raw diagnostic text.
    """
    stripped = text.lstrip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
        return True
    except (ValueError, TypeError):
        return False


def _oversized_error_event(adapter, prefix: bytes, spill):
    spill.seek(0)
    full = prefix + spill.read()
    event = adapter.parse_event(full.decode("utf-8", errors="replace"))
    if not isinstance(event, dict):
        return None
    if event.get("type") == "result" and not (
            event.get("is_error")
            or event.get("cancelled")
            or event.get("is_cancelled")):
        return None
    return event


async def _classify_oversized_line(adapter, raw_line: bytes, spill):
    """Classify an over-cap line as error / raw diagnostic / normal.

    Returns ``("error", event)``, ``("raw", None)`` or ``("drop", None)``.
    The order-independent top-level scan runs off the event loop; the full
    event is read only after the scan says it is error-shaped.
    """
    prefix = bytes(raw_line)[:_STDOUT_MAX_LINE]
    if not prefix.lstrip().startswith(b"{"):
        return "raw", None
    shaped = await asyncio.to_thread(_classify_oversized_json, prefix, spill)
    if not shaped:
        return "drop", None
    event = _oversized_error_event(adapter, prefix, spill)
    if event is None:
        return "drop", None
    return "error", event


def _error_log_identity(w: Worker, s) -> dict:
    item = getattr(w, "_current_queue_item", None)
    queue_item_id = None
    if isinstance(item, dict):
        value = item.get("queueItemId") or item.get("id")
        if isinstance(value, str) and value:
            queue_item_id = value
    return {
        "session": s,
        "session_id": getattr(w, "session_id", None),
        "session_name": getattr(s, "name", None),
        "adapter": getattr(getattr(w, "adapter", None), "name", None),
        "model": getattr(s, "model", None),
        "worker_id": getattr(w, "worker_id", None),
        "generation": getattr(w, "generation", None),
        "task_seq": getattr(w, "_current_seq", None),
        "task_id": getattr(w, "_current_task_id", None),
        "execution_seq": getattr(w, "_execution_seq", None) or None,
        "queue_item_id": queue_item_id,
        "source_session_id": getattr(w, "_current_source_session_id", None),
    }


async def _record_terminal_error_sample(
    w: Worker, s, status: str, result_text: str | None,
    error_context: dict | None,
) -> None:
    if status not in {"error", "cancelled"}:
        return
    ctx = dict(error_context or {})
    raw = ctx.get("cli_raw")
    cli_text = None
    if isinstance(raw, dict):
        _, cli_text, _ = _error_log.cli_error_fields(raw)
    if not (isinstance(cli_text, str) and cli_text.strip()) and ctx.get("cli_error_events"):
        for candidate in reversed(ctx["cli_error_events"]):
            _, candidate_text, _ = _error_log.cli_error_fields(candidate)
            if isinstance(candidate_text, str) and candidate_text.strip():
                cli_text = candidate_text
                break
    result_text = result_text if isinstance(result_text, str) else ""
    if isinstance(cli_text, str) and cli_text.strip():
        text, source = cli_text, "cli.error"
    elif ctx.get("origin") in {"worker.stream.result", "worker.oneshot"}:
        # The provider reported a failed terminal without an error object.
        # Keep its complete text, but label it as the terminal result so no
        # assistant message can be mistaken for the error detail.
        text, source = result_text, "pan.terminal_result"
    else:
        text, source = result_text, "pan.runtime"
    may_be_assistant = bool(
        status == "error" and result_text
        and w._current_task_last_assistant_text == result_text
    )
    try:
        if ctx.get("previous_captured"):
            previous = ctx.get("previous_message")
        else:
            previous = _sess.history_tail_snapshot(s) if s is not None else None
    except Exception:
        previous = None
    await _error_log.record_session_error_async(
        kind="cancelled" if status == "cancelled" else "terminal_error",
        error_text=text,
        error_text_source=source,
        terminal=True,
        pan_status=status,
        cancelled=(status == "cancelled"),
        cli_raw=raw,
        cli_error_events=ctx.get("cli_error_events"),
        cli_raw_output=ctx.get("cli_raw_output"),
        cli_origin=ctx.get("origin"),
        cli_returncode=ctx.get("returncode"),
        cli_timed_out=ctx.get("timed_out"),
        pan_extra={
            "origin": ctx.get("origin"),
            "terminalResult": result_text,
            "terminalResultMayBeAssistantText": may_be_assistant,
            "workerStatus": w.status,
            "terminalKey": ctx.get("terminal_key"),
            "returncode": ctx.get("returncode"),
        },
        previous_message=previous,
        previous_captured=True,
        raw_diagnostics_source=_raw_capture_source(w),
        **_error_log_identity(w, s),
    )


async def _record_transient_error_sample(
    w: Worker, event: dict, kind: str, *, recovered: bool | None = None,
) -> None:
    """Record a non-terminal CLI/native/tool error without changing its flow."""
    s = _session(w)
    raw_error = event.get("error")
    text = ""
    source = "pan.runtime"
    if raw_error is not None:
        _, extracted, extracted_source = _error_log.cli_error_fields(event)
        if isinstance(extracted, str):
            text = extracted
            source = extracted_source or "cli.error"
    elif isinstance(event.get("error_text"), str):
        text = event["error_text"]
        source = "cli.error_text"
    elif isinstance(event.get("item"), dict):
        _, extracted, extracted_source = _error_log.cli_error_fields(event["item"])
        if isinstance(extracted, str):
            text = extracted
            source = extracted_source or "cli.error"
    if not text and event.get("item_status"):
        source = "cli.item_status"
    try:
        previous = _sess.history_tail_snapshot(s) if s is not None else None
    except Exception:
        previous = None
    fingerprint_source = raw_error
    if fingerprint_source is None and isinstance(event.get("item"), dict):
        fingerprint_source = event.get("item")
    try:
        fingerprint = hashlib.sha1(json.dumps(
            fingerprint_source if fingerprint_source is not None else text,
            ensure_ascii=False, sort_keys=True, default=str,
        ).encode("utf-8")).hexdigest()
    except Exception:
        fingerprint = hashlib.sha1(repr(fingerprint_source).encode("utf-8")).hexdigest()
    dedupe_token = "|".join([
        str(event.get("type") or ""),
        str(event.get("turn_id") or event.get("turnId") or ""),
        str(event.get("item_id") or event.get("itemId") or ""),
        str(recovered),
        fingerprint,
    ])
    await _error_log.record_session_error_async(
        kind=kind,
        error_text=text,
        error_text_source=source,
        terminal=False,
        recovered=recovered,
        cli_raw=event,
        cli_origin="worker.stream.event",
        pan_extra={
            "origin": "worker.stream.event",
            "workerStatus": getattr(w, "status", None),
            "itemType": event.get("item_type"),
            "itemStatus": event.get("item_status"),
            "nativeErrorCode": event.get("error") if isinstance(event.get("error"), str) else None,
        },
        previous_message=previous,
        previous_captured=True,
        dedupe_token=dedupe_token,
        **_error_log_identity(w, s),
    )


async def _record_worker_start_failure(
    s, session_id: str, worker_id: str, message: str, *,
    w: Worker | None = None, origin: str = "worker.create",
) -> None:
    try:
        previous = _sess.history_tail_snapshot(s) if s is not None else None
    except Exception:
        previous = None
    identity = (
        _error_log_identity(w, s) if w is not None else {
            "session": s,
            "session_id": session_id,
            "session_name": getattr(s, "name", None),
            "adapter": getattr(s, "adapter", None),
            "model": getattr(s, "model", None),
            "worker_id": worker_id,
            "generation": None,
        }
    )
    await _error_log.record_session_error_async(
        kind="worker_start_failure",
        error_text=message,
        error_text_source="pan.runtime",
        terminal=True,
        pan_status="error",
        pan_extra={"origin": origin, "workerStatus": "error"},
        previous_message=previous,
        previous_captured=True,
        **identity,
    )


async def _record_worker_crash(
    w: Worker, s, returncode, detail: str, *, origin: str,
) -> None:
    try:
        previous = _sess.history_tail_snapshot(s) if s is not None else None
    except Exception:
        previous = None
    await _error_log.record_session_error_async(
        kind="worker_crash",
        error_text=detail,
        error_text_source="pan.runtime",
        terminal=True,
        pan_status="error",
        pan_extra={
            "origin": origin,
            "returncode": returncode,
            "workerStatus": getattr(w, "status", None),
        },
        previous_message=previous,
        previous_captured=True,
        raw_diagnostics_source=_raw_capture_source(w),
        **_error_log_identity(w, s),
    )


async def _persist_terminal_state(
    w: Worker, s, status: str, result_text: str | None, *, expected_owner: dict | None = None,
    error_context: dict | None = None,
) -> dict | None:
    """Persist the minimum terminal fact set before any completion broadcast."""
    if expected_owner is not None and (w.offline_token != expected_owner["token"]
            or w.generation != expected_owner["generation"]):
        return None
    if not w._terminal_handled:
        w._terminal_finishing = True
    try:
        async with queue_lock(w.session_id):
            if expected_owner is not None and (
                    workers.get(w.worker_id) is not w
                    or w.offline_token != expected_owner["token"]
                    or w.generation != expected_owner["generation"]):
                return None
            async with unread_done_lock(w.session_id):
                return await _persist_terminal_state_locked(
                    w, s, status, result_text, error_context=error_context)
    except BaseException as exc:
        # Persistence owns its rollback/committed-outcome decision. Never turn
        # an aborted terminal chain into an idle restart or hide its exception.
        w._terminal_finishing = False
        if not isinstance(exc, asyncio.CancelledError):
            _log.error("[Worker %s] terminal persistence failed session=%s generation=%s",
                       w.worker_id, w.session_id, w.generation, exc_info=True)
        _abort_delayed_restart(w, "terminal persistence", exc)
        raise


async def _persist_terminal_state_locked(
    w: Worker, s, status: str, result_text: str | None,
    *, error_context: dict | None = None,
) -> dict | None:
    if w._host_exit_illegal:
        return None
    if w._terminal_handled:
        _log.warning(
            "[Worker %s] duplicate terminal event ignored generation=%s",
            w.worker_id, w.generation,
        )
        return None

    task_seq = w._current_seq
    task_id = w._current_task_id
    task_idempotent = w._current_task_idempotent
    source_session_id = w._current_source_session_id
    terminal_key = _terminal_enrichment_key(
        task_id, task_seq, w.worker_id, w.generation,
        task_idempotent=task_idempotent,
    )
    result_text = result_text if result_text is not None else ""
    w.status = status
    if s is None:
        # There is no durable Session to protect.  Callers must not publish a
        # terminal result that cannot be reconciled through session_get.
        raise RuntimeError(
            f"Session {w.session_id} disappeared before terminal persistence"
        )
    prior_result = s.last_result if isinstance(s.last_result, dict) else None
    async def finish_replayed_run():
        # The existing terminal key proves this accepted idempotent execution
        # is already safe. Settle its current legality without rebroadcasting
        # results or treating an arbitrary replay as a new Worker activation.
        identity = _offline.owner(w)
        if s.legal_offline is False and _offline.same_owner(s.offline_run, identity):
            await _commit_offline(s, lambda candidate: _offline.finish(
                candidate, identity, reason="task/durable-terminal-replay"))
    if (prior_result and prior_result.get("terminalKey") == terminal_key
            and prior_result.get("status") == status):
        # A replacement Worker can observe the provider's already-completed
        # turn again.  The durable terminal key suppresses a second result
        # broadcast when the in-memory guard was lost with the old Worker.
        _log.warning(
            "[Worker %s] durable duplicate terminal ignored key=%s",
            w.worker_id, terminal_key,
        )
        await finish_replayed_run()
        w.status = "idle"
        w._terminal_handled = True
        # A duplicate terminal is still a terminal for the current consumer
        # generation.  Without waking _consumer_stream, a durable duplicate
        # can strand the serial queue until the watchdog removes the worker.
        _signal_task_done(w)
        w.last_activity = time.monotonic()
        return None

    # ``last_result`` is deliberately only the latest compatibility view.  A
    # reconnect may cross several terminal turns, so keep a small durable
    # ordered window as the source for cursor replay.  Check the window before
    # allocating a new cursor as well; a replacement Worker may lose the
    # in-memory terminal guard after a crash.
    prior_terminals = [
        item for item in (getattr(s, "terminal_results", None) or [])
        if isinstance(item, dict)
    ]
    if any(
        item.get("terminalKey") == terminal_key
        and item.get("status") == status
        for item in prior_terminals
    ):
        _log.warning(
            "[Worker %s] durable terminal replay duplicate ignored key=%s",
            w.worker_id, terminal_key,
        )
        await finish_replayed_run()
        w.status = "idle"
        w._terminal_handled = True
        _signal_task_done(w)
        w.last_activity = time.monotonic()
        return None

    # Record the error sample before the base commit mutates terminal state.
    # The previous-message snapshot is taken synchronously inside this call,
    # so a later report/steer/native append cannot replace it.
    if status in {"error", "cancelled"}:
        await _record_terminal_error_sample(
            w, s, status, result_text,
            {**(error_context or {}), "terminal_key": terminal_key},
        )

    try:
        result_cursor = max(
            int(getattr(s, "result_cursor", 0) or 0),
            max((int(item.get("resultCursor", 0) or 0)
                 for item in prior_terminals), default=0),
        ) + 1
    except (TypeError, ValueError):
        result_cursor = max(0, int(getattr(s, "result_cursor", 0) or 0)) + 1
    old_terminal_state = {
        "result_cursor": s.result_cursor,
        "unread_done_generation": s.unread_done_generation,
        "unread_done_read_generation": s.unread_done_read_generation,
        "unread_done_count": s.unread_done_count,
        "active_task_id": s.active_task_id,
        "usage_enrichment_pending": list(s.usage_enrichment_pending),
    }
    old_runtime_state = {
        "_current_queue_item": w._current_queue_item,
        "_current_report_items": list(w._current_report_items),
        "_current_task_assistant_ids": set(w._current_task_assistant_ids),
    }
    s.result_cursor = result_cursor
    terminal_record = {
        "resultCursor": result_cursor,
        "terminalKey": terminal_key,
        "status": status,
        "result": result_text,
        "autoReportAdmission": copy.deepcopy(w._auto_report_admission),
        "taskSeq": task_seq,
        "executionSeq": w._execution_seq or None,
        "taskId": task_id,
        "taskIdempotent": task_idempotent,
        "workerId": w.worker_id,
        "generation": w.generation,
        "sourceSessionId": source_session_id,
        "timestamp": datetime.now().isoformat(),
        "historyEpoch": getattr(s, "history_epoch", None),
        "historyRevision": getattr(s, "history_revision", 0),
    }
    s.terminal_results = (
        prior_terminals + [terminal_record]
    )[-_sess.RESULT_REPLAY_MAX_ENTRIES:]

    s.last_result = {
        "status": status,
        "result": result_text,
        "cli_session_id": s.cli_session_id,
        "timestamp": datetime.now().isoformat(),
        "taskSeq": task_seq,
        "executionSeq": w._execution_seq or None,
        "taskId": task_id,
        "taskIdempotent": task_idempotent,
        "workerId": w.worker_id,
        "generation": w.generation,
        "resultCursor": result_cursor,
        "sourceSessionId": source_session_id,
        "terminalKey": terminal_key,
    }
    # 未读 done 游标：只在此唯一终态入口（既有三重去重之后）累加 generation，
    # 每次真实 done 终态 +1，随本次 base commit 落盘。error/cancelled 不计；
    # report 批次在此刻仍持有 _current_report_items（下方 _ack_current_reports
    # 才清空）且 task_seq 为 None，不计入 manager 自己的 done——子 report 到达
    # 不再计一次。count 仅由 generation - read 物化，不是 ack 边界。
    # 全局未读记录策略（unread_policy）在此判定：被排除的 Session 不累计，
    # 并把可能残留的旧未读（旧数据/协调写失败）顺带对齐清零，保证
    # 「排除 ⇒ 持久化 count=0」。判定失败按既有行为记录（绝不失败终态提交）。
    from packages.core import unread_policy
    try:
        record_unread = unread_policy.should_record_unread(s)
    except Exception:
        _log.warning(
            "[Worker %s] unread-recording policy evaluation failed; "
            "recording done unread as before", w.worker_id, exc_info=True,
        )
        record_unread = True
    generation = max(0, int(getattr(s, "unread_done_generation", 0) or 0))
    read_generation = max(0, int(
        getattr(s, "unread_done_read_generation", 0) or 0))
    if record_unread:
        if (status == "done" and task_seq is not None
                and not w._current_report_items):
            generation += 1
            s.unread_done_generation = generation
            s.unread_done_read_generation = min(read_generation, generation)
            s.unread_done_count = generation - s.unread_done_read_generation
    elif read_generation < generation:
        # 排除状态：不累计，且把存量未读对齐清零（单调推进 read）。
        s.unread_done_read_generation = generation
        s.unread_done_count = 0
    if status == "done" and isinstance(result_text, str) and result_text.strip():
        last = s.history[-1] if s.history else None
        already_appended_this_turn = (
            last and last.get("role") == "assistant"
            and last.get("content") == result_text
            and last.get("messageId") in w._current_task_assistant_ids
        )
        if not already_appended_this_turn:
            entry = {"role": "assistant", "content": result_text}
            _sess.append_history(s, entry)
            # Keep this append associated with the turn if the base commit
            # fails and its terminal event is retried.
            old_runtime_state["_current_task_assistant_ids"].add(entry["messageId"])
    from packages.core import qq_reports
    old_qq_outbox = dict(s.qq_report_outbox)
    if (status in {"done", "error"} and task_seq is not None
            and not w._current_report_items):
        body = result_text if status == "error" else (w._current_task_last_assistant_text or result_text)
        if body:
            qq_reports.prepare(s, terminal_key, body)
    w._current_task_assistant_ids.clear()

    # The result must cover the canonical history revision after the final
    # append, not the revision that happened to exist when the replay record
    # was allocated above. Persist this coverage in the same base commit as
    # last_result/terminal_results before publishing result or idle.
    history_epoch = getattr(s, "history_epoch", None)
    history_revision = getattr(s, "history_revision", 0)
    terminal_record["historyEpoch"] = history_epoch
    terminal_record["historyRevision"] = history_revision
    s.last_result["historyEpoch"] = history_epoch
    s.last_result["historyRevision"] = history_revision

    # Queue rows crossed the existing provider hand-off boundary earlier; this
    # only clears runtime routing references and never changes FIFO state.
    _ack_current_task(w, s)
    _ack_current_reports(w, s)
    _clear_active_task_if_current(s, task_id)
    enrichment_key = _queue_usage_enrichment(
        s, w.adapter, task_id=task_id, task_seq=task_seq,
        worker_id=w.worker_id, generation=w.generation,
        task_idempotent=task_idempotent,
    )
    if enrichment_key:
        s.last_result["usageEnrichmentKey"] = enrichment_key

    # Keep the persisted legal state aligned with the existing lifecycle
    # ledger, but save only after history/result/job preparation is complete.
    await _record_legal_worker_state(w, status, "task/complete", persist=False)
    w.status = "idle"
    await _record_legal_worker_state(w, "idle", "task/complete-idle", persist=False)
    # This is the base commit.  If it fails, the exception prevents both
    # worker.result and idle broadcasts, as required by the terminal contract.
    outcome = _sess.DurableOutcome()
    terminal_owner = _offline.owner(w)
    offline_before = (s.legal_offline, s.offline_run)
    rolled_back = False
    def rollback_terminal():
        nonlocal rolled_back
        if rolled_back:
            return
        rolled_back = True
        s.legal_offline, s.offline_run = offline_before
        s.last_result = prior_result
        s.terminal_results = prior_terminals
        for key, value in old_terminal_state.items():
            setattr(s, key, value)
        for key, value in old_runtime_state.items():
            setattr(w, key, value)
        s.qq_report_outbox = old_qq_outbox
        w.status = status
    try:
        await _flush_history_now(w, outcome=outcome, rollback=rollback_terminal,
                                 offline_identity=terminal_owner)
    except BaseException:
        if outcome.succeeded:
            # Cancellation still propagates, but the authoritative ticket
            # committed. Keep its dedup/outbox fact and runtime terminal latch.
            w.status = status
            w._terminal_handled = True
        else:
            rollback_terminal()
        raise
    w.status = status
    w._terminal_handled = True
    return {
        "autoReportAdmission": copy.deepcopy(w._auto_report_admission or {"version": 1, "inputs": [], "enabled": False}),
        "taskSeq": task_seq,
        "executionSeq": w._execution_seq or None,
        "taskId": task_id,
        "taskIdempotent": task_idempotent,
        "resultCursor": result_cursor,
        "terminalKey": terminal_key,
        "sourceSessionId": source_session_id,
        "result": result_text,
        "status": status,
        "enrichmentKey": enrichment_key,
        "historyEpoch": getattr(s, "history_epoch", None),
        "historyRevision": getattr(s, "history_revision", 0),
    }


async def _publish_terminal_events(w: Worker, terminal: dict, s) -> None:
    """Publish result then idle, without waiting for usage enrichment."""
    from packages.core import qq_reports
    qq_reports.kick(w.session_id)
    completion_notification = _notifications.dispatch_completion_nonblocking(
        s, terminal["status"], terminal["result"],
    )
    await _bcast({
        "type": "worker.result",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "generation": w.generation,
        "status": terminal["status"],
        "result": terminal["result"],
        "taskSeq": terminal["taskSeq"],
        "executionSeq": terminal.get("executionSeq"),
        "taskId": terminal["taskId"],
        "resultCursor": terminal["resultCursor"],
        "terminalKey": terminal["terminalKey"],
        "sourceSessionId": terminal["sourceSessionId"],
        "historyEpoch": terminal.get("historyEpoch"),
        "historyRevision": terminal.get("historyRevision", 0),
        "terminalCoverage": {
            "historyEpoch": terminal.get("historyEpoch"),
            "historyRevision": terminal.get("historyRevision", 0),
        },
        **({"notification": completion_notification}
           if completion_notification else {}),
    })
    w.status = "idle"
    await _bcast({
        "type": "worker.status",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "generation": w.generation,
        "status": "idle",
        "taskSeq": terminal["taskSeq"],
        "executionSeq": terminal.get("executionSeq"),
        "sourceSessionId": terminal["sourceSessionId"],
    })


def _finish_terminal_bookkeeping(w: Worker, terminal: dict) -> None:
    """Complete in-memory idempotency bookkeeping after terminal publication."""
    task_id = terminal["taskId"]
    if terminal["taskIdempotent"] and task_id and task_id in _task_status:
        _task_status[task_id] = {
            "status": terminal["status"],
            "result": terminal["result"],
            "workerId": w.worker_id,
            "taskId": task_id,
            "ts": time.monotonic(),
        }
    w._current_task_id = None
    w._current_task_idempotent = False
    w._current_source_session_id = None
    w._auto_report_admission = None
    w.last_activity = time.monotonic()
    _signal_task_done(w)
    _maybe_restart_pending(w)
    if terminal.get("enrichmentKey"):
        _schedule_usage_enrichment(w.session_id)


def _runtime_stopped(w: Worker) -> bool:
    """Return true only after all tracked provider runtimes are stopped."""
    for process in (w.process, w._mcp_proc):
        if process is not None and getattr(process, "returncode", None) is None:
            return False
    return True


def begin_host_exit() -> None:
    """Synchronous signal fence: host signals never constitute safe completion."""
    begin_shutdown()
    for w in workers.values():
        w._host_exit_illegal = True


def begin_shutdown() -> None:
    """Close the service gate before an orderly Pan exit begins."""
    global _shutdown_started
    _shutdown_started = True
    stop_global_watchdog()
    # Writes are awaited per sample, so this only waits for an in-flight
    # executor task; it does not introduce a background queue.
    _error_log.flush()


def reopen_after_failed_shutdown() -> None:
    """Restore normal Worker admission after a lifecycle handoff fails."""
    global _shutdown_started
    _shutdown_started = False
    start_global_watchdog()


def _mcp_configured(s: _sess.Session | None) -> bool:
    """Use v2 logical bindings, or legacy descriptors, independently of output mode."""
    if s and "plugin_bindings" in s.adapter_config:
        return any(binding.get("enabled") for binding in s.adapter_config["plugin_bindings"])
    return bool(s and s.adapter_config.get("mcp_servers"))


# ── stdout reader ──


# stdout 分块读超时：仅用于周期性把事件循环交还（可取消、可重试），
# 属「cbc 静默」正常态 —— 超时不代表不活跃，不据此刷新 last_activity
# （刷新会导致 stream worker 的 idle 回收 / queued 静默超时永不触发，
# 回归来源 252c41d）。活性基准见 _read_stdout 的有效输出路径。
_STDOUT_READ_TIMEOUT_SEC: float = 60.0

# Shutdown drain: how long a cancelled-but-unresponsive recovery task (mid
# real subprocess spawn on the Windows Proactor loop) is waited for before
# giving up.  Giving up is safe: the dangling task only produces a warning at
# loop close, while awaiting it forever deadlocks the shutdown.
_DRAIN_CANCEL_GRACE_SEC: float = 1.0

_PENDING_INTERACTION_TYPES = frozenset({
    "approval.request",
    "codex.user_input",
    "codex.elicitation",
    "codex.terminal_interaction",
})


def _interaction_key(event: dict) -> str | None:
    """Return a stable key for a native interactive event."""
    event_type = event.get("type")
    if event_type in {
        "approval.request", "codex.user_input", "codex.elicitation",
    }:
        request_id = event.get("request_id")
        return f"request:{request_id}" if request_id is not None else None
    if event_type == "codex.terminal_interaction":
        item_id = event.get("item_id")
        return f"terminal:{item_id}" if item_id is not None else None
    return None


def _update_pending_interactions(w: Worker, event: dict) -> dict | None:
    """Track or retire a native interactive event for dashboard replay.

    This is deliberately worker-local and ephemeral.  If the native process
    dies, its open JSON-RPC requests die with it and the worker is removed;
    there is no safe request to replay into a newly spawned process.
    """
    event_type = event.get("type")
    if event_type == "pan.control_mode":
        future = w._native_mode_receipts.get(event.get("requestId"))
        if future is not None and not future.done():
            future.set_result(dict(event))
        elif w._native_mode_request_id is not None and event.get("requestId") == w._native_mode_request_id:
            # Timeout/cancellation is not evidence the kernel rejected the
            # handoff. Consume its late authoritative receipt, but never a
            # superseded request's frame or an unsolicited ownership claim.
            _apply_native_mode_receipt(w, event)
        return None
    if event_type == "codex.thread_status":
        native_status = event.get("native_status")
        w.native_status = dict(native_status) if isinstance(native_status, dict) else None
        return None
    if event_type == "codex.token_usage":
        token_usage = event.get("token_usage")
        w.native_usage = dict(token_usage) if isinstance(token_usage, dict) else None
        return None
    if event_type == "codex.rate_limits":
        rate_limits = event.get("rate_limits")
        if not isinstance(rate_limits, dict) or not rate_limits:
            w.native_rate_limits = None
            w.native_rate_limits_received_at = None
            w.native_rate_limits_updated_at = None
            return None
        w.native_rate_limits = dict(rate_limits)
        received_at = (
            datetime.now(timezone.utc).isoformat()
            if isinstance(rate_limits, dict) else None
        )
        w.native_rate_limits_received_at = received_at
        w.native_rate_limits_updated_at = received_at
        return {"rate_limits": dict(rate_limits), "received_at": received_at}
    if event_type == "codex.plan":
        plan = event.get("plan")
        if isinstance(plan, list):
            w.native_plan = dict(event)
        return None
    if event_type == "codex.diff":
        diff = event.get("diff")
        w.native_diff = dict(event) if isinstance(diff, str) and diff else None
        return None
    if event_type in _PENDING_INTERACTION_TYPES:
        key = _interaction_key(event)
        if key is not None:
            w.pending_interactions[key] = dict(event)
        return None
    if event_type == "codex.request_resolved":
        request_id = event.get("request_id")
        if request_id is not None:
            w.pending_interactions.pop(f"request:{request_id}", None)
        return None
    if event_type == "claude.permission_resolved":
        request_id = event.get("request_id")
        if request_id is not None:
            w.pending_interactions.pop(f"request:{request_id}", None)
        return None
    if event_type == "codex.item.completed":
        item_id = event.get("item_id")
        if item_id is not None:
            w.pending_interactions.pop(f"terminal:{item_id}", None)
        return None
    if event_type == "result":
        w.pending_interactions.clear()
        w.native_status = None
        w.native_usage = None
        w.native_plan = None
        w.native_diff = None


def pending_interaction_events(w: Worker) -> list[dict]:
    """Return copies of prompts that a dashboard may safely replay."""
    return [dict(event) for event in (w.pending_interactions or {}).values()]


def native_status_event(w: Worker) -> dict | None:
    """Return the latest native status as a replayable worker event."""
    if not w.native_status:
        return None
    return {"type": "codex.thread_status", "native_status": dict(w.native_status)}


def native_usage_event(w: Worker) -> dict | None:
    """Return the latest native usage as a replayable worker event."""
    native_usage = getattr(w, "native_usage", None)
    if not native_usage:
        return None
    return {"type": "codex.token_usage", "token_usage": dict(native_usage)}


def native_rate_limits_event(w: Worker) -> dict | None:
    """Return the latest account rate-limit snapshot for a reconnecting UI."""
    rate_limits = getattr(w, "native_rate_limits", None)
    if not rate_limits:
        return None
    return {"type": "codex.rate_limits", "rate_limits": dict(rate_limits)}


def native_plan_event(w: Worker) -> dict | None:
    """Return the current turn plan for a reconnecting dashboard."""
    plan = getattr(w, "native_plan", None)
    return dict(plan) if isinstance(plan, dict) else None


def native_diff_event(w: Worker) -> dict | None:
    """Return the current turn diff for a reconnecting dashboard."""
    diff = getattr(w, "native_diff", None)
    return dict(diff) if isinstance(diff, dict) else None


def clear_native_runtime_state(w: Worker) -> None:
    """Drop app-server state that belongs only to the current OS process.

    Codex thread identity lives on the Session and must survive a respawn;
    status, usage, and open UI prompts belong to the old app-server process and
    must not be replayed while its replacement is starting.
    """
    w.pending_interactions.clear()
    w.native_status = None
    w.native_usage = None
    w.native_rate_limits = None
    w.native_rate_limits_received_at = None
    w.native_rate_limits_updated_at = None
    w.native_plan = None
    w.native_diff = None
    # The public Worker id/object survives restart, the kernel owner does not.
    # Settle old callers without cancelling their HTTP tasks; they must never
    # apply a retiring kernel's receipt to the replacement process.
    for future in w._native_mode_receipts.values():
        if not future.done():
            future.set_result({"ok": False, "error": "Worker restarted"})
    w._native_mode_receipts.clear()
    w._native_mode_request_id = None
    w.native_control_mode = "pan"
    w.native_control_generation = 0
    w.native_control_error = None
    w.native_kernel_ready = False
    w.native_passthrough_enabled = None
    w._native_external_turn_active = False
    w._native_reservation_busy = False
    if _runtime_stopped(w):
        w.native_input_uncertain = False
    w.native_output.clear()
    w.native_output_total = 0


_STDOUT_MAX_LINE = 256 * 1024  # 单行解析上限 256KB（既有阈值，不改变）
_OVERSIZE_MARKER = b" ... [tool result truncated]"


class _CapturedLine(bytes):
    """One stdout line plus an optional spill file for its overflow.

    A plain ``bytes`` subclass keeps the existing iteration contract and tests
    working.  ``spill`` is a temporary file containing everything after the
    first ``_STDOUT_MAX_LINE`` bytes; it is closed by the generator when the
    consumer advances or the generator is closed.
    """

    def __new__(cls, value, spill=None):
        obj = super().__new__(cls, value)
        obj.spill = spill
        return obj


async def _iter_stdout_lines(w: Worker):
    """分块读取 worker stdout，按换行切分产出行对象。

    替代 ``readline()``：Windows asyncio 管道上，超大单行（如 Read 大文件的
    tool result，几百 KB ~ MB）会让 readline 永久挂起（Pan 卡死根因）。分块
    read(65536) + 自切行不依赖单次读完一行。

    解析阈值仍是 256KB。超过阈值的行只保留前 256KB 供原解析路径使用；溢出
    字节写入临时文件（``_CapturedLine.spill``），供错误保真路径在识别为错误
    后完整读取。这样不会把整行堆在内存里，也不改变既有历史截断阈值。
    """
    current = bytearray()
    spill = None
    last_spill = None
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(
                    w.process.stdout.read(65536), timeout=_STDOUT_READ_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                # cbc 静默（长思考 / idle 等待新任务）是正常状态，不代表不活跃；
                # last_activity 只由真实输出路径（_read_stdout 有效事件）与任务
                # 入队刷新。此处若刷新会让 watchdog 的 idle/queued 判定永不触发。
                _log.debug(
                    "[Worker %s] stdout 静默 %.0fs（read timeout），继续等待",
                    w.worker_id, _STDOUT_READ_TIMEOUT_SEC,
                )
                continue
            if not chunk:
                break  # EOF
            start = 0
            while True:
                newline = chunk.find(b"\n", start)
                segment = chunk[start:] if newline < 0 else chunk[start:newline]
                if segment:
                    room = max(0, _STDOUT_MAX_LINE - len(current))
                    if spill is None and len(segment) <= room:
                        current.extend(segment)
                    else:
                        if room:
                            current.extend(segment[:room])
                            segment = segment[room:]
                        if segment:
                            if spill is None:
                                spill = tempfile.TemporaryFile()
                            spill.write(segment)
                if newline < 0:
                    break
                if last_spill is not None:
                    last_spill.close()
                    last_spill = None
                if spill is not None:
                    spill.seek(0)
                    line = _CapturedLine(bytes(current) + _OVERSIZE_MARKER, spill)
                    last_spill = spill
                    spill = None
                else:
                    line = _CapturedLine(bytes(current), None)
                current.clear()
                yield line
                start = newline + 1
        if last_spill is not None:
            last_spill.close()
            last_spill = None
        if spill is not None:
            spill.seek(0)
            yield _CapturedLine(bytes(current) + _OVERSIZE_MARKER, spill)
            last_spill = spill
            spill = None
        elif current:
            yield _CapturedLine(bytes(current), None)  # 最后一行无换行（EOF 残留）
    finally:
        if last_spill is not None:
            last_spill.close()
        if spill is not None:
            spill.close()


def _signal_task_done(w: Worker) -> None:
    """标记当前 stream 任务完成，唤醒 _consumer_stream 的等待。

    与 _consumer_stream 的 ev.wait() 配对：result 处理完成（或进程 EOF）时调用，
    让 consumer 知道当前任务已结束、可以推进下一个排队任务。
    """
    w._terminal_finishing = False
    ev = getattr(w, "_task_done", None)
    if ev is not None:
        ev.set()


def _ack_current_task(w: Worker, s) -> None:
    """收到最终 result 后清理当前 task 运行上下文。

    queue item 的移除已在 provider hand-off 回调中完成；此处只清理运行期引用。
    hand-off 之前的异常由队列 coordinator 负责 requeue，不在 terminal result
    路径里猜测投递是否成功。
    """
    item = w._current_queue_item
    w._current_queue_item = None


def _clear_active_task_if_current(s, task_id: str | None) -> bool:
    """Clear the persisted routing context only for the completed task.

    A newer assign may already have replaced ``active_task_id`` while an older
    FIFO item was still running.  Comparing the ids prevents that completion
    from clearing the newer task's context.
    """
    if s is None or not task_id or getattr(s, "active_task_id", None) != task_id:
        return False
    s.active_task_id = None
    return True


def _clear_active_task_if_not_pending(s) -> bool:
    """Drop a completed/aborted context, while retaining a queued retry."""
    active = getattr(s, "active_task_id", None) if s is not None else None
    if not active:
        return False
    if any(_is_formal_task_item(item) and item.get("taskId") == active
           for item in (getattr(s, "queue_pending", None) or [])):
        return False
    s.active_task_id = None
    return True


def _ack_current_reports(w: Worker, s) -> None:
    """收到最终 result 后清理当前报告批次运行上下文。

    报告批次的 queue item 已由 provider hand-off 回调处理；这里不按 terminal
    result 二次出队，也不额外补发信号。
    """
    items = w._current_report_items
    w._current_report_items = []


async def _finish_task_error(w: Worker, s, result: str, *,
                             error_context: dict | None = None) -> None:
    """把执行前失败也收敛成可见的 terminal result。"""
    if s is None:
        raise RuntimeError(
            f"Session {w.session_id} disappeared before terminal persistence"
        )
    terminal = await _persist_terminal_state(
        w, s, "error", result,
        error_context={
            "origin": "worker.preflight",
            "previous_captured": True,
            "previous_message": _sess.history_tail_snapshot(s),
            **(error_context or {}),
        },
    )
    if terminal is None:
        return
    await _publish_terminal_events(w, terminal, s)
    await _enqueue_report(
        w.session_id, terminal["status"], terminal["result"],
        terminal["taskId"], w.worker_id,
        diagnostic=_error_report_diagnostic(
            w, terminal["status"], terminal.get("taskSeq")),
    )
    _finish_terminal_bookkeeping(w, terminal)


# ── 流式块防抖落盘（A1）──
# save 是 O(history) 全量序列化（实测 1295 条 ≈ 8ms/次 vs 新会话 0.76ms）。逐块
# save 是热路径最大浪费。改为：assistant 块 append 只标记 dirty，由单一防抖任务
# 批量落盘（500ms 窗口 / 每 _STREAM_SAVE_MAX_BLOCKS 块，先到者）；result 处理、
# worker 退出 / kill / 重启前强制 flush，保证不丢。防抖任务为单写者，串行落盘
# 避免并发写盘竞态（多个流式块并行 append 时 save 合并）。

_STREAM_SAVE_DEBOUNCE_SEC: float = 0.5    # 防抖窗口：窗口内合并多次 append
_STREAM_SAVE_MAX_BLOCKS: int = 20         # 或累计块数达上限 → 提前落盘（长流不至于久不落盘）


def _mark_history_dirty(w: Worker) -> None:
    """流式块 append 后标记待落盘（同步，不阻塞事件循环）。

    合并并发 append：已有防抖任务则不重复创建（save 合并）；块数达上限提前
    唤醒防抖任务立即落盘。唤醒用「计数 _hist_wake_count（权威）+ 事件（阻塞唤醒）」，
    等待前 set 的唤醒不会被防抖任务的 ev.clear() 冲掉。
    """
    w._hist_dirty = True
    w._hist_block_count += 1
    ev = w._hist_flush_event
    if ev is None:
        ev = w._hist_flush_event = asyncio.Event()
    if w._hist_save_task is None or w._hist_save_task.done():
        w._hist_save_task = asyncio.create_task(_flush_history_loop(w))
    if w._hist_block_count >= _STREAM_SAVE_MAX_BLOCKS:
        w._hist_wake_count += 1
        ev.set()


async def _flush_history_loop(w: Worker) -> None:
    """防抖落盘循环：单写者串行落盘，避免写盘竞态。

    - 窗口超时（_STREAM_SAVE_DEBOUNCE_SEC 无新块）→ 落盘当前 dirty 块
    - 块数达上限（_mark_history_dirty 唤醒）→ 提前落盘
    - _flush_history_now 置 force（result/退出路径）→ 立即落盘后退出
    """
    ev = w._hist_flush_event
    if ev is None:
        ev = w._hist_flush_event = asyncio.Event()
    try:
        while True:
            force = w._hist_force_flush
            if force:
                w._hist_force_flush = False
            else:
                if w._hist_wake_count > 0:
                    # 等待前先消费积压唤醒（防止等待前已 set 的事件被下方 clear 冲掉）
                    w._hist_wake_count -= 1
                else:
                    ev.clear()
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=_STREAM_SAVE_DEBOUNCE_SEC)
                    except asyncio.TimeoutError:
                        pass
                    if w._hist_wake_count > 0:
                        w._hist_wake_count -= 1
                force = w._hist_force_flush
                if force:
                    w._hist_force_flush = False
            s = _session(w)
            if s is not None and (w._hist_dirty or force):
                w._hist_dirty = False
                w._hist_block_count = 0
                await _sess.save_async(s)
            if force:
                break
    finally:
        w._hist_save_task = None


async def _flush_history_now(
    w: Worker, *, outcome: _sess.DurableOutcome | None = None, rollback=None,
    offline_identity: dict | None = None,
) -> None:
    """立即落盘（result 处理 / worker 退出 / kill / 重启前调用），保证缓冲块不丢。

    单写者协作：若防抖任务在跑，置 force + 唤醒并 shield 等待其落完（由该任务完成
    落盘），避免与它并发写同一文件；无防抖任务则直接落盘。调用方被取消时 shield
    保护防抖任务继续落盘，取消仍向上传播（不吞 CancelledError）。
    """
    if outcome is not None:
        # Terminal commits need their own observable result. The existing
        # per-Session ticket gate orders this write with debounce writes, so
        # cancellation cannot mistake an unfinished shielded task for failure.
        s = _session(w)
        if s is None:
            raise RuntimeError("Session disappeared before terminal persistence")
        w._hist_dirty = False
        w._hist_block_count = 0
        w._hist_force_flush = False
        def commit():
            try:
                offline_values = None
                if offline_identity is not None:
                    if (w.offline_token != offline_identity["token"]
                            or w.generation != offline_identity["generation"]):
                        raise RuntimeError("Stale terminal owner at durable commit")
                    candidate = copy.copy(s)
                    for key, value in _offline.snapshot(s).items():
                        setattr(candidate, key, value)
                    _offline.attach_foreground(candidate)
                    if not w._host_exit_illegal:
                        _offline.finish(candidate, offline_identity, reason="task/safe-terminal")
                    offline_values = _offline.snapshot(candidate)
                _sess._save_body(s, offline_override=offline_values)
            except BaseException:
                if rollback is not None:
                    rollback()
                raise
        await _sess._persist_async_outcome(s.id, commit, outcome)
        return
    if w._hist_flush_event is None:
        w._hist_flush_event = asyncio.Event()
    w._hist_force_flush = True
    task = w._hist_save_task
    if task is not None and not task.done():
        w._hist_flush_event.set()
        await asyncio.shield(task)  # 防抖任务独立完成落盘；调用方取消照常传播
        return None
    s = _session(w)
    if s is None:
        w._hist_force_flush = False
        w._hist_dirty = False
        w._hist_block_count = 0
        return
    w._hist_dirty = False
    w._hist_block_count = 0
    w._hist_force_flush = False
    await _sess.save_async(s)


async def _admit_native_operation(w, runtime, *, request_id, thread_id, control_generation, operation, context=None):
    identity = _offline.owner(w)
    async with queue_lock(w.session_id):
        s = _session(w)
        def valid():
            return (not _shutdown_started and not w._host_exit_illegal and s is not None and _control_runtime_matches(w.worker_id, runtime)
                    and w.offline_token == identity["token"] and w.native_control_mode == "native"
                    and w.native_control_generation == control_generation
                    and thread_id == s.cli_session_id and w.status not in {"held", "restarting"})
        if not valid():
            raise RuntimeError("Auxiliary native owner retired before admission")
        def commit(candidate):
            if not valid():
                raise RuntimeError("Auxiliary native owner changed before persistence")
            existing = candidate.execution_operations.get(request_id)
            if existing and (existing.get("safeTerminal") or existing.get("retirement")
                    or existing.get("state") == "illegal"):
                raise RuntimeError("Auxiliary admission already retired")
            record = _offline.admit(candidate, identity, kind=operation, operation_id=request_id,
                           nativeAdmissionId=request_id, nativeThreadId=thread_id,
                           nativeControlGeneration=control_generation, connection=copy.deepcopy(context or {}))
            if existing is None and operation in {"opaque", "compact/active"}:
                record.update(state="uncertain", uncertainty="execution-resource-proof-unavailable")
        await _commit_offline(s, commit)
        record = s.execution_operations[request_id]
        if record.get("safeTerminal") or record.get("retirement") or record.get("state") == "illegal":
            raise RuntimeError("Auxiliary admission already retired")
        if not valid():
            raise RuntimeError("Auxiliary runtime retired during persistence")
        return {"requestId": request_id, "runId": request_id}


async def _native_operation_event(w, runtime, event) -> bool:
    if w._host_exit_illegal:
        return False
    async with queue_lock(w.session_id):
        s = _session(w)
        if s is None or not _control_runtime_matches(w.worker_id, runtime):
            return False
        key = event.get("runId") or event.get("requestId")
        record = s.execution_operations.get(key)
        identity = _offline.owner(w)
        if (not record or record.get("kind") == "foreground"
                or record.get("nativeAdmissionId") != event.get("requestId")
                or not _offline.same_owner(record, identity)
                or record.get("state") == "illegal" or record.get("safeTerminal")
                or record.get("retirement")):
            return False
        def commit(candidate):
            if w._host_exit_illegal or not _control_runtime_matches(w.worker_id, runtime):
                raise RuntimeError("Auxiliary runtime changed before persistence")
            op = candidate.execution_operations[key]
            action = event.get("type")
            if action == "pan.native_operation_bind":
                binding = event.get("binding") or {}
                if op.get("binding") and op["binding"] != binding:
                    op.update(state="uncertain", uncertainty="conflicting-resource-binding")
                else:
                    op["binding"] = copy.deepcopy(binding)
            elif action == "pan.native_operation_terminal":
                proof = event.get("proof")
                binding = event.get("binding") or {}
                allowed = ((op["kind"] == "command/exec" and proof == "command-deferred-final")
                    or (op["kind"] == "review/detached" and proof == "review-turn-completed"
                        and bool(op.get("binding")) and op["binding"] == binding))
                if allowed and not op.get("uncertainty"):
                    _offline.finish_operation(candidate, key, identity, reason=proof, proof=proof)
            elif action == "pan.native_turn_not_forwarded":
                if not op.get("binding"):
                    _offline.finish_operation(candidate, key, identity, reason="native/not-forwarded",
                                              proof="provider-rejected" if event.get("providerRejected") else "not-forwarded")
            elif action == "pan.native_operation_uncertain":
                op.update(state="uncertain", uncertainty=event.get("reason") or "unconfirmed-native-operation")
            candidate.operation_revision += 1
        changed = await _commit_offline(s, commit)
    if changed:
        await _offline_snapshot_event(s)
    return changed


@_plugin_native_admission
async def _admit_native_turn(w: Worker, runtime, *, request_id: str | None = None,
                             thread_id: str | None = None,
                             control_generation: int | None = None,
                             operation: str = "turn/start", context: dict | None = None) -> dict:
    if not isinstance(request_id, str) or not request_id:
        raise RuntimeError("Invalid native admission identity")
    codex = w.adapter.name == "codex"
    if codex and operation in {"command/exec", "review/detached", "compact/active", "opaque"}:
        return await _admit_native_operation(w, runtime, request_id=request_id, thread_id=thread_id,
            control_generation=control_generation, operation=operation, context=context)
    if codex and operation not in {"turn/start", "thread/compact/start", "observed-turn"}:
        raise RuntimeError("Unsupported admission operation")
    identity = _offline.owner(w)
    report_context = await _auto_report.context()
    async with queue_lock(w.session_id):
        s = _session(w)
        def valid():
            if _shutdown_started or w._host_exit_illegal or not _control_runtime_matches(w.worker_id, runtime):
                return False
            if codex:
                return (s is not None and thread_id == s.cli_session_id
                        and (w.native_control_mode == "native" or (operation == "observed-turn"
                             and w.native_control_mode == "pan"))
                        and type(control_generation) is int
                        and control_generation == w.native_control_generation)
            return True
        if s is None or not valid():
            raise RuntimeError("Native runtime changed or retired before admission")
        current = s.offline_run or {}
        if request_id in w._native_admission_ids:
            if (current.get("nativeAdmissionId") == request_id and current.get("state") == "running"
                    and _offline.same_owner(current, identity)):
                return {"requestId": request_id, "runId": current["id"]}
            raise RuntimeError("Native admission has already retired")
        if (w._queue_unit_active or w._native_external_turn_active
                or w.status in {"held", "restarting"}):
            raise RuntimeError("Native execution conflicts with current owner")
        next_execution = w._execution_seq + 1
        admitted = {**identity, "executionSeq": next_execution}
        native_admission = _auto_report.admission([{"source": "native-tui"}], *report_context)
        def commit(candidate):
            if not valid() or w.offline_token != identity["token"]:
                raise RuntimeError("Native owner changed before durable admission")
            candidate.task_seq += 1
            _offline.begin(candidate, admitted)
            candidate.offline_run.update(autoReportAdmission=copy.deepcopy(native_admission), nativeAdmissionId=request_id,
                nativeThreadId=thread_id, nativeControlGeneration=control_generation, nativeOperation=operation,
                nativeAdmissionTiming="after-start" if operation == "observed-turn" else "before-forward")
        await _commit_offline(s, commit)
        if not valid():
            raise RuntimeError("Native runtime retired during admission")
        # Only publish execution identity after the ordered writer committed it.
        w._current_seq = s.task_seq
        w._execution_seq = next_execution
        w._auto_report_admission = native_admission
        w._current_task_id = None
        w._current_task_idempotent = False
        w._current_source_session_id = None
        w._terminal_handled = False
        w._task_started_at = time.monotonic()
        w._current_queue_item = None
        w._current_report_items.clear()
        w._current_serialized = None
        w._claimed_queue_index = None
        w._current_task_assistant_ids.clear()
        w._current_task_last_assistant_text = ""
        w._current_task_last_assistant_seq = None
        w._native_external_turn_active = True
        w._native_reservation_busy = False
        w._native_admission_id = request_id
        w._native_admission_ids.add(request_id)
        w.status = "running"
        await _record_legal_worker_state(w, "running", "native/turn-admitted")
        return {"requestId": request_id, "runId": s.offline_run["id"]}


async def _retire_native_admission(w, runtime, event) -> bool:
    s = _session(w)
    op = s.execution_operations.get(event.get("runId") or event.get("requestId")) if s else None
    if op and op.get("kind") != "foreground":
        return await _native_operation_event(w, runtime, event)
    async with queue_lock(w.session_id):
        s = _session(w)
        run = (s.offline_run or {}) if s else {}
        if (s is None or not _control_runtime_matches(w.worker_id, runtime)
                or not event.get("requestId") or run.get("nativeAdmissionId") != event["requestId"]
                or run.get("state") != "running" or s.legal_offline is not False
                or not _offline.same_owner(run, _offline.owner(w))
                or (event.get("runId") is not None and event["runId"] != run.get("id"))
                or run.get("nativeTurnId")):
            return False
        identity = _offline.owner(w)
        def finish(candidate):
            if (not _control_runtime_matches(w.worker_id, runtime)
                    or candidate.offline_run.get("nativeAdmissionId") != event["requestId"]):
                raise RuntimeError("Native owner changed before cancellation")
            if not _offline.finish(candidate, identity, reason="native/not-forwarded"):
                raise RuntimeError("Native execution changed before cancellation")
        await _commit_offline(s, finish)
        w._native_external_turn_active = False
        w._native_reservation_busy = False
        w._native_admission_id = None
        w._terminal_handled = True
        w.status = "idle"
        await _record_legal_worker_state(w, "idle", "native/not-forwarded")
        return True


async def _read_stdout(w: Worker):
    adapter = w.adapter
    receipt_runtime = (w, w.generation, w.process, getattr(w.process, "stdin", None))
    execution_owner = _offline.owner(w)
    s = None  # bound even if stdout yields no parseable event (EOF check below)
    # 分块读取 stdout（read 65536 + 按换行切分），避免大 tool result 单行在
    # Windows asyncio 管道上挂起（Pan 卡死根因）；大行截断占位（_iter_stdout_lines）。
    line_count = 0
    async for line in _iter_stdout_lines(w):
        if not _control_runtime_matches(w.worker_id, receipt_runtime):
            return
        line_count += 1
        line_bytes = bytes(line)
        line_str = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
        if not line_str:
            continue
        event = adapter.parse_event(line_str)
        if event is None:
            spill = getattr(line, "spill", None)
            if spill is not None:
                # The parser cap hid the tail.  Recognition is order-
                # independent and top-level-only; only an error-shaped
                # envelope is read back in full.
                try:
                    status, event = await _classify_oversized_line(
                        adapter, line_bytes, spill)
                except (OSError, ValueError) as exc:
                    _log.warning(
                        "[Worker %s] oversized line classification failed: %s",
                        w.worker_id, exc,
                    )
                    status, event = "drop", None
                if status == "raw":
                    try:
                        await asyncio.to_thread(
                            _capture_raw_oversized, w,
                            line_bytes[:_STDOUT_MAX_LINE], spill)
                    except OSError as exc:
                        _log.warning(
                            "[Worker %s] raw diagnostic capture failed: %s",
                            w.worker_id, exc,
                        )
                    continue
                if event is None:
                    continue
            else:
                # Unrecognized text is retained as raw diagnostic context for a
                # later terminal/crash sample; it is never classified as error.
                if not _looks_like_structured_text(line_str):
                    _capture_raw_line(w, line_bytes)
                continue
        if _route_steer_receipt(w, event, runtime=receipt_runtime):
            continue
        if (getattr(adapter, "steer_requires_run_id", False) and not w._terminal_handled
                and w.status == "running" and _control_runtime_matches(w.worker_id, receipt_runtime)):
            sid = (_session(w).cli_session_id if _session(w) is not None else None)
            run = event.get("_requestId")
            if (sid and event.get("session_id") == sid and isinstance(run, str) and run
                    and w._steer_run_id is None):
                w._steer_run_id = run
        if w.status == "restarting":
            # Retirement is published only after the turn's result/report and
            # queue cleanup. Buffered old frames must not reopen that boundary.
            continue

        # 活性探测：任何有效输出都刷新 last_activity（watchdog 据此判定卡死）
        w.last_activity = time.monotonic()
        if event.get("type") in {"pan.control_mode", "pan.native_write"}:
            if event.get("type") == "pan.native_write":
                receipt = w._native_mode_receipts.get(event.get("requestId"))
                if receipt is not None and not receipt.done():
                    receipt.set_result(event)
                continue
            _update_pending_interactions(w, event)
            continue
        if event.get("type") == "pan.native_output":
            import base64
            try:
                chunk = base64.b64decode(event.get("data", ""), validate=True)
            except (ValueError, TypeError):
                continue
            if len(chunk) > 65536:
                continue
            w.native_output.extend(chunk)
            w.native_output_total += len(chunk)
            if len(w.native_output) > 4 * 1024 * 1024:
                del w.native_output[:len(w.native_output) - 4 * 1024 * 1024]
            continue
        if (event.get("type") == "pan.native_input_state"
                and adapter.name == "cbc" and w.native_control_enabled):
            w.native_input_uncertain = event.get("uncertain") is not False
            continue
        if (event.get("type") == "pan.native_ready"
                and adapter.name == "cbc" and w.native_control_enabled):
            w.native_kernel_ready = True
            continue
        if (event.get("type") in {"pan.native_operation_bind", "pan.native_operation_terminal",
                                  "pan.native_operation_uncertain"} and adapter.name == "codex"):
            await _native_operation_event(w, receipt_runtime, event)
            continue
        if event.get("type") == "pan.native_error":
            # Keep the existing generic Worker field unchanged; the sample log
            # retains the raw code/detail that field intentionally flattens.
            await _record_transient_error_sample(w, event, "native_operation_error")
            w.native_control_error = "Native terminal operation failed"
            continue
        if event.get("type") == "pan.native_settings_uncertain":
            await _record_native_settings_state(w, uncertain=True)
            continue
        if event.get("type") == "pan.native_turn_admission" and adapter.name in {"cbc", "codex"}:
            request_id = event.get("requestId")
            receipt = {}
            ok = False
            try:
                receipt = await _admit_native_turn(w, receipt_runtime, request_id=request_id,
                    thread_id=event.get("threadId"), control_generation=event.get("controlGeneration"),
                    operation=event.get("operation", "turn/start"), context=event.get("connection"))
                ok = True
            except Exception:
                _log.exception("Native admission rejected for Session %s", w.session_id)
            # A response belongs to this captured pipe only; never the successor.
            if _control_runtime_matches(w.worker_id, receipt_runtime):
                receipt_runtime[3].write((json.dumps({"type": "native_admission",
                    "requestId": request_id, "ok": ok, **receipt}) + "\n").encode())
                await receipt_runtime[3].drain()
            continue
        if event.get("type") == "pan.native_turn_not_forwarded" and adapter.name == "codex":
            await _retire_native_admission(w, receipt_runtime, event)
            continue
        if event.get("type") == "pan.native_turn_started":
            if adapter.name == "codex":
                if event.get("observedAfterStart") is True:
                    await _admit_native_turn(w, receipt_runtime, request_id=event.get("requestId"),
                        thread_id=event.get("threadId"), control_generation=event.get("controlGeneration"),
                        operation="observed-turn")
                async with queue_lock(w.session_id):
                    s = _session(w)
                    def bind(candidate):
                        run = candidate.offline_run or {}
                        if (not _control_runtime_matches(w.worker_id, receipt_runtime)
                                or not _offline.same_owner(run, _offline.owner(w))
                                or run.get("nativeAdmissionId") != event.get("requestId")
                                or (run.get("id") != event.get("runId") and not (
                                    event.get("observedAfterStart") is True
                                    and run.get("nativeAdmissionTiming") == "after-start"))
                                or run.get("state") != "running"
                                or not event.get("turn_id")):
                            raise RuntimeError("Native started without matching durable admission")
                        previous = run.get("nativeTurnId")
                        if previous is not None and previous != event["turn_id"]:
                            raise RuntimeError("Conflicting native turn identity")
                        run["nativeTurnId"] = event["turn_id"]
                    if s is not None:
                        await _commit_offline(s, bind)
            continue
        if event.get("type") == "pan.native_busy":
            w._native_reservation_busy = event.get("busy") is True
            if not w._native_reservation_busy and not w._native_external_turn_active:
                _maybe_restart_pending(w)
                if w.native_control_mode == "pan" and w.pending_signal is not None:
                    w.pending_signal.put_nowait({"type": "queue_signal"})
            continue
        if event.get("type") == "pan.native_user":
            s = _session(w)
            text = event.get("text")
            if s is not None and w._native_external_turn_active and isinstance(text, str) and (text or event.get("nativeContent")):
                run = s.offline_run or {}
                if adapter.name == "codex" and (
                        not event.get("requestId") or run.get("nativeAdmissionId") != event["requestId"]
                        or (run.get("id") != event.get("runId") and not (
                            event.get("runId") is None and run.get("nativeAdmissionTiming") == "after-start"))
                        or not _offline.same_owner(run, _offline.owner(w))
                        or event.get("thread_id") != s.cli_session_id
                        or event.get("turn_id") != run.get("nativeTurnId")):
                    continue
                row = {"role": "user", "content": text, "source": "native-tui"}
                if not text:
                    row["messageId"] = _sess._new_pan_message_id()
                for incoming, durable in (("item_id", "nativeItemId"), ("turn_id", "turnId"),
                                           ("nativeContent", "nativeContent")):
                    if event.get(incoming) is not None:
                        row[durable] = event[incoming]
                # Replay identity is scoped to this native turn, never body.
                if row.get("nativeItemId") and row.get("turnId") and any(
                        old.get("role") == "user" and old.get("source") == "native-tui"
                        and old.get("nativeItemId") == row["nativeItemId"]
                        and old.get("turnId") == row["turnId"] for old in s.history):
                    continue
                _sess.append_history(s, row)
                await _sess.save_async(s)
                await _bcast({"type": "session.updated", "sessionId": w.session_id})
            continue
        if event.get("type") == "pan.steer_receipt":
            receipt = w._steer_receipts.get(event.get("requestId"))
            if receipt is not None and not receipt.done():
                receipt.set_result(event)
            continue
        if adapter.name == "codex" and event.get("external_turn") is True:
            s = _session(w)
            run = (s.offline_run or {}) if s else {}
            if (not event.get("requestId") or run.get("nativeAdmissionId") != event.get("requestId")
                    or (run.get("id") != event.get("runId") and not (
                        event.get("runId") is None and run.get("nativeAdmissionTiming") == "after-start"))
                    or not _offline.same_owner(run, _offline.owner(w))
                    or (event.get("turn_id") is not None and run.get("nativeTurnId") != event["turn_id"])):
                _log.warning("Discarded unowned native external result for %s", w.session_id)
                continue
        if w._terminal_handled and not adapter.is_result_event(event):
            # `worker.result` closes this serialized provider turn. A provider
            # frame buffered behind its terminal result must not be appended
            # after the DONE marker or relabelled with the next queue item's
            # taskSeq. `_consumer_stream` clears this latch immediately before
            # writing the next prompt to the provider.
            _log.warning(
                "[Worker %s] post-terminal provider event ignored generation=%s",
                w.worker_id, w.generation,
            )
            continue
        event_type = event.get("type")
        if event_type == "codex.turn_error":
            # Transient in-turn notice. Existing stream broadcast is preserved;
            # the sample consumes nothing from it.
            await _record_transient_error_sample(w, event, "transient_provider_error")
        elif event_type == "codex.transient_error_recovered":
            await _record_transient_error_sample(
                w, event, "transient_provider_error", recovered=True)
            continue
        elif event_type == "codex.item_error":
            item_type = str(event.get("item_type") or "")
            await _record_transient_error_sample(
                w, event,
                "tool_failure" if item_type in _TOOL_ITEM_TYPES else "transient_provider_error",
            )
            continue
        elif event_type == "error":
            # A bare provider error event is not a terminal result. Keep it
            # observable without promoting it to a task failure.
            await _record_transient_error_sample(w, event, "transient_provider_error")
        # Keep only native interactive prompts in the worker-local replay
        # cache.  The cache is consumed by the dashboard after a WS reconnect;
        # normal stream events remain live-only to avoid retaining history.
        quota_update = _update_pending_interactions(w, event)
        if quota_update is not None:
            try:
                await asyncio.to_thread(
                    _codex_quota_store.update_current_profile,
                    quota_update["rate_limits"],
                    observed_at=quota_update["received_at"],
                    received_at=quota_update["received_at"],
                    source="app-server-push",
                )
            except Exception:  # quota persistence must not kill the Worker stream
                _log.exception("failed to persist Codex quota snapshot")

        # 提取 session_id + model 并写入 Session
        # An init is adapter readiness proof. Constructed Idle/spawn alone is not.
        if adapter.is_init_event(event):
            await _activate_offline_worker(w, ready_proof=True, expected_owner=execution_owner)
            first_ready = not w.native_kernel_ready
            w.native_kernel_ready = True
            if first_ready and adapter.name == "codex":
                await _record_native_settings_state(w, uncertain=False)
            s = _session(w)
            if s:
                sid = adapter.extract_session_id(event)
                if sid and not s.cli_session_id:
                    s.cli_session_id = sid
                model = adapter.extract_model(event)
                if model and not s.model:
                    s.model = model
                await _sess.save_async(s)

        # 收集对话历史（replay 期间跳过，避免重复追加）
        if adapter.is_assistant_event(event) and not w._replaying:
            s = _session(w)
            if s:
                for b in adapter.extract_assistant_blocks(event):
                    if event.get("turn_id") is not None:
                        b.setdefault("turnId", event["turn_id"])
                    if event.get("block_id") is not None:
                        b.setdefault("blockId", event["block_id"])
                    if w._native_external_turn_active:
                        b.setdefault("source", "native-tui")
                    _sess.append_history(s, b)
                    if (b.get("role") == "assistant"
                            and _sess.is_pan_message_id(b.get("messageId"))):
                        w._current_task_assistant_ids.add(b["messageId"])
                        if isinstance(b.get("content"), str) and b["content"].strip():
                            w._current_task_last_assistant_text = b["content"]
                            w._current_task_last_assistant_seq = w._current_seq
                # A1 防抖：append 只标记 dirty，由防抖任务批量落盘（不逐块全量 save）
                _mark_history_dirty(w)

        # 任务完成 → 保存 Session + last_result
        if adapter.is_result_event(event):
            is_error = adapter.is_result_error(event)
            is_cancelled = bool(event.get("cancelled") or event.get("is_cancelled"))
            # Snapshot the preceding message before the activation await can
            # let a concurrent append run.  Only a failure/cancel needs it.
            previous_snapshot = None
            previous_captured = False
            if (is_error or is_cancelled) and not w._replaying:
                try:
                    previous_snapshot = _sess.history_tail_snapshot(_session(w))
                except Exception:
                    previous_snapshot = None
                previous_captured = True
            await _activate_offline_worker(w, ready_proof=True, expected_owner=execution_owner)
            if w._terminal_handled:
                _log.warning(
                    "[Worker %s] duplicate result event ignored generation=%s",
                    w.worker_id, w.generation,
                )
                w.status = "idle"
                continue
            s = _session(w)
            w.status = "cancelled" if is_cancelled else ("error" if is_error else "done")

            # replay 结束：标记完成，不保存（history 无变化）
            if w._replaying:
                w._replaying = False
                w._terminal_handled = True
                w.status = "idle"
                await _record_legal_worker_state(w, "idle", "task/replay-complete")
                _signal_task_done(w)
                _maybe_restart_pending(w)
                continue

            # taskSeq 统一用 _current_seq（_consumer 取出 item 时记录，item.seq
            # 由 send_task 分配并随 item 落盘）。序号计数器在 session.task_seq
            # 上，跨 worker respawn 保持单调递增。
            task_seq = w._current_seq
            result_text = adapter.extract_result_text(event)
            terminal = await _persist_terminal_state(
                w, s, w.status, result_text, expected_owner=execution_owner,
                error_context={
                    "origin": "worker.stream.result",
                    "cli_raw": event,
                    "previous_message": previous_snapshot,
                    "previous_captured": previous_captured,
                },
            )
            if terminal is None:
                continue
            # Base state is durable before this point.  Usage enrichment is only
            # scheduled after the result/idle pair and never delays either.
            await _publish_terminal_events(w, terminal, s)
            # Snapshot the input metadata before any cleanup.  Reports must be
            # paired with this turn's queue item, never with a global/previous
            # task id.
            # 订阅制报告：完成 → 若被订阅则 append 到 manager 的落盘队列（立项 4.3）
            # error 报告从本 run 的已知捕获附最后一条有用消息（result 空时
            # 保住 429 原因/重置时间，result 已有错误时保留上下文）。
            await _enqueue_report(w.session_id, terminal["status"], terminal["result"],
                                  terminal["taskId"], w.worker_id,
                                  admission=terminal.get("autoReportAdmission"),
                                  diagnostic=_error_report_diagnostic(
                                      w, terminal["status"], terminal.get("taskSeq")))
            was_native = w._native_external_turn_active
            w._native_external_turn_active = False
            w._native_reservation_busy = False
            _finish_terminal_bookkeeping(w, terminal)
            if was_native and w.native_control_mode == "pan" and w.pending_signal is not None:
                w.pending_signal.put_nowait({"type": "queue_signal"})
            continue

        # replay 期间不广播 stream 事件
        if not w._replaying:
            await _bcast({
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": w.generation,
                "taskSeq": w._current_seq,
                "executionSeq": w._execution_seq or None,
                "taskId": w._current_task_id,
                "event": event,
            })

    # stdout EOF — 进程退出了
    # A serialized restart marks the object before its first await.  Its old
    # stdout task may still observe EOF in that tiny window; the restart owns
    # the transition and will clean up the process itself.
    if (w.status == "restarting" or not _control_runtime_matches(w.worker_id, receipt_runtime)):
        _cleanup_system_prompt_file(w.system_prompt_file)
        w.system_prompt_file = None
        return
    _cleanup_system_prompt_file(w.system_prompt_file)
    w.system_prompt_file = None
    process = w.process
    if process is not None and getattr(process, "returncode", None) is None:
        # Observed EOF can precede asyncio's returncode update on Windows.
        # Wait briefly so crash diagnostics carry the actual exit code.
        wait = getattr(process, "wait", None)
        if callable(wait):
            try:
                await asyncio.wait_for(wait(), timeout=1.0)
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                pass
    code = process.returncode if process is not None else "unknown"
    _log.info("[Worker %s] %s 进程退出，返回码 %s", w.worker_id, adapter.name, code)
    _cancel_claude_permission_requests(w.worker_id, "Claude worker exited")

    # B2: 进程退出检测路径 — 任务进行中（running/queued）异常退出/崩溃 → 向被管
    # manager 推送 zombie 报告。正常完成后的退出（status 已回 idle）由
    # _enqueue_zombie_report 内部判定跳过——done/error 报告完成时已推送。
    abnormal = w.status in {"running", "queued"}
    await _detect_offline_exit(w, execution_owner, "process/EOF")
    # EOF is loss of control, not proof every native descendant has exited.
    # Retire the tracked runtime before permitting a successor writer.
    if not _runtime_stopped(w):
        await _kill_process_tree(w)
        if not _runtime_stopped(w):
            w.status = "error"
            await _record_worker_crash(
                w, _session(w), code,
                f"{adapter.name} process exited but its runtime did not stop "
                f"(returncode={code})",
                origin="worker.eof_unreaped",
            )
            return
    await _enqueue_zombie_report(w, f"process exited (returncode={code})")

    # 如果已经通过 result event 收到正常输出（last_result 有内容且非 error），
    # 不要覆盖。某些 CLI 退出时返回非零码（如 kimi 的 0xC0000409）但回复已完整。
    if code != 0 and w.status == "idle" and s and s.last_result and s.last_result.get("status") != "error":
        _log.info("[Worker %s] 已有正常结果，忽略非零退出码 %s", w.worker_id, code)
        w.status = "idle"
    else:
        w.status = "error"
        await _record_worker_crash(
            w, _session(w), code,
            f"{adapter.name} process exited unexpectedly (returncode={code})",
            origin="worker.eof",
        )
        await _bcast({
            "type": "worker.crashed",
            "workerId": w.worker_id,
            "sessionId": w.session_id,
            "generation": w.generation,
            "returncode": code,
        })
    # zombie：进程已死、尚未回收的瞬间状态。先广播再移除，让订阅方能观测到。
    w.status = "zombie"
    await _bcast({
        "type": "worker.zombie",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "generation": w.generation,
        "returncode": code,
    })
    # H2: worker 退出 → 名下 pending 的 taskId 标 error（防止"超时+crash"组合
    # 让同 taskId 重试永久卡 pending）
    _mark_worker_tasks_error(w.worker_id, f"worker exited (returncode={code})")
    # 唤醒可能在 _consumer_stream 里等待任务完成的协程（EOF 时任务不会正常结束）
    _signal_task_done(w)
    # A1 崩溃安全：进程退出前 flush 防抖缓冲的流式块（若仍有未落盘内容）
    if w._hist_dirty:
        await _flush_history_now(w)
    # 从 workers dict 移除尸体——否则 find_worker_by_session 会返回这个死 worker，
    # 后续 send_task 才报 'process dead'，晚了一步
    _cancel_pending_restart(w)
    workers.pop(w.worker_id, None)
    _unregister_worker(w)
    if abnormal:
        _schedule_session_recovery(w.session_id, force=True)


# ── consumer ──

async def _legacy_consumer_reference(w: Worker):
    """Consumer loop. Two execution modes (selected by resolve_execution_mode):

    - Stream mode (default, no MCP): long-running cbc process with
      --input-format stream-json. Each message is written to stdin.
    - Stream + MCP mode: same long-running process, spawned with --mcp-config
      (cbc >= 2.137.0). Enabled via adapter_config.output_mode == "stream".
    - One-shot mode: new provider process per message with --mcp-config when
      the selected adapter exposes that mode and output_mode=oneshot.

    Report consumption (订阅制，立项 4.3): a ``report_signal`` item only
    wakes the consumer; the report payload lives in the persisted
    ``Session.queue_pending``. On signal, all backlog reports are pulled from
    the source of truth, concatenated verbatim into ONE message (visible
    separator + source), and processed as a single message. Non-report
    messages (assign tasks / normal messages / system_prompt) stay single.
    """
    while True:
        item = await w.pending_signal.get()
        if item is None:
            break
        if not isinstance(item, dict):
            # The signal queue is an internal wake-up channel, not a message
            # transport.  Never let malformed/legacy values kill the consumer
            # (which would strand every durable item behind this signal).
            _log.warning("[Worker %s] ignoring malformed worker signal: %r",
                         w.worker_id, item)
            continue

        # 报告唤醒信号：正文在落盘 queue_pending，批量拉取拼接成一条消息处理
        if item.get("type") == "report_signal":
            s = _session(w)
            w._current_source_session_id = None
            if s and s.queue_pending:
                await _consume_pending_reports(w, s)
            if w._current_report_items:
                _log.warning(
                    "[Worker %s] report batch returned without terminal result; "
                    "%d item(s) were already consumed and will not be replayed",
                    w.worker_id, len(w._current_report_items),
                )
                break
            continue

        # 任务唤醒信号（L4 落盘）：正文在落盘 queue_pending，按 id 认领单个 task。
        # 认领会在本函数内立即从持久队列删除；queue_pending 的消费边界是
        # Worker 持久化接管，而不是 provider 返回 terminal result。
        claimed = None
        task_candidate = None
        if item.get("type") == "task_signal":
            # Memory lookup may await. 先读取候选正文，等 lookup 完成后再做同步
            # claim+出队，避免 claim 尚未持久化时 worker 被取消而留下可重放窗口。
            candidate_session = _session(w)
            if candidate_session is not None:
                task_candidate = next(
                    (queued for queued in candidate_session.queue_pending
                     if _is_task_item(queued) and queued.get("id") == item.get("id")),
                    None,
                )
            if (task_candidate is not None
                    and isinstance(task_candidate.get("text"), str)
                    and _task_source(task_candidate) is not None):
                text = await _maybe_inject_memory(candidate_session, task_candidate["text"])
            claimed = await _claim_pending_task(w, item.get("id"))
            if claimed is None:
                continue  # 信号重复 / item 已被消费 / 会话消失 → 跳过
            source = _task_source(claimed)
            if source is None:
                _log.warning("[Worker %s] task id=%s has unknown source; ignoring without replay",
                             w.worker_id, claimed.get("id"))
                _inflight_task_ids.discard(claimed.get("id"))
                continue
            # 若候选在 memory lookup 期间被替换，使用持久真源中的原文；正常路径
            # text 已经是 lookup 后的注入文本。
            if task_candidate is not claimed:
                text = await _maybe_inject_memory(_session(w), claimed["text"])
            w._current_seq = claimed.get("seq")
            w._current_task_id = claimed.get("taskId")
            w._current_task_idempotent = _is_formal_task_item(claimed)
            w._current_source_session_id = claimed.get("sourceSessionId")
            w._current_queue_item = claimed
        else:
            # Body-bearing signals have no durable item/receipt and therefore
            # cannot satisfy at-most-once semantics.  The production protocol
            # only emits task_signal/report_signal; reject legacy direct forms
            # instead of silently assigning them a user/agent identity.
            _log.warning("[Worker %s] ignoring non-durable worker signal: %r",
                         w.worker_id, item)
            continue

        try:
            # resume replay 等待已删除（worker-resume-replay 结论）：cbc 在 stdin 有
            # prompt 时不向外重放 stdout 历史（executeStreamMode 日志 ResumeReplay
            # skipped (hasPrompt=true)），而 Pan 的 spawn 是 stdin=PIPE 且等待期不写
            # 不关——此前 _replaying 永不自然结束，这里每个任务都白等满 10s。
            # 现在消息立即进 _consumer_stream 写 stdin，首个 result 即任务结果。
            # （若未来某个 cbc 版本在 EOF 型 stdin 下真重放，_read_stdout 的
            # _replaying 分支仍保留作死代码兜底。）

            s = _session(w)
            if s:
                # 旧版本可能已经把 history 标记写入、但 queue item 仍未被迁移；
                # 对账只用于避免重复追加 history，绝不把已经接管的 item 重放。
                already_delivered = claimed is not None and _delivery_mark_in_history(s, claimed)
                if already_delivered:
                    _log.info(
                        "[Worker %s] task id=%s reconciliation: delivery mark already "
                        "in history; reusing the existing user entry", w.worker_id,
                        claimed.get("id"))
                injected_text = text
                hist_entry = {"role": "user", "content": injected_text}
                if claimed is not None:
                    # 投递标记记为 history 条目元数据（不进消息正文，恢复对账据此
                    # 去重）；直连消息（claimed=None）不经队列，无对账、不打标记
                    hist_entry["delivered_keys"] = [_delivery_key(claimed)]
                    hist_entry["queueItemIds"] = [_queue_item_id(claimed)]
                    hist_entry["source"] = source
                    if claimed.get("sourceSessionId") is not None:
                        hist_entry["sourceSessionId"] = claimed.get("sourceSessionId")
                    if claimed.get("taskId") is not None:
                        hist_entry["taskId"] = claimed.get("taskId")
                    if claimed.get("clientMessageId"):
                        # Keep a durable browser receipt beyond the bounded
                        # in-memory ledger so a very late reconnect cannot
                        # execute an old message for a second time.
                        hist_entry["clientMessageId"] = claimed["clientMessageId"]
                old_history_len = len(s.history)
                if not already_delivered:
                    _sess.append_history(s, hist_entry)
                try:
                    # Queue consumption is committed together with the history
                    # receipt before writing stdin/spawning oneshot.  Once this
                    # save succeeds, a restart must never see this item again.
                    await _save_receipt(s)
                except Exception as e:
                    if not already_delivered:
                        del s.history[old_history_len:]
                        _sess.replace_history(s, s.history)
                    if claimed is not None:
                        # _claim_pending_task removed the item synchronously to
                        # reserve it against API delete/order and duplicate signals.
                        restore_at = w._claimed_queue_index
                        if restore_at is None:
                            restore_at = len(s.queue_pending)
                        s.queue_pending.insert(min(restore_at, len(s.queue_pending)), claimed)
                        w._claimed_queue_index = None
                        claimed["deliveryState"] = _DELIVERY_QUEUED
                    _log.warning(
                        "[Worker %s] task receipt save failed, kept item in "
                        "queue_pending: %s", w.worker_id, e)
                    break
                if claimed is not None:
                    w._claimed_queue_index = None
            # The item is already durably consumed.  Do not reinsert it if the
            # provider later exits without a terminal result; that would violate
            # the at-most-once execution boundary.

            # 选择执行模式：oneshot（每次任务新开进程）vs stream（长驻，可带 MCP）。
            # 由 adapter.execution_modes + session.output_mode 决定（去 cbc 化）。
            mode = resolve_execution_mode(w.adapter, s)

            if mode == "oneshot":
                await _consumer_oneshot(w, text, source, s)
            else:
                await _consumer_stream(w, text, source, s)
            # The task is already consumed.  Do not consume another signal if an
            # adapter returned without producing a terminal result (for example
            # because its process died); the current execution must settle before
            # the serial consumer advances, and it must never be re-enqueued.
            if claimed is not None and w._current_queue_item is claimed:
                _log.warning(
                    "[Worker %s] task id=%s returned without terminal result; "
                    "item was already consumed and will not be replayed",
                    w.worker_id, claimed.get("id"),
                )
                break
        finally:
            # 无论正常完成、中止还是异常/取消，都释放 in-flight 标记——item 是否
            # 留在队列由「是否已确认出队」决定，标记只负责运行期去重。
            if claimed is not None:
                _inflight_task_ids.discard(claimed.get("id"))


# The implementation below is the queue consumer used by the new hand-off
# contract.  The older consumer above is intentionally kept in this diff as a
# compatibility reference for downstream imports, but is shadowed by this
# definition before the module is used.  Keeping the replacement local makes
# the migration reviewable and avoids changing the adapter-facing helpers in
# one large, fragile edit.


def _report_consume_unit(s, item) -> list[dict] | None:
    """Resolve a frozen report unit from original IDs; never split or invent it."""
    ids = item.get("reportConsumeIds")
    if (not isinstance(ids, list) or not ids
            or not all(isinstance(value, str) and value for value in ids)
            or len(ids) != len(set(ids))):
        return None
    rows = [row for row in s.queue_pending
            if (row.get("queueItemId") or row.get("id")) in ids]
    if ([row.get("queueItemId") or row.get("id") for row in rows] != ids
            or any(row.get("reportConsumeIds") != ids or not _is_pauseable_agent_report(row)
                   for row in rows)):
        return None
    if len(rows) != 1 and any(_queue_item_kind(row) == "task" for row in rows):
        return None
    return rows


def _select_queue_unit(s) -> list[dict] | None:
    """Select the first unlocked queued unit in durable FIFO order."""
    if getattr(s, "queue_paused", False) or _native_handoff_blocked(s):
        return None
    pending = list(s.queue_pending or [])
    for index, item in enumerate(pending):
        kind = _queue_item_kind(item)
        if kind is None:
            # A malformed/unknown row is durable data, so never skip over it.
            return None
        if _delivery_state(item) != _DELIVERY_QUEUED:
            # reserved/writing/sent rows belong to the current/recovering
            # hand-off.  They must not let a later item overtake them.
            return None
        if item.get("reportConsumeIds"):
            unit = _report_consume_unit(s, item)
            # An existing batch is indivisible, even after edits/setting changes.
            if (not unit or unit[0] is not item
                    or any(not queue_item_eligible(s, row) for row in unit)):
                return None
            return unit
        if _queue_item_locked(item):
            continue
        if not _retry_due(item):
            return None
        if queue_item_edit_locked(s, _queue_item_id(item)):
            # Keep FIFO ordering while the browser owns this item's edit
            # lease.  Releasing or saving the lease sends a fresh wakeup.
            return None
        if not queue_item_eligible(s, item):
            return None
        if kind == "task" or item.get("selfConsumeRequested"):
            return [item]
        unit = [item]
        for follower in pending[index + 1:]:
            if _queue_item_locked(follower):
                # A locked row remains in place and ends this delivery batch.
                break
            if (follower.get("reportConsumeIds")
                    or _queue_item_kind(follower) not in {"report", "qq", "wechat"}
                    or not _is_dispatchable(follower)
                    or queue_item_edit_locked(s, _queue_item_id(follower))):
                break
            unit.append(follower)
        return unit
    return None


def _remove_queue_items_by_identity(s, items: list[dict]) -> None:
    identities = {id(item) for item in items}
    s.queue_pending = [item for item in s.queue_pending if id(item) not in identities]


def _task_status_queued(w: Worker, item: dict) -> None:
    task_id = item.get("taskId")
    if not task_id:
        return
    current = _task_status.get(task_id)
    if current is None:
        return
    current.update({
        "status": "pending",
        "workerId": w.worker_id,
        "taskId": task_id,
        "sessionId": w.session_id,
        "ts": time.monotonic(),
    })


async def _reserve_queue_unit(w: Worker, s, items: list[dict], text: str,
                              expected_revisions: list[int] | None = None) -> bool:
    """Persist reservation/writing states without claiming a delivered history row."""
    if not _process_alive(w):
        return False
    report_context = await _auto_report.context()
    async with queue_lock(s.id):
        if w.status == "restarting" or (w.pending_restart and w.status in {"idle", "queued"}):
            return False
        current_unit = _select_queue_unit(s)
        if (current_unit is None
                or len(current_unit) != len(items)
                or any(current is not selected
                       for current, selected in zip(current_unit, items))):
            # Selection happens before reservation. Re-select under the lock
            # so a pause toggle or queue reorder between those steps cannot
            # reserve a task that has just been overtaken by the FIFO head.
            return False
        if expected_revisions is not None and expected_revisions != [
                item.get("revision", 1) for item in items]:
            # An edit may finish while memory projection awaits. Do not hand
            # off text computed from the old body after its lease is released.
            return False
        for item in items:
            if (not any(existing is item for existing in s.queue_pending)
                    or not _is_dispatchable(item)
                    or _queue_item_locked(item)
                    or queue_item_edit_locked(s, _queue_item_id(item))):
                return False

        # Compute on detached candidates; publish only with durable reservation.
        candidates = copy.deepcopy(items)
        report_admission = _auto_report.admission(candidates, *report_context)
        # Reservation proves only ownership. History starts at successful hand-off.
        history_added = False

        # 保留（reserved）会把行移出待处理计数集：版本与状态在同一次落盘提交，
        # 防止 HTTP 快照与广播补丁之间出现“同版本不同计数”的窗口。
        old_queue_revision = getattr(s, "queue_revision", 0)
        if any(item.get("selfConsumeRequested") for item in items):
            outcome = _sess.DurableOutcome()
            def reserve_selected():
                originals = [(item, dict(item)) for item in items]
                old_revision = s.queue_revision
                old_ledger = {_queue_item_id(item): s.queue_delivery_ledger.get(_queue_item_id(item))
                              for item in items}
                try:
                    for item, candidate in zip(items, candidates):
                        item["autoReportAdmission"] = candidate["autoReportAdmission"]
                        item.update(deliveryState=_DELIVERY_RESERVED, reservedBy=w.worker_id,
                                    reservedGeneration=w.generation, reservedAt=time.time())
                        s.queue_delivery_ledger[_queue_item_id(item)] = dict(item)
                    s.queue_revision += 1
                    _sess._save_body(s)
                except BaseException:
                    for item, original in originals:
                        item.clear()
                        item.update(original)
                    for identity, original in old_ledger.items():
                        if original is None:
                            s.queue_delivery_ledger.pop(identity, None)
                        else:
                            s.queue_delivery_ledger[identity] = original
                    s.queue_revision = old_revision
                    raise
            await _sess._persist_async_outcome(s.id, reserve_selected, outcome)
            w._auto_report_admission = report_admission
            for item in items:
                item["deliveryState"] = _DELIVERY_WRITING
            return history_added
        prior_admissions = [(item, item.get("autoReportAdmission")) for item in items]
        for item, candidate in zip(items, candidates):
            item["autoReportAdmission"] = candidate["autoReportAdmission"]
            item["deliveryState"] = _DELIVERY_RESERVED
            item["reservedBy"] = w.worker_id
            item["reservedGeneration"] = w.generation
            item["reservedAt"] = time.time()
            _remember_queue_item(s, item, _DELIVERY_RESERVED)
        s.queue_revision = old_queue_revision + 1
        try:
            await _save_receipt(s)
        except Exception:
            s.queue_revision = old_queue_revision
            for item, previous in prior_admissions:
                if previous is None:
                    item.pop("autoReportAdmission", None)
                else:
                    item["autoReportAdmission"] = previous
            for item in items:
                _queue_item_backoff(item, "queue reservation save failed")
                _remember_queue_item(s, item, _DELIVERY_QUEUED)
            return False

        w._auto_report_admission = report_admission
        # Keep the more precise writing state in memory for API/runtime observers,
        # but the durable RESERVED marker is sufficient for restart recovery: any
        # row left reserved is treated as an unfinished hand-off and requeued.
        for item in items:
            item["deliveryState"] = _DELIVERY_WRITING
        return history_added


async def _requeue_queue_unit(w: Worker, s, items: list[dict], reason: str,
                              history_added: bool = False, *, immediate: bool = False) -> None:
    async with queue_lock(s.id):
        await _requeue_queue_unit_locked(w, s, items, reason, history_added, immediate=immediate)


async def _requeue_queue_unit_locked(w: Worker, s, items: list[dict], reason: str,
                              history_added: bool = False, *, immediate: bool = False) -> None:
    """Put an unfinished hand-off back, with bounded retry backoff."""
    requeued = False
    for item in items:
        if _delivery_state(item) == _DELIVERY_SENT:
            continue
        _queue_item_backoff(item, reason, immediate=immediate)
        _remember_queue_item(s, item, _DELIVERY_QUEUED)
        requeued = True
        if item.get("type") == "task":
            _task_status_queued(w, item)
    # 回到 queued 会把行重新计入待处理：版本随状态一起推进（保存失败时内存
    # 状态保持 requeued，版本不回退也保持单调）。
    if requeued:
        s.queue_revision = getattr(s, "queue_revision", 0) + 1
    if history_added:
        for index in range(len(s.history) - 1, -1, -1):
            entry = s.history[index]
            if any(_delivery_key_matches(key, item)
                   for key in (entry.get("delivered_keys") or ())
                   for item in items):
                s.history.pop(index)
                _sess.replace_history(s, s.history)
                break
    try:
        await _save_receipt(s)
    except Exception as exc:
        _log.warning(
            "[Worker %s] failed to persist queue requeue for session=%s: %s",
            w.worker_id, s.id, exc,
        )
        if w.pending_restart:
            # Do not retire a runtime on an uncommitted requeue. Preserve the
            # legacy retry behavior when no delayed restart was requested.
            _abort_delayed_restart(w, "queue requeue persistence", exc)
            raise
    _schedule_queue_retry(s.id)


async def _commit_queue_handoff(w: Worker, s, items: list[dict]) -> bool:
    """Commit the exact provider hand-off, then remove the durable rows."""
    if not all(any(existing is item for existing in s.queue_pending) for item in items):
        return False
    # The adapter has crossed its actual hand-off boundary. Persist the body
    # and receipt now; reservation/writing must never look like a sent turn.
    history_row = _queue_history_row(items)
    if not all(_delivery_mark_in_history(s, item) for item in items):
        _sess.append_history(s, history_row)
    w._current_handoff_acked = True
    old_queue_revision = getattr(s, "queue_revision", 0)
    s.queue_revision = old_queue_revision + 1
    for item in items:
        _set_delivery_state(s, item, _DELIVERY_SENT)
        _clear_delivery_reservation(item)
    outcome = _sess.DurableOutcome()
    try:
        # The first save is the durable sent_to_cli marker.  A crash between
        # this save and the removal save is recovered by migration.
        await _save_receipt(s, outcome=outcome)
    except asyncio.CancelledError:
        w._current_handoff_acked = True
        _remove_queue_items_by_identity(s, items)
        # Handoff is already real even if cancellation coincided with a failed
        # writer. An independent owner finishes persistence without resending.
        await asyncio.shield(_own_queue_steer_task(_persist_queue_steer_state(w, s)))
        raise
    except Exception as exc:
        _log.warning(
            "[Worker %s] handoff receipt save failed; retrying persistence only: %s",
            w.worker_id, exc,
        )
        try:
            await _save_receipt(s)
        except Exception:
            _mark_history_dirty(w)

    w._current_handoff_acked = True
    _remove_queue_items_by_identity(s, items)
    try:
        await _save_receipt(s)
    except Exception as exc:
        # sent_to_cli has already been persisted.  The durable migration will
        # remove a stale row if this second save did not reach disk.
        _log.warning(
            "[Worker %s] queue removal save pending after successful hand-off: %s",
            w.worker_id, exc,
        )
        _mark_history_dirty(w)
    delivered_ids = [_queue_item_id(item) for item in items]
    delivered_keys = [_delivery_key(item) for item in items]
    delivered_is_task = _queue_item_kind(items[0]) == "task"
    delivered_text = (
        items[0].get("text", "") if delivered_is_task
        else _format_report_batch(items)
    )
    # A delivered task row carries the same source metadata its history receipt
    # gets, so the live projection can classify the row (agent dispatch vs
    # user/report/automation) before the canonical history refresh lands.  The
    # report channel keeps its header marker and stays untouched.
    delivered_message = {
        "role": "user",
        "content": delivered_text,
        "queueItemIds": delivered_ids,
        "deliveryKeys": delivered_keys,
    }
    if delivered_is_task:
        delivered_message["source"] = _task_source(items[0]) or "user"
        if items[0].get("sourcePluginId"):
            delivered_message["sourcePluginId"] = items[0]["sourcePluginId"]
        if items[0].get("sourceSessionId") is not None:
            delivered_message["sourceSessionId"] = items[0].get("sourceSessionId")
        if items[0].get("taskId") is not None:
            delivered_message["taskId"] = items[0].get("taskId")
            if items[0].get("taskIdSource") is not None:
                delivered_message["taskIdSource"] = items[0].get("taskIdSource")
        if isinstance(items[0].get("parts"), list):
            delivered_message["parts"] = public_message_parts(items[0].get("parts"))
    delivered_message.update(_queue_origin_metadata(items))
    for key in ("messageId", "ts"):
        if key in history_row:
            delivered_message[key] = history_row[key]
    await _bcast({
        "type": "queue.item_delivered",
        "sessionId": s.id,
        # The receipt may trail the first stream frame while persistence
        # yields. Bind it to this execution so clients can place its input
        # before these blocks without moving another turn's queued input.
        "workerId": w.worker_id,
        "generation": getattr(w, "generation", 0),
        "executionSeq": w._execution_seq,
        "queueItemIds": delivered_ids,
        # The delivery event is the first client-visible proof that the
        # pending rows crossed the provider hand-off boundary.  Include the
        # post-removal revision so a delayed GET /queue cannot overwrite the
        # delivered snapshot with an older response.
        "queueRevision": getattr(s, "queue_revision", 0),
        "messages": [_public_queue_receipt(delivered_message)],
    })
    await _bcast({
        "type": "queue.snapshot",
        "sessionId": s.id,
        "queueRevision": getattr(s, "queue_revision", 0),
    })
    return True


async def _deliver_queue_unit(w: Worker, s, items: list[dict]) -> None:
    """Reserve one FIFO unit, hand it to the adapter, and wait for completion."""
    if not items:
        return
    w._execution_seq += 1
    expected_revisions = [item.get("revision", 1) for item in items]
    kind = _queue_item_kind(items[0])
    if kind == "task":
        if not _is_valid_task_item(items[0]) or _task_source(items[0]) is None:
            _log.warning("[Worker %s] malformed task at queue head; leaving queued",
                         w.worker_id)
            return
        source = _task_source(items[0])
        w._current_seq = items[0].get("seq")
        w._current_task_id = items[0].get("taskId")
        w._current_task_idempotent = _is_formal_task_item(items[0])
        w._current_source_session_id = items[0].get("sourceSessionId")
        history_text = items[0]["text"]
    else:
        source = "report"
        w._current_seq = None
        w._current_task_id = None
        w._current_task_idempotent = False
        w._current_source_session_id = None
        history_text = _format_report_batch(items)
    w._current_queue_item = items[0] if kind == "task" else None
    w._current_report_items = list(items) if kind != "task" else []
    w._current_handoff_acked = False
    history_added = False
    w._queue_unit_active = True
    try:
        projected_text = (
            project_message_parts(items[0].get("parts"), history_text)
            if kind == "task" else history_text
        )
        text = await _maybe_inject_memory(s, projected_text)
        # History/UI keeps the safe Markdown fallback, while only the adapter
        # receives the server-path projection in ``text``.
        history_added = await _reserve_queue_unit(
            w, s, items, history_text, expected_revisions=expected_revisions)
        if not history_added and any(_delivery_state(item) == _DELIVERY_QUEUED
                                     for item in items):
            # A failed reservation has already placed the item back in queue.
            return
        async def on_handoff() -> bool:
            return await _commit_queue_handoff(w, s, items)

        mode = resolve_execution_mode(w.adapter, s)
        consumer = _consumer_oneshot if mode == "oneshot" else _consumer_stream
        params = inspect.signature(consumer).parameters.values()
        supports_callback = any(
            parameter.name == "on_handoff"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in params
        )
        if supports_callback:
            await consumer(w, text, source, s, on_handoff=on_handoff)
        else:
            # A four-argument replacement cannot expose its actual handoff.
            # Fail closed and requeue instead of marking a receipt before an
            # unguarded write. Both production consumers support this callback.
            raise RuntimeError("Queue consumer must support the handoff callback")
    except asyncio.CancelledError:
        if not w._current_handoff_acked:
            try:
                await asyncio.shield(_requeue_queue_unit(
                    w, s, items, "worker cancelled before CLI hand-off", history_added,
                    immediate=w.pending_restart and w.status == "restarting"))
            except Exception as exc:
                _log.warning("[Worker %s] cancellation requeue failed: %s", w.worker_id, exc)
        raise
    except Exception as exc:
        if not w._current_handoff_acked:
            await _requeue_queue_unit(w, s, items, f"CLI hand-off failed: {exc}", history_added)
        _log.warning("[Worker %s] queue unit execution failed: %s", w.worker_id, exc)
        if w._current_handoff_acked:
            raise
    finally:
        try:
            if not w._current_handoff_acked and any(
                    _delivery_state(item) in {_DELIVERY_RESERVED, _DELIVERY_WRITING,
                                               _DELIVERY_IN_FLIGHT}
                    for item in items):
                await _requeue_queue_unit(
                    w, s, items, "CLI returned before hand-off confirmation", history_added,
                    immediate=w.pending_restart and w.status in {"idle", "queued", "restarting"})
            if w._current_queue_item is items[0] if kind == "task" else False:
                w._current_queue_item = None
            if kind != "task":
                w._current_report_items = []
            if w._current_task_id == items[0].get("taskId") if kind == "task" else False:
                w._current_task_id = None
                w._current_task_idempotent = False
            if w.status != "restarting" and not w._current_handoff_acked and (getattr(s, "queue_paused", False)
                                                or w.pending_restart):
                w.status = "idle"
                await _record_legal_worker_state(w, "idle", "queue/restart-pending" if w.pending_restart else "queue/paused")
                await _bcast({"type": "worker.status", "workerId": w.worker_id,
                              "sessionId": s.id, "generation": w.generation, "status": "idle"})
        except BaseException as exc:
            _abort_delayed_restart(w, "queue unit cleanup", exc)
            raise
        finally:
            # Clear even if requeue persistence, legal-state save or broadcast
            # aborts. Failed cleanup is NOT a safe idle boundary.
            w._queue_unit_active = False
        _maybe_restart_pending(w)
        _wake_after_queue_unit(w, s)


def _wake_after_queue_unit(w: Worker, s) -> None:
    """Re-arm one generic wakeup without making signals the queue."""
    if s is None or w.status == "restarting":
        return
    if _has_sendable_queued_items(s):
        if w.pending_signal is not None:
            w.pending_signal.put_nowait({"type": "queue_signal"})
    else:
        _schedule_queue_retry(s.id)


async def _consumer(w: Worker):
    """Consume the durable FIFO head; pending_signal only wakes this loop."""
    while True:
        signal = await w.pending_signal.get()
        if signal is None:
            return
        if not isinstance(signal, dict) or signal.get("type") not in {
            "queue_signal", "task_signal", "report_signal"
        }:
            _log.warning("[Worker %s] ignoring malformed queue wakeup: %r",
                         w.worker_id, signal)
            continue
        s = _session(w)
        if s is None:
            continue
        _maybe_restart_pending(w)
        if w.status == "restarting":
            continue
        unit = _select_queue_unit(s)
        if unit is None:
            _schedule_queue_retry(s.id)
            continue
        await _deliver_queue_unit(w, s, unit)


# ── 订阅制报告消费（立项 4.3）──

# 通道 inbox 提醒的抬头前缀，按 type 取值（enqueue_channel_reminder 的 channel）。
# 加新通道只需在此登记一行，无需改 _format_report_batch 的分支。
_CHANNEL_HEADERS = {"qq": "@@@@by qq", "wechat": "@@@@by wechat"}


def _format_report_batch(reports: list[dict]) -> str:
    """积压报告拼接为可读文本。

    Agent 报告/通知使用 ``@@@@by agent : {sessionId} | {title}``；
    ``noticeKind=background_job_terminal`` 使用固定的 ``////by pan system``
    抬头。报告形状：{"status","result","sessionId","taskId","workerId"}。
    title 取被管 session 的 name（`_sess.get(session_id).name`），session 不存在则回退 unknown。
    result 值单独成行、去引号、保留多行原文；None → null。
    """
    def _field_value(v) -> str:
        if v is None:
            return "null"
        return str(v)

    parts = []
    for r in reports:
        # 通道 inbox 更新提醒（type=qq/wechat/…）：与 agent 汇报同通道
        # （queue_pending + report_signal），但抬头/字段不同。
        # 抬头按 channel 名渲染为 `@@@@by <channel>`（见 _CHANNEL_HEADERS）。
        channel_name = _CHANNEL_HEADERS.get(r.get("type"))
        if channel_name is not None:
            qq_target = r.get("channelTarget") or r.get("qqTarget") or ""
            nickname = r.get("nickname") or ""
            bot_uin = str(r.get("botUin") or "")
            # 多账号：抬头带 bot 来源标识，agent 可见该会话由哪个 bot 收到
            header = f"{channel_name} : {qq_target} | {nickname}"
            if bot_uin:
                header += f" | bot {bot_uin}"
            # 微信等不能主动推送的通道：告知 agent 此刻能否回复
            if r.get("channel") == "wechat" and not r.get("canReply", True):
                header += " | canReply: false"
            lines = [
                header,
                f"targetType: {_field_value(r.get('targetType'))}",
                f"targetId: {_field_value(r.get('targetId'))}",
                f"nickname: {_field_value(r.get('nickname'))}",
            ]
            if bot_uin:
                lines.append(f"botUin: {bot_uin}")
            lines += [
                "message:",
                _field_value(r.get("text")),
                f"time: {_field_value(r.get('time'))}",
            ]
            parts.append("\n".join(lines))
            continue
        if r.get("noticeKind") == "background_job_terminal":
            target_ids = r.get("targetSessionIds")
            if not isinstance(target_ids, list):
                target_ids = [r.get("targetSessionId") or r.get("sessionId")]
            target_ids = [value for value in target_ids if value is not None]
            lines = [
                "////by pan system",
                f"status: {_field_value(r.get('status'))}",
                f"noticeKind: {_field_value(r.get('noticeKind'))}",
                f"jobId: {_field_value(r.get('jobId'))}",
                f"targetSessionId: {_field_value(r.get('targetSessionId') or r.get('sessionId'))}",
                f"targetSessionIds: {_field_value(target_ids)}",
            ]
            if r.get("creatorSessionId") is not None:
                lines.append(f"creatorSessionId: {_field_value(r.get('creatorSessionId'))}")
            if r.get("eventId") is not None:
                lines.append(f"eventId: {_field_value(r.get('eventId'))}")
            lines += [
                "result:",
                _field_value(r.get("result")),
            ]
            parts.append("\n".join(lines))
            continue
        # Compatibility for terminal notices persisted by the pre-T-046
        # scheduler: those rows have source=automation and an envelope, but
        # do not yet have the explicit noticeKind/top-level projections.
        # Keep them as Pan system messages instead of reviving the old
        # `@@@@by agent : automation | automation` rendering.
        if r.get("source") == "automation":
            envelope = r.get("envelope") if isinstance(r.get("envelope"), dict) else {}
            event_id = r.get("eventId")
            job_id = r.get("jobId") or envelope.get("jobId")
            if job_id is None and isinstance(event_id, str):
                job_id = event_id.removesuffix(":terminal")
            target_id = r.get("targetSessionId") or envelope.get("targetSessionId")
            target_ids = r.get("targetSessionIds") or envelope.get("targetSessionIds")
            if not isinstance(target_ids, list):
                target_ids = [target_id] if target_id is not None else []
            lines = [
                "////by pan system",
                f"status: {_field_value(envelope.get('status', r.get('status')))}",
                f"jobId: {_field_value(job_id)}",
                f"targetSessionId: {_field_value(target_id)}",
                f"targetSessionIds: {_field_value(target_ids)}",
            ]
            creator_id = r.get("creatorSessionId") or envelope.get("creatorSessionId")
            if creator_id is not None:
                lines.append(f"creatorSessionId: {_field_value(creator_id)}")
            lines += [
                "result:",
                _field_value(r.get("result")),
            ]
            parts.append("\n".join(lines))
            continue
        # Reports identify the producing session in sourceSessionId. The
        # legacy sessionId field remains the report subject/fallback.
        if r.get("type") == "notice":
            # Pre-separation notice rows stored the sender in sessionId. Only
            # honor that legacy shape when no explicit source type exists.
            candidate_sid = r.get("sourceSessionId") or (
                r.get("sessionId") if not r.get("source") else "") or ""
        else:
            candidate_sid = r.get("sourceSessionId") or r.get("sessionId") or ""
        sid = candidate_sid if isinstance(candidate_sid, str) else ""
        title = "unknown"
        if sid:
            sess = _get_session_shallow(sid)
            if sess and sess.name:
                title = sess.name
        elif r.get("source") == "automation":
            title = "automation"
        src = sid or r.get("workerId") or r.get("source") or "unknown"
        lines = [
            f"@@@@by agent : {src} | {title}",
            f"status: {_field_value(r.get('status'))}",
        ]
        # B2: zombie 报告带 type 标记，manager 可在拼装消息中区分异常死亡
        if r.get("type"):
            lines.append(f"type: {_field_value(r.get('type'))}")
        lines += [
            "result:",
            _field_value(r.get("result")),
        ]
        # error 报告的最后消息上下文（仅 error 出口写入这些字段；旧/成功/取消
        # 报告行没有它们，正文逐字节不变）。缺失时明确标注，避免 MA 把
        # 「本轮无消息」误读成「诊断丢失」。
        if "lastMessage" in r or "lastMessageRole" in r:
            last_message = r.get("lastMessage")
            if isinstance(last_message, str) and last_message.strip():
                lines.append(
                    f"lastMessageRole: {_field_value(r.get('lastMessageRole'))}")
                lines += ["lastMessage:", last_message]
                if r.get("lastMessageTruncated"):
                    lines.append("lastMessageTruncated: true")
            else:
                lines.append("lastMessage: (no diagnostics available)")
        lines += [
            f"sessionId: {_field_value(r.get('sessionId'))}",
            f"taskId: {_field_value(r.get('taskId'))}",
            f"workerId: {_field_value(r.get('workerId'))}",
        ]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _process_alive(w: Worker) -> bool:
    """检查 worker runtime 是否仍可接收任务。

    stream worker 由 cbc 子进程的 returncode 表示活性；oneshot worker 没有
    常驻 OS 进程（process=None），此时由其常驻 consumer task 表示 runtime。
    ``_consume_task is None`` 兼容构造完成、尚未启动 consumer 的短暂窗口。
    """
    if w.process is not None:
        return w.process.returncode is None
    return w._consume_task is None or not w._consume_task.done()


def _cancel_claude_permission_requests(worker_id: str, message: str) -> None:
    """Resolve open Claude permission waits as denied when a worker dies.

    A permission callback is owned by the Claude process that requested it.
    Leaving its MCP request hanging would keep the child process and the Pan
    consumer alive forever after kill/restart.  Deny is the only safe result
    when that process is no longer available.
    """
    w = workers.get(worker_id)
    for request_id, (owner_id, future) in list(_claude_permission_requests.items()):
        if owner_id != worker_id:
            continue
        _claude_permission_requests.pop(request_id, None)
        if w is not None:
            w.pending_interactions.pop(f"request:{request_id}", None)
        if not future.done():
            future.set_result({
                "type": "permission_response",
                "request_id": request_id,
                "decision": "decline",
                "message": message,
            })


async def request_claude_permission(
    worker_id: str, tool_name: str, tool_input: dict | None = None,
) -> dict:
    """Wait for a dashboard decision for Claude's MCP permission callback.

    This is called by ``mcp__pan__permission_prompt`` over the local HTTP API.
    It returns Claude Code's documented ``allow/deny`` result shape rather than
    exposing Pan's internal control envelope to the MCP client.
    """
    w = workers.get(worker_id)
    if w is None:
        return {"behavior": "deny", "message": "Pan worker is not available"}
    if getattr(w.adapter, "name", "") != "claude":
        return {"behavior": "deny", "message": "Worker is not a Claude worker"}
    if not _process_alive(w):
        return {"behavior": "deny", "message": "Claude worker is not running"}

    request_id = uuid.uuid4().hex
    original_input = dict(tool_input or {})
    event = {
        "type": "approval.request",
        "adapter": "claude",
        "method": "claude/permission",
        "request_id": request_id,
        "params": {
            "tool_name": str(tool_name or "unknown"),
            "input": original_input,
        },
    }
    future = asyncio.get_running_loop().create_future()
    _claude_permission_requests[request_id] = (worker_id, future)
    _update_pending_interactions(w, event)
    try:
        # The broadcast is inside the same finally-protected region as the
        # wait.  A disconnected/failed dashboard broadcaster must not strand
        # the MCP callback in the global pending map.
        await _bcast({
            "type": "worker.stream",
            "workerId": worker_id,
            "sessionId": w.session_id,
            "generation": w.generation,
            "taskSeq": w._current_seq,
            "executionSeq": w._execution_seq or None,
            "taskId": w._current_task_id,
            "event": event,
        })
        try:
            control = await asyncio.wait_for(
                future, timeout=_CLAUDE_PERMISSION_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            return {"behavior": "deny", "message": "Permission request timed out"}
    finally:
        _claude_permission_requests.pop(request_id, None)
        w.pending_interactions.pop(f"request:{request_id}", None)

    if not isinstance(control, dict):
        return {"behavior": "deny", "message": "Invalid permission response"}
    decision = str(control.get("decision") or control.get("behavior") or "decline")
    if decision in {"accept", "allow", "acceptForSession"}:
        updated_input = control.get("updatedInput")
        if not isinstance(updated_input, dict):
            updated_input = original_input
        return {"behavior": "allow", "updatedInput": updated_input}
    return {
        "behavior": "deny",
        "message": str(control.get("message") or "User denied this action"),
    }


async def _resolve_claude_permission(worker_id: str, control: dict) -> bool:
    """Resolve one pending Claude permission request, if it belongs to worker."""
    request_id = control.get("request_id")
    if request_id is None:
        return False
    key = str(request_id)
    pending = _claude_permission_requests.get(key)
    if pending is None or pending[0] != worker_id:
        return False
    _claude_permission_requests.pop(key, None)
    w = workers.get(worker_id)
    if w is not None:
        w.pending_interactions.pop(f"request:{key}", None)
    future = pending[1]
    if not future.done():
        future.set_result(dict(control))
    if w is not None:
        await _bcast({
            "type": "worker.stream",
            "workerId": worker_id,
            "sessionId": w.session_id,
            "generation": w.generation,
            "taskSeq": w._current_seq,
            "executionSeq": w._execution_seq or None,
            "taskId": w._current_task_id,
            "event": {
                "type": "claude.permission_resolved",
                "request_id": key,
            },
        })
    return True


def _is_task_item(item) -> bool:
    return isinstance(item, dict) and item.get("type") == "task"


def _is_formal_task_item(item) -> bool:
    """Return whether a task row owns the assign idempotency key.

    An inherited ``taskId`` links an agent_send message to the active formal
    task for reporting, but must not make repeated follow-up messages look like
    duplicate assign calls.  Older task rows have no origin marker and are
    therefore treated as formal for backward compatibility.
    """
    return (_is_task_item(item)
            and bool(item.get("taskId"))
            and item.get("taskIdSource", "assign") != "active")


def _is_valid_task_item(item) -> bool:
    return _is_task_item(item) and isinstance(item.get("text"), str)


def _is_report_item(item) -> bool:
    """Return whether a persisted item is an actual report/notice/通道 item."""
    return _queue_item_kind(item) == "report" or (
        isinstance(item, dict) and item.get("type") in {"qq", "wechat"}
    )


# Durable delivery state.  The queue row stays in ``Session.queue_pending``
# until the provider hand-off has succeeded.  ``pending_signal`` is only a
# wake-up hint and never owns the message body.
_DELIVERY_QUEUED = "queued"
_DELIVERY_RESERVED = "reserved"
_DELIVERY_WRITING = "writing"
_DELIVERY_SENT = "sent_to_cli"
# Old states are accepted as migration inputs.  They are deliberately mapped
# back to queued because the new contract prefers recovery/retry and explicitly
# accepts the small duplicate window around an uncertain provider hand-off.
_DELIVERY_IN_FLIGHT = "in_flight"
_DELIVERY_WRITE_FAILED = "write_failed"
_DELIVERY_UNKNOWN = "unknown_after_crash"
_DELIVERY_DELETED = "deleted"
_DELIVERY_STATES = {
    _DELIVERY_QUEUED,
    _DELIVERY_RESERVED,
    _DELIVERY_WRITING,
    _DELIVERY_SENT,
    _DELIVERY_IN_FLIGHT,
    _DELIVERY_WRITE_FAILED,
    _DELIVERY_UNKNOWN,
    _DELIVERY_DELETED,
}

_QUEUE_RETRY_BASE_SEC = 1.0
_QUEUE_RETRY_MAX_SEC = 30.0


def _delivery_state(item: dict) -> str:
    state = item.get("deliveryState")
    return state if state in _DELIVERY_STATES else _DELIVERY_QUEUED


def _retry_due(item: dict) -> bool:
    """Whether a queued item may be attempted now.

    ``nextAttemptAt`` is wall-clock time so a restart can honor the same
    backoff.  Invalid or absent values are treated as immediately due for
    compatibility with old queue rows.
    """
    value = item.get("nextAttemptAt")
    return not isinstance(value, (int, float)) or value <= time.time()


def _is_dispatchable(item: dict) -> bool:
    return _delivery_state(item) == _DELIVERY_QUEUED and _retry_due(item)


def _queue_item_kind(item: dict) -> str | None:
    """Return the processing kind without guessing task/report from status."""
    if not isinstance(item, dict):
        return None
    if item.get("type") == "task":
        return "task"
    if item.get("type") in {"report", "notice", "qq", "wechat"}:
        # 通道类提醒（qq/wechat）各自成一种 kind，抬头/字段渲染不同；
        # report/notice 统一按 report 处理。
        return item["type"] if item["type"] in {"qq", "wechat"} else "report"
    # Pre-redesign reports had no type but carried a result.  Preserve that
    # shape while ensuring a task envelope always wins over ``result``.
    if "result" in item:
        return "report"
    return None


def _is_pauseable_agent_report(item) -> bool:
    """Only classify durable child-agent reports as pausable reports.

    Explicit notice and channel types retain their own delivery behavior. The
    source check also recognizes legacy report envelopes without a type.
    """
    if not isinstance(item, dict):
        return False
    item_type = item.get("type")
    if item_type in {"notice", "qq", "wechat"}:
        return False
    if item_type == "task":
        return item.get("source") == "report"
    if item_type in {"report", "zombie"}:
        return item.get("source") in {None, "report"}
    return item_type is None and item.get("source") == "report" and "result" in item


def _queue_item_locked(item) -> bool:
    return isinstance(item, dict) and bool(
        _message_locks.source(item) is not None or item.get("queueLockAutoReport")
        or item.get("forceSendHeld")
        or item.get("queueSteerPending") or item.get("selfConsumeUncertain")
    )


def queue_item_eligible(s, item, *, queued: bool = True) -> bool:
    """Shared own-queue and final handoff gates; lock sources are authoritative.

    Stop reports materializes queueLockAutoReport on the affected reports;
    an explicit UI unlock can release a report while the future-report rule
    remains enabled. Do not infer extra locks from that future-report flag.
    """
    return (s is not None and isinstance(item, dict)
            and not getattr(s, "queue_paused", False) and not _native_handoff_blocked(s)
            and _queue_item_kind(item) is not None
            and not _queue_item_locked(item)
            and not queue_item_edit_locked(s, _queue_item_id(item))
            and (not queued or _is_dispatchable(item)))


def _only_locked_queue_items(s) -> bool:
    """Whether automatic recovery has only locked queued rows to ignore."""
    pending = list(getattr(s, "queue_pending", None) or []) if s is not None else []
    return bool(pending) and all(
        isinstance(item, dict)
        and _delivery_state(item) == _DELIVERY_QUEUED
        and _queue_item_locked(item)
        for item in pending
    )


def _queue_recovery_blocked(s) -> bool:
    """A Session pause and locked-only backlog independently block recovery."""
    native_blocked = _native_handoff_blocked(s)
    if (s is not None and type(s.adapter_config.get("native_control_next")) is bool
            and find_worker_by_session(s.id) is None and s.id not in native_starting_sessions):
        # After Pan restarts there is no old owner to retire. Let normal durable
        # queue recovery spawn once; _create_worker promotes the saved intent.
        native_blocked = False
    return bool(getattr(s, "queue_paused", False)) or native_blocked or (
        _only_locked_queue_items(s) or (_offline.is_illegal(s) and not _has_sendable_queued_items(s)))


native_starting_sessions: set[str] = set()


def _native_handoff_blocked(s) -> bool:
    if s is None:
        return False
    if s.id in native_starting_sessions:
        return True
    if type(s.adapter_config.get("native_control_next")) is bool:
        return True
    w = find_worker_by_session(s.id)
    return w is not None and (w.native_control_mode != "pan" or w._native_reservation_busy or w._native_external_turn_active
                             or getattr(w, "native_input_uncertain", False))


def _has_sendable_queued_items(s) -> bool:
    """Whether an immediate queue wake has an unlocked queued item."""
    if s is None or getattr(s, "queue_paused", False) or _native_handoff_blocked(s):
        return False
    return any(
        isinstance(item, dict)
        and _queue_item_kind(item) is not None
        and _delivery_state(item) == _DELIVERY_QUEUED
        and _retry_due(item)
        and not _queue_item_locked(item)
        for item in (s.queue_pending or [])
    )


def _serialize_for_cli(adapter, text: str) -> bytes:
    """Serialize a complete protocol message before crossing the CLI boundary."""
    data = adapter.encode_user_message(text)
    if not isinstance(data, bytes):
        data = bytes(data)
    return data


# Compatibility helpers used by the HTTP queue API.  Session.queue_pending is
# still the only pending queue; the ledger below is only a durable receipt
# index, so a successful hand-off remains addressable after the row is removed.
def _queue_item_id(item: dict) -> str:
    value = item.get("queueItemId") or item.get("id")
    if isinstance(value, str) and value:
        item.setdefault("queueItemId", value)
        return value
    value = "q_" + uuid.uuid4().hex
    item["id"] = value
    item["queueItemId"] = value
    return value


def _queue_kind(item: dict) -> str | None:
    return _queue_item_kind(item)


def _queue_source(item: dict) -> str | None:
    if _queue_item_kind(item) != "task":
        return item.get("source") if isinstance(item.get("source"), str) else None
    return _task_source(item)


_IDEMPOTENCY_INDEX_VERSION = 1
_IDEMPOTENCY_BUCKETS = ("taskId", "clientMessageId")
# A freshly completed receipt must survive at least this small crash/retry
# window even if a test or deployment overrides the normal seven-day TTL.
# Count eviction uses the same floor, so an in-flight retry cannot lose its
# only durable proof merely because the ledger is temporarily over capacity.
_RECEIPT_MIN_RETRY_SEC = getattr(_sess, "QUEUE_RECEIPT_MIN_RETRY_SEC", 60.0)
_RECEIPT_COMPACT_KEYS = frozenset({
    "id", "queueItemId", "type", "kind", "source", "sourceSessionId",
    "sessionId", "taskId", "taskIdSource", "clientMessageId", "eventId",
    "jobId", "creatorSessionId", "targetSessionId", "targetSessionIds",
    "status", "workerId", "seq", "revision", "createdAt", "updatedAt",
    "receiptAt", "deliveredAt", "deletedAt", "deliveryState",
    "dispatchState", "receiptVersion", "receiptOnly", "reportConsumeIds",
})


def _list_sessions_shallow():
    """Use the shallow loader while tolerating legacy test/embedder shims."""
    try:
        return _sess.list_all(load_history=False)
    except TypeError:
        return _sess.list_all()


def _get_session_shallow(session_id: str):
    """Read queue/metadata state without hydrating a cold history JSONL."""
    try:
        return _sess.get(session_id, load_history=False)
    except TypeError:
        return _sess.get(session_id)


def _empty_idempotency_index() -> dict:
    return {
        "version": _IDEMPOTENCY_INDEX_VERSION,
        "taskId": {},
        "clientMessageId": {},
    }


def _idempotency_index(s, *, normalize: bool = True) -> tuple[dict, bool]:
    """Return the index and whether its top-level shape needed repair.

    Full entry normalization is only needed once per loaded Session.  Hot
    idempotency lookups pass ``normalize=False`` so a 4k-entry index does not
    turn every retry into another linear scan.
    """
    raw = getattr(s, "queue_idempotency_index", None)
    changed = False
    if not isinstance(raw, dict) or raw.get("version") != _IDEMPOTENCY_INDEX_VERSION:
        index = _empty_idempotency_index()
        changed = raw is not None
    else:
        index = raw
        for bucket in _IDEMPOTENCY_BUCKETS:
            values = index.get(bucket)
            if not isinstance(values, dict):
                index[bucket] = {}
                changed = True
                continue
            if normalize:
                for key, entry in list(values.items()):
                    if not isinstance(key, str) or not key:
                        values.pop(key, None)
                        changed = True
                    elif isinstance(entry, str):
                        values[key] = {"queueItemId": entry, "seenAt": 0.0}
                        changed = True
                    elif not isinstance(entry, dict):
                        values.pop(key, None)
                        changed = True
    if index is not raw:
        s.queue_idempotency_index = index
    return index, changed


def _idempotency_seen_at(item: dict | None, fallback: float | None = None) -> float:
    if isinstance(item, dict):
        for key in (
            "receiptAt", "deliveredAt", "deletedAt", "createdAt", "updatedAt",
            "seenAt",
        ):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return float(fallback if fallback is not None else time.time())


def _index_idempotency_key(
    s, bucket: str, key: object, *, queue_item_id: str | None,
    seen_at: float | None = None, prefer_existing: bool = False,
) -> bool:
    if bucket not in _IDEMPOTENCY_BUCKETS or not isinstance(key, str) or not key:
        return False
    index, changed = _idempotency_index(s, normalize=False)
    values = index[bucket]
    existing = values.get(key)
    if isinstance(existing, str):
        existing = {"queueItemId": existing, "seenAt": 0.0}
        values[key] = existing
        changed = True
    if not isinstance(existing, dict):
        values[key] = {
            "queueItemId": queue_item_id,
            "seenAt": _idempotency_seen_at(None, seen_at),
        }
        return True
    old_id = existing.get("queueItemId")
    # A current queue/ledger record is more useful than a history-only
    # tombstone.  Otherwise preserve the first durable receipt for idempotency.
    if queue_item_id and not old_id:
        if old_id != queue_item_id:
            existing["queueItemId"] = queue_item_id
            changed = True
    if not isinstance(existing.get("seenAt"), (int, float)):
        existing["seenAt"] = _idempotency_seen_at(None, seen_at)
        changed = True
    elif seen_at is not None and queue_item_id and not old_id:
        existing["seenAt"] = float(seen_at)
        changed = True
    return changed


def _index_queue_item(s, item: dict, *, state: str | None = None) -> bool:
    """Index formal task/client receipt keys without scanning history."""
    if not isinstance(item, dict):
        return False
    item_id = _queue_item_id(item)
    seen_at = _idempotency_seen_at(item)
    changed = False
    if _is_formal_task_item(item) and item.get("taskId"):
        changed = _index_idempotency_key(
            s, "taskId", item.get("taskId"), queue_item_id=item_id,
            seen_at=seen_at, prefer_existing=True,
        ) or changed
    if item.get("clientMessageId"):
        changed = _index_idempotency_key(
            s, "clientMessageId", item.get("clientMessageId"),
            queue_item_id=item_id, seen_at=seen_at, prefer_existing=True,
        ) or changed
    return changed


def _synthetic_receipt(s, bucket: str, key: str, entry: dict) -> dict:
    """Represent an indexed-but-compacted receipt without restoring its body."""
    queue_item_id = entry.get("queueItemId")
    receipt = {
        "type": "task" if bucket in {"taskId", "clientMessageId"} else "receipt",
        "kind": "task",
        "deliveryState": _DELIVERY_SENT,
        "dispatchState": _DELIVERY_SENT,
        "receiptOnly": True,
        "receiptVersion": 1,
    }
    if isinstance(queue_item_id, str) and queue_item_id:
        receipt.update({"id": queue_item_id, "queueItemId": queue_item_id})
    if bucket == "taskId":
        receipt["taskId"] = key
    else:
        receipt["clientMessageId"] = key
    return receipt


def _history_idempotency_rows(s) -> list[dict]:
    """Load only a bounded tail when rebuilding an old Session's index."""
    limit = max(
        getattr(_sess, "ACCEPTED_INPUT_ID_MAX", 256),
        getattr(_sess, "QUEUE_IDEMPOTENCY_INDEX_MAX_ENTRIES", 4096),
    )
    if getattr(s, "_history_loaded", True):
        return [row for row in (s.history or [])[-limit:]
                if isinstance(row, dict)]
    history_path = _sess._history_path(s.id)
    if history_path.exists():
        # Queue GET runs during Session selection. Use the validated summary
        # count to parse only the bounded recovery tail, instead of decoding
        # every JSONL row on the event loop. Stale/incomplete projections still
        # take the history reader's compatibility path.
        projection = getattr(s, "summary_projection", None)
        known_total = (
            projection.get("history_total")
            if _sess.is_complete_summary_projection(projection) else None
        )
        rows, _ = _sess._history_page_from_jsonl(
            history_path, before=0, limit=limit, known_total=known_total,
        )
        return [row for row in rows if isinstance(row, dict)]
    page = _sess.history_page(s.id, limit=limit)
    if not page:
        return []
    return [row for row in page.get("history", []) if isinstance(row, dict)]


def _ensure_idempotency_index(s) -> bool:
    """Build/repair the durable task/client receipt index once per Session.

    Old data has no index.  The compatibility rebuild scans at most the
    bounded history tail once; subsequent assign/browser retries use the
    persisted O(1) maps and the ledger's queueItemId lookup.
    """
    if s is None:
        return False
    had_index = bool(getattr(s, "_idempotency_index_present", False))
    index, changed = _idempotency_index(
        s, normalize=not getattr(s, "_idempotency_index_built", False),
    )
    if getattr(s, "_idempotency_index_built", False):
        return changed
    if not getattr(s, "_idempotency_index_built", False):
        rows = _history_idempotency_rows(s)
        for entry in rows:
            # A legacy history row often has no timestamp.  Treat the first
            # index build as the receipt's observation time rather than as a
            # 1970 epoch value, otherwise the compatibility index would be
            # expired immediately on its first compaction pass.
            seen_at = _idempotency_seen_at(entry)
            task_id = entry.get("taskId")
            if task_id and entry.get("taskIdSource", "assign") != "active":
                changed = _index_idempotency_key(
                    s, "taskId", task_id, queue_item_id=(
                        entry.get("queueItemId") or entry.get("queueItemID")
                    ), seen_at=seen_at,
                ) or changed
            client_id = entry.get("clientMessageId")
            if client_id:
                changed = _index_idempotency_key(
                    s, "clientMessageId", client_id, queue_item_id=(
                        entry.get("queueItemId") or entry.get("queueItemID")
                    ), seen_at=seen_at,
                ) or changed
        s._idempotency_index_built = True
        changed = (not had_index) or changed

    # Queue/ledger rows are bounded independently from history and may have
    # been edited by the HTTP queue API since the last index persistence.  A
    # legacy pending row without a ledger mirror is copied once here so a
    # subsequent lookup can resolve its queueItemId directly, without a
    # fallback scan through the ever-growing pending list.
    for item in (s.queue_pending or []):
        item_id = _queue_item_id(item) if isinstance(item, dict) else None
        if (item_id and item_id not in s.queue_delivery_ledger
                and isinstance(item, dict)):
            s.queue_delivery_ledger[item_id] = dict(item)
            changed = True
        changed = _index_queue_item(s, item) or changed
    for item in (getattr(s, "queue_delivery_ledger", {}) or {}).values():
        changed = _index_queue_item(s, item) or changed
    for client_id in getattr(s, "accepted_input_ids", []) or []:
        changed = _index_idempotency_key(
            s, "clientMessageId", client_id, queue_item_id=None,
        ) or changed
    s._idempotency_index_present = True
    return changed


def _ledger_record(s, queue_item_id: str | None) -> dict | None:
    if not queue_item_id:
        return None
    record = (getattr(s, "queue_delivery_ledger", {}) or {}).get(str(queue_item_id))
    return record if isinstance(record, dict) else None


def _remember_queue_item(s, item: dict, state: str | None = None) -> dict:
    item_id = _queue_item_id(item)
    if state in {_DELIVERY_SENT, _DELIVERY_DELETED}:
        item.setdefault("receiptAt", time.time())
        if state == _DELIVERY_SENT:
            item.setdefault("deliveredAt", item.get("receiptAt"))
        elif state == _DELIVERY_DELETED:
            item.setdefault("deletedAt", item.get("receiptAt"))
    record = dict(item)
    if state is not None:
        record["deliveryState"] = state
    s.queue_delivery_ledger[item_id] = record
    _index_queue_item(s, record, state=state)
    return record


def _sync_queued_ledger(s) -> bool:
    changed = False
    for item in (s.queue_pending or []):
        if not isinstance(item, dict) or _queue_item_kind(item) is None:
            continue
        item_id = _queue_item_id(item)
        previous = _ledger_record(s, item_id)
        if previous is None or _delivery_state(previous) == _DELIVERY_QUEUED:
            record = dict(item)
            if previous != record:
                s.queue_delivery_ledger[item_id] = record
                changed = True
            changed = _index_queue_item(s, record) or changed
    return changed


def _set_delivery_state(s, item: dict, state: str) -> None:
    item["deliveryState"] = state
    item["dispatchState"] = state
    if state in {_DELIVERY_SENT, _DELIVERY_DELETED}:
        now = time.time()
        item.setdefault("receiptAt", now)
        if state == _DELIVERY_SENT:
            item.setdefault("deliveredAt", item.get("receiptAt"))
        else:
            item.setdefault("deletedAt", item.get("receiptAt"))
    _remember_queue_item(s, item, state)


def _recover_delivery_states(s) -> bool:
    return _migrate_queue_delivery_state(s, restore_ledger=True)


def _find_queue_item_by_idempotency(s, client_message_id=None, task_id=None):
    if s is None:
        return None
    _ensure_idempotency_index(s)
    for bucket, key in (("clientMessageId", client_message_id),
                        ("taskId", task_id)):
        if not key:
            continue
        entry = (getattr(s, "queue_idempotency_index", {}) or {}).get(bucket, {}).get(key)
        if not isinstance(entry, dict):
            continue
        queue_item_id = entry.get("queueItemId")
        if isinstance(queue_item_id, str) and queue_item_id:
            record = _ledger_record(s, queue_item_id)
            if isinstance(record, dict):
                return record
            # The index is an acceleration layer, not an independent receipt
            # store.  If its ledger target has been evicted (or an operator
            # has compacted the ledger directly), discard the stale pointer
            # and let the process-local terminal cache or the durable history
            # index decide the retry outcome.  Returning a synthetic
            # sent_to_cli receipt here would mask a valid local ``done``
            # result and could keep an expired task id alive forever.
            values = (getattr(s, "queue_idempotency_index", {}) or {}).get(
                bucket, {})
            if values.get(key) is entry:
                values.pop(key, None)
            return None
        # History/accepted-input entries without a queue receipt still count
        # for the boolean dedup path, but cannot provide a terminal receipt
        # payload to callers such as assign().
        return None
    return None


def _clear_delivery_reservation(item: dict) -> None:
    for key in ("reservedBy", "reservedGeneration", "reservedAt"):
        item.pop(key, None)


def _queue_item_backoff(item: dict, reason: str, *, immediate: bool = False) -> None:
    """Return an item to queued with bounded, persisted retry backoff."""
    if item.get("selfConsumeAttempted"):
        # A selected original item whose write outcome is unknown must not be
        # retried by recovery/watchdog/FIFO, including after main restarts.
        item["selfConsumeUncertain"] = True
    attempts = item.get("deliveryAttempts", 0)
    try:
        attempts = max(0, int(attempts))
    except (TypeError, ValueError):
        attempts = 0
    item["deliveryAttempts"] = attempts + 1
    delay = 0.0 if immediate else min(
        _QUEUE_RETRY_MAX_SEC,
        _QUEUE_RETRY_BASE_SEC * (2 ** min(attempts, 5)),
    )
    item["deliveryState"] = _DELIVERY_QUEUED
    item["nextAttemptAt"] = time.time() + delay
    if reason:
        item["lastDeliveryError"] = str(reason)[:1000]
    _clear_delivery_reservation(item)


def _compact_receipt_record(record: dict, queue_item_id: str) -> dict:
    """Drop message bodies from an old terminal receipt, keeping its proof."""
    compact = {
        key: value for key, value in record.items()
        if key in _RECEIPT_COMPACT_KEYS
    }
    compact["id"] = queue_item_id
    compact["queueItemId"] = queue_item_id
    compact["receiptOnly"] = True
    compact["receiptVersion"] = 1
    compact.setdefault("receiptAt", _idempotency_seen_at(record))
    return compact


def _remove_idempotency_receipt(s, record: dict, queue_item_id: str) -> bool:
    """Remove only index entries that still point at this evicted receipt."""
    changed = False
    index, normalized = _idempotency_index(s, normalize=False)
    changed = normalized
    for bucket in _IDEMPOTENCY_BUCKETS:
        values = index[bucket]
        for key, entry in list(values.items()):
            if not isinstance(entry, dict):
                values.pop(key, None)
                changed = True
                continue
            if entry.get("queueItemId") == queue_item_id:
                values.pop(key, None)
                changed = True
    return changed


def _compact_delivery_receipts(s) -> bool:
    """Bound terminal receipt count/age while preserving recovery rows.

    ``queue_pending`` is never shortened here.  Every non-terminal ledger row
    is retained, as is a terminal row that still has a matching pending copy.
    Terminal rows are first reduced to metadata-only receipts after the
    compaction age, then evicted by TTL/count; the idempotency index is swept
    in the same operation so an expired key is the only way a retry can start
    a new item.
    """
    if s is None:
        return False
    changed = _ensure_idempotency_index(s)
    ledger = getattr(s, "queue_delivery_ledger", None)
    if not isinstance(ledger, dict):
        s.queue_delivery_ledger = ledger = {}
        changed = True
    pending_ids = {
        _queue_item_id(item) for item in (s.queue_pending or [])
        if isinstance(item, dict)
    }
    now = time.time()
    ttl = max(
        _RECEIPT_MIN_RETRY_SEC,
        float(getattr(_sess, "QUEUE_RECEIPT_TTL_SEC", 7 * 24 * 60 * 60)),
    )
    # Compact reasonably old bodies even when the count bound has not fired.
    # This keeps the common long-running case cheap without changing the
    # receipt key/state visible to duplicate callers.
    compact_after = min(ttl, 15 * 60)
    terminal: list[tuple[str, dict, float]] = []
    for queue_item_id, record in list(ledger.items()):
        if not isinstance(record, dict):
            ledger.pop(queue_item_id, None)
            changed = True
            continue
        state = _delivery_state(record)
        if state not in {_DELIVERY_SENT, _DELIVERY_DELETED}:
            changed = _index_queue_item(s, record, state=state) or changed
            continue
        receipt_at = _idempotency_seen_at(record, now)
        if not record.get("receiptAt"):
            record["receiptAt"] = receipt_at
            changed = True
        if queue_item_id not in pending_ids:
            terminal.append((str(queue_item_id), record, receipt_at))
        if (now - receipt_at) >= compact_after and not record.get("receiptOnly"):
            ledger[queue_item_id] = _compact_receipt_record(record, str(queue_item_id))
            changed = True

    terminal.sort(key=lambda value: value[2])
    evict = {
        queue_item_id for queue_item_id, _, receipt_at in terminal
        if (now - receipt_at) >= ttl
    }
    overflow = len(terminal) - max(
        0, int(getattr(_sess, "QUEUE_RECEIPT_MAX_ENTRIES", 2048)),
    )
    if overflow > 0:
        evict.update(queue_item_id for queue_item_id, _, _ in terminal[:overflow])
        # Never use count pressure to evict a receipt still inside the
        # minimum crash/retry window.  A short-lived overflow is safer than
        # opening a duplicate-provider-turn window for a fresh receipt.
        evict.difference_update(
            queue_item_id for queue_item_id, _, _ in terminal
            if (now - _idempotency_seen_at(
                ledger.get(queue_item_id), now)) < _RECEIPT_MIN_RETRY_SEC
        )
    for queue_item_id in evict:
        if queue_item_id in pending_ids:
            continue
        removed = ledger.pop(queue_item_id, None)
        if isinstance(removed, dict):
            changed = _remove_idempotency_receipt(
                s, removed, str(queue_item_id),
            ) or changed

    # Bound the compact index independently.  Active/pending entries are
    # never dropped; detached tombstones expire with the same retention window.
    index, normalized = _idempotency_index(s, normalize=False)
    changed = normalized or changed
    active_ids = set(pending_ids)
    active_ids.update(
        str(queue_item_id) for queue_item_id, record in ledger.items()
        if isinstance(record, dict)
        and _delivery_state(record) not in {_DELIVERY_SENT, _DELIVERY_DELETED}
    )
    index_entries: list[tuple[float, str, str, dict]] = []
    for bucket in _IDEMPOTENCY_BUCKETS:
        for key, entry in list(index[bucket].items()):
            if not isinstance(entry, dict):
                index[bucket].pop(key, None)
                changed = True
                continue
            qid = entry.get("queueItemId")
            seen_at = _idempotency_seen_at(entry, now)
            if qid in active_ids:
                continue
            if (isinstance(qid, str) and qid in ledger
                    and _delivery_state(ledger[qid]) not in {
                        _DELIVERY_SENT, _DELIVERY_DELETED}):
                continue
            index_entries.append((seen_at, bucket, key, entry))
    index_entries.sort(key=lambda value: value[0])
    index_limit = max(
        0, int(getattr(_sess, "QUEUE_IDEMPOTENCY_INDEX_MAX_ENTRIES", 4096)),
    )
    index_evict = {
        (bucket, key) for seen_at, bucket, key, _ in index_entries
        if (now - seen_at) >= ttl
    }
    if len(index_entries) > index_limit:
        eligible_entries = [
            value for value in index_entries
            if (now - value[0]) >= _RECEIPT_MIN_RETRY_SEC
        ]
        overflow = len(index_entries) - index_limit
        index_evict.update(
            (bucket, key)
            for _, bucket, key, _ in eligible_entries[:overflow]
        )
    for bucket, key in index_evict:
        if index[bucket].pop(key, None) is not None:
            changed = True

    client_index = index["clientMessageId"]
    bounded_ids = [
        value for value in (getattr(s, "accepted_input_ids", []) or [])
        if value in client_index
    ]
    accepted_limit = int(getattr(_sess, "ACCEPTED_INPUT_ID_MAX", 256))
    bounded_ids = list(dict.fromkeys(bounded_ids))[-accepted_limit:]
    if bounded_ids != getattr(s, "accepted_input_ids", []):
        s.accepted_input_ids = bounded_ids
        changed = True
    return changed


def _prepare_receipt_persistence(s) -> bool:
    """Normalize/index/compact receipt metadata before a durable save."""
    changed = _sync_queued_ledger(s)
    changed = _ensure_idempotency_index(s) or changed
    changed = _compact_delivery_receipts(s) or changed
    return changed


def _has_dispatchable_items(s) -> bool:
    """Whether a session has work that automatic recovery may actually send."""
    if s is None or getattr(s, "queue_paused", False):
        return False
    if any(
        isinstance(item, dict)
        and _queue_item_kind(item) is not None
        and _delivery_state(item) == _DELIVERY_QUEUED
        and not _queue_item_locked(item)
        for item in (s.queue_pending or [])
    ):
        return True
    # A live worker is needed once to normalize old reserved/in-flight rows.
    return any(
        isinstance(item, dict)
        and _delivery_state(item) in {
            _DELIVERY_RESERVED, _DELIVERY_WRITING, _DELIVERY_IN_FLIGHT,
            _DELIVERY_WRITE_FAILED, _DELIVERY_UNKNOWN,
        }
        for item in (s.queue_pending or [])
    )


# 合法 queue 行 source 标签（来源元数据，开放但受校验）。source 只是描述
# 消息/报告来自哪个来源，不是身份或权限凭证——授权继续由 sourceSessionId +
# managed/readonly 校验承担（_validate_source_metadata / HTTP _source_access_error）。
#
# 既有合法来源（当前调用链与历史数据）：
#   user          浏览器/用户消息（enqueue_user_message）
#   agent         agent 发起的消息；HTTP /api/* 与 worker 层缺省值
#   system_prompt worker 注入 system prompt 的任务
#   report        订阅制 report / zombie / notice 落盘行
# 已规划的未来外部来源（open 集合，新增来源先在此登记再使用）：
#   meta-agent    外部 meta-agent 编排（未来 external orchestration）
#   automation    定时 / 脚本 / CI 自动化
SOURCE_TYPES = {"plugin", "system", "user", "agent", "system_prompt", "report",
                "meta-agent", "automation"}

# App Settings → Behavior → Message auto force send.  The three switches are
# independent and default off; each names a structured queue origin, never a
# body prefix.
AUTO_FORCE_SOURCE_CLASSES = ("system", "ma", "report")


def _normalize_source_type(source, default: str = "agent") -> tuple[str | None, str | None]:
    """Normalize the source-type label without ever treating an ID as one.

    source 是开放但受校验的来源元数据：缺省/空值取 default，显式传入的值必须
    在 SOURCE_TYPES 白名单内（含未来 external meta-agent / automation）。
    """
    if source is None or source == "":
        source = default
    if not isinstance(source, str) or source not in SOURCE_TYPES:
        return None, f"Unknown task source: {source}"
    return source, None


def _normalize_source_session_id(source_session_id) -> tuple[str | None, str | None]:
    """Normalize and validate the concrete source Session ID metadata."""
    if source_session_id is None or source_session_id == "":
        return None, None
    if not isinstance(source_session_id, str):
        return None, "sourceSessionId must be a string"
    if _get_session_shallow(source_session_id) is None:
        return None, f"Source session {source_session_id} not found"
    return source_session_id, None


def _validate_source_metadata(target, source, source_session_id,
                              default: str = "agent") -> tuple[str | None, str | None, str | None]:
    """Validate source type, source Session metadata, and readonly access."""
    source_type, error = _normalize_source_type(source, default=default)
    if error:
        return None, None, error
    source_sid, error = _normalize_source_session_id(source_session_id)
    if error:
        return None, None, error
    if (target is not None and source_sid and source_sid != target.id
            and target.readonly_session and target.managed_by == source_sid):
        return None, None, READONLY_SESSION_ERROR
    if source_type in {"agent", "meta-agent"} and source_sid and target is not None:
        source_type = _agent_message_source(source_sid, target.id)
    return source_type, source_sid, None


def _agent_message_source(sender_id: str, target_id: str) -> str:
    # Classify from persisted identity only. A corrupt cyclic chain fails closed
    # to MA for every target, even if a target was encountered before the loop.
    ancestors = set()
    visited = {sender_id}
    current = _get_session_shallow(sender_id)
    while current and current.managed_by:
        parent = _get_session_shallow(current.managed_by)
        if parent is None:
            break
        if parent.id in visited:
            return "agent"
        visited.add(parent.id)
        ancestors.add(parent.id)
        current = parent
    return "report" if target_id in ancestors else "agent"


async def _capture_auto_report(w, items):
    snapshot = _auto_report.admission(items, *await _auto_report.context())
    w._auto_report_admission = snapshot
    return snapshot


def _task_source(item: dict) -> str | None:
    """Return an explicit task origin, safely normalizing legacy text items."""
    source = item.get("source")
    if source is None:
        # Pre-L4 agent sends carried source=agent; an unmarked text envelope is
        # therefore safest as a dashboard/user task, never an agent report.
        return "user"
    return source if isinstance(source, str) and source in SOURCE_TYPES else None


def _migrate_legacy_task_items(s) -> bool:
    """Normalize pre-envelope user messages in-place and return whether changed.

    Old files used ``{text, source}`` as queue rows.  They are user/agent tasks,
    never reports, so the migration is intentionally conservative and gives an
    unmarked row ``source=user``.  The migration only changes the in-memory
    Session; the caller persists it before starting the consumer.
    """
    changed = False
    for item in (s.queue_pending or []):
        if not isinstance(item, dict) or item.get("type") is not None:
            continue
        if not isinstance(item.get("text"), str):
            continue
        item["type"] = "task"
        item.setdefault("id", uuid.uuid4().hex)
        if _task_source(item) is None:
            item["source"] = "user"
        else:
            item.setdefault("source", "user")
        if item.get("seq") is None:
            s.task_seq += 1
            item["seq"] = s.task_seq
        item.setdefault("taskId", None)
        item.setdefault("deliveryState", _DELIVERY_QUEUED)
        changed = True
        _log.info("[Session %s] migrated legacy text queue item id=%s to task envelope",
                  s.id, item["id"])
    return changed


def _migrate_queue_delivery_state(s, *, restore_ledger: bool = False) -> bool:
    """Migrate old queue rows to the new hand-off contract.

    ``in_flight``/``reserved``/``writing`` from an interrupted Worker are
    ambiguous: the old process may or may not have reached the provider.  The
    new contract explicitly accepts that duplicate window, so these rows are
    restored to ``queued`` instead of being silently discarded.  A durable
    ``sent_to_cli`` row is the opposite: it records a completed hand-off and
    can safely be removed during migration.
    """
    changed = _migrate_legacy_task_items(s)
    keep: list = []
    pending_ids: set[str] = set()
    for item in (s.queue_pending or []):
        if not isinstance(item, dict):
            keep.append(item)
            continue
        if item.get("type") is None and "result" in item:
            item["type"] = "report"
            item.setdefault("source", "report")
            item.setdefault("id", "q_" + uuid.uuid4().hex)
            changed = True
        elif item.get("type") in {"qq", "wechat"}:
            if item.get("source") != item["type"]:
                item["source"] = item["type"]
                changed = True
            if not item.get("id"):
                item["id"] = "q_" + uuid.uuid4().hex
                changed = True
        elif item.get("type") in {"report", "notice"} or (
                item.get("type") not in {None, "task", "qq", "wechat"}
                and "result" in item):
            if not item.get("source"):
                item["source"] = "report"
                changed = True
            if not item.get("id"):
                item["id"] = "q_" + uuid.uuid4().hex
                changed = True
        if _queue_item_kind(item) is None:
            keep.append(item)
            continue
        kind = _queue_item_kind(item)
        if item.get("kind") != kind:
            item["kind"] = kind
            changed = True
        if not item.get("queueItemId"):
            item["queueItemId"] = item["id"]
            changed = True
        if not item.get("createdAt"):
            item["createdAt"] = time.time()
            changed = True
        pending_ids.add(_queue_item_id(item))
        state = _delivery_state(item)
        ledger_record = _ledger_record(s, _queue_item_id(item))
        ledger_state = _delivery_state(ledger_record) if ledger_record else None
        # A sent ledger receipt is durable proof that the provider hand-off
        # crossed the at-most-once boundary.  If a crash left the old row in
        # queue_pending, remove only that stale row; never replay it.
        if ledger_state == _DELIVERY_SENT:
            _remember_queue_item(s, item, _DELIVERY_SENT)
            changed = True
            continue
        # The pending row is the retryable source of truth, but a stale ledger
        # reservation must not remain visible after recovery.  Treat the
        # provider boundary as uncertain and normalize both copies to queued;
        # the existing bounded backoff preserves the documented duplicate
        # window when the hand-off outcome cannot be known.
        if (state == _DELIVERY_QUEUED and ledger_state in {
                _DELIVERY_RESERVED, _DELIVERY_WRITING, _DELIVERY_IN_FLIGHT,
                _DELIVERY_WRITE_FAILED, _DELIVERY_UNKNOWN,
        }):
            item["lastDeliveryState"] = ledger_state
            _queue_item_backoff(item, "reconciled stale queue delivery ledger")
            _remember_queue_item(s, item, _DELIVERY_QUEUED)
            keep.append(item)
            changed = True
            continue
        if state == _DELIVERY_SENT:
            _remember_queue_item(s, item, _DELIVERY_SENT)
            changed = True
            continue
        # Queue snapshots also run this migration. A reservation still owned
        # by the active coordinator is not a crash receipt: normalizing it
        # would reopen the delivery boundary while stdin is being written.
        reserved_by = item.get("reservedBy")
        owner = workers.get(reserved_by) if isinstance(reserved_by, str) else None
        if (state in {_DELIVERY_RESERVED, _DELIVERY_WRITING, _DELIVERY_IN_FLIGHT}
                and owner is not None and owner.session_id == s.id
                and owner.generation == item.get("reservedGeneration")
                and owner._queue_unit_active and _process_alive(owner)
                and _worker_owns_queue_item(owner, item)):
            keep.append(item)
            continue
        if state in {
            _DELIVERY_RESERVED, _DELIVERY_WRITING, _DELIVERY_IN_FLIGHT,
            _DELIVERY_WRITE_FAILED, _DELIVERY_UNKNOWN,
        }:
            item["lastDeliveryState"] = state
            _queue_item_backoff(item, "recovered unfinished CLI hand-off")
            _remember_queue_item(s, item, _DELIVERY_QUEUED)
            changed = True
        elif state != _DELIVERY_QUEUED:
            item["deliveryState"] = _DELIVERY_QUEUED
            _clear_delivery_reservation(item)
            changed = True
        elif any(key in item for key in ("reservedBy", "reservedGeneration", "reservedAt")):
            _clear_delivery_reservation(item)
            changed = True
        keep.append(item)

    # A process can die after the reservation save and before the pending row
    # is observed again.  The ledger is the recovery index, but never the GET
    # /queue snapshot: restore only unfinished records into the real pending
    # queue, while sent/deleted records remain diagnostic/idempotency receipts.
    if not restore_ledger:
        changed = changed or len(keep) != len(s.queue_pending or [])
        s.queue_pending = keep
        # Building the in-memory acceleration index is deliberately not a
        # queue-contract migration.  A clean legacy row must keep the
        # historical ``changed=False`` result (callers use it to decide
        # whether recovery needs a write); the next receipt save persists the
        # index, while a restart can safely rebuild it from the durable queue
        # and bounded history tail.
        _ensure_idempotency_index(s)
        return changed

    for queue_item_id, record in list((getattr(s, "queue_delivery_ledger", {}) or {}).items()):
        if not isinstance(record, dict):
            continue
        record_id = _queue_item_id(record)
        state = _delivery_state(record)
        if state == _DELIVERY_SENT or state == "deleted":
            continue
        if record_id in pending_ids:
            continue
        if _queue_item_kind(record) is None:
            continue
        restored = dict(record)
        restored["kind"] = _queue_item_kind(restored)
        restored["queueItemId"] = record_id
        restored.setdefault("id", record_id)
        restored.setdefault("createdAt", time.time())
        if state != _DELIVERY_QUEUED:
            restored["lastDeliveryState"] = state
            _queue_item_backoff(restored, "recovered unfinished CLI hand-off")
        else:
            restored["deliveryState"] = _DELIVERY_QUEUED
            restored["dispatchState"] = _DELIVERY_QUEUED
            _clear_delivery_reservation(restored)
        keep.append(restored)
        pending_ids.add(record_id)
        _remember_queue_item(s, restored, _DELIVERY_QUEUED)
        changed = True
    if len(keep) != len(s.queue_pending or []):
        changed = True
    s.queue_pending = keep
    # See the non-restoring branch above: index construction is an
    # acceleration/migration detail, not a reason to report a queue-state
    # change for an otherwise canonical row.
    _ensure_idempotency_index(s)
    return changed


def _worker_has_pending_work(w: Worker) -> bool:
    """idle watchdog 回收前检查是否还有待消费信号或持久队列项。"""
    s = _session(w)
    if s is not None and getattr(s, "queue_paused", False):
        return False
    if w.pending_signal is not None and not w.pending_signal.empty():
        return True
    return _has_dispatchable_items(s)


# ── 投递标记与恢复对账 ──
#
# 以下是旧 receipt-before-provider 路径的兼容参考实现，不再由活动 consumer
# 调用。新路径由下方的 hand-off coordinator 负责：queue_pending 只在 provider
# hand-off 成功后移除，未完成 hand-off 的 item 由恢复/重试重新入队。

# 对账扫描 history 尾部深度：仅用于升级旧格式时识别已消费 item；新路径不依赖
# history 对账来决定是否重放，避免“宁可重复”的旧策略破坏 at-most-once。
_DELIVERY_SCAN_DEPTH: int = 50


def _delivery_key(item: dict) -> str:
    """队列项的投递标记 key。

    Task 用 queue item id 作为稳定身份。Report/QQ 保留 taskId + 消息指纹，
    区分同一 taskId 下的不同报告。投递状态和恢复元数据不参与身份，恢复时从
    reserved/writing 归一到 queued 不能改变历史收据键。
    """
    bookkeeping = {
        "deliveryState", "dispatchState", "lastDeliveryState",
        "reservedBy", "reservedGeneration", "reservedAt",
        "deliveryAttempts", "nextAttemptAt", "lastDeliveryError",
        "queueItemId", "receiptAt", "deliveredAt", "deletedAt",
        "position", "revision", "autoReportAdmission", "autoReportExecutionSeq",
        # Last-message context is a derived annotation of the same report, not
        # report identity: adding/upgrading it must not change the receipt key
        # of a report that a previous version already delivered.
        "lastMessage", "lastMessageRole", "lastMessageTruncated",
    }
    if item.get("type") == "task":
        return f"task:{_queue_item_id(item)}"
    identity = {k: v for k, v in item.items() if k not in bookkeeping}
    digest = hashlib.sha1(
        json.dumps(identity, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:12]
    tid = item.get("taskId")
    return f"report:{tid if tid else 'anon'}:{digest}"


def _delivery_key_matches(key: str, item: dict) -> bool:
    """Match stable keys and receipts written by previous versions."""
    current = _delivery_key(item)
    if key == current:
        return True
    if item.get("type") == "task":
        # Older task receipts appended a mutable-metadata digest after this
        # unique queue item id. Preserve those marks during rolling upgrades.
        return key.startswith(f"{current}:")

    # The legacy report/QQ hash included delivery phase fields. Its initial
    # pre-handoff shape was queued, with no recovery/terminal timestamps.
    legacy_bookkeeping = {
        "autoReportAdmission", "autoReportExecutionSeq",
        "deliveryState", "reservedBy", "reservedGeneration", "reservedAt",
        "deliveryAttempts", "nextAttemptAt", "lastDeliveryError", "queueItemId",
        # Rows written before the last-message context existed never hashed
        # these derived fields.
        "lastMessage", "lastMessageRole", "lastMessageTruncated",
    }
    identity = {k: v for k, v in item.items() if k not in legacy_bookkeeping}
    if "dispatchState" in identity:
        identity["dispatchState"] = _DELIVERY_QUEUED
    for field in ("lastDeliveryState", "receiptAt", "deliveredAt", "deletedAt"):
        identity.pop(field, None)
    digest = hashlib.sha1(
        json.dumps(identity, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:12]
    tid = item.get("taskId")
    return key == f"report:{tid if tid else 'anon'}:{digest}"


def _delivery_mark_in_history(s, item: dict) -> bool:
    """history 尾部是否已有该队列项的投递标记（消费前对账，查条目元数据）。"""
    for h in s.history[-_DELIVERY_SCAN_DEPTH:]:
        if any(_delivery_key_matches(key, item)
               for key in (h.get("delivered_keys") or ())):
            return True
    return False


async def _save_receipt(s, *, outcome: _sess.DurableOutcome | None = None) -> None:
    """Persist a queue receipt even when restart cancels the consumer.

    ``restart_worker`` deliberately cancels the old consumer before killing the
    provider.  A cancellation arriving during ``save_async`` must not leave the
    in-memory queue item removed while the durable write is still undecided.
    Shield the save, wait for its thread to settle, then re-raise the original
    cancellation only after a successful commit.  Callers can safely rollback
    on ordinary save errors; a successful cancelled save remains consumed.
    """
    # Keep the receipt/index state bounded at the same persistence boundary as
    # queue hand-off.  This does not alter queue_pending or terminal event
    # ordering; it only removes/compresses old terminal metadata.
    _prepare_receipt_persistence(s)
    if outcome is not None:
        # The ordered outcome API already owns cancellation through writer
        # retirement and exposes both cancelled success and cancelled failure.
        await _sess.save_async(s, outcome=outcome)
        return
    save_task = asyncio.create_task(_sess.save_async(s))
    try:
        await asyncio.shield(save_task)
    except asyncio.CancelledError:
        # A second cancellation can arrive while waiting for the shielded
        # thread.  Keep waiting until the durable result is observable.
        while not save_task.done():
            try:
                await asyncio.shield(save_task)
            except asyncio.CancelledError:
                continue
            except Exception:
                # Inspect the retired writer below instead of replacing the
                # caller's cancellation with its concurrent disk exception.
                break
        if save_task.cancelled():
            # Shield normally prevents this, but an event-loop shutdown or a
            # caller that explicitly cancels the save task can still cancel it.
            # Convert that pre-commit condition into an ordinary error so the
            # caller restores its in-memory queue snapshot instead of silently
            # dropping the item.
            raise RuntimeError("receipt save cancelled before commit")
        save_error = save_task.exception()
        if save_error is not None:
            raise save_error
        # Receipt committed; propagate cancellation to the worker lifecycle.
        raise


async def _legacy_consume_pending_reports(w: Worker, s):
    """从落盘 queue_pending 取全部积压报告，拼接为一条消息交给模型处理。

    报告批次在 Worker 接管时立即从 queue_pending 出队。构造注入文本时先落盘
    history 投递标记与出队结果，再交给 provider；result/error/cancelled 只负责
    记录执行结果，不再决定是否消费。崩溃在接管后不会自动重投；save 失败则
    队列原样保留。

    消费前确认 worker 进程存活（CLI 子进程死 → 中止并保留 queued 项，由全局
    watchdog spawn 恢复；一旦本 Worker 完成接管并保存出队，就不再重放）。

    L4 落盘：任务消息（type=="task"）与报告共存于同一队列；此处**只消费报告**，
    task item 保留在队列中由 task_signal 按 id 消费，互不误删。

    恢复对账：save 顺序 jsonl-先写 → 崩溃窗口内 history 条目已带 delivered_keys
    元数据、旧队列项仍在。升级恢复时复用已有 history 用户项并直接消费旧 item，
    不会自动重试报告批次；新报告与旧报告混合时仍保持队列顺序。
    """
    # Upgrade any stale pre-receipt-ack rows before inspecting the report
    # batch.  The helper also handles legacy task rows sharing this inbox; take
    # a full snapshot so a failed migration save cannot lose either kind.
    old_queue_snapshot = list(s.queue_pending)
    old_history_len = len(s.history)
    legacy_changed = _consume_legacy_handoff_items(s)
    if legacy_changed:
        try:
            await _save_receipt(s)
        except Exception as exc:
            s.queue_pending = old_queue_snapshot
            del s.history[old_history_len:]
            _sess.replace_history(s, s.history)
            _log.warning(
                "[Worker %s] legacy queue cleanup save failed; leaving rows "
                "untouched: %s", w.worker_id, exc)
            return

    reports = [it for it in s.queue_pending if _is_report_item(it)]
    if not reports:
        # 队列里只剩 task item（报告已被其他信号消费）→ 不发空消息
        return
    # A report batch is one delivery unit.  Legacy in-flight members are removed
    # by _consume_legacy_handoff_items before this path; new batches are removed
    # atomically with their receipt save and are never replayed.
    if any(_delivery_state(it) == _DELIVERY_IN_FLIGHT for it in reports):
        _log.info(
            "[Worker %s] report batch has legacy in-flight item(s); recovery should "
            "have finalized them",
            w.worker_id,
        )
        return
    undelivered = [it for it in reports if not _delivery_mark_in_history(s, it)]
    text = _format_report_batch(reports)

    # 报告不是 assign 任务：无 seq 配对，清空当前配对上下文避免 last_result 错位
    w._current_seq = None
    w._current_task_id = None
    w._current_task_idempotent = False
    w._replaying = False

    injected_text = await _maybe_inject_memory(s, text)
    if not _process_alive(w):
        _log.warning(
            "[Worker %s] report consumption aborted: process dead, "
            "%d report(s) kept in queue_pending", w.worker_id, len(undelivered))
        return

    # Mark every report in the batch before the adapter sees the prompt.  This
    # is the durable at-most-once boundary for report/QQ messages as well.
    old_states = {id(it): it.get("deliveryState") for it in reports}
    for it in reports:
        it["deliveryState"] = _DELIVERY_IN_FLIGHT

    # Reserve the whole report batch by identity before the save await.  Reports
    # and tasks share queue_pending, so only these report objects are removed;
    # any queued task remains in its original relative position.
    report_positions = [
        (index, it) for index, it in enumerate(s.queue_pending)
        if _is_report_item(it)
    ]
    s.queue_pending = [it for it in s.queue_pending if not _is_report_item(it)]

    # 只有新报告需要追加 history；已经有 delivered_keys 的报告属于旧数据的
    # 已写入执行上下文，复用原用户条目，避免恢复时污染上下文。
    if undelivered:
        old_history_len = len(s.history)
        report_entry = {"role": "user", "content": injected_text,
                        "delivered_keys": [_delivery_key(it) for it in undelivered],
                        "source": "report"}
        source_ids = sorted({it.get("sourceSessionId") for it in undelivered
                             if isinstance(it.get("sourceSessionId"), str)
                             and it.get("sourceSessionId")})
        if source_ids:
            report_entry["sourceSessionIds"] = source_ids
        _sess.append_history(s, report_entry)
        try:
            await _save_receipt(s)
        except Exception as e:
            del s.history[old_history_len:]
            _sess.replace_history(s, s.history)
            for index, it in reversed(report_positions):
                s.queue_pending.insert(min(index, len(s.queue_pending)), it)
            for it in reports:
                previous = old_states[id(it)]
                if previous is None:
                    it.pop("deliveryState", None)
                else:
                    it["deliveryState"] = previous
            _log.warning(
                "[Worker %s] report handoff save failed, %d item(s) kept in "
                "queue_pending: %s", w.worker_id, len(undelivered), e)
            return
    else:
        try:
            await _save_receipt(s)
        except Exception as e:
            for index, it in reversed(report_positions):
                s.queue_pending.insert(min(index, len(s.queue_pending)), it)
            for it in reports:
                previous = old_states[id(it)]
                if previous is None:
                    it.pop("deliveryState", None)
                else:
                    it["deliveryState"] = previous
            _log.warning(
                "[Worker %s] report handoff state save failed, %d item(s) kept "
                "queued: %s", w.worker_id, len(reports), e)
            return

    w._current_report_items = reports

    mode = resolve_execution_mode(w.adapter, s)
    if mode == "oneshot":
        await _consumer_oneshot(w, injected_text, "report", s)
    else:
        await _consumer_stream(w, injected_text, "report", s)


# 任务投递的 in-flight 标记（内存）：已认领、尚未确认出队的 task item id。
# 防同一 id 被重复认领（重复信号 / 新旧 worker 消费者交叠窗口）。进程重启即清空，
# 真源仍是落盘 queue_pending——标记只做运行期去重，不承载持久语义。
_inflight_task_ids: set[str] = set()


async def _legacy_claim_pending_task(w: Worker, task_id: str | None) -> dict | None:
    """在落盘 queue_pending 中按 id 认领一个任务 item。

    认领会同步从 ``queue_pending`` 删除；调用方随后把这次接管与 history
    receipt 一起持久化。队列消费边界在 Worker 接管，而不是 terminal result。
    只允许队列中更早的 task 都处于 queued（或已经出队）；如果更早的 task
    仍是旧版本遗留的 in-flight，当前 item 不得越过它。

    返回 item；信号重复 / item 不存在 / 已被消费 / 会话消失 → None。

    与 _consume_pending_reports 的互斥：task（type=="task"）与 report item
    共存于同一队列；_consume_pending_reports 只消费 report（跳过 task），
    本函数只按 id 认领单个 task，互不误删。
    """
    s = _session(w)
    if not s:
        _log.warning("[Worker %s] task_signal: session %s not found",
                     w.worker_id, w.session_id)
        return None
    # Stream workers must have a live provider before accepting the durable
    # item.  One-shot workers have process=None and are considered live here.
    if not _process_alive(w):
        _log.warning(
            "[Worker %s] task_signal: provider process is dead; leaving task id=%s queued",
            w.worker_id, task_id,
        )
        return None
    for index, it in enumerate(s.queue_pending):
        if _is_task_item(it) and it.get("id") == task_id:
            if not isinstance(it.get("text"), str):
                _log.warning(
                    "[Worker %s] task id=%s has malformed text; leaving it queued",
                    w.worker_id, task_id)
                return None
            _, _, source_error = _validate_source_metadata(
                s, it.get("source"), it.get("sourceSessionId"))
            if source_error:
                _log.warning(
                    "[Worker %s] task id=%s has invalid source metadata; "
                    "leaving it queued: %s", w.worker_id, task_id, source_error)
                return None
            if task_id in _inflight_task_ids:
                _log.warning(
                    "[Worker %s] task_signal: task id=%s already in-flight, "
                    "skip duplicate claim", w.worker_id, task_id)
                return None
            if not _is_dispatchable(it):
                _log.info(
                    "[Worker %s] task id=%s is already in-flight; "
                    "automatic replay suppressed", w.worker_id, task_id)
                return None
            # Legacy versions wrote the history delivery mark before this
            # durable state existed.  Treat that evidence as an uncertain
            # hand-off instead of replaying it automatically.
            if _delivery_mark_in_history(s, it):
                # Legacy history already proves this item crossed the receipt
                # boundary.  Consume the stale queue row as a migration; never
                # turn it into a second provider execution.
                s.queue_pending.pop(index)
                try:
                    await _save_receipt(s)
                except Exception as exc:
                    s.queue_pending.insert(index, it)
                    _log.warning(
                        "[Worker %s] legacy task id=%s cleanup save failed; "
                        "left queued for migration: %s", w.worker_id, task_id, exc)
                    return None
                _log.info(
                    "[Worker %s] task id=%s has legacy history handoff mark; "
                    "consumed stale queue row without replay", w.worker_id, task_id)
                return None
            source = _task_source(it)
            _, source_sid, source_error = _validate_source_metadata(
                s, source, it.get("sourceSessionId"))
            if source is None or source_error:
                _log.warning(
                    "[Worker %s] task id=%s has invalid source metadata; leaving it queued: %s",
                    w.worker_id, task_id, source_error or source)
                return None
            for prior in s.queue_pending[:index]:
                if (_is_task_item(prior) and _is_dispatchable(prior)
                        and _task_source(prior) is not None):
                    # Signals are only hints and can arrive out of order after
                    # concurrent durable saves.  Re-arm the earliest task and
                    # the current signal so FIFO order is restored without
                    # executing the later task ahead of it.  Duplicate hints
                    # are harmless because claim is by durable item id.
                    _log.info(
                        "[Worker %s] task id=%s blocked by earlier queued task "
                        "id=%s; rearming FIFO signals", w.worker_id, task_id,
                        prior.get("id"))
                    if w.pending_signal is not None:
                        w.pending_signal.put_nowait({
                            "type": "task_signal", "id": prior.get("id")})
                        w.pending_signal.put_nowait({
                            "type": "task_signal", "id": task_id})
                    return None
            _inflight_task_ids.add(task_id)
            it["deliveryState"] = _DELIVERY_IN_FLIGHT
            # Reserve and consume by object identity before any later await.
            # This prevents queue delete/reorder or a duplicate wake-up from
            # exposing the same item to another consumer while receipt is saved.
            w._claimed_queue_index = index
            s.queue_pending.pop(index)
            return it
    _log.warning("[Worker %s] task_signal: task id=%s not found in queue_pending (len=%d)",
                 w.worker_id, task_id, len(s.queue_pending))
    return None


async def _consume_pending_reports(w: Worker, s):
    """Compatibility entry point that consumes the durable FIFO report unit."""
    unit = _select_queue_unit(s)
    if unit and _queue_item_kind(unit[0]) in {"report", "qq", "wechat"}:
        await _deliver_queue_unit(w, s, unit)


async def _claim_pending_task(w: Worker, task_id: str | None) -> dict | None:
    async with queue_lock(w.session_id):
        return await _claim_pending_task_locked(w, task_id)


async def _claim_pending_task_locked(w: Worker, task_id: str | None) -> dict | None:
    """Compatibility claim that reserves, but does not remove, a task row."""
    s = _session(w)
    if s is None or not _process_alive(w):
        return None
    unit = _select_queue_unit(s)
    if not unit or _queue_item_kind(unit[0]) != "task":
        return None
    item = unit[0]
    if item.get("id") != task_id:
        return None
    item["deliveryState"] = _DELIVERY_RESERVED
    item["dispatchState"] = _DELIVERY_RESERVED
    item["reservedBy"] = w.worker_id
    item["reservedGeneration"] = w.generation
    item["reservedAt"] = time.time()
    _remember_queue_item(s, item, _DELIVERY_RESERVED)
    # 保留（reserved）会把行移出待处理计数集：版本与状态在同一次落盘提交，
    # 防止 HTTP 快照与广播补丁之间出现“同版本不同计数”的窗口。
    old_queue_revision = getattr(s, "queue_revision", 0)
    s.queue_revision = old_queue_revision + 1
    try:
        await _save_receipt(s)
    except Exception:
        s.queue_revision = old_queue_revision
        _queue_item_backoff(item, "task reservation save failed")
        _remember_queue_item(s, item, _DELIVERY_QUEUED)
        return None
    return item


_REPORT_LAST_MESSAGE_MAX_CHARS = 1200


def _error_report_diagnostic(
    w: Worker, status: str, run_seq: int | None = None,
) -> dict | None:
    """Diagnostic fields for an error completion report, or None when N/A.

    An error terminal can carry an empty (or only generic) ``result`` while the
    run's final assistant message held the real diagnosis — e.g. an explicit 429
    with the reset time.  Capture that text for the report so the manager can
    see why the turn failed.

    Provenance is the run, never a guess: the text is only read from the
    worker's in-memory per-task capture (set from this run's assistant blocks
    and cleared when a task hand-off starts), plus the recorded task seq, so a
    stale message from a previous turn can never attach to this report.

    ``run_seq`` identifies the run whose terminal is being reported.  Callers
    without a run identity (zombie paths) pass None: the capture is then only
    accepted while the worker is actually running the turn that produced it; a
    queued item or an idle worker has no message of its own.

    Returns None for non-error statuses (done/cancelled stay untouched) and a
    field dict otherwise — with explicit ``None`` text when no message was
    captured, so the rendered report can state that no diagnostics are
    available instead of silently looking like a lost one.
    """
    if status != "error":
        return None
    text = w._current_task_last_assistant_text
    seq = w._current_task_last_assistant_seq
    if run_seq is None:
        run_seq = w._current_seq if w.status == "running" else None
    if not (isinstance(text, str) and text.strip()
            and seq is not None and run_seq is not None and seq == run_seq):
        return {
            "lastMessageRole": None,
            "lastMessage": None,
            "lastMessageTruncated": False,
        }
    text = text.strip()
    truncated = len(text) > _REPORT_LAST_MESSAGE_MAX_CHARS
    if truncated:
        # Keep both ends: the reason usually leads and the reset time trails.
        half = _REPORT_LAST_MESSAGE_MAX_CHARS // 2
        text = (f"{text[:half].rstrip()}\n"
                f"...[truncated {len(text)} chars]...\n"
                f"{text[-half:].lstrip()}")
    return {
        "lastMessageRole": "assistant",
        "lastMessage": text,
        "lastMessageTruncated": truncated,
    }


def _stage_new_queue_item(s, item: dict, *, wake: bool = False) -> None:
    """Inside an ordered writer candidate; never use for retry/requeue/edit."""
    _offline.lock_new_message(s, item)
    if wake:
        # This newly created message has no user Manual lock. Release only
        # the Illegal lock, leaving Pause/leases/owner gates untouched.
        if _message_locks.source(item) == "illegal":
            _message_locks.set_lock(item, None)
        item["wakeMessage"] = True
        s.queue_pending.insert(0, item)
    else:
        s.queue_pending.append(item)
    for position, row in enumerate(s.queue_pending):
        row["position"] = position
    _remember_queue_item(s, item, _DELIVERY_QUEUED)
    _prepare_receipt_persistence(s)
    s.queue_revision = getattr(s, "queue_revision", 0) + 1


async def _append_new_queue_item(s, item: dict, *, wake: bool = False,
                                 metadata: dict | None = None) -> dict:
    # queue_lock is held by caller. Stage before atomic replacement so an
    # unrelated metadata writer cannot persist an unlocked new Illegal row.
    def stage(candidate):
        for key, value in (metadata or {}).items():
            setattr(candidate, key, copy.deepcopy(value))
        _stage_new_queue_item(candidate, item.copy(), wake=wake)
    await _commit_offline(s, stage)
    return next(row for row in s.queue_pending if row.get("id") == item["id"])


async def _enqueue_report(session_id: str, status: str, result: str,
                          task_id: str | None, worker_id: str,
                          report_type: str | None = None,
                          diagnostic: dict | None = None,
                          admission: dict | None = None):
    """订阅制报告入队：session 完成 → 若被其 managed_by 订阅，报告 append 到
    manager 的落盘队列 queue_pending，并唤醒 manager 的 consumer。

    **done / error 都入队（决策保留，遗留待办 L6）**：协调者（manager）需要
    知道失败——失败是编排的必要信息（重试/排查），不能只报成功。若后续只想报
    done，在此按 status 过滤即可。

    report_type：附加的语义标记（如 "zombie"）。非 None 时在报告 dict 里写入
    "type" 字段，供 manager 区分异常死亡与正常完成。

    diagnostic：``_error_report_diagnostic`` 的返回值。非 None 时把最后消息
    上下文写进报告行（仅 error 出口传入；成功/取消路径不改变正文）。

    未订阅 / 无 managed_by → 不 append（保留现有 worker.result 广播不变）。
    """
    if _shutdown_started:
        return
    if admission is not None and admission.get("enabled") is not True:
        return
    s = _get_session_shallow(session_id)
    if not s or not s.managed_by:
        return
    manager = _get_session_shallow(s.managed_by)
    if not manager:
        return
    if session_id not in (manager.report_subscriptions or set()):
        return

    item = {
        "type": "report",
        "kind": "report",
        "id": "q_" + uuid.uuid4().hex,
        "queueItemId": None,
        "source": "report",
        "sourceSessionId": session_id,
        "status": status,
        "result": result,
        "sessionId": session_id,
        "taskId": task_id,
        "workerId": worker_id,
        "deliveryState": _DELIVERY_QUEUED,
        "dispatchState": _DELIVERY_QUEUED,
        "revision": 1,
        "createdAt": time.time(),
    }
    item["queueItemId"] = item["id"]
    if report_type is not None:
        item["type"] = report_type
    if diagnostic is not None:
        item.update(diagnostic)
    async with queue_lock(manager.id):
        if (getattr(manager, "agent_reports_paused", False)
                and _is_pauseable_agent_report(item)):
            _message_locks.manual_change(item, None, True)
        item = await _append_new_queue_item(manager, item)
        queue_revision = manager.queue_revision
    await _bcast({"type": "queue.item_added", "sessionId": manager.id,
                  "queueItemId": item["id"],
                  "queueRevision": queue_revision,
                  "item": dict(item)})

    # 唤醒 manager 的 consumer（若 worker 存活）——报告正文在落盘队列，
    # 信号只负责唤醒，不承载正文
    await _wake_worker(s.managed_by)
    _schedule_auto_force(manager.id, item)


_TERMINAL_RESULT_STATUSES = frozenset({"done", "error", "cancelled"})


def manual_report_key(session_id: str, task_id: str | None,
                      task_seq: int | None,
                      result_cursor: int | None = None) -> str:
    """Deterministic idempotency key for one explicit run report.

    Keyed on the provable **run** identity only (session + taskId + taskSeq,
    falling back to the durable result cursor for a run without a taskSeq),
    never on a client-supplied message id: an arbitrary or repeated message id
    must not be able to turn one run into several deliveries, while distinct
    runs of one task id still stay distinct.  Callers derive it from the
    resolved terminal row, so one run reached through either identity path
    (taskSeq or resultCursor) keeps exactly one key.
    """
    task_part = task_id if task_id else "-"
    if task_seq is not None:
        seq_part = str(task_seq)
    elif result_cursor is not None:
        seq_part = f"rc:{result_cursor}"
    else:
        seq_part = "-"
    return f"manual:{session_id}:{task_part}:{seq_part}"


def task_tail_block_id(s, terminal: dict) -> str | None:
    """messageId of that task run's final text block, or None when unprovable.

    A durable terminal row records the history epoch/revision it committed at
    **after** the run's final text row was appended (``_persist_terminal_state``
    sets it from the post-append revision on purpose), so within an unchanged
    epoch the recorded revision counts the rows this run covered.  The run's
    final row therefore sits one before the exclusive end offset:

        covered_end = len(history) - (history_revision_now - terminal_revision)
        tail_index  = covered_end - 1

    Returns None whenever the relation cannot be proven for this Session
    (legacy rows without epoch/revision, a rewritten history epoch, an unloaded
    or empty history, or no assistant row inside the covered region).  Callers
    must then refuse instead of guessing which block belongs to the task.
    """
    epoch = terminal.get("historyEpoch")
    revision = terminal.get("historyRevision")
    if not isinstance(epoch, str) or not epoch:
        return None
    if getattr(s, "history_epoch", None) != epoch:
        return None
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        return None
    rows = getattr(s, "history", None)
    if not isinstance(rows, list) or not rows:
        return None
    current = max(0, int(getattr(s, "history_revision", 0) or 0))
    if revision > current:
        return None
    covered_end = len(rows) - (current - revision)
    if covered_end <= 0 or covered_end > len(rows):
        return None
    for index in range(covered_end - 1, -1, -1):
        row = rows[index]
        if not isinstance(row, dict) or row.get("role") != "assistant":
            continue
        message_id = row.get("messageId")
        return message_id if isinstance(message_id, str) and message_id else None
    return None


def _terminal_row_cursor(row: dict) -> int:
    """Ordered cursor for one durable terminal row (mirrors the web layer)."""
    value = row.get("resultCursor")
    if isinstance(value, bool):
        value = None
    try:
        if value is not None:
            return max(0, int(value))
    except (TypeError, ValueError):
        pass
    try:
        return max(0, int(row.get("taskSeq", 0) or 0))
    except (TypeError, ValueError):
        return 0


def durable_terminal_for_task(s, task_id: str | None,
                              task_seq: int | None = None) -> dict | None:
    """Latest durable terminal record for one task, or None.

    Reads the same bounded per-task index the result-replay contract uses
    (``Session.terminal_results``, newest last; legacy Sessions keep only
    ``last_result``).  Any completed task in that window stays resolvable, so an
    older task's report never depends on it still being the newest terminal.
    ``task_seq`` is an optional stricter match: when provided, the terminal must
    belong to that exact run.
    """
    rows = [
        dict(item) for item in (getattr(s, "terminal_results", None) or [])
        if isinstance(item, dict)
        and item.get("status") in _TERMINAL_RESULT_STATUSES
        and item.get("taskId") == task_id
        and (task_seq is None or item.get("taskSeq") == task_seq)
    ]
    if not rows:
        last = getattr(s, "last_result", None)
        if (isinstance(last, dict)
                and last.get("status") in _TERMINAL_RESULT_STATUSES
                and last.get("taskId") == task_id
                and (task_seq is None or last.get("taskSeq") == task_seq)):
            return dict(last)
        return None
    rows.sort(key=lambda item: (_terminal_row_cursor(item),
                                str(item.get("terminalKey") or "")))
    return rows[-1]


def _cursor_value(row: dict) -> int | None:
    """Raw durable result cursor of a terminal row, or None when absent."""
    raw = row.get("resultCursor")
    if isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def durable_terminal_by_cursor(s, result_cursor) -> dict | None:
    """Durable terminal record identified by its result cursor, or None.

    The result cursor is the durable, monotonic position of one terminal row
    in the bounded replay window — the run identity the session identity view
    exposes for runs without a formal taskId (plain conversations, native
    turns).  Only **positive** cursors identify a run: production allocates
    them as max(existing)+1, so the identity view's 0 for an identity-less row
    is a derived compat value and never a binding.  An exact raw
    ``resultCursor`` match wins; legacy rows without the field fall back to a
    compatibility cursor derived ONLY from a stored ``taskSeq`` — a row with no
    stored taskSeq is never matched, so no cursor can fabricate an identity for
    it.  When several rows share a compatibility cursor the match is ambiguous
    and this returns None: the caller must refuse instead of guessing.  The
    legacy ``last_result`` view is the final fallback, under the same rules.
    """
    if isinstance(result_cursor, bool) or not isinstance(result_cursor, int):
        return None
    if result_cursor <= 0:
        return None
    cursor = result_cursor
    rows = [
        dict(item) for item in (getattr(s, "terminal_results", None) or [])
        if isinstance(item, dict)
        and item.get("status") in _TERMINAL_RESULT_STATUSES
        and _cursor_value(item) == cursor
    ]
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        return None  # duplicate raw cursors: never guess
    compat = [
        dict(item) for item in (getattr(s, "terminal_results", None) or [])
        if isinstance(item, dict)
        and item.get("status") in _TERMINAL_RESULT_STATUSES
        and _cursor_value(item) is None
        and item.get("taskSeq") is not None
        and _terminal_row_cursor(item) == cursor
    ]
    if len(compat) == 1:
        return compat[0]
    if len(compat) > 1:
        return None  # ambiguous compatibility cursor: never guess
    last = getattr(s, "last_result", None)
    if (isinstance(last, dict)
            and last.get("status") in _TERMINAL_RESULT_STATUSES
            and (_cursor_value(last) == cursor
                 or (_cursor_value(last) is None
                     and last.get("taskSeq") is not None
                     and _terminal_row_cursor(last) == cursor))):
        return dict(last)
    return None


def _find_manual_report_receipt(manager, key: str) -> dict | None:
    """Locate a delivered manual report by key: durable receipt first, then a
    still-pending queue row (the receipt is written before the broadcast)."""
    receipts = getattr(manager, "manual_report_receipts", None) or {}
    record = receipts.get(key)
    if isinstance(record, dict):
        return record
    for item in getattr(manager, "queue_pending", None) or []:
        if (isinstance(item, dict) and item.get("manualReportKey") == key):
            return item
    return None


def _remember_manual_report_receipt(manager, key: str, record: dict) -> None:
    """Record one delivered manual report, bounded FIFO on the manager."""
    receipts = getattr(manager, "manual_report_receipts", None)
    if not isinstance(receipts, dict):
        receipts = {}
        manager.manual_report_receipts = receipts
    receipts.pop(key, None)
    receipts[key] = record
    while len(receipts) > _sess.MANUAL_REPORT_RECEIPT_MAX:
        receipts.pop(next(iter(receipts)))


async def enqueue_manual_report(session_id: str, task_id: str | None,
                                task_seq: int | None,
                                message_id: str,
                                result_cursor: int | None = None,
                                ) -> tuple[dict | None, dict | None]:
    """Deliver one explicit browser-requested completion report to the manager.

    与 ``_enqueue_report`` 的关系：**路由目标与内容语义完全相同**（manager 的
    落盘队列 queue_pending + 同一报告 item 形状 + 同一唤醒/广播路径），但：

    - **按运行解析终态**：内容取自该运行在**持久终态索引**
      （``terminal_results``，旧会话回退 ``last_result``）中的记录，而不是
      “当前最新终态”。因此更早完成的运行同样可汇报，最新运行在跑也不会
      使旧运行的汇报失效；索引里找不到该运行的终态 → 明确失败
      （``no_task_result``），绝不拿别的运行的结果顶替。
    - **任何已完成运行均可汇报**：正式 assign 任务与无 taskId 的普通对话/
      原生轮次走同一路径。无 taskId 时调用方必须以该运行的 ``taskSeq`` 或持久
      ``resultCursor`` 绑定具体运行（两者都经会话身份视图 ``terminalResults``
      暴露，服务端正文权威）。只有**可证明的持久运行身份**才构成绑定：非空
      taskId、已存储的 taskSeq、或**正** resultCursor（生产只分配正游标；身份
      视图给无身份旧行显示的 0 是派生兼容值）。三者都不提供 → 明确失败
      （``no_run_identity``），绝不退化为“汇报最新终态”；≤0 的 resultCursor
      按未提供处理（不能引用任何可证明运行，丢弃不会误绑）；解析出的终态若
      无任何可证明身份 → 同样 ``no_run_identity`` 明确拒绝，不允许派生 0 把
      无身份旧行解析成可投递运行（跨时间回执会因此串运行）。``resultCursor``
      与 ``taskId``/``taskSeq`` 同时提供却描述不同运行 → 明确失败
      （``run_mismatch``），不猜向任何一边。
    - **校验消息与运行的持久关联**：``messageId`` 必须正是该运行终态的
      **末块**（由终态行记录的 history epoch/revision 推出边界后取边界内最后
      一条 assistant 行）。无法证明该关联时明确失败（``block_unproven`` /
      ``block_mismatch``），不用“最新”近似，也不接受任意消息 id。
    - **不读取也不修改 report_subscriptions**：manager 退订
      （``report_unsubscribe`` / report-to-manager 关闭）后，显式手动汇报依然
      送达；本函数也绝不临时开订阅。
    - **幂等以运行身份为准**：key 由解析后的终态行推导（sessionId, taskId,
      taskSeq；该行无 taskSeq 时用其持久 resultCursor），客户端 messageId 只
      作为审计字段，不能改变去重结果；同一运行反复点击/换 messageId/换身份
      路径/未确认重试都只送达一次，不同运行各自独立。
    - 落盘失败整体回滚 → 明确失败，可安全重试；候选的 apply、写入与失败回滚
      全部在**同一个持久化 ticket** 内执行（旧状态在 ticket 执行时捕获，不覆
      盖期间其他 writer 的提交），因此已在执行/已排队的 manager writer 都不
      可能把失败候选写上盘；请求断开（取消）复用 DurableOutcome ticket 规则：
      已落盘则照常送达并唤醒 manager，未落盘则整体回滚，绝不留下失败候选。

    返回 ``(result, error)``：成功时 result=``{"queueItemId", "managerId",
    "alreadyReported"}``；失败时 error=``{"code", "message", "retryable"}``。
    """
    if _shutdown_started:
        return None, {"code": "shutting_down",
                      "message": "Pan main service is shutting down",
                      "retryable": True}
    s = _get_session_shallow(session_id)
    if not s:
        return None, {"code": "session_not_found",
                      "message": f"Session {session_id} not found",
                      "retryable": False}
    if not s.managed_by:
        return None, {"code": "no_report_target",
                      "message": "该 Session 没有 manager，无法汇报",
                      "retryable": False}
    # The block proof reads canonical history rows; a cold cache returns the
    # Session without its history JSONL, so hydrate through the existing read
    # instead of failing every cold report as unprovable.
    if not getattr(s, "_history_loaded", True):
        s = _sess.get(session_id)
        if not s:
            return None, {"code": "session_not_found",
                          "message": f"Session {session_id} not found",
                          "retryable": False}
    if not s.managed_by:
        return None, {"code": "no_report_target",
                      "message": "该 Session 没有 manager，无法汇报",
                      "retryable": False}
    manager = _get_session_shallow(s.managed_by)
    if not manager:
        return None, {"code": "no_report_target",
                      "message": "manager 会话不存在，无法汇报",
                      "retryable": False}
    # A cursor at or below zero is not a durable run identity: production only
    # allocates positive cursors, and the identity view's derived 0 for an
    # identity-less row must never be submitted as a binding.  Dropping it
    # cannot mis-bind (it references no provable run), so a request that also
    # carries a legal identity still resolves through that identity.
    if (isinstance(result_cursor, bool) or not isinstance(result_cursor, int)
            or result_cursor <= 0):
        result_cursor = None
    if task_id is None and task_seq is None and result_cursor is None:
        return None, {"code": "no_run_identity",
                      "message": "汇报必须绑定具体完成运行（taskId/taskSeq/resultCursor 之一），无法推断",
                      "retryable": False}
    if result_cursor is not None:
        # The durable cursor is the strongest run identity: it pins one exact
        # terminal row.  Any additionally submitted identity must describe the
        # same run, otherwise the request is refused — never resolved to
        # whichever side happens to match.
        terminal = durable_terminal_by_cursor(s, result_cursor)
        if terminal is None:
            return None, {"code": "no_task_result",
                          "message": "该 resultCursor 对应的终态结果不可用，无法汇报",
                          "retryable": False}
        if task_seq is not None and terminal.get("taskSeq") != task_seq:
            return None, {"code": "run_mismatch",
                          "message": "taskSeq 与 resultCursor 描述的不是同一次运行，无法汇报",
                          "retryable": False}
        if task_id is not None and terminal.get("taskId") != task_id:
            return None, {"code": "run_mismatch",
                          "message": "taskId 与 resultCursor 描述的不是同一次运行，无法汇报",
                          "retryable": False}
    else:
        terminal = durable_terminal_for_task(s, task_id, task_seq)
        if terminal is None:
            return None, {"code": "no_task_result",
                          "message": "该任务的终态结果不可用，无法汇报",
                          "retryable": False}
    run_task_id = terminal.get("taskId")
    run_task_seq = terminal.get("taskSeq")
    run_cursor = _cursor_value(terminal)
    # Provable durable run identity is the delivery precondition: a terminal
    # row with no taskId, no taskSeq and no positive raw cursor (legacy rows
    # the identity view can only show as derived 0) must never become a
    # deliverable run — its degenerate key would collide across runs.
    if (not run_task_id and run_task_seq is None
            and not (isinstance(run_cursor, int) and run_cursor > 0)):
        return None, {"code": "no_run_identity",
                      "message": "该终态没有可证明的持久运行身份，无法汇报",
                      "retryable": False}
    tail_block = task_tail_block_id(s, terminal)
    if tail_block is None:
        return None, {"code": "block_unproven",
                      "message": "无法证明该消息属于该任务的末块，暂不能汇报",
                      "retryable": False}
    if tail_block != message_id:
        return None, {"code": "block_mismatch",
                      "message": "提交的消息不是该任务的末块，无法汇报",
                      "retryable": False}
    key = manual_report_key(session_id, run_task_id, run_task_seq, run_cursor)
    async with queue_lock(manager.id):
        existing = _find_manual_report_receipt(manager, key)
        if existing is not None:
            return ({
                "queueItemId": existing.get("queueItemId") or existing.get("id"),
                "managerId": manager.id,
                "alreadyReported": True,
            }, None)
        item = {
            "type": "report",
            "kind": "report",
            "id": "q_" + uuid.uuid4().hex,
            "queueItemId": None,
            "source": "report",
            "sourceSessionId": session_id,
            "status": terminal.get("status"),
            "result": terminal.get("result"),
            "sessionId": session_id,
            "taskId": terminal.get("taskId"),
            "taskSeq": terminal.get("taskSeq"),
            "workerId": terminal.get("workerId"),
            "manual": True,
            "messageId": message_id,
            "manualReportKey": key,
            "deliveryState": _DELIVERY_QUEUED,
            "dispatchState": _DELIVERY_QUEUED,
            "revision": 1,
            "createdAt": time.time(),
        }
        item["queueItemId"] = item["id"]

        # Reuse the durable ticket pattern: the candidate APPLY, the write and
        # its failure rollback all run inside ONE persistence ticket, so no
        # writer that is already executing or queued for this session can
        # persist the candidate before a failed rollback.  The queue lock only
        # orders report writers; it does not serialize with the writer
        # executor.
        _prepare_receipt_persistence(manager)
        outcome = _sess.DurableOutcome()
        committed = [False]
        cancelled = [False]

        def _apply_write_or_rollback() -> None:
            # Snapshot the old state at ticket EXECUTION time: commits another
            # writer made between submission and execution are part of the
            # snapshot and are never clobbered by the rollback.
            old_receipts = dict(getattr(manager, "manual_report_receipts", None) or {})
            old_queue_revision = getattr(manager, "queue_revision", 0)
            old_offline = _offline.snapshot(manager)

            def _rollback() -> None:
                """Undo the whole attempt in memory."""
                _offline.restore(manager, old_offline)
                manager.manual_report_receipts = old_receipts

            try:
                # Apply the candidate in memory inside the ticket.
                item["position"] = len(manager.queue_pending)
                _stage_new_queue_item(manager, item)
                _remember_manual_report_receipt(manager, key, {
                    "sessionId": session_id,
                    "taskId": run_task_id,
                    "taskSeq": run_task_seq,
                    "messageId": message_id,
                    "queueItemId": item["id"],
                    "at": time.time(),
                })
                manager.queue_revision = old_queue_revision + 1
                _sess._save_body(manager)
                committed[0] = True
            except BaseException:
                # Roll the candidate back BEFORE this ticket retires: a later
                # queued writer for this session serializes after the clean
                # state instead of persisting the failed candidate.
                _rollback()
                raise

        try:
            await _sess._persist_async_outcome(manager.id, _apply_write_or_rollback, outcome)
        except asyncio.CancelledError:
            cancelled[0] = True
        except Exception as exc:
            if not committed[0]:
                # The ticket already rolled the candidate back; nothing durable
                # was written.
                return None, {"code": "persist_failed",
                              "message": f"汇报落盘失败: {exc}",
                              "retryable": True}
            # The durable write landed despite the raised error: committed.
        if cancelled[0] and not committed[0]:
            # The ticket retired without a committed writer: the candidate was
            # either never applied or already rolled back inside the ticket, so
            # no failed candidate is left behind.
            return None, {"code": "persist_failed",
                          "message": "汇报未落盘（请求已断开），可重试",
                          "retryable": True}
    await _bcast({"type": "queue.item_added", "sessionId": manager.id,
                  "queueItemId": item["id"],
                  "queueRevision": manager.queue_revision,
                  "item": dict(item)})
    # 与自动汇报同一唤醒路径：正文在落盘队列，信号只负责唤醒。
    await _wake_worker(s.managed_by)
    _schedule_auto_force(manager.id, item)
    if cancelled[0]:
        # The caller disconnected, but the report is durably delivered: end the
        # cancelled request without inventing a failure.
        raise asyncio.CancelledError()
    return ({"queueItemId": item["id"], "managerId": manager.id,
             "alreadyReported": False}, None)


async def _wake_worker(session_id: str, auto_spawn: bool = False) -> None:
    """唤醒某 session 的 worker consumer（若存活）。

    信号只负责唤醒、不承载正文（立项 4.3/4.7：正文在落盘 queue_pending）。
    worker 死亡时立即请求 session recovery；全局 watchdog 只作为 spawn 失败
    或调度失败时的兜底（spawn 后若 queue_pending 非空自动补发信号）。保留
    ``auto_spawn`` 参数以兼容旧调用方；现在所有无活 worker 的唤醒都合并为
    一个 session 级恢复任务，避免并发报告/QQ 提醒重复 spawn。
    """
    if _shutdown_started:
        return
    retiring = find_worker_by_session(session_id)
    if retiring is not None and retiring.pending_restart:
        _maybe_restart_pending(retiring)
        if retiring.status == "restarting":
            return
    if getattr(_get_session_shallow(session_id), "queue_paused", False):
        return
    mw = find_worker_by_session(session_id)
    if (mw and mw.pending_signal is not None
            and not (mw.process is not None and mw.process.returncode is not None)):
        if _has_sendable_queued_items(_get_session_shallow(session_id)):
            mw.last_activity = time.monotonic()
            await mw.pending_signal.put({"type": "queue_signal"})
        else:
            _schedule_queue_retry(session_id)
    elif not mw or mw.status not in {"held", "restarting"}:
        session = _get_session_shallow(session_id)
        if _queue_recovery_blocked(session):
            return
        if auto_spawn and (session is None or not session.queue_pending):
            # Legacy callers use auto_spawn to materialize an idle worker even
            # before the first item exists; retain that contract synchronously.
            created = await create_worker(session_id)
            if isinstance(created, str):
                _log.warning("[Pan] auto-spawn worker failed for session=%s: %s",
                             session_id, created)
        elif auto_spawn:
            task = _schedule_session_recovery(session_id)
            if task is not None:
                await task
        else:
            _schedule_session_recovery(session_id)


async def enqueue_channel_reminder(
    channel: str, target_type: str, target_id: str, *,
    nickname: str = "", text: str = "", time_str: str = "",
    bot_uin: str = "", can_reply: bool = True,
) -> int:
    """通道无关 inbox 提醒入队：订阅了该会话的 session 各收到一条提醒。

    ``channel`` 决定订阅集合与提醒抬头（"qq" / "wechat" / 将来的 "feishu"）。
    订阅字段保持平行（``qq_subscriptions`` / ``wechat_subscriptions``），但**读
    取逻辑**在此泛化——``getattr(s, f"{channel}_subscriptions")`` 按通道名取集
    合，将来加第三个通道只需传新 channel 值，无需在此处再加分支。未知 channel
    取不到属性 → 空集合 → 返回 0（不抛，插件误配不应打挂 Pan Core）。

    多账号（bot_uin 非空）：命中两类订阅——不区分 bot 的旧键 ``<type>:<id>``
    与精确键 ``<type>:<id>@<bot_uin>``；bot_uin 为空（旧来源）仅命中旧键。
    （目前仅 QQ 有多账号语义，微信走 bot_uin 空。）

    镜像 report 汇报链路：提醒项 append 到订阅者 session 的落盘 queue_pending，
    再唤醒其 worker consumer（无正文 queue_signal）。无活 worker 时立即 auto_spawn
    恢复（事件驱动，消除消息等待 watchdog tick 的最长 30s 延迟），spawn
    失败打日志、由全局 watchdog 兜底。返回投递的订阅者数量。

    提醒项双写 ``qqTarget``（老代码/老前端与 pan-qq MCP 已在读的键，勿删）与
    ``channelTarget``（新统一字段），并带 ``channel`` / ``canReply``。
    ``_format_report_batch`` 按 ``type`` 渲染为 `@@@@by <channel>` 抬头。
    """
    if _shutdown_started:
        return 0
    target_key = f"{target_type}:{target_id}"
    bot_key = f"{target_key}@{bot_uin}" if bot_uin else None
    item = {
        "source": channel,
        "sourcePluginId": channel,
        "type": channel,
        "kind": channel,
        "id": "q_" + uuid.uuid4().hex,
        "queueItemId": None,
        # 双写兼容键：qqTarget 是老代码/老前端/pan-qq MCP 一直在读的字段，
        # 即便通道是 wechat 也保留它，避免老消费方拿不到 target。
        "qqTarget": target_key,
        "channelTarget": target_key,
        "channel": channel,
        "canReply": can_reply,
        "targetType": target_type,
        "targetId": str(target_id),
        "nickname": nickname,
        "text": text,
        "time": time_str,
        "deliveryState": _DELIVERY_QUEUED,
        "dispatchState": _DELIVERY_QUEUED,
        "revision": 1,
        "createdAt": time.time(),
    }
    item["queueItemId"] = item["id"]
    if bot_uin:
        item["botUin"] = str(bot_uin)
    delivered = 0
    for s in _list_sessions_shallow():
        # 未知 channel → getattr 取不到 → 空集合，安全返回 0。
        subs = getattr(s, f"{channel}_subscriptions", set()) or set()
        if target_key not in subs and not (bot_key and bot_key in subs):
            continue
        # Each subscriber owns an independent durable delivery state.  Reusing
        # one dict across sessions would let one worker's in-flight transition
        # suppress delivery to every other subscriber.
        subscriber_item = dict(item)
        async with queue_lock(s.id):
            subscriber_item = await _append_new_queue_item(s, subscriber_item)
            queue_revision = s.queue_revision
        await _bcast({"type": "queue.item_added", "sessionId": s.id,
                      "queueItemId": subscriber_item["id"],
                      "queueRevision": queue_revision,
                      "item": dict(subscriber_item)})
        await _wake_worker(s.id, auto_spawn=True)
        delivered += 1
    return delivered


async def enqueue_plugin_message(plugin_id: str, session_id: str, text: str,
                                 *, client_message_id: str | None = None) -> dict:
    if not isinstance(text, str) or not text:
        return {"error": "text is required"}
    target = _get_session_shallow(session_id) if isinstance(session_id, str) else None
    if target is None:
        return {"error": "Session not found"}
    async with queue_lock(target.id):
        item, error = await _persist_task_item(target, text, "plugin", None, None,
            client_message_id, idempotent_task_id=False, source_plugin_id=plugin_id)
    if error:
        return {"error": error}
    await _wake_worker(target.id, auto_spawn=True)
    return {"status": "queued", "sessionId": target.id,
            "queueItemId": _queue_item_id(item) if item else None}


async def enqueue_qq_reminder(target_type: str, target_id: str,
                              nickname: str = "", text: str = "",
                              time_str: str = "", bot_uin: str = "") -> int:
    """QQ inbox 更新提醒入队（薄 wrapper，保留旧签名/旧调用方零改动）。

    实现已泛化到 ``enqueue_channel_reminder("qq", ...)``；此处只做转调，行为
    与字段与改造前完全一致（含 qqTarget 兼容键）。
    """
    return await enqueue_channel_reminder(
        "qq", target_type, target_id,
        nickname=nickname, text=text, time_str=time_str, bot_uin=bot_uin,
    )


async def enqueue_notice(target_session_id: str, text: str,
                         source: str = "agent",
                         source_session_id: str | None = None,
                         event_id: str | None = None,
                         *, notice_kind: str | None = None,
                         job_id: str | None = None,
                         notice_status: str | None = None,
                         creator_session_id: str | None = None,
                         target_session_ids: list[str] | None = None,
                         envelope: dict | None = None) -> dict:
    """向显式指定的 session 投递一条提醒（MCP agent_notify 的后端实现）。

    复刻 _enqueue_report 的持久化投递三步（append 落盘队列 + save_async +
    唤醒），但有两点不同：

    1. 目标是**显式指定**的 target_session_id（调用方自己或其 managed 的
       session，隔离由 MCP 层 _check_access 实施本层不检查）；
    2. 唤醒用 ``_wake_worker(target, auto_spawn=True)``——无活 worker 时
       **立即** create_worker 恢复（事件驱动，不等 watchdog tick；spawn
       失败打 warning，消息已落盘、由全局 watchdog 兜底）。

    普通 agent 通知保持 report 兼容形状；后台 Job 终态通知可额外携带
    ``noticeKind``, ``jobId``, ``status``, ``creatorSessionId``,
    ``targetSessionId`` 和 ``targetSessionIds``。这些字段是结构化 envelope
    元数据，不改变
    ``agent_notify`` 的调用语义。
    加 type="notice" 区分语义：消费端按
    ``type != "task"`` 取报告、前端 normalize 非 task/qq 按 report 分支
    渲染，均天然兼容。渲染时抬头取 sourceSessionId（``@@@@by agent``），
    sourceSessionId 缺省（无调用方身份）由 source 标签标识；自动化为 automation。

    返回 {"ok": True, "sessionId": ..., "pending": <队列长度>}；session
    不存在返回 {"ok": False, "error": {...}}。
    """
    if _shutdown_started:
        return {"ok": False, "error": {
            "code": "pan_shutting_down",
            "message": "Pan main service is shutting down"}}
    target = _get_session_shallow(target_session_id)
    if not target:
        return {"ok": False, "error": {
            "code": "session_not_found",
            "message": f"Session {target_session_id} not found"}}
    # A terminal background event is retried by Pan after crashes.  Search both
    # the pending queue and the durable delivery ledger: the queue row may have
    # already crossed the provider boundary and therefore no longer be queued.
    if event_id:
        all_items = list(target.queue_pending or []) + list(
            (getattr(target, "queue_delivery_ledger", {}) or {}).values())
        if any(isinstance(item, dict) and item.get("eventId") == event_id
               for item in all_items):
            return {"ok": True, "sessionId": target_session_id,
                    "pending": len(target.queue_pending or []),
                    "duplicate": True, "eventId": event_id}
    source_type, source_sid, source_error = _validate_source_metadata(
        target, source, source_session_id)
    if source_error:
        code = ("readonly_session" if source_error == READONLY_SESSION_ERROR
                else "invalid_source_metadata")
        return {"ok": False, "error": {
            "code": code,
            "message": source_error}}
    item = {
        "kind": "report",
        "id": "q_" + uuid.uuid4().hex,
        "queueItemId": None,
        "source": source_type,
        "status": "notice",
        "type": "notice",
        "result": text,
        "sessionId": target_session_id,
        "taskId": None,
        "workerId": None,
        # Keep the explicit durable state used by report/QQ delivery.  A
        # missing state is treated as queued for backward compatibility, but
        # new notices must survive restart with the same queue contract.
        "deliveryState": _DELIVERY_QUEUED,
        "dispatchState": _DELIVERY_QUEUED,
        "revision": 1,
        "createdAt": time.time(),
    }
    if event_id:
        item["eventId"] = event_id
    if envelope is not None:
        item["envelope"] = dict(envelope)
        item["envelope"].setdefault(
            "jobId", event_id.removesuffix(":terminal")
            if isinstance(event_id, str) else None)
    item["queueItemId"] = item["id"]
    if source_sid is not None:
        item["sourceSessionId"] = source_sid
    if notice_kind is not None:
        item["noticeKind"] = notice_kind
    if notice_kind == "background_job_terminal" and notice_status is not None:
        item["status"] = notice_status
    if job_id is not None:
        item["jobId"] = job_id
    if creator_session_id is not None:
        item["creatorSessionId"] = creator_session_id
    item["targetSessionId"] = target_session_id
    item["targetSessionIds"] = (
        list(target_session_ids) if target_session_ids is not None
        else [target_session_id]
    )
    async with queue_lock(target.id):
        if event_id and any(row.get("eventId") == event_id for row in
                list(target.queue_pending or []) + list(target.queue_delivery_ledger.values())
                if isinstance(row, dict)):
            return {"ok": True, "sessionId": target_session_id,
                    "pending": len(target.queue_pending), "duplicate": True, "eventId": event_id}
        item = await _append_new_queue_item(target, item)
        queue_revision = target.queue_revision
    await _bcast({"type": "queue.item_added", "sessionId": target.id,
                  "queueItemId": item["id"],
                  "queueRevision": queue_revision,
                  "item": dict(item)})
    _log.info("[Session %s] queued notice source=%s sourceSessionId=%s",
              target_session_id, source_type, source_sid)
    await _wake_worker(target_session_id, auto_spawn=True)
    _schedule_auto_force(target.id, item)
    return {"ok": True, "sessionId": target_session_id,
            "pending": len(target.queue_pending)}


async def _enqueue_zombie_report(w: Worker, reason: str) -> None:
    """被管 session 的 worker 异常死亡 → 向 manager 推送 zombie 报告（B2）。

    复用 _enqueue_report 的订阅判定（仅 manager 已订阅该 session 才入队）与
    唤醒逻辑；报告形状为 ``{"status": "error", "type": "zombie", ...}``。

    **关键语义**：只有任务进行中（status == running/queued）被判定异常死亡才
    报 zombie——watchdog 卡死/超时回收（task_timeout / queued 超时）、进程崩溃
    /异常退出（进程退出检测路径）都落在 running/queued 上；正常完成后的 idle
    回收不报（done/error 报告在完成时已推送）。与 watchdog 的判定对齐。

    _zombie_reported 标记防止 watchdog kill 路径与进程退出检测路径竞态双报。
    """
    if w.status not in ("running", "queued"):
        return
    if w._zombie_reported:
        return
    w._zombie_reported = True
    task_id = w._current_task_id
    await _enqueue_report(w.session_id, "error", f"worker died: {reason}",
                          task_id, w.worker_id, report_type="zombie",
                          diagnostic=_error_report_diagnostic(w, "error"))
    # A hand-off-complete task has no queue row to recover.  Clear its
    # persisted context after emitting the exact zombie report; an unfinished
    # queue row remains the active retry context for the replacement Worker.
    s = _session(w)
    if s and _clear_active_task_if_not_pending(s):
        try:
            await _sess.save_async(s)
        except Exception as exc:
            _log.warning("[Worker %s] failed to persist zombie task context clear: %s",
                         w.worker_id, exc)


# ── watchdog：超时 / 空闲回收 ──


async def _watchdog(w: Worker):
    """周期性检查 worker 活性，超时/空闲则 kill。

    - stream 模式（w.process 非 None）：
      - running：任务运行时长（_task_started_at 起算）超 _WORKER_TASK_TIMEOUT_SEC → 判定卡死 → kill
      - queued：持续无输出（last_activity 起算）超 _WORKER_TIMEOUT_SEC → 判定卡死 → kill
      - idle：任务完成且长时间无新任务 → 空闲回收 → kill
    - MCP one-shot 模式（w.process 为 None）：
      - 只做 idle 回收（超时已由 _consumer_oneshot 读取超时承担，running 不干预）
    - held（takeover 模式）/ zombie：跳过，不回收

    每个动作（kill / 跳过）都写日志，记录 worker_id、idle_for、阈值
    （_WORKER_TIMEOUT_SEC / _WORKER_IDLE_SEC）与判定分支（branch=...），
    便于事后追溯 watchdog 对每个 worker 的处置。
    """
    # 已记录过的跳过状态：held/zombie 只在状态变化时打 INFO（避免长驻 held
    # 期间每 tick 刷屏），重复 tick 只打 DEBUG 保留完整审计。
    last_skip_status: str | None = None
    while True:
        await asyncio.sleep(_WATCHDOG_TICK_SEC)

        if w.worker_id not in workers:
            _log.debug(
                "[Worker %s] watchdog exit: worker_id=%s not in registry, loop ending",
                w.worker_id, w.worker_id,
            )
            return

        idle_for = time.monotonic() - w.last_activity

        if w.status in ("held", "zombie"):
            if w.status != last_skip_status:
                _log.info(
                    "[Worker %s] watchdog skip: status=%s idle_for=%.0fs "
                    "branch=skip_held_zombie",
                    w.worker_id, w.status, idle_for,
                )
                last_skip_status = w.status
            else:
                _log.debug(
                    "[Worker %s] watchdog skip (repeated): status=%s idle_for=%.0fs "
                    "branch=skip_held_zombie",
                    w.worker_id, w.status, idle_for,
                )
            continue
        last_skip_status = None

        # MCP one-shot：只回收长期 idle 的 worker（running 由读取超时兜底）
        if w.process is None:
            if w.status == "idle" and idle_for > _WORKER_IDLE_SEC:
                if _worker_has_pending_work(w):
                    _log.debug(
                        "[Worker %s] watchdog keep: reason=pending_work mode=mcp "
                        "idle_for=%.0fs branch=mcp_pending_work",
                        w.worker_id, idle_for,
                    )
                    continue
                _log.info(
                    "[Worker %s] watchdog kill: reason=idle_reclaim mode=mcp status=%s "
                    "idle_for=%.0fs idle_threshold=%.0fs branch=mcp_idle_reclaim",
                    w.worker_id, w.status, idle_for, _WORKER_IDLE_SEC,
                )
                await kill_worker(w.worker_id)
                return
            continue

        # stream 模式：running 用任务运行时长判定，queued 用静默时长判定，idle 空闲回收
        if (w.native_control_mode != "pan" or w._native_reservation_busy or w._native_external_turn_active
                or w.native_input_uncertain):
            # User-owned native turns and their resident kernel are not abandoned
            # Pan tasks. Browser detach/control handoff never reclaims them.
            # Unknown input is not a timeout receipt: recover=True could resend
            # already-accepted bytes. Retirement must remain an explicit action.
            continue
        if w.status == "running":
            # 任务运行时长 = 当前时间 - 任务开始处理时刻（_consumer_stream 进入 running 时记录）。
            # 不用 last_activity（无输出时长）：长思考/大文件读取会长时间无 stdout 输出，
            # 若按无输出判定会被误杀。真卡死的任务会一直跑下去，最终超过任务时长上限。
            task_run_for = time.monotonic() - w._task_started_at
            if task_run_for > _WORKER_TASK_TIMEOUT_SEC:
                _log.warning(
                    "[Worker %s] watchdog kill: reason=task_timeout mode=stream status=%s "
                    "task_run_for=%.0fs task_timeout_threshold=%.0fs branch=stream_task_timeout",
                    w.worker_id, w.status, task_run_for, _WORKER_TASK_TIMEOUT_SEC,
                )
                # B2: 异常死亡（running 卡死超时）→ 向被管 manager 推送 zombie 报告
                await _enqueue_zombie_report(
                    w, f"task timeout (running {task_run_for:.0f}s > {_WORKER_TASK_TIMEOUT_SEC:.0f}s)")
                await kill_worker(w.worker_id, recover=True)
                return
        elif w.status == "queued" and idle_for > _WORKER_TIMEOUT_SEC:
            _log.warning(
                "[Worker %s] watchdog kill: reason=timeout mode=stream status=%s "
                "idle_for=%.0fs timeout_threshold=%.0fs branch=stream_queued_timeout",
                w.worker_id, w.status, idle_for, _WORKER_TIMEOUT_SEC,
            )
            # B2: 异常死亡（queued 静默超时）→ 向被管 manager 推送 zombie 报告
            await _enqueue_zombie_report(
                w, f"queued timeout (no output for {idle_for:.0f}s)")
            await kill_worker(w.worker_id, recover=True)
            return
        if w.status == "idle" and idle_for > _WORKER_IDLE_SEC:
            if _worker_has_pending_work(w):
                _log.debug(
                    "[Worker %s] watchdog keep: reason=pending_work mode=stream "
                    "idle_for=%.0fs branch=stream_pending_work",
                    w.worker_id, idle_for,
                )
                continue
            _log.info(
                "[Worker %s] watchdog kill: reason=idle_reclaim mode=stream status=%s "
                "idle_for=%.0fs idle_threshold=%.0fs branch=stream_idle_reclaim",
                w.worker_id, w.status, idle_for, _WORKER_IDLE_SEC,
            )
            # B2: idle 回收是「正常完成后的回收」——done/error 报告完成时已推送，
            # 不报 zombie（与 watchdog 判定对齐）
            await kill_worker(w.worker_id)
            return


# ── 全局 watchdog：落盘队列自愈（立项 4.4）──
# worker 级 _watchdog 随 worker 生灭（worker 死亡时它自己也结束），无法自愈；
# 本任务生命周期=Pan 服务（由 server lifespan 启动/关闭），周期扫描"落盘队列
# queue_pending 非空或异常死亡重建标记仍在、但没有活 worker 的 session"，自动
# create_worker 恢复。
# spawn 走 create_worker（自带防重复，立项 4.5），不会对同一 session 重复 spawn。

_GLOBAL_WATCHDOG_TICK_SEC: float = _WATCHDOG_TICK_SEC  # 沿用 worker 级间隔
_global_watchdog_task: asyncio.Task | None = None


async def _recover_session(session_id: str, *, force: bool = False) -> None:
    """Best-effort recovery for durable work or an abnormally dead worker."""
    s = _get_session_shallow(session_id)
    if not s:
        _recovery_required.discard(session_id)
        return
    await _prepare_offline_recovery(s)
    if _queue_recovery_blocked(s):
        # A forced recovery requested by an abnormal exit still must not
        # launch a provider solely to wait on a locked queue backlog.
        _recovery_required.discard(session_id)
        return
    if not force and not _has_dispatchable_items(s) and session_id not in _recovery_required:
        return
    current = find_worker_by_session(session_id)
    # takeover and an explicit restart own the lifecycle; do not race them.
    if current and current.status in {"held", "restarting"}:
        _recovery_required.discard(session_id)
        return
    if find_alive_worker_by_session(session_id) is not None:
        _recovery_required.discard(session_id)
        return
    created = await create_worker(session_id)
    if isinstance(created, str):
        _log.warning(
            "[Pan] Immediate queue recovery failed for session=%s: %s",
            session_id, created,
        )
        return
    _recovery_required.discard(session_id)


def _schedule_session_recovery(
    session_id: str, *, force: bool = False,
) -> asyncio.Task | None:
    """Schedule at most one recovery attempt for a session in this loop."""
    if _shutdown_started:
        # The service is winding down; a fresh recovery spawn would race the
        # loop teardown (see _shutdown_started).  The durable queue keeps the
        # work; the next start recovers it.
        return None
    if getattr(_get_session_shallow(session_id), "queue_paused", False):
        _recovery_required.discard(session_id)
        return None
    if force:
        _recovery_required.add(session_id)
    existing = _recovery_tasks.get(session_id)
    if existing is not None and not existing.done():
        return existing
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Called without a running loop (only possible for an embedding caller);
        # the global watchdog will still discover the durable backlog later.
        return None
    task = loop.create_task(
        _recover_session(session_id, force=force),
        name=f"pan-recover-{session_id}",
    )
    _recovery_tasks[session_id] = task

    def _finish(done: asyncio.Task) -> None:
        if _recovery_tasks.get(session_id) is done:
            _recovery_tasks.pop(session_id, None)
        if done.cancelled():
            return
        try:
            done.result()
        except Exception:
            _log.exception("[Pan] Immediate queue recovery crashed for session=%s", session_id)

    task.add_done_callback(_finish)
    return task


async def drain_recoveries(timeout: float = 10.0) -> int:
    """Bounded shutdown drain for fire-and-forget recovery tasks.

    Must run on a still-healthy loop (uvicorn lifespan shutdown).  Recovery
    tasks that are mid real ``create_subprocess_exec`` can never observe
    cancellation on the Windows Proactor loop — their waiter future stays
    unresolved — so this function:

    1. raises ``_shutdown_started`` so no new recovery gets scheduled;
    2. lets in-flight recoveries finish naturally within ``timeout``;
    3. cancels stragglers but waits only ``_DRAIN_CANCEL_GRACE_SEC`` and then
       gives up on them (dangling tasks only produce a "Task was destroyed"
       warning at loop close; awaiting them unboundedly is what deadlocked
       the shutdown).

    Returns the number of tasks that were still pending when the drain began.
    """
    global _shutdown_started
    _shutdown_started = True
    inflight = [t for t in list(_recovery_tasks.values()) if not t.done()]
    _recovery_tasks.clear()
    if inflight:
        _log.info("[Pan] Shutdown: draining %d in-flight recovery task(s)", len(inflight))
        await asyncio.wait(set(inflight), timeout=timeout)
        stragglers = [t for t in inflight if not t.done()]
        for t in stragglers:
            t.cancel()
        if stragglers:
            await asyncio.wait(set(stragglers), timeout=_DRAIN_CANCEL_GRACE_SEC)
    return len(inflight)


def _queue_wake_deadlines(session) -> list[float]:
    """Finite queue gates need a wake when both retry and edit lease are due."""
    now = time.time()
    deadlines = []
    for item in session.queue_pending or []:
        if (not isinstance(item, dict) or _queue_item_kind(item) is None
                or _delivery_state(item) != _DELIVERY_QUEUED or _queue_item_locked(item)):
            continue
        retry = item.get("nextAttemptAt")
        due = retry if isinstance(retry, (int, float)) and retry > now else now
        lease = _active_queue_edit_lock(session, _queue_item_id(item), now)
        if lease is not None:
            due = max(due, lease["expiresAt"])
        if due > now:
            deadlines.append(due)
    return deadlines


def _schedule_queue_retry(session_id: str) -> asyncio.Task | None:
    """Wake at a retry/lease deadline; queue mutations can re-arm the wait."""
    session = _get_session_shallow(session_id)
    if (session is None or getattr(session, "queue_paused", False)
            or not _queue_wake_deadlines(session)):
        # Immediate queue items already have a queue_signal.  Only create a
        # timer for an item actually waiting for backoff or an edit lease;
        # otherwise the timer would emit a duplicate wakeup on every spawn.
        return None
    existing = _queue_retry_tasks.get(session_id)
    if existing is not None and not existing.done():
        _queue_retry_wakes[session_id].set()
        return existing
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    changed = asyncio.Event()
    _queue_retry_wakes[session_id] = changed

    async def _wait_and_wake() -> None:
        while True:
            changed.clear()
            s = _get_session_shallow(session_id)
            if s is None or getattr(s, "queue_paused", False):
                return
            future_times = _queue_wake_deadlines(s)
            if not future_times:
                worker = find_alive_worker_by_session(session_id)
                if (worker and worker.pending_signal is not None
                        and _has_sendable_queued_items(s)):
                    worker.pending_signal.put_nowait({"type": "queue_signal"})
                elif worker is None and _has_dispatchable_items(s):
                    _schedule_session_recovery(session_id)
                return
            delay = max(0.0, min(future_times) - time.time())
            try:
                await asyncio.wait_for(changed.wait(), delay)
            except asyncio.TimeoutError:
                pass

    task = loop.create_task(_wait_and_wake(), name=f"pan-queue-retry-{session_id}")
    _queue_retry_tasks[session_id] = task

    def _finish(done: asyncio.Task) -> None:
        if _queue_retry_tasks.get(session_id) is done:
            _queue_retry_tasks.pop(session_id, None)
            _queue_retry_wakes.pop(session_id, None)
        if not done.cancelled():
            try:
                done.result()
            except Exception:
                _log.exception("[Pan] queue retry timer failed for session=%s", session_id)

    task.add_done_callback(_finish)
    return task


def start_global_watchdog() -> asyncio.Task:
    """启动服务级 watchdog（生命周期=Pan 服务）。幂等：已在运行则复用。"""
    global _global_watchdog_task
    if _global_watchdog_task is not None and not _global_watchdog_task.done():
        return _global_watchdog_task
    _global_watchdog_task = asyncio.create_task(_global_watchdog())
    _log.info("[Pan] Global watchdog started (tick=%.0fs)", _GLOBAL_WATCHDOG_TICK_SEC)
    return _global_watchdog_task


def stop_global_watchdog():
    """取消服务级 watchdog（Pan 关闭时调用）。"""
    global _global_watchdog_task
    if _global_watchdog_task is not None:
        _global_watchdog_task.cancel()
        _global_watchdog_task = None


async def _global_watchdog():
    """服务级常驻循环：周期扫描并自动恢复（见模块注释）。"""
    # Usage jobs do not require a provider Worker and are safe to resume at
    # startup.  Keep durable queue recovery on its historical first-tick
    # timing; an immediate queue scan can race callers that are still setting
    # up a Session after service startup.
    try:
        recover_pending_usage_enrichment()
    except Exception:
        _log.exception("[Pan] startup usage enrichment recovery failed")
    while True:
        await asyncio.sleep(_GLOBAL_WATCHDOG_TICK_SEC)
        try:
            await _global_watchdog_tick()
        except Exception as e:
            _log.warning("[Pan] Global watchdog tick error: %r", e)


async def _global_watchdog_tick():
    """单轮扫描：有持久积压/异常重建意图且无活 worker → create_worker 恢复。

    整个 tick 由 _global_watchdog 的 try/except 兜底，单个 session 的异常不会
    中断后续轮次。
    """
    for s in list(_list_sessions_shallow()):
        # Usage post-processing is independent from Worker liveness.  A
        # crashed/restarted Worker must not strand a terminal's eventual usage
        # job, and this scan must not spawn a provider merely for enrichment.
        if _has_pending_usage_enrichment(s):
            _schedule_usage_enrichment(s.id)
        try:
            await _prepare_offline_recovery(s)
        except Exception:
            _log.exception("Legality recovery blocked for Session %s", s.id)
            continue
        if not _has_dispatchable_items(s) and s.id not in _recovery_required:
            continue
        current = find_worker_by_session(s.id)
        if current and current.status in {"held", "restarting"}:
            continue
        if find_alive_worker_by_session(s.id) is not None:
            continue  # 已有活 worker → 正常
        # Use the same de-duplicated recovery path as lifecycle events.  The
        # per-session spawn lock inside create_worker closes the final race
        # with a simultaneous restart-or-start request.
        _log.info(
            "[Pan] Global watchdog recover: session=%s queue_pending=%d items, no live worker",
            s.id, len(s.queue_pending),
        )
        recovery = _schedule_session_recovery(s.id)
        if recovery is None:
            continue
        try:
            await recovery
        except Exception:
            _log.warning(
                "[Pan] Global watchdog recover failed for session=%s",
                s.id,
            )


class _QueuePausedBeforeHandoff(Exception):
    pass


class _OneShotSpawnFailed(Exception):
    """Only raised by the subprocess creation operation, before a handle exists."""



async def _queue_handoff(w: Worker, s, operation, on_handoff):
    from packages.plugins.lifecycle import session_claim, action_groups, pinned
    async with session_claim(s):
        if set(pinned(s.id)[0]) - action_groups(s.adapter_config):
            request_delayed_restart(s.id)
            raise _QueuePausedBeforeHandoff('Loaded Action Group was detached; Worker restart required')
        return await _queue_handoff_admitted(w, s, operation, on_handoff)


async def _queue_handoff_admitted(w: Worker, s, operation, on_handoff):
    """Serialize pause with write/drain or spawn and receipt, not execution.

    Pause waits for a handoff already holding this lock. Reserved rows that
    have not entered it remain retractable and are requeued by the caller.
    """
    async with queue_lock(s.id):
        async def reject(reason):
            if getattr(s, "offline_run", None) and not w._current_handoff_acked:
                identity = _offline.owner(w)
                await _commit_offline(s, lambda candidate: _offline.finish(
                    candidate, identity, reason="before-handoff/rejected"))
            raise _QueuePausedBeforeHandoff(reason)
        if w.status == "restarting" or (w.pending_restart and not w._current_handoff_acked):
            await reject("Worker retiring before CLI hand-off")
        if getattr(s, "queue_paused", False):
            await reject("Session queue paused before CLI hand-off")
        if w.native_control_mode != "pan":
            await reject("Native control owns CLI hand-off")
        if w.native_input_uncertain:
            await reject("Native input acceptance is unknown")
        items = ([w._current_queue_item] if w._current_queue_item is not None
                 else w._current_report_items)
        if any(not queue_item_eligible(s, item, queued=False) for item in items):
            await reject("Queue item blocked before CLI hand-off")
        selected = [item for item in items if item.get("selfConsumeRequested")]
        if selected:
            outcome = _sess.DurableOutcome()
            def apply_attempt():
                originals = [(item, dict(item)) for item in selected]
                old_ledger = { _queue_item_id(item): s.queue_delivery_ledger.get(_queue_item_id(item))
                               for item in selected }
                try:
                    for item in selected:
                        item["selfConsumeAttempted"] = True
                        s.queue_delivery_ledger[_queue_item_id(item)] = dict(item)
                    _sess._save_body(s)
                except BaseException:
                    for item, original in originals:
                        item.clear()
                        item.update(original)
                    for identity, original in old_ledger.items():
                        if original is None:
                            s.queue_delivery_ledger.pop(identity, None)
                        else:
                            s.queue_delivery_ledger[identity] = original
                    raise
            await _sess._persist_async_outcome(s.id, apply_attempt, outcome)
        # self-consume attempt persistence can yield after running preparation.
        # A newly accepted retirement intent still wins before actual handoff.
        if w.status == "restarting" or (w.pending_restart and not w._current_handoff_acked):
            await reject("Worker retiring before CLI hand-off")
        result = await operation()
        if on_handoff is not None:
            await on_handoff()
        return result


async def _consumer_stream(w: Worker, text: str, source: str, s, *, on_handoff=None):
    """Stream mode: write to the adapter's long-running stdin."""
    if w.status == "restarting" or (w.pending_restart and w.status in {"idle", "queued"}):
        return
    standalone_items = []
    if on_handoff is None:
        if w._current_queue_item is not None:
            standalone_items = [w._current_queue_item]
        elif w._current_report_items:
            standalone_items = list(w._current_report_items)
        if standalone_items:
            on_handoff = lambda: _commit_queue_handoff(w, s, standalone_items)
    if w.process is None or w.process.returncode is not None:
        # No provider hand-off happened.  The queue coordinator will requeue
        # the still-pending item after this helper returns.
        await _finish_task_error(
            w,
            s,
            f"Worker process dead (returncode={w.process.returncode if w.process else 'none'})",
            error_context={"origin": "worker.preflight.process_dead"},
        )
        return

    # Clear the completion latch before handing the prompt to the provider.
    # The provider can answer synchronously (especially with a cached turn);
    # clearing it at the end would lose that wake-up and leave the serial
    # consumer stuck in running forever.
    ev = getattr(w, "_task_done", None)
    if ev is not None:
        ev.clear()

    # 任务运行时长起算点：进入 running 即开始计时（watchdog 据此判定卡死）。
    # 区别于 last_activity（无输出时长）：长思考/大文件读取会长时间无 stdout，
    # 用「任务运行时长」作为 running 超时判据，避免误杀静默思考中的任务。
    w._task_started_at = time.monotonic()
    await _begin_offline_run(w)
    w.status = "running"
    await _record_legal_worker_state(w, "running", "task/start")

    # Clear before writing.  A fast provider can emit init+result before the
    # status/save broadcasts below; clearing after the write would erase that
    # legitimate completion and leave the consumer waiting forever.
    ev = getattr(w, "_task_done", None)
    if ev is not None:
        ev.clear()
    try:
        data = w.adapter.encode_user_message(text)
        w._current_serialized = data + b"\n"
        # Keep the previous terminal latched while task-start persistence and
        # broadcasts yield control. Clear it only at the provider hand-off
        # boundary so already-buffered post-terminal frames cannot be assigned
        # to this queue item's taskSeq.
        async def write_input():
            _reset_raw_capture(w)
            w._current_task_last_assistant_text = None
            w._current_task_last_assistant_seq = None
            w._current_task_assistant_ids.clear()
            w._terminal_handled = False
            w._steer_run_id = None
            written = w.process.stdin.write(w._current_serialized)
            if written is not None and written != len(w._current_serialized):
                raise OSError("short stdin write")
            await w.process.stdin.drain()
        await _queue_handoff(w, s, write_input, on_handoff)
    except asyncio.CancelledError:
        if standalone_items and not w._current_handoff_acked:
            await asyncio.shield(_requeue_queue_unit(
                w, s, standalone_items, "worker cancelled before CLI hand-off"))
        raise
    except Exception as exc:
        if standalone_items and not w._current_handoff_acked:
            await _requeue_queue_unit(w, s, standalone_items,
                                       f"CLI hand-off failed: {exc}")
            return
        raise

    await _bcast({
        "type": "worker.status",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "generation": w.generation,
        "status": "running",
        "taskSeq": w._current_seq,
        "executionSeq": w._execution_seq or None,
        "source": source,
        "sourceSessionId": w._current_source_session_id,
    })

    # Receipt/history 已在 _consumer 中持久化完成；这里仅保存 stdin 写入后可能
    # 产生的运行态元数据（例如 provider session id），不再承担 queue 消费语义。
    # 重取 session 避免把已删除会话写回复活（#10 模式，同
    # _consumer_oneshot 的输出处理处）。
    sess = _session(w)
    if sess:
        await _sess.save_async(sess)

    # Direct legacy embedders may call this helper with a pre-reserved item,
    # without the coordinator's completion event.  Their contract ends at the
    # stdin hand-off; the production coordinator continues waiting so FIFO is
    # preserved for the next item.
    if standalone_items:
        return

    # 等待当前任务完成（result 事件 → _read_stdout 置 idle 并 set _task_done）后再返回。
    # 不能轮询 status：status 在多协程间共享，result 处理中先置 done 后置 idle，旧任务
    # result 协程的 idle 会覆盖新任务已设的 running，导致等待提前退出、多条消息同时在
    # cbc 长驻进程管道里飞行，result 与 seq/taskId 错位、history 重复（实测复现）。
    # 用独立 Event 标记"本次任务完成"，_consumer 才能串行推进下一个排队任务。
    if ev is not None:
        await ev.wait()


def _extract_cbc_error(output: bytes) -> str | None:
    """Extract cbc's structured error message from one-shot stream-json output.

    cbc exits 0 even on failure (e.g. ``--resume`` pointing at a session that
    no longer exists), emitting ``{"type":"error","error":"..."}`` instead of
    a ``result`` event. Without this, a broken ``cli_session_id`` surfaces as
    a silent ``(no output)`` error, masking the real cause.
    """
    for line in output.decode(errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "error":
            msg = event.get("error")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    return None


def _log_mcp_launch(worker_id: str, session, args: list[str]) -> None:
    """Log non-sensitive MCP launch facts for adapter diagnosis.

    Do not log argv values: prompts and system prompts can contain user data.
    The descriptor summary intentionally contains only server names, executable
    paths, cwd and environment *keys*, which is enough to diagnose a launcher
    cwd/PYTHONPATH/API propagation failure without exposing credentials.
    """
    descriptors: list[dict] = []
    try:
        mcp_index = args.index("--mcp-config")
    except ValueError:
        mcp_index = -1
    if mcp_index >= 0:
        for raw_path in args[mcp_index + 1:]:
            if str(raw_path).startswith("--"):
                break
            try:
                data = json.loads(Path(raw_path).read_text(encoding="utf-8"))
                for name, entry in (data.get("mcpServers") or {}).items():
                    descriptors.append({
                        "name": name,
                        "command": entry.get("command"),
                        "cwd": entry.get("cwd"),
                        "env_keys": sorted((entry.get("env") or {}).keys()),
                    })
            except (OSError, json.JSONDecodeError, AttributeError):
                descriptors.append({"path": str(raw_path), "readable": False})
    _log.info(
        "[Worker %s] Claude MCP launch: process_cwd=%s session_workdir=%s "
        "argv_flags=%s descriptors=%s",
        worker_id,
        os.getcwd(),
        getattr(session, "workdir", None),
        [arg for arg in args if isinstance(arg, str) and arg.startswith("--")],
        descriptors,
    )


async def _consumer_oneshot(w: Worker, text: str, source: str, s, *, on_handoff=None):
    """One-shot mode: 每任务 spawn 一个一次性进程（prompt 作末参）。

    argv 由 ``adapter.oneshot_args(s, text)`` 提供（cbc/Claude 实现；kimi/opencode 因
    ``execution_modes == ["stream"]`` 永不进入此路径）。stdout 收集后用统一
    ``adapter.parse_event`` 事件模型解析。取代旧 ``_consumer_mcp`` 的 cbc 特定
    拼装与 ``hasattr`` 探测（adapter-architecture P1 建议 4）。

    详见 docs/design/adapter-p1-oneshot.md §4。
    """
    if w.status == "restarting" or (w.pending_restart and w.status in {"idle", "queued"}):
        return
    _reset_raw_capture(w)
    w._current_task_last_assistant_text = None
    w._current_task_last_assistant_seq = None
    w._current_task_assistant_ids.clear()
    w._terminal_handled = False
    standalone_items = []
    if on_handoff is None:
        if w._current_queue_item is not None:
            standalone_items = [w._current_queue_item]
        elif w._current_report_items:
            standalone_items = list(w._current_report_items)
        if standalone_items:
            on_handoff = lambda: _commit_queue_handoff(w, s, standalone_items)

    await _begin_offline_run(w)
    w.status = "running"
    await _record_legal_worker_state(w, "running", "task/start")
    await _bcast({
        "type": "worker.status",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "generation": w.generation,
        "status": "running",
        "taskSeq": w._current_seq,
        "executionSeq": w._execution_seq or None,
        "source": source,
        "sourceSessionId": w._current_source_session_id,
    })

    adapter = w.adapter

    # argv 全部来自 adapter（去 provider 特化）：one-shot 不传
    # --input-format stream-json，
    # --resume / --mcp-config / --system-prompt（仅首条）/ prompt 末参。
    args = adapter.oneshot_args(s, text) if hasattr(adapter, "oneshot_args") else []
    if not args:
        # 防御：adapter 不支持 oneshot 却进入此路径（不应发生，resolve 已 gate）。
        _log.error(
            "[Worker %s] oneshot_args 返回空 argv（adapter=%s 不支持 oneshot？）；跳过本次任务",
            w.worker_id, getattr(adapter, "name", "?"),
        )
        await _finish_task_error(
            w, s, "adapter 不支持 oneshot 执行模式",
            error_context={"origin": "worker.preflight.oneshot_args"},
        )
        w.last_activity = time.monotonic()
        _maybe_restart_pending(w)
        return

    _log_mcp_launch(w.worker_id, s, args)
    _log.info(
        "[Worker %s] one-shot spawn: executable=%s argc=%d",
        w.worker_id, args[0], len(args),
    )

    execution_owner = _offline.owner(w)
    proc = None
    try:
        try:
            async def spawn_input():
                nonlocal proc
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *args,
                        cwd=s.workdir or None,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        **command_window_options(),
                    )
                except Exception as exc:
                    raise _OneShotSpawnFailed(str(exc)) from exc
                w._mcp_proc = proc
                identity = _offline.owner(w)
                def attach_process(candidate):
                    if not _offline.same_owner(candidate.offline_run, identity):
                        raise RuntimeError("One-shot execution owner changed")
                    candidate.offline_run = {**candidate.offline_run, "processes": identity["processes"]}
                    candidate.offline_owner = dict(identity)
                await _commit_offline(s, attach_process)
                return proc
            proc = await _queue_handoff(w, s, spawn_input, on_handoff)
        except asyncio.CancelledError:
            if standalone_items and not w._current_handoff_acked:
                await asyncio.shield(_requeue_queue_unit(
                    w, s, standalone_items, "worker cancelled before CLI hand-off"))
            raise
        except _QueuePausedBeforeHandoff:
            if standalone_items:
                await _requeue_queue_unit(w, s, standalone_items, "Session queue paused")
            else:
                raise
            w.status = "idle"
            await _record_legal_worker_state(w, "idle", "queue/paused")
            return
        except _OneShotSpawnFailed as e:
            _log.error("[Worker %s] one-shot spawn failed: %s", w.worker_id, e)
            if standalone_items:
                await _requeue_queue_unit(w, s, standalone_items,
                                           f"one-shot spawn failed: {e}")
            await _finish_task_error(
                w, s, format_cli_spawn_error(adapter.name, e),
                error_context={"origin": "worker.oneshot.spawn_failed"},
            )
            # M3: 置 idle 同步刷新活性时间，避免该 worker 刚忙完就被 watchdog 当空闲回收
            w.last_activity = time.monotonic()
            _maybe_restart_pending(w)
            return
        except Exception as exc:
            if standalone_items and not w._current_handoff_acked:
                await _requeue_queue_unit(w, s, standalone_items,
                                           f"one-shot hand-off callback failed: {exc}")
            raise

        # Track in-flight process so kill_worker can terminate it (see #3).
        w._mcp_proc = proc

        # Save provider/session metadata only; queue removal was committed by the
        # callback above and does not wait for provider business output.
        sess = _session(w)
        if sess:
            await _sess.save_async(sess)

        # Collect output
        output = b""
        timed_out = False
        if not _DEFAULTS_INITIALIZED:
            load_worker_config()
        read_timeout = _WORKER_TIMEOUT_SEC
        try:
            while True:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=read_timeout)
                if not chunk:
                    break
                output += chunk
                if len(output) > 16 * 1024 * 1024:
                    _log.warning(
                        "[Worker %s] MCP output exceeded 16MB, aborting read",
                        w.worker_id,
                    )
                    proc.kill()
                    break
        except asyncio.TimeoutError:
            timed_out = True
            _log.warning("[Worker %s] MCP process timeout", w.worker_id)

        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    finally:
        # This scope begins before creation and covers the handoff callback,
        # metadata persistence and reads. Never abandon a successfully created
        # process when any of those operations raises or is cancelled.
        if proc is not None:
            async def cleanup_process():
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except (asyncio.TimeoutError, OSError) as exc:
                    _log.warning("[Worker %s] one-shot cleanup wait failed: %s", w.worker_id, exc)

            cleanup_task = asyncio.create_task(cleanup_process())
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # A second cancellation must not interrupt reap/cleanup.
                while not cleanup_task.done():
                    try:
                        await asyncio.shield(cleanup_task)
                    except asyncio.CancelledError:
                        continue
                cleanup_task.result()
                raise
            finally:
                if w._mcp_proc is proc:
                    w._mcp_proc = None

    returncode = proc.returncode
    _log.info(
        "[Worker %s] one-shot exit: code=%s timed_out=%s output_bytes=%d",
        w.worker_id, returncode, timed_out, len(output),
    )

    # DEBUG: save raw provider output for inspection
    debug_path = (
        os.path.join(s.workdir, f".pan-{adapter.name}-raw.jsonl") if s else None
    )
    if debug_path:
        try:
            os.makedirs(s.workdir, exist_ok=True)
            with open(debug_path, "wb") as df:
                df.write(output)
        except Exception:
            pass

    # Parse stream-json output through the adapter protocol — collect first,
    # apply to the session only after confirming it still exists (#10).  This
    # keeps Claude's one-shot fallback on the same event contract as cbc and
    # avoids hard-coding provider event names in the Worker.
    result_text = ""
    result_event: dict | None = None
    cli_session_id = None
    captured_model = None
    mcp_statuses: list[dict] = []
    assistant_events: list[dict] = []  # raw assistant events, re-broadcast as worker.stream
    assistant_blocks: list[dict] = []  # extracted history blocks (assistant/thinking/tool)
    error_events: list[dict] = []      # raw structured CLI errors (for the sample log)
    unparsed_lines: list[str] = []     # non-JSON stdout, kept for the sample log
    for line in output.decode(errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        event = adapter.parse_event(line)
        if event is None:
            unparsed_lines.append(line)
            continue
        if event.get("type") == "error":
            error_events.append(event)

        if adapter.is_result_event(event):
            result_event = event
            extracted = adapter.extract_result_text(event)
            result_text = str(extracted) if extracted is not None else ""
        elif adapter.is_init_event(event):
            await _activate_offline_worker(w, ready_proof=True, expected_owner=execution_owner)
            cli_session_id = adapter.extract_session_id(event)
            captured_model = adapter.extract_model(event)
            if adapter.name == "claude":
                raw_statuses = event.get("mcp_servers")
                if isinstance(raw_statuses, list):
                    mcp_statuses = [
                        {"name": item.get("name"), "status": item.get("status")}
                        for item in raw_statuses
                        if isinstance(item, dict)
                    ]
        elif adapter.is_assistant_event(event):
            # Extract blocks the same way stream mode's _read_stdout does, so
            # history format and the re-broadcast event match stream mode.
            blocks = adapter.extract_assistant_blocks(event)
            if blocks:
                assistant_events.append(event)
                assistant_blocks.extend(blocks)

    if mcp_statuses:
        _log.info("[Worker %s] Claude MCP status: %s", w.worker_id, mcp_statuses)

    # Re-fetch the session: it may have been deleted while the process ran.
    # Never write through a stale reference — that would resurrect a deleted
    # session on disk (#10).
    s = _sess.get(w.session_id)
    if s is None:
        _log.warning(
            "[Worker %s] Session %s deleted while task in flight; discarding result",
            w.worker_id,
            w.session_id,
        )
        # M3: 置 idle 同步刷新活性时间，避免该 worker 刚忙完就被 watchdog 当空闲回收
        w.last_activity = time.monotonic()
        w.status = "idle"
        return

    if cli_session_id:
        # Bind cli_session_id only when the session has none yet, or when the
        # captured id matches the existing binding (idempotent no-op). Never
        # overwrite a working binding with an unrelated id: a failed resume
        # (cbc exits 0, emits {"type":"error"} and NO init event) leaves
        # cli_session_id=None here, but a stale/mismatched init event must
        # not clobber the session's real cbc session (#bind-override).
        if not s.cli_session_id:
            s.cli_session_id = cli_session_id
        elif s.cli_session_id != cli_session_id:
            _log.warning(
                "[Worker %s] cli_session_id mismatch: existing=%s captured=%s; keeping existing",
                w.worker_id, s.cli_session_id, cli_session_id,
            )
    if captured_model and not s.model:
        s.model = captured_model
    # Append extracted blocks (assistant/thinking/tool) — same as stream mode.
    for block in assistant_blocks:
        _sess.append_history(s, block)
        if (block.get("role") == "assistant"
                and _sess.is_pan_message_id(block.get("messageId"))):
            w._current_task_assistant_ids.add(block["messageId"])
            if isinstance(block.get("content"), str) and block["content"].strip():
                w._current_task_last_assistant_text = block["content"]
                w._current_task_last_assistant_seq = w._current_seq

    # Surface failures the user can actually see (#8 timeout, #9 non-zero exit).
    if result_event is not None and adapter.is_result_error(result_event):
        extract_err = getattr(adapter, "extract_oneshot_error", None)
        structured_error = extract_err(output) if extract_err else None
        status, result = "error", structured_error or result_text or "(no output)"
    elif timed_out and not result_text:
        status, result = (
            "error",
            f"Task timed out after {read_timeout:.0f}s (no output) and the process was killed",
        )
    elif result_event is None and returncode not in (None, 0):
        tail = output.decode(errors="replace")[-2000:].strip()
        status, result = "error", f"{adapter.name} exited with code {returncode}:\n{tail}"
    elif result_event is None and returncode == 0:
        # A zero exit without a result can still carry a structured provider
        # error (for example an invalid --resume id).
        extract_err = getattr(adapter, "extract_oneshot_error", _extract_cbc_error)
        structured_error = extract_err(output)
        status, result = "error", structured_error or "(no output)"
    else:
        status, result = (
            "done" if result_event is not None and result_text else "error",
            result_text or "(no output)",
        )

    # Persist last_result/history/task association first.  In particular, do
    # not let usage lookup (which may wait on provider files) sit on this path.
    previous_snapshot = None
    if status in {"error", "cancelled"}:
        try:
            previous_snapshot = _sess.history_tail_snapshot(s)
        except Exception:
            previous_snapshot = None
    if result_event is not None:
        await _activate_offline_worker(w, ready_proof=True, expected_owner=execution_owner)
    terminal = await _persist_terminal_state(
        w, s, status, result, expected_owner=execution_owner,
        error_context={
            "origin": "worker.oneshot",
            "cli_raw": result_event,
            "cli_error_events": error_events,
            "cli_raw_output": "\n".join(unparsed_lines),
            "returncode": returncode,
            "timed_out": timed_out,
            "previous_message": previous_snapshot,
            "previous_captured": True,
        },
    )
    if terminal is None:
        return

    # Broadcast assistant events as worker.stream so the frontend displays the
    # reply in real-time — MCP mode otherwise only emits worker.result, which
    # both frontends render as a bare "[DONE]" system message (#stream-mcp).
    for event in assistant_events:
        await _bcast({
            "type": "worker.stream",
            "workerId": w.worker_id,
            "sessionId": w.session_id,
            "generation": w.generation,
            "taskSeq": w._current_seq,
            "executionSeq": w._execution_seq or None,
            "taskId": w._current_task_id,
            "event": event,
        })

    # Base persistence has succeeded.  Publish terminal state without waiting
    # for usage enrichment, then preserve the existing report delivery path.
    await _publish_terminal_events(w, terminal, s)
    # 订阅制报告：完成 → 若被订阅则 append 到 manager 的落盘队列（立项 4.3）
    await _enqueue_report(w.session_id, terminal["status"], terminal["result"],
                          terminal["taskId"], w.worker_id,
                          admission=terminal.get("autoReportAdmission"),
                          diagnostic=_error_report_diagnostic(
                              w, terminal["status"], terminal.get("taskSeq")))
    _finish_terminal_bookkeeping(w, terminal)


# ── lifecycle ──

# spawn 防重复（立项 4.5）：同一 session 的并发 create_worker 通过 per-session
# lock 串行化，避免竞态双 spawn。dict 的 setdefault 是纯同步原子操作（无 await
# 临界区），事件循环内天然安全，不需要额外 guard lock。
_spawn_locks: dict[str, asyncio.Lock] = {}


async def _session_spawn_lock(session_id: str) -> asyncio.Lock:
    """获取该 session 的 spawn 锁（并发 create_worker 串行化）。"""
    return _spawn_locks.setdefault(session_id, asyncio.Lock())


def _recover_pending_signals(w: Worker, s) -> bool:
    """spawn/重启后恢复消费：把落盘 queue_pending 的积压项转成唤醒信号。

    L4 落盘：任务与报告都持久化在 Session.queue_pending（落盘真源）。Worker
    死亡/回收后，未完成 hand-off 的旧状态迁回 queued，再补发一个无正文信号；
    pending_signal 只负责唤醒，不携带消息正文或消费决定。

    调用时机：create_worker 在 system_prompt 注入**之前**（避免对注入任务重复
    发信号）、_restart_tasks 重建 consumer 后。
    """
    migrated = _migrate_queue_delivery_state(s, restore_ledger=True)
    pending = s.queue_pending
    if not pending:
        return migrated
    if not getattr(s, "queue_paused", False) and any(isinstance(item, dict) and _queue_item_kind(item) is not None
           and _delivery_state(item) == _DELIVERY_QUEUED
           and not _queue_item_locked(item)
           for item in pending):
        w.pending_signal.put_nowait({"type": "queue_signal"})
    _schedule_queue_retry(s.id)
    return migrated


async def create_worker(session_id: str) -> Worker | str:
    """Spawn a CLI process for the given Session UUID.

    Returns Worker on success, error string on failure.

    防重复 spawn（立项 4.5）：同一 session 的并发调用由 per-session lock 串行化；
    已有活 worker 时直接复用现有 Worker，不重复创建（任何 session 不应被重复
    spawn worker）。显式重启场景（/api/spawn 等）会先 kill 再进入本函数，不受影响。

    Three execution modes (see resolve_execution_mode):
    - Stream mode (default, no MCP): long-running process with --input-format stream-json.
    - Stream + MCP mode: long-running process spawned with --mcp-config
      (adapter_config.output_mode == "stream", requires cbc >= 2.137.0).
    - One-shot MCP mode: no long-running process. Each task spawns a one-shot
      cbc process. Used when MCP configured and output_mode unset/oneshot.
    """
    if _shutdown_started:
        return "Pan main service is shutting down"
    lock = await _session_spawn_lock(session_id)
    async with lock:
        if _shutdown_started:
            return "Pan main service is shutting down"
        return await _create_worker(session_id)


async def restart_or_start_worker(session_id: str, *, expected_worker_id: str | None = None,
                                  expected_generation: int | None = None,
                                  allow_illegal_recovery: bool = True) -> Worker | str:
    """Atomically restart a live session worker, or create one if absent.

    The session lock covers the liveness decision and lifecycle transition, so
    a destroy/recovery race cannot turn two near-simultaneous clicks into two
    workers.  ``_create_worker`` is used under the lock to avoid re-entering
    ``create_worker`` and deadlocking.
    """
    if _shutdown_started:
        return "Pan main service is shutting down"
    if _get_session_shallow(session_id) is None:
        return f"Session {session_id} not found"
    recovery_pending = session_id in _recovery_required
    requested = find_worker_by_session(session_id)
    requested_generation = requested.generation if requested else None
    lock = await _session_spawn_lock(session_id)
    async with lock:
        if _shutdown_started:
            return "Pan main service is shutting down"
        if not allow_illegal_recovery:
            target = _get_session_shallow(session_id)
            await _prepare_offline_recovery(target)
            if _offline.is_illegal(target):
                return "illegal_queue_hold"
        alive = _find_control_worker(session_id)
        if alive is not None and requested is alive and alive.generation != requested_generation:
            return alive  # A concurrent click already supplied this generation.
        if expected_generation is not None and (
                alive is None or alive.worker_id != expected_worker_id
                or alive.generation != expected_generation):
            # An immediate/delayed restart already supplied another generation.
            # Never interrupt that replacement again for a stale own-queue call.
            return alive if alive is not None else "Worker identity changed"
        _cancel_session_restart(session_id)
        if alive is not None:
            if recovery_pending and session_id not in _recovery_required:
                # A watchdog/lifecycle recovery won the lock and already
                # supplied the replacement.  Do not immediately respawn it
                # again merely because the user clicked Restart meanwhile.
                return alive
            try:
                error = await _restart_worker_unlocked(alive.worker_id)
            except (RuntimeError, OSError) as exc:
                return await _control_stop_failed(alive, exc)
            if error:
                return error
            _recovery_required.discard(session_id)
            return alive
        try:
            result = await _create_worker(session_id)
        except (RuntimeError, OSError) as exc:
            return f"Worker start refused: {exc}"
        if isinstance(result, Worker):
            _recovery_required.discard(session_id)
        return result


def _write_system_prompt_file(s, prompt: str) -> str:
    """Write one fresh-session prompt under that session's checkout/workdir."""
    workdir = Path(s.workdir or os.getcwd()).resolve()
    directory = workdir / ".pan" / "system-prompts"
    directory.mkdir(parents=True, exist_ok=True)
    safe_session_id = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in str(s.id)
    )[:80] or "session"
    fd, raw_path = tempfile.mkstemp(
        prefix=f"{safe_session_id}-", suffix=".txt", dir=str(directory), text=False,
    )
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(prompt)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return str(path)


def _cleanup_system_prompt_file(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        _log.warning("failed to remove temporary system prompt file: %s", path)


def _spawn_system_prompt_args(
    adapter, s, mcp_on: bool, prompt_file_sink: list[str] | None = None,
) -> list[str] | None:
    """stream spawn 的 --system-prompt 注入决策（_create_worker 用）。

    返回传给 ``_spawn_process`` 的 extra_args（含该 flag）或 None：

    - 有 system_prompt 且是全新会话（无 cli_session_id）时，优先考虑 CLI 级注入。
      这能避免把人设作为首条 user 消息发送（尤其是 MCP 场景的 roleplay trap）。
    - 仅当 adapter 声明 ``supports_spawn_system_prompt``（可选能力，getattr
      探测，缺省 False）：cbc CLI 原生支持；kimi 由 wrapper 转为其 CLI 原生
      --agent-file；codex 由 wrapper 转为 developer_instructions。不支持的 adapter 强传会让子进程 argparse 报
      ``unrecognized arguments`` 直接 exit 2 —— 会话永不回复
      （SMA(NoAdapter)+kimi 卡死根因），此时返回 None，由 _create_worker
      退回首条消息注入。
    """
    if not (s.system_prompt and not s.cli_session_id):
        return None
    if getattr(adapter, "supports_spawn_system_prompt", False):
        if getattr(adapter, "supports_spawn_system_prompt_file", False) and prompt_file_sink is not None:
            path = _write_system_prompt_file(s, s.system_prompt)
            prompt_file_sink.append(path)
            return ["--system-prompt-file", path]
        # Adapters without a file-aware wrapper must fall back to the existing
        # first-message stdin injection for long prompts.  In particular this
        # covers native cbc/claude CLIs: passing the body as --system-prompt
        # would put it back across the Windows CreateProcess argv boundary.
        if len(s.system_prompt) > SYSTEM_PROMPT_ARG_MAX_CHARS:
            return None
        return ["--system-prompt", s.system_prompt]
    return None


async def _apply_pending_native_configuration(s) -> None:
    """Commit a Session upgrade/downgrade only after the old runtime has stopped.

    Pending intent survives a Pan restart. Preserve thread/history/queue and never
    mutate the running adapter's transport configuration mid-turn.
    """
    desired = s.adapter_config.get("native_control_next")
    if s.adapter not in {"codex", "cbc"} or type(desired) is not bool:
        return
    before = dict(s.adapter_config)
    if desired:
        s.adapter_config["native_control"] = True
        s.adapter_config["output_mode"] = "stream"
    else:
        s.adapter_config.pop("native_control", None)
    s.adapter_config.pop("native_control_next", None)
    try:
        await _sess.save_async(s)
    except BaseException:
        s.adapter_config.clear()
        s.adapter_config.update(before)
        raise
    await _bcast({"type": "session.updated", "sessionId": s.id})


@_admit_plugin_startup
async def _create_worker(session_id: str) -> Worker | str:
    """create_worker 的锁内实现（勿直接调用）。"""
    s = _sess.get(session_id)
    if not s:
        return f"Session {session_id} not found"

    # 防重复 spawn（立项 4.5）：已有活 worker 直接复用，不重复建。
    alive = find_alive_worker_by_session(session_id)
    if alive is not None:
        _log.info(
            "[Worker %s] create_worker dedup: session=%s already has live worker, reusing",
            alive.worker_id, session_id,
        )
        return alive

    await _prepare_offline_recovery(s)
    old = find_worker_by_session(session_id)
    if old:
        # 有注册但进程已死的 worker（_read_stdout 尚未 pop）→ 清理后重建。
        # Deliberately do NOT clear cli_session_id: resuming the cbc JSONL is
        # the intended context-continuity mechanism, and clearing it here would
        # force a cold start on every restart (#11, resolved by design).
        stop_error = await _kill_worker_unlocked(old.worker_id)
        if type(s.adapter_config.get("native_control_next")) is bool and (stop_error or not _runtime_stopped(old)):
            workers[old.worker_id] = old
            old._consume_task = None  # Retained owner, never a second queue writer.
            old.status = "error"
            _register_worker(old)
            return "Old Worker runtime did not stop; Session conversion retained"

    await _apply_pending_native_configuration(s)
    try:
        adapter = get_adapter(s.adapter)
        if s.adapter == "cbc" and s.adapter_config.get("native_control") is True:
            from .adapters.cbc.native_adapter import CbcNativeAdapter
            adapter = CbcNativeAdapter()
    except KeyError:
        # A legacy/corrupt session may outlive the adapter that created it.
        # Surface an actionable worker error instead of allowing a raw
        # registry KeyError to escape from spawn/respawn paths.
        from .adapters.registry import list_adapters
        available = ", ".join(a.name for a in list_adapters())
        return (
            f"Unknown adapter {s.adapter!r} for session {s.name!r}. "
            f"Available adapters: {available}"
        )
    worker_id = await _next_worker_id()

    mcp_on = _mcp_configured(s)
    mode = resolve_execution_mode(adapter, s)
    prompt_files: list[str] = []

    if mode == "oneshot":
        # One-shot mode: no long-running process, consumer spawns per-task
        # (no stdin). See docs/design/adapter-p1-oneshot.md.
        from packages.plugins.lifecycle import capture_async
        await capture_async(s)
        proc = None
        spawn_injected = False
    else:
        # Stream mode: spawn long-running process.
        # If MCP is configured (mcp_on, output_mode="stream"), the process is
        # spawned with --mcp-config (build_spawn_args -> mcp_args) so the
        # long-running stream keeps MCP tools (cbc >= 2.137.0).
        from packages.plugins.lifecycle import io
        extra_args = await io(_spawn_system_prompt_args, adapter, s, mcp_on, prompt_files)
        spawn_injected = extra_args is not None
        proc = await _spawn_process(session_id, adapter=adapter, extra_args=extra_args)
        if isinstance(proc, str):
            for path in prompt_files:
                _cleanup_system_prompt_file(path)
            await _record_worker_start_failure(s, session_id, worker_id, proc)
            return proc

    w = Worker(worker_id=worker_id, session_id=session_id,
               adapter=adapter,
               native_control_enabled=s.adapter in {"codex", "cbc"} and s.adapter_config.get("native_control") is True,
               status="idle", process=proc, pending_signal=asyncio.Queue(),
               system_prompt_file=prompt_files[0] if prompt_files else None,
               _task_done=asyncio.Event(),
               _hist_flush_event=asyncio.Event(),
               generation=_next_worker_generation(session_id),
               codex_profile_key=(
                   _codex_quota_store.resolve_profile_identity().profile_key
                   if getattr(adapter, "name", "") == "codex" else None
               ))
    w.last_activity = time.monotonic()
    workers[worker_id] = w
    _register_worker(w)

    w._offline_predecessors = set(s.illegal_batches)
    try:
        await _claim_offline_owner(w)
    except BaseException:
        await _kill_process_tree(w)
        workers.pop(worker_id, None)
        _unregister_worker(w)
        raise
    if mode != "oneshot":
        w._stdout_task = asyncio.create_task(_read_stdout(w))
    # watchdog 两种模式都启用：stream 做超时+空闲回收，MCP 只做空闲回收
    if not _DEFAULTS_INITIALIZED:
        load_worker_config()
    w._watchdog_task = asyncio.create_task(_watchdog(w))

    # Spawn/start succeeded: this is the first legal lifecycle fact for this
    # Worker.  A later EOF/zombie path must not replace it.
    await _record_legal_worker_state(w, "idle", "create/start")

    await _bcast({
        "type": "worker.spawned",
        "workerId": worker_id,
        "sessionId": session_id,
        "generation": w.generation,
        "name": s.name,
        "status": w.status,
        "model": s.model or adapter.default_model,
    })

    await _sess.save_async(s)

    # L4 落盘恢复：把本次 spawn 前尚未完成 hand-off 的 queue_pending 项转成
    # 唤醒信号；旧版本遗留的 in-flight/reserved/writing 项先迁回 queued，按
    # 受控退避重试，不会被静默丢弃。
    # 必须放在 system_prompt 注入**之前**：注入会往 queue_pending 追加新 task 并
    # 自带 wake-up，先做恢复可避免对同一 item 重复发信号。
    queue_migrated = _recover_pending_signals(w, s)
    if queue_migrated:
        # Persist the compatibility migration before the consumer can execute
        # it.  Otherwise another crash would classify the same user message as
        # a report again on the following start.
        await _save_receipt(s)
    # Queue signals are intentionally buffered while the consumer is being
    # created.  Starting it after the migration save closes the old-data race:
    # a legacy row is never consumed from memory before its new envelope is
    # durable.
    w._consume_task = asyncio.create_task(_consumer(w))

    # Inject system_prompt
    # - Pure stream (no MCP): injected as a separate first message (existing).
    # - With MCP (one-shot or stream+MCP with adapter support): skipped here —
    #   injected via --system-prompt at spawn / in _consumer_oneshot, because a
    #   separate first message biases the LLM into pure roleplay and prevents it
    #   from discovering MCP tools via ToolSearch.
    # - stream+MCP 但 adapter 不支持 spawn 注入（supports_spawn_system_prompt
    #   为 False，见上方 spawn 块注释）：退化为首条消息注入——功能可用优先于
    #   roleplay 风险。
    # - 注入去重（fork/takeover 修复）：只对「全新会话」（尚无 cli_session_id）
    #   首次 spawn 注入。cli_session_id 已存在 = 会话已 resume/fork，system_prompt
    #   已由 cbc JSONL（模型侧上下文）承载，再以消息注入会把 system_prompt 当作
    #   一条 user 消息塞进对话——表现为 fork 首句话前 / takeover 恢复后重复出现
    #   系统提示词。与 stream 路径的 `not s.cli_session_id` 守卫保持一致。
    # - oneshot 模式：system_prompt 由 CbcAdapter.oneshot_args 在每轮任务里
    #   以 --system-prompt 注入（仅首条），此处不再注入，避免重复。
    if s.system_prompt and not s.cli_session_id:
        if mode == "oneshot":
            _log.info("[Worker %s] oneshot mode: system_prompt 由 oneshot_args 逐任务注入", worker_id)
        elif not spawn_injected and not any(
                isinstance(item, dict) and item.get("type") == "task"
                and item.get("source") == "system_prompt"
                and item.get("text") == s.system_prompt
                for item in s.queue_pending):
            _log.info("[Worker %s] injecting system_prompt (%d chars)", worker_id, len(s.system_prompt))
            await send_task(worker_id, s.system_prompt, source="system_prompt")
        elif spawn_injected:
            _log.info("[Worker %s] stream mode: system_prompt injected via --system-prompt", worker_id)
        else:
            _log.info("[Worker %s] recovering queued system_prompt without reinjection", worker_id)

    return w


async def reserve_takeover_owner(w: Worker) -> None:
    """External TUI has no turn/admission channel. Reserve before opening it.

    A lost service cannot prove all detached descendants died merely because
    the terminal PID vanished. Only confirmed retirement clears this marker.
    """
    s = _session(w)
    if s is None:
        raise RuntimeError("Session disappeared before takeover")
    identity = {**_offline.owner(w), "externalTakeover": True, "ownerId": uuid.uuid4().hex,
                "runtimeExpected": True, "ownershipUnknown": True, "processes": []}
    async with queue_lock(w.session_id):
        def reserve(candidate):
            if (workers.get(w.worker_id) is not w or w.offline_token != identity["token"]
                    or w.generation != identity["generation"]):
                raise RuntimeError("Takeover runtime changed before admission")
            candidate.offline_owner = identity
        await _commit_offline(s, reserve)


async def _kill_takeover_terminal(w: Worker) -> bool:
    """杀掉 takeover 模式打开的终端及子进程树。异步版，不阻塞事件循环。"""
    if w.takeover_job is not None:
        job = w.takeover_job
        if not hasattr(job, "terminate_and_verify"):
            await asyncio.to_thread(job.stop)
            raise RuntimeError("Takeover Job has no verifiable retirement proof")
        # Bound the control response even if a kernel/provider stop stalls.
        # Keep the single in-flight verifier: a retry must never close/reuse
        # its Job handle while the original thread still owns the call.
        if w._takeover_stop_task is None:
            w._takeover_stop_task = asyncio.create_task(asyncio.to_thread(job.terminate_and_verify))
        try:
            proof = await asyncio.wait_for(asyncio.shield(w._takeover_stop_task),
                                           timeout=_TAKEOVER_STOP_TIMEOUT)
        except TimeoutError as exc:
            if w._takeover_stop_task.done():
                w._takeover_stop_task = None
                raise
            raise RuntimeError(
                f"Takeover retirement timed out after {_TAKEOVER_STOP_TIMEOUT:g}s; verifier retained for retry"
            ) from exc
        except Exception:
            if w._takeover_stop_task.done():
                w._takeover_stop_task = None
            raise
        s = _session(w)
        if s is not None:
            captured = copy.deepcopy(s.offline_owner)
            prior_key = (captured or {}).get("previousOwnerRetirement")
            prior = s.owner_retirements.get(prior_key, {})
            if prior.get("proof", {}).get("jobId") == proof["jobId"]:
                pass  # Persistence succeeded earlier; retry only handle release.
            elif not captured or not captured.get("externalTakeover"):
                raise RuntimeError("Takeover owner scope is unconfirmed; retaining Job handle")
            else:
                async with queue_lock(s.id):
                    def retire(candidate):
                        if candidate.offline_owner != captured or w.takeover_job is not job:
                            raise RuntimeError("Takeover owner changed during retirement")
                        key = captured.get("ownerId") or f"{captured['token']}:{captured['generation']}"
                        candidate.owner_retirements[key] = {"owner": captured, "proof": proof}
                        _offline.retire_owner(candidate, captured, reason="takeover/stopped", proof=proof)
                        # A failed Kill may already have classified this external
                        # operation Illegal. Whole-Job proof now retires it, while
                        # its queue incident stays locked until successor readiness.
                        for op in _offline.outstanding(candidate, captured):
                            if op.get("kind") == "external-takeover":
                                op["retirement"] = {"safe": True, "proof": proof,
                                                    "reason": "takeover/stopped"}
                        _offline.aggregate(candidate)
                        candidate.offline_owner = {**_offline.owner(w), "previousOwnerRetirement": key}
                    await _commit_offline(s, retire)
        # Preserve both the Job and its verifiable empty state until the
        # ordered metadata replace succeeds. A failed write is retryable.
        await asyncio.to_thread(job.close_retired)
        w.takeover_job = None
        w._takeover_stop_task = None
        w.takeover_pid = None
        return True
    if not w.takeover_pid:
        s = _session(w)
        if s is not None and (s.offline_owner or {}).get("externalTakeover"):
            raise RuntimeError("Takeover owner is unknown; refusing a second writer")
        return False
    pid = w.takeover_pid
    # The generic kill helper logs errors for legacy callers. Its return alone
    # is not proof of retirement; retain the owner unless every observed PID
    # identity is confirmed dead. A vanished root cannot prove orphan death.
    try:
        root = psutil.Process(pid)
        identities = [{"pid": p.pid, "createdAt": p.create_time()}
                      for p in [root, *root.children(recursive=True)]]
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        raise RuntimeError("Takeover owner is unknown; refusing a second writer") from exc
    _log.info("[Worker %s] 杀 takeover 终端 PID=%s", w.worker_id, pid)
    try:
        await asyncio.to_thread(_kill_pid_tree, pid)
        _log.info("[Worker %s] takeover 终端已结束 PID=%s", w.worker_id, pid)
    except Exception as e:
        _log.warning("[Worker %s] 杀 takeover 终端异常: %s", w.worker_id, e)
        raise RuntimeError("Takeover retirement could not be confirmed") from e
    status = await asyncio.to_thread(_offline.previous_owner_status, {"processes": identities})
    if status != "dead":
        raise RuntimeError(f"Takeover owner is {status}; refusing a second writer")
    s = _session(w)
    if s is not None and (s.offline_owner or {}).get("externalTakeover"):
        raise RuntimeError("Takeover descendant coverage is unknown without a Job retirement proof")
    w.takeover_pid = None
    await _claim_offline_owner(w)
    return True


_TAKEOVER_STOP_TIMEOUT = 8.0  # Job verification itself normally finishes within 5s.


def _find_control_worker(session_id: str) -> Worker | None:
    """Control retires registered ownership, even without a reusable consumer.

    Queue dispatch deliberately continues using find_alive_worker_by_session.
    A dead/failed consumer never makes its process or external owner disappear.
    """
    return find_worker_by_session(session_id)


async def _stop_takeover_for_control(w: Worker) -> str | None:
    try:
        await _kill_takeover_terminal(w)
        return None
    except Exception as exc:
        return await _control_stop_failed(w, exc)


async def _control_stop_failed(w: Worker, exc: Exception) -> str:
    # Never report success or leave the UI held after an unconfirmed stop.
    # Preserve the exact owner and retained Job for a safe retry.
    w.status = "error"
    _log.error("[Worker %s] control stop failed; retaining owner", w.worker_id,
               exc_info=(type(exc), exc, exc.__traceback__))
    error = f"Worker/Takeover stop failed; owner retained, retry Kill: {exc}"
    s = _session(w)
    if s is not None:
        try:
            async with queue_lock(s.id):
                def failed(candidate):
                    identity = candidate.offline_owner
                    if not identity:
                        return
                    if not _offline.outstanding(candidate, identity):
                        _offline.admit(candidate, identity,
                            kind="external-takeover" if identity.get("externalTakeover") else "control-stop",
                            operation_id=identity.get("ownerId") or f"{identity['token']}:{identity['generation']}:stop")
                    _offline.detect(candidate, "takeover/stop-failed", identity=identity,
                        eligible=lambda item: _delivery_state(item) == _DELIVERY_QUEUED
                            and not _queue_item_locked(item),
                        remember=lambda item: _remember_offline_lock(candidate, item))
                await _commit_offline(s, failed)
            await _offline_snapshot_event(s)
        except Exception as persistence_error:
            error += f"; Illegal Offline persistence failed (retry required): {persistence_error}"
    await _bcast({"type": "worker.status", "workerId": w.worker_id,
                  "sessionId": w.session_id, "generation": w.generation,
                  "status": "error", "error": error})
    return error


def _confirm_native_input_retirement(w: Worker) -> bool:
    if not w.native_input_uncertain:
        return True
    if not _runtime_stopped(w):
        w.status = "error"
        return False
    w.native_input_uncertain = False
    return True


async def _cancel_worker_task(task: asyncio.Task | None) -> None:
    """Cancel a worker task and wait until its finally blocks have run.

    ``Task.cancel()`` only schedules cancellation.  Restarting a worker before
    the old consumer has unwound races with queue recovery: the old consumer
    can still own the in-flight task id while the replacement tries to claim
    the same persisted item.
    """
    if task is None or task is asyncio.current_task() or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _stop_worker_tasks(w: Worker, *, include_watchdog: bool = True) -> None:
    """Stop the current generation in a deterministic order."""
    if include_watchdog:
        await _cancel_worker_task(w._watchdog_task)
    await _cancel_worker_task(w._consume_task)
    await _cancel_worker_task(w._stdout_task)
    w._watchdog_task = None
    w._consume_task = None
    w._stdout_task = None


async def _kill_process_tree(w: Worker) -> None:
    """杀 worker 的 CLI 子进程树。异步版，不阻塞事件循环。"""
    async def wait_for_exit(process, label: str) -> None:
        wait = getattr(process, "wait", None)
        if not callable(wait):
            return
        try:
            await asyncio.wait_for(wait(), timeout=5)
        except asyncio.TimeoutError:
            _log.warning("[Worker %s] %s did not exit after kill; forcing", w.worker_id, label)
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                return
            try:
                await asyncio.wait_for(wait(), timeout=2)
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                _log.warning("[Worker %s] %s still has not exited", w.worker_id, label)

    # Stream mode: w.process is the long-running cbc
    if w.process:
        process = w.process
        pid = w.process.pid
        try:
            await asyncio.to_thread(_kill_pid_tree, pid)
        except Exception:
            try:
                process.kill()
            except (ProcessLookupError, Exception):
                pass
        await wait_for_exit(process, "CLI process")

    # One-shot mode: w._mcp_proc is the in-flight short-lived provider process
    # (may be None).
    if w._mcp_proc:
        process = w._mcp_proc
        mpid = w._mcp_proc.pid
        try:
            await asyncio.to_thread(_kill_pid_tree, mpid)
        except Exception:
            try:
                process.kill()
            except (ProcessLookupError, Exception):
                pass
        await wait_for_exit(process, "MCP process")
    _cleanup_system_prompt_file(w.system_prompt_file)
    w.system_prompt_file = None


async def _takeover_worker_unlocked(worker_id: str) -> str | None:
    """Stop Pan's runtime and hold the Worker for an interactive takeover.

    Takeover launches a separate native CLI TUI with the same session/thread.
    It must not call ``restart_worker``: that would create a replacement
    app-server immediately before the TUI resumes the same Codex thread,
    producing the native ``already has an active writer`` error.
    """
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    if _shutdown_started:
        return "Pan main service is shutting down"
    if w.status == "held":
        return "Worker already in takeover mode"
    retiring_owner = _offline.owner(w)

    _cancel_pending_restart(w)
    _cancel_claude_permission_requests(worker_id, "Claude worker entered takeover mode")

    current = asyncio.current_task()
    if w._watchdog_task and w._watchdog_task is not current:
        await _cancel_worker_task(w._watchdog_task)
    if w._consume_task:
        await _cancel_worker_task(w._consume_task)
    if w._stdout_task:
        await _cancel_worker_task(w._stdout_task)

    clear_native_runtime_state(w)
    await _kill_takeover_terminal(w)
    await _kill_process_tree(w)
    if not _runtime_stopped(w):
        return await _control_stop_failed(w, RuntimeError("Runtime stop was not confirmed (timed out); takeover refused"))
    if not _confirm_native_input_retirement(w):
        return "Old runtime stop was not confirmed; input uncertainty retained"
    if _runtime_stopped(w):
        await _record_legal_worker_state(w, "held", "pan/takeover")
    w.process = None
    w._mcp_proc = None
    if w._hist_dirty:
        await _flush_history_now(w)
    await _finish_offline_stop(w, retiring_owner, "pan/takeover")
    w.status = "held"
    return None


def _user_kill_report_enabled() -> bool:
    """用户主动 Kill 是否向 manager 报告 error（App Settings → Behavior）。

    config.json:
        "ui": { "reportWorkerKillErrors": false }

    缺省该键（旧配置）→ 视为关闭（默认 false）。该开关只抑制用户显式
    Kill（``report_abnormal=True``）触发的 zombie/error 报告；异常死亡与
    watchdog 报告不经此函数，始终按原语义上报。
    """
    ui = load_config().get("ui") or {}
    return bool(ui.get("reportWorkerKillErrors", False))


async def _kill_worker_unlocked(
    worker_id: str, *, recover: bool = False,
    report_abnormal: bool = False,
    preserve_restart_intent: bool = False,
) -> str | None:
    """Kill the Worker process. Does NOT touch the Session.

    ``report_abnormal`` is reserved for an explicit user kill request.  The
    watchdog and internal restart paths already have their own lifecycle
    semantics (including watchdog zombie reporting), so they leave it false.
    The report must be queued before cancelling the consumer: cancellation
    unwinds the in-flight queue unit and clears ``_current_task_id``.
    """
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    if not preserve_restart_intent:
        _cancel_pending_restart(w)

    error = await _stop_takeover_for_control(w)
    if error:
        return error

    abnormal = w.status in {"running", "queued"}
    retiring_owner = _offline.owner(w)
    if recover and abnormal:
        await _detect_offline_exit(w, retiring_owner, "watchdog/aborted")

    if report_abnormal and abnormal and _user_kill_report_enabled():
        await _enqueue_zombie_report(w, "worker killed")

    _cancel_claude_permission_requests(worker_id, "Claude worker was stopped")

    # 若 kill_worker 由该 worker 自己的 watchdog 触发，不能 cancel 当前任务
    # （否则 kill 流程刚 cancel 就收到 CancelledError 被中断，进程杀不掉、
    #  worker 也不 pop）——让 watchdog 自然 return 即可。
    current = asyncio.current_task()
    if w._watchdog_task and w._watchdog_task is not current:
        _log.info("[Worker %s] kill_worker: cancelling watchdog task", worker_id)
        await _cancel_worker_task(w._watchdog_task)
    elif w._watchdog_task is current:
        _log.info(
            "[Worker %s] kill_worker: skip watchdog self-cancel "
            "(kill triggered by watchdog itself)",
            worker_id,
        )
    if w._consume_task:
        await _cancel_worker_task(w._consume_task)
    if w._stdout_task:
        await _cancel_worker_task(w._stdout_task)
    if w._interrupt_guard_task and w._interrupt_guard_task is not asyncio.current_task():
        await _cancel_worker_task(w._interrupt_guard_task)
    await _stop_worker_tasks(w)
    await _kill_process_tree(w)
    await _kill_takeover_terminal(w)

    if not _runtime_stopped(w):
        return await _control_stop_failed(w, RuntimeError("Runtime stop was not confirmed (timed out); process is still alive"))

    if _runtime_stopped(w):
        await _record_legal_worker_state(w, "offline", "pan/kill")

    # H2: worker 被杀 → 名下 pending 的 taskId 标 error（防止幂等重试永久卡 pending）
    _mark_worker_tasks_error(worker_id, "worker killed")
    s = _session(w)
    if s and _clear_active_task_if_not_pending(s):
        try:
            await _sess.save_async(s)
        except Exception as exc:
            _log.warning("[Worker %s] failed to persist kill task context clear: %s",
                         worker_id, exc)
            raise
    # A1 崩溃安全：kill 前 flush 防抖缓冲的流式块
    if w._hist_dirty:
        await _flush_history_now(w)

    if not recover:
        await _finish_offline_stop(w, retiring_owner, "pan/stop")
    workers.pop(w.worker_id, None)
    _unregister_worker(w)
    await _bcast({
        "type": "worker.destroyed",
        "workerId": worker_id,
        "sessionId": w.session_id,
        "generation": w.generation,
    })
    # A dead worker with an active turn or durable queue must be replaced
    # promptly.  Idle workers with no queued work remain normally reclaimable.
    has_pending = _has_dispatchable_items(_session(w))
    if (recover and abnormal) or has_pending:
        _schedule_session_recovery(w.session_id, force=recover and abnormal)
    return None


async def kill_worker(worker_id: str, *, recover: bool = False,
                      report_abnormal: bool = False) -> str | None:
    """Serialized compatibility wrapper for worker-id callers."""
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    generation = w.generation
    _cancel_session_restart(w.session_id)
    lock = await _session_spawn_lock(w.session_id)
    async with lock:
        current = workers.get(worker_id)
        if current is not w or w.generation != generation:
            # A concurrent restart already owns this lifecycle transition.
            return "Worker identity changed during Kill; retry against the current worker"
        try:
            return await _kill_worker_unlocked(
                worker_id, recover=recover, report_abnormal=report_abnormal,
            )
        except (RuntimeError, OSError) as exc:
            return await _control_stop_failed(w, exc)


async def kill_session_worker(session_id: str, *,
                              report_abnormal: bool = False) -> Worker | str | None:
    """Kill the live worker for a session, if present."""
    if _get_session_shallow(session_id) is None:
        return f"Session {session_id} not found"
    _cancel_session_restart(session_id)
    lock = await _session_spawn_lock(session_id)
    async with lock:
        w = _find_control_worker(session_id)
        if w is None:
            s = _get_session_shallow(session_id)
            identity = (s.offline_owner or {}) if s else {}
            if identity.get("externalTakeover"):
                # After host loss there is no retained Job handle. Reconstruct
                # only the control identity to diagnose the incident, never a
                # provider/runtime or permission to start a second writer.
                retained = Worker(worker_id=identity["workerId"], session_id=session_id,
                                  adapter=get_adapter(s.adapter), status="error",
                                  offline_token=identity["token"], generation=identity["generation"])
                return await _control_stop_failed(retained, RuntimeError(
                    "External takeover owner is unknown after host loss; verify its process tree before recovery"))
            return None
        try:
            error = await _kill_worker_unlocked(
                w.worker_id, report_abnormal=report_abnormal,
            )
        except (RuntimeError, OSError) as exc:
            return await _control_stop_failed(w, exc)
        return error or w


async def takeover_worker(worker_id: str) -> str | None:
    """Serialized compatibility wrapper for worker-id callers."""
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    generation = w.generation
    _cancel_session_restart(w.session_id)
    lock = await _session_spawn_lock(w.session_id)
    async with lock:
        current = workers.get(worker_id)
        if current is not w or w.generation != generation:
            return None
        try:
            return await _takeover_worker_unlocked(worker_id)
        except (RuntimeError, OSError) as exc:
            return await _control_stop_failed(w, exc)


async def takeover_session_worker(session_id: str) -> Worker | str | None:
    """Put the live worker for a session into takeover mode, if present."""
    if _get_session_shallow(session_id) is None:
        return f"Session {session_id} not found"
    _cancel_session_restart(session_id)
    lock = await _session_spawn_lock(session_id)
    async with lock:
        w = _find_control_worker(session_id)
        if w is None:
            return None
        try:
            error = await _takeover_worker_unlocked(w.worker_id)
        except (RuntimeError, OSError) as exc:
            return await _control_stop_failed(w, exc)
        return error or w


async def cleanup_worker_background(worker_id: str, session_id: str):
    """Background worker cleanup — always cleans up and broadcasts, even on failure.

    Call via ``asyncio.create_task()`` from delete-session flows to avoid
    blocking the HTTP response on process termination.
    """
    w = workers.get(worker_id)
    if not w:
        return
    _cancel_pending_restart(w)
    try:
        if not w:
            return
        _cancel_claude_permission_requests(worker_id, "Claude worker was cleaned up")
        if w._watchdog_task:
            await _cancel_worker_task(w._watchdog_task)
        if w._consume_task:
            await _cancel_worker_task(w._consume_task)
        if w._stdout_task:
            await _cancel_worker_task(w._stdout_task)
        if w._interrupt_guard_task:
            await _cancel_worker_task(w._interrupt_guard_task)
        await _stop_worker_tasks(w)
        await _kill_process_tree(w)
        await _kill_takeover_terminal(w)
        # A1 崩溃安全：清理前 flush 防抖缓冲的流式块
        if w._hist_dirty:
            await _flush_history_now(w)
    except Exception as exc:
        _log.warning("[Worker %s] BG cleanup error: %r", worker_id, exc)
    finally:
        workers.pop(worker_id, None)
        _unregister_worker(w)
        # H2: worker 回收 → 名下 pending 的 taskId 标 error（与 kill_worker 一致）
        _mark_worker_tasks_error(worker_id, "worker cleanup")
        s = _session(w)
        if s and _clear_active_task_if_not_pending(s):
            try:
                await _sess.save_async(s)
            except Exception as exc:
                _log.warning("[Worker %s] failed to persist cleanup task context clear: %s",
                             worker_id, exc)
        try:
            await _bcast({
                "type": "worker.destroyed",
                "workerId": worker_id,
                "sessionId": session_id,
                "generation": w.generation,
            })
        except Exception as bcast_err:
            _log.warning("[Worker %s] BG cleanup bcast error: %r", worker_id, bcast_err)


def _maybe_restart_pending(w: Worker) -> None:
    """Linearize idle retirement BEFORE scheduling or waking the next FIFO unit.

    A temporary idle during terminal persistence/publication is not an idle
    boundary. A handed-off queue unit must finish its report/finally cleanup.
    Preparation before running is cancellable: lifecycle stop awaits its
    reservation unwind, rather than waiting for memory/network preparation.
    No lock or await is needed for this event-loop-local decision.
    """
    if (not w.pending_restart or w.status not in {"idle", "queued"}
            or w._native_reservation_busy or w._native_external_turn_active
            or (w._queue_unit_active and w._current_handoff_acked)
            or w._terminal_finishing or workers.get(w.worker_id) is not w):
        return
    intent = _delayed_restarts.get(w.session_id)
    if (intent is None or intent["status"] != "pending"
            or intent["workerId"] != w.worker_id
            or intent["generation"] != w.generation):
        return
    # The old consumer can no longer reserve or hand off another queue item.
    w.status = "restarting"
    _set_restart_intent(intent, "restarting")
    asyncio.create_task(_respawn_worker(w, intent))


def delayed_restart_state(session_id: str) -> dict | None:
    intent = _delayed_restarts.get(session_id)
    return dict(intent) if intent else None


def _set_restart_intent(intent: dict, status: str, error: str | None = None) -> None:
    global _delayed_restart_revision
    _delayed_restart_revision += 1
    intent.update(status=status, error=error, revision=_delayed_restart_revision)
    # Snapshot now: a later transition must not mutate an already queued event.
    asyncio.create_task(_bcast({"type": "worker.delayed_restart",
                               "sessionId": intent["sessionId"],
                               "delayedRestart": dict(intent)}))


def _abort_delayed_restart(w: Worker, boundary: str, exc: BaseException) -> None:
    """Expose an unsafe completion boundary without scheduling a replacement.

    The original exception remains the caller's responsibility. Clearing the
    intent projection lets an immediate Restart recover this runtime; terminal
    persistence retains its existing rollback/dedup semantics.
    """
    intent = _delayed_restarts.get(w.session_id)
    if (intent and intent["workerId"] == w.worker_id
            and intent["generation"] == w.generation
            and intent["status"] in {"pending", "restarting"}):
        w.pending_restart = False
        w.status = "error"
        cancelled = isinstance(exc, asyncio.CancelledError)
        _set_restart_intent(intent, "cancelled" if cancelled else "failed",
                            f"{boundary}: {type(exc).__name__}: {exc}; use Restart to recover")


def _cancel_pending_restart(w: Worker) -> None:
    """Immediate lifecycle operations supersede this generation's intent."""
    w.pending_restart = False
    intent = _delayed_restarts.get(w.session_id)
    if (intent and intent["workerId"] == w.worker_id
            and intent["generation"] == w.generation
            and intent["status"] in {"pending", "restarting"}):
        _set_restart_intent(intent, "cancelled")


def _cancel_session_restart(session_id: str) -> None:
    """An immediate user lifecycle request wins even while spawn lock is held."""
    intent = _delayed_restarts.get(session_id)
    if intent and intent["status"] in {"pending", "restarting"}:
        w = find_worker_by_session(session_id)
        if w is not None:
            w.pending_restart = False
        _set_restart_intent(intent, "cancelled")


def request_delayed_restart(session_id: str) -> dict | str:
    """Merge settings/user intent; absent runtime follows Restart's start policy.

    Acceptance and generation capture are synchronous. Lifecycle I/O reuses
    the existing per-session spawn lock; no global/queue lock waits for exit.
    Intent is runtime-only and never migrates to a different Worker generation.
    """
    if _shutdown_started:
        return "Pan main service is shutting down"
    if _get_session_shallow(session_id) is None:
        return f"Session {session_id} not found"
    prior = _delayed_restarts.get(session_id)
    if prior and prior["status"] in {"pending", "restarting"}:
        owner = workers.get(prior["workerId"])
        if prior["status"] == "restarting" or (
                owner is not None and owner.generation == prior["generation"]):
            return dict(prior)
        _set_restart_intent(prior, "cancelled", "Worker generation was replaced")
    tracked = find_worker_by_session(session_id)
    if tracked is not None and tracked.status == "held":
        return "Worker is held (takeover mode); use Restart after finishing takeover"
    w = find_alive_worker_by_session(session_id)
    if w is not None and w.status not in {"idle", "running", "queued", "done", "error", "cancelled"}:
        return f"Worker is {w.status}; delayed restart requires an idle or active task runtime"
    if (w is not None and w.status in {"done", "error", "cancelled"}
            and not w._terminal_finishing and not w._queue_unit_active):
        return f"Worker is {w.status}; use Restart to recover the runtime"
    intent = {"sessionId": session_id, "workerId": w.worker_id if w else None,
              "generation": w.generation if w else None,
              "action": "restart" if w else "start"}
    _delayed_restarts[session_id] = intent
    _set_restart_intent(intent, "pending" if w else "restarting")
    if w:
        w.pending_restart = True
        _maybe_restart_pending(w)
    else:
        asyncio.create_task(_respawn_worker(None, intent))
    return dict(intent)


async def _respawn_worker(w: Worker | None, intent: dict) -> None:
    """Reuse settings respawn; fence the captured generation under its lock."""
    sid = intent["sessionId"]
    lock = await _session_spawn_lock(sid)
    try:
        async with lock:
            if _delayed_restarts.get(sid) is not intent or intent["status"] != "restarting":
                return
            if _shutdown_started:
                _set_restart_intent(intent, "cancelled", "Pan main service is shutting down")
                return
            if w is not None:
                if workers.get(w.worker_id) is not w or w.generation != intent["generation"]:
                    _set_restart_intent(intent, "cancelled", "Worker generation was replaced")
                    return
                error = await _kill_worker_unlocked(w.worker_id, preserve_restart_intent=True)
                if error:
                    raise RuntimeError(error)
                if not _runtime_stopped(w):
                    # The existing kill path has already removed its registry
                    # entry. Retain the fenced runtime so recovery cannot open
                    # a second native writer while the old process still lives.
                    workers[w.worker_id] = w
                    w._consume_task = None  # fenced live runtime, no queue writer
                    _register_worker(w)
                    raise RuntimeError("Old Worker runtime did not stop; replacement was not started")
            if intent["status"] != "restarting":
                return
            result = await _create_worker(sid)
            if isinstance(result, str):
                raise RuntimeError(result)
            if intent["status"] != "restarting":
                return
            intent["replacementWorkerId"] = result.worker_id
            intent["replacementGeneration"] = result.generation
            _set_restart_intent(intent, "completed")
    except asyncio.CancelledError:
        if intent["status"] == "restarting":
            _set_restart_intent(intent, "cancelled", "Restart coordinator cancelled")
        raise
    except Exception as exc:
        if w is not None:
            w.pending_restart = False
            if workers.get(w.worker_id) is w:
                w.status = "error"
        if intent["status"] == "restarting":
            _set_restart_intent(intent, "failed", str(exc))
        _log.exception("[Session %s] delayed restart failed", sid)


@_admit_plugin_startup
async def _spawn_process(session_id: str,
                         adapter: CliAdapter,
                         extra_args: list[str] | None = None
                         ) -> asyncio.subprocess.Process | str:
    s = _sess.get(session_id)
    if not s:
        return f"Session {session_id} not found"

    try:
        from packages.plugins.lifecycle import capture_async, io
        await capture_async(s)
        args = await io(adapter.build_spawn_args, s, extra_args)
        _log_mcp_launch(worker_id=s.worker_id if hasattr(s, "worker_id") else session_id,
                        session=s, args=args)
        return await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=s.workdir or None,
            **command_window_options(),
        )
    except FileNotFoundError as e:
        return format_cli_spawn_error(adapter.name, e)
    except ValueError as e:
        return f"MCP configuration error for {adapter.name}: {e}"
    except OSError as e:
        return format_cli_spawn_error(adapter.name, e)


async def _restart_tasks(w: Worker):
    # A1 崩溃安全：重启前先 flush 防抖缓冲的流式块（换进程后 resume 依赖已落盘内容）
    if w._hist_dirty:
        await _flush_history_now(w)
    w.pending_signal = asyncio.Queue()
    w._task_done = asyncio.Event()
    w._current_seq = None
    w._current_task_id = None
    w._current_task_idempotent = False
    w._current_queue_item = None
    w._claimed_queue_index = None
    w._current_report_items = []
    w._current_handoff_acked = False
    w._queue_unit_active = False
    w._terminal_finishing = False
    w._task_started_at = 0.0
    w.last_activity = time.monotonic()
    w._offline_ready_token = None
    w._native_admission_id = None
    s = _session(w)
    w._offline_predecessors = set(s.illegal_batches) if s else set()
    await _claim_offline_owner(w)
    if w.process is not None:
        w._stdout_task = asyncio.create_task(_read_stdout(w))
    if not _DEFAULTS_INITIALIZED:
        load_worker_config()
    w._watchdog_task = asyncio.create_task(_watchdog(w))
    # L4 落盘恢复：新 consumer 的信号队列是新建的，旧信号已随旧队列丢弃——
    # 重新为尚未接管的 queue_pending 积压发信号；已消费 item 不会重现。
    s = _session(w)
    if s and _has_pending_usage_enrichment(s):
        _schedule_usage_enrichment(s.id)
    if s and _recover_pending_signals(w, s):
        await _save_receipt(s)
    # Start only after recovery has migrated unfinished rows and queued the
    # generic wake-up signal.  The signal queue safely buffers that wake-up.
    w._consume_task = asyncio.create_task(_consumer(w))


async def _restart_worker_unlocked(worker_id: str) -> str | None:
    """Restart a Worker process. Preserves the session."""
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    _cancel_pending_restart(w)
    retiring_owner = _offline.owner(w)

    # Stop the takeover owner before changing generation/state or spawning.
    # A failed stop leaves a held worker retryable and cannot create a writer.
    error = await _stop_takeover_for_control(w)
    if error:
        return error

    _cancel_claude_permission_requests(worker_id, "Claude worker was restarted")

    # Block new work from the retiring generation.  send_task still persists a
    # concurrent message, and _restart_tasks will recover it after the new
    # consumer exists.
    w.status = "restarting"
    _bump_worker_generation(w)

    # cancel stale tasks FIRST — before killing the process.
    # _read_stdout detects EOF on process death and calls workers.pop(),
    # which would remove the worker being restarted.  Cancelling first
    # means _read_stdout never sees the EOF.
    if w._consume_task:
        await _cancel_worker_task(w._consume_task)
    if w._stdout_task:
        await _cancel_worker_task(w._stdout_task)
    if w._interrupt_guard_task and w._interrupt_guard_task is not asyncio.current_task():
        await _cancel_worker_task(w._interrupt_guard_task)
    await _stop_worker_tasks(w)

    # Do this before any await that can service a dashboard reconnect. The
    # Worker object is reused, but these snapshots are process-local.
    clear_native_runtime_state(w)

    # kill takeover terminal if one was opened
    await _kill_takeover_terminal(w)

    # kill existing cbc process tree（psutil 递归杀，避免 node.exe 孤儿）
    await _kill_process_tree(w)
    if not _runtime_stopped(w):
        return await _control_stop_failed(w, RuntimeError("Runtime stop was not confirmed (timed out); process is still alive"))
    if not _confirm_native_input_retirement(w):
        return "Old runtime stop was not confirmed; input uncertainty retained"
    if w._hist_dirty:
        await _flush_history_now(w)
    await _finish_offline_stop(w, retiring_owner, "pan/restart-stop")
    if _runtime_stopped(w):
        await _record_legal_worker_state(w, "offline", "pan/restart-stop")
    w.process = None

    # One-shot workers deliberately have no long-running stream process.
    # Restarting one therefore means rebuilding the Pan consumer/runtime only;
    # calling _spawn_process here would launch a stream CLI with the wrong
    # protocol and can leave force-send callers waiting forever.
    if resolve_execution_mode(w.adapter, _session(w)) == "oneshot":
        from packages.plugins.lifecycle import session_claim, capture_async
        async with session_claim(_session(w)):
            await capture_async(_session(w))
        w.status = "idle"
        await _restart_tasks(w)
        await _record_legal_worker_state(w, "idle", "pan/restart-start")
        s = _session(w)
        await _bcast({
            "type": "worker.restarted",
            "workerId": worker_id,
            "sessionId": w.session_id,
            "generation": w.generation,
            "name": s.name if s else worker_id,
            "status": w.status,
        })
        return None

    proc = await _spawn_process(w.session_id, adapter=w.adapter)
    if isinstance(proc, str):
        w.status = "error"
        await _record_worker_start_failure(
            _session(w), w.session_id, w.worker_id, proc,
            w=w, origin="worker.pan_restart",
        )
        _schedule_session_recovery(w.session_id)
        return f"Spawn failed ({w.session_id}): {proc}"
    w.process = proc
    w.status = "idle"
    await _record_legal_worker_state(w, "idle", "pan/restart-start")
    # resume 不再置 _replaying（worker-resume-replay 结论）：cbc stdin 有 prompt
    # 时不重放 stdout 历史，首个 result 即任务结果；若仍置 True，_read_stdout
    # 会把首个 result 当 replay 结束丢弃（L507-512 continue）→ 任务永不完成。
    await _restart_tasks(w)

    s = _session(w)
    await _bcast({
        "type": "worker.restarted",
        "workerId": worker_id,
        "sessionId": w.session_id,
        "generation": w.generation,
        "name": s.name if s else worker_id,
        "status": w.status,
    })
    return None


async def restart_worker(worker_id: str) -> str | None:
    """Serialized compatibility wrapper for worker-id callers."""
    if _shutdown_started:
        return "Pan main service is shutting down"
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    generation = w.generation
    _cancel_session_restart(w.session_id)
    lock = await _session_spawn_lock(w.session_id)
    async with lock:
        current = workers.get(worker_id)
        if current is not w or w.generation != generation:
            return None
        try:
            return await _restart_worker_unlocked(worker_id)
        except (RuntimeError, OSError) as exc:
            return await _control_stop_failed(w, exc)


async def _respawn_worker_unlocked(worker_id: str, extra_args: list[str] | None = None) -> str | None:
    """Kill + re-spawn with extra args (model/mode switch)."""
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    _cancel_pending_restart(w)
    retiring_owner = _offline.owner(w)

    _cancel_claude_permission_requests(worker_id, "Claude worker was respawned")

    # cancel stale tasks FIRST — same race as restart_worker:
    # if we kill before cancelling, _read_stdout sees EOF and
    # pops the worker from workers dict during spawn.
    if w._consume_task:
        await _cancel_worker_task(w._consume_task)
    if w._stdout_task:
        await _cancel_worker_task(w._stdout_task)
    w.status = "restarting"
    _bump_worker_generation(w)
    if w._interrupt_guard_task and w._interrupt_guard_task is not asyncio.current_task():
        await _cancel_worker_task(w._interrupt_guard_task)
    await _stop_worker_tasks(w)

    # Do this before killing/spawning: a reconnect must never replay prompts or
    # usage emitted by the process that is being replaced.
    clear_native_runtime_state(w)

    await _kill_takeover_terminal(w)
    await _kill_process_tree(w)
    if not _confirm_native_input_retirement(w):
        return "Old runtime stop was not confirmed; input uncertainty retained"
    if w._hist_dirty:
        await _flush_history_now(w)
    await _finish_offline_stop(w, retiring_owner, "pan/respawn-stop")
    if _runtime_stopped(w):
        await _record_legal_worker_state(w, "offline", "pan/respawn-stop")
    w.process = None

    # Settings changes can also request a respawn.  Keep one-shot sessions on
    # their consumer path for the same reason as restart: their provider
    # process is created per task by _consumer_oneshot, never by this helper.
    if resolve_execution_mode(w.adapter, _session(w)) == "oneshot":
        from packages.plugins.lifecycle import session_claim, capture_async
        async with session_claim(_session(w)):
            await capture_async(_session(w))
        w.status = "idle"
        await _restart_tasks(w)
        await _record_legal_worker_state(w, "idle", "pan/respawn-start")
        await _bcast({
            "type": "worker.reconfigured",
            "workerId": worker_id,
            "sessionId": w.session_id,
            "generation": w.generation,
            "status": "idle",
        })
        return None

    proc = await _spawn_process(w.session_id, adapter=w.adapter, extra_args=extra_args)
    if isinstance(proc, str):
        w.status = "error"
        await _record_worker_start_failure(
            _session(w), w.session_id, w.worker_id, proc,
            w=w, origin="worker.restart",
        )
        _schedule_session_recovery(w.session_id)
        return proc
    w.process = proc
    w.status = "idle"
    await _record_legal_worker_state(w, "idle", "pan/respawn-start")
    await _restart_tasks(w)

    await _bcast({
        "type": "worker.reconfigured",
        "workerId": worker_id,
        "sessionId": w.session_id,
        "generation": w.generation,
        "status": "idle",
    })
    return None


async def respawn_worker(worker_id: str, extra_args: list[str] | None = None) -> str | None:
    """Serialized compatibility wrapper for worker-id callers."""
    if _shutdown_started:
        return "Pan main service is shutting down"
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    generation = w.generation
    _cancel_session_restart(w.session_id)
    lock = await _session_spawn_lock(w.session_id)
    async with lock:
        current = workers.get(worker_id)
        if current is not w or w.generation != generation:
            return None
        return await _respawn_worker_unlocked(worker_id, extra_args)


async def branch_worker(worker_id: str, new_session_id: str) -> Worker | str:
    """Fork a new Worker from an existing one's session.

    new_session_id must already exist (created by session.create()).
    """
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"

    s = _sess.get(new_session_id)
    if not s:
        return "New session not found"

    # inherit model/mode/thinking and adapter-specific session settings from
    # the original session.  Provider-owned identity/usage cursors are
    # deliberately excluded: the fork gets a new native CLI session below.
    orig = _sess.get(w.session_id)
    if orig:
        inherited_config = {
            key: value for key, value in orig.adapter_config.items()
            if key not in {"cli_session_id", "codex_prev_usage"}
        }
        inherited_config.update({
            key: value for key, value in s.adapter_config.items()
            if key not in {"cli_session_id", "codex_prev_usage"}
        })
        s.adapter_config = inherited_config
        if not s.model:
            s.model = orig.model
        if not s.permission_mode:
            s.permission_mode = orig.permission_mode
        if not s.adapter_config.get("effort"):
            s.adapter_config["effort"] = orig.adapter_config.get("effort", "")
        s.adapter_config["always_thinking_enabled"] = s.adapter_config.get("always_thinking_enabled") or orig.adapter_config.get("always_thinking_enabled")
        if not s.adapter_config.get("max_thinking_tokens"):
            s.adapter_config["max_thinking_tokens"] = orig.adapter_config.get("max_thinking_tokens")
        # For adapters that fork via file copy (e.g. kimi), pass the parent's
        # cli_session_id so fork_args can create the branched session.
        if orig.cli_session_id and not s.cli_session_id:
            s.cli_session_id = orig.cli_session_id
        if not s.system_prompt:
            s.original_prompt = orig.original_prompt
            s.handoff_prompt = orig.handoff_prompt
        if not s.character_id:
            s.character_id = orig.character_id
        if not s.session_template:
            s.session_template = orig.session_template
        if not s.pan_access:
            s.pan_access = dict(orig.pan_access)

    # Most legacy adapters fork by passing a CLI-specific flag.  Codex's
    # app-server has no reliable headless fork flag, so its SessionsProvider
    # materializes a new native thread in the Codex state/history databases.
    extra_args = w.adapter.fork_args(s)
    provider = None
    if w.adapter.supports_fork and not extra_args:
        try:
            provider = get_sessions_provider(w.adapter.name)
        except KeyError:
            provider = None

    if not w.adapter.supports_fork or (not extra_args and provider is None):
        return f"Adapter '{w.adapter.name}' does not support fork"

    if not extra_args and provider is not None:
        if not orig or not orig.cli_session_id:
            return "Session has no native CLI session ID — cannot fork"
        try:
            new_cli_id = await asyncio.to_thread(
                provider.fork_session,
                orig.cli_session_id,
                s.name,
                s.workdir or None,
            )
            s.cli_session_id = new_cli_id
            parsed_history = await asyncio.to_thread(
                provider.parse_history, new_cli_id, s.workdir or None
            )
            _sess.replace_history(
                s, _sess.assign_pan_message_ids(parsed_history),
            )
            raw_usage_entries = await asyncio.to_thread(
                provider.get_raw_usage, new_cli_id, s.workdir or None
            )
            # New branch Session has no Worker or pending enrichment yet.
            _sess.replace_usage_totals(
                s, _sess.accumulate_raw_usage(None, _sess.normalize_native_usage_entries(
                    s.adapter, raw_usage_entries)),
                native_entries=raw_usage_entries)
        except Exception as exc:
            return f"Fork failed: {exc}"

    new_id = await _next_worker_id()
    proc = await _spawn_process(new_session_id, adapter=w.adapter, extra_args=extra_args)
    if isinstance(proc, str):
        return proc

    new_w = Worker(worker_id=new_id, session_id=new_session_id,
                   adapter=w.adapter,
                   status="idle", process=proc, pending_signal=asyncio.Queue(),
                   _task_done=asyncio.Event(),
                   _hist_flush_event=asyncio.Event(),
                   generation=_next_worker_generation(new_session_id),
                   codex_profile_key=getattr(w, "codex_profile_key", None))
    # 注意：branch 不设 _replaying（与 create_worker/restart_worker 一致，现全局恒
    # False）。原注释假设"cbc --resume --fork-session 会把父会话历史重放到 stdout
    # 供 branch 空 history 填充"——worker-resume-replay 实测 fork+prompt **不重放**
    # （stdin 有 prompt → ResumeReplay skipped），该假设不成立。父上下文实际由 cbc
    # 内部 fork 的 JSONL 承载（模型侧完整）；Pan 侧新 session 的 history 只记录新
    # 回合（父历史不回填 → 展示层缺父回合，属既有局限，非模型上下文缺陷）。
    workers[new_id] = new_w
    _register_worker(new_w)
    new_w._offline_predecessors = set(s.illegal_batches)
    await _claim_offline_owner(new_w)
    new_w.last_activity = time.monotonic()
    new_w._stdout_task = asyncio.create_task(_read_stdout(new_w))
    new_w._consume_task = asyncio.create_task(_consumer(new_w))
    if not _DEFAULTS_INITIALIZED:
        load_worker_config()
    new_w._watchdog_task = asyncio.create_task(_watchdog(new_w))

    await _sess.save_async(s)

    await _bcast({
        "type": "worker.spawned",
        "workerId": new_id,
        "sessionId": new_session_id,
        "generation": new_w.generation,
        "name": s.name,
        "status": "idle",
        "parentWorkerId": worker_id,
    })
    return new_w


def _apply_native_mode_receipt(w: Worker, receipt: dict) -> bool:
    generation = receipt.get("generation")
    if (receipt.get("mode") not in {"pan", "native"}
            or type(generation) is not int or generation < w.native_control_generation):
        return False
    w.native_control_mode = receipt["mode"]
    w.native_control_generation = generation
    w._native_mode_request_id = None
    w.native_control_error = None if receipt.get("ok") is True else "Native control request rejected"
    if receipt.get("native_busy") is True:
        w._native_reservation_busy = True
    if w.native_control_mode == "pan" and w.pending_signal is not None:
        w.pending_signal.put_nowait({"type": "queue_signal"})
    return True


async def switch_native_control(worker_id: str, mode: str, *, timeout: float = 15.0) -> dict:
    """Internal handoff seam; no process lifecycle or durable queue mutation.

    A lost acknowledgement retains the handoff gate; retry can reconcile the
    kernel's authoritative generation. Never guess ownership after timeout.
    """
    w = workers.get(worker_id)
    if w is None or w.adapter.name not in {"codex", "cbc"} or mode not in {"pan", "native"}:
        return {"ok": False, "error": "Unsupported native control"}
    s = _session(w)
    if s is None or s.adapter_config.get("native_control") is not True:
        return {"ok": False, "error": "Native control is not enabled"}
    runtime_generation = w.generation
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        await asyncio.wait_for(w._native_mode_lock.acquire(), max(0.0, deadline - time.monotonic()))
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Control switch busy"}
    request_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    try:
        async def handoff():
            if w.generation != runtime_generation or w.status == "restarting":
                raise RuntimeError("Worker restarted")
            if w.process is None or w.process.returncode is not None or w.process.stdin is None:
                raise RuntimeError("Worker process is not running")
            w.native_control_mode = "switching"
            w._native_mode_request_id = request_id
            w._native_mode_receipts[request_id] = future
            wire = w.adapter.encode_control_message({"type": "control_mode", "mode": mode,
                "generation": w.native_control_generation, "requestId": request_id}) + b"\n"
            w.process.stdin.write(wire)
            await w.process.stdin.drain()
        async def locked_handoff():
            async with queue_lock(w.session_id):
                await handoff()
        await asyncio.wait_for(locked_handoff(), max(0.0, deadline - time.monotonic()))
        receipt = await asyncio.wait_for(asyncio.shield(future), max(0.0, deadline - time.monotonic()))
        if w.generation != runtime_generation:
            return {"ok": False, "error": "Worker restarted"}
        if not _apply_native_mode_receipt(w, receipt):
            return {"ok": False, "error": "Invalid control receipt"}
        return receipt
    except (asyncio.TimeoutError, OSError, RuntimeError):
        if w.generation == runtime_generation and w.native_control_mode == "switching":
            w.native_control_error = "Control ownership unconfirmed"
        return {"ok": False, "error": "Control ownership unconfirmed"}
    finally:
        w._native_mode_receipts.pop(request_id, None)
        w._native_mode_lock.release()


async def write_native_control(worker_id: str, control: dict, *, timeout: float = 2.0) -> dict:
    """Private native input/resize seam; receipt is emitted after the PTY write."""
    w = workers.get(worker_id)
    policy = control.get("type") == "native_passthrough"
    cbc_interrupt = bool(w and w.adapter.name == "cbc" and w.native_control_enabled
                         and control.get("type") == "interrupt")
    if (w is None or w.adapter.name not in {"codex", "cbc"} or
            (policy and (not w.native_control_enabled or not w.native_kernel_ready)) or
            (cbc_interrupt and w.native_control_mode != "pan") or
            (not policy and not cbc_interrupt and w.native_control_mode != "native")):
        return {"ok": False, "error": "Native control is not active"}
    if policy and (set(control) != {"type", "enabled"} or type(control.get("enabled")) is not bool):
        return {"ok": False, "error": "Invalid native policy"}
    if policy and w.adapter.name != "codex":
        return {"ok": False, "error": "Unsupported native policy"}
    if not policy and (type(control.get("generation")) is not int or control.get("generation") != w.native_control_generation):
        return {"ok": False, "error": "Stale control generation"}
    if not cbc_interrupt and control.get("type") not in {"native_input", "native_resize", "native_passthrough"}:
        return {"ok": False, "error": "Unsupported native operation"}
    if len(w._native_mode_receipts) >= 32:
        return {"ok": False, "error": "Native input busy"}
    runtime_generation = w.generation
    request_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    w._native_mode_receipts[request_id] = future
    async def send():
        async with queue_lock(w.session_id):
            if (w.generation != runtime_generation or w.status == "restarting"
                    or (cbc_interrupt and w.native_control_mode != "pan")
                    or (not policy and not cbc_interrupt and w.native_control_mode != "native") or w.process is None
                    or w.process.returncode is not None or w.process.stdin is None):
                raise RuntimeError("Native worker is unavailable")
            w.process.stdin.write(w.adapter.encode_control_message({**control, "requestId": request_id}) + b"\n")
            await w.process.stdin.drain()
        return await asyncio.shield(future)
    try:
        receipt = await asyncio.wait_for(send(), timeout)
        if w.generation != runtime_generation:
            return {"ok": False, "error": "Native worker changed"}
        if policy and receipt.get("ok") is True:
            w.native_passthrough_enabled = control["enabled"]
        return receipt
    except (asyncio.TimeoutError, OSError, RuntimeError):
        return {"ok": False, "error": "Native operation unconfirmed"}
    finally:
        w._native_mode_receipts.pop(request_id, None)


async def _record_native_settings_state(w: Worker, *, uncertain: bool) -> None:
    """Only a new kernel's init clears the durable uncertainty marker."""
    s = _session(w)
    if s is None:
        return
    if uncertain:
        if s.adapter_config.get("native_settings_uncertain") is True:
            return
        s.adapter_config["native_settings_uncertain"] = True
    else:
        w.native_passthrough_enabled = s.adapter_config.get("native_passthrough") is True
        if "native_settings_uncertain" not in s.adapter_config:
            return
        s.adapter_config.pop("native_settings_uncertain", None)
    await _sess.save_async(s)
    await _bcast({"type": "session.updated", "sessionId": w.session_id})


def steer_delivery_route(w: Worker | None, s) -> str:
    """Capability-driven urgent delivery; lifecycle intent always wins idle work.

    Only a *running* Worker can be urgently re-routed.  Idle/queued/held or
    missing runtimes (including an empty queue) use the ordinary durable send
    path: no Steer attempt and no restart.  A running Worker prefers native
    Steer when its adapter supports it; otherwise the caller moves the
    original item to the queue head and restarts + sends.  Illegal sessions
    stay locked for the explicit Wake route and are never force-routed here.
    """
    if w is None:
        return "queue"
    if s is not None and _offline.is_illegal(s):
        return "queue"
    if w.status == "restarting" or (w.pending_restart and w.status != "running"):
        return "queue"
    capable = (resolve_execution_mode(w.adapter, s) == "stream"
               and getattr(w.adapter, "supports_native_steer", False))
    if w.pending_restart and not capable:
        return "queue"
    if capable:
        return "steer" if w.status == "running" else "queue"
    if w.status != "running":
        # An idle non-Steer adapter needs a normal consumer handoff, not a
        # restart: force semantics must not replace an empty queue with a
        # fresh process.
        return "queue"
    if any(queue_item_edit_locked(s, item_id)
           for item_id in list(getattr(s, "queue_edit_locks", {}))):
        return "queue"
    return "restart"


def _steer_origin(w: Worker) -> dict:
    s = _session(w)
    return {"runId": w._steer_run_id, "cliSessionId": s.cli_session_id if s else None,
            "workerId": w.worker_id, "workerGeneration": w.generation,
            "taskSeq": w._current_seq, "executionSeq": w._execution_seq,
            "taskId": w._current_task_id, "status": "accepted", "consumption": "unconfirmed"}


def _prepare_steer_control(w: Worker, text: str, request_id: str, origin=None) -> dict:
    control = {"type": "steer", "text": text, "requestId": request_id}
    if getattr(w.adapter, "steer_requires_run_id", False):
        origin = origin or _steer_origin(w)
        if not origin.get("runId") or not origin.get("cliSessionId"):
            raise ValueError("CBC Steer requires a confirmed active CLI session/run")
        control.update(sessionId=origin["cliSessionId"], expectedRequestId=origin["runId"])
    return control


def _route_steer_receipt(w: Worker, event: dict, *, runtime=None) -> bool:
    normalize = getattr(w.adapter, "normalize_steer_receipt", None)
    receipt = (event if event.get("type") == "pan.steer_receipt" else
               normalize(event) if normalize is not None else None)
    if receipt is None:
        return False
    request = receipt.get("requestId")
    future = w._steer_receipts.get(request)
    context = w._steer_contexts.get(request)
    if future is not None and not future.done():
        if context is not None:
            expected = context["runtime"]
            source = runtime or (w, w.generation, w.process, getattr(w.process, "stdin", None))
            if (source[0] is not expected[0] or source[1] != expected[1]
                    or source[2] is not expected[2] or source[3] is not expected[3]):
                return True  # A replacement reader cannot acknowledge the old pipe.
            if (getattr(w.adapter, "steer_requires_run_id", False)
                    and receipt.get("outcome") == "accepted"
                    and receipt.get("cliSessionId") != context["origin"].get("cliSessionId")):
                receipt = {"ok": False, "outcome": "unknown", "error": "CBC Steer Session receipt mismatch"}
        future.set_result(receipt)
    return True  # Duplicate/unowned control receipts never become chat history.


def _control_runtime_matches(worker_id: str, runtime) -> bool:
    owner, generation, process, stdin = runtime
    return (workers.get(worker_id) is owner
            and getattr(owner, "generation", None) == generation
            and owner.process is process
            and getattr(process, "stdin", None) is stdin)


async def _send_control_message_unlocked(worker_id: str, control: dict, *, drain: bool = True,
                                         _expected_runtime=None, outcome=None) -> str | None:
    if outcome is None:
        outcome = {}
    outcome['status'] = 'rejected'
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    runtime = _expected_runtime or (
        w, getattr(w, "generation", None), w.process, getattr(w.process, "stdin", None))
    if not _control_runtime_matches(worker_id, runtime):
        return "Worker changed before control handoff"
    if w.native_control_mode != "pan":
        return "Pan controls are suspended during native control"
    if isinstance(control, dict) and control.get("type") == "steer":
        if w.status != "running":
            return "Steer requires a running Worker"
        if not getattr(w.adapter, "supports_native_steer", False):
            return "Adapter does not support native Steer"
        if (getattr(w.adapter, "steer_requires_run_id", False)
                and (w._terminal_handled or control.get("expectedRequestId") != w._steer_run_id)):
            return "CBC turn changed before Steer handoff"
    if not isinstance(control, dict) or control.get("type") not in {
        "interrupt", "steer", "compact", "approval_response", "user_input_response", "permission_response",
        "elicitation_response", "terminal_input", "terminal_terminate",
    }:
        return "Unsupported worker control"

    # Claude Code invokes the permission prompt through MCP, so its response
    # resolves the waiting HTTP/MCP future rather than being written to stdin.
    if control.get("type") == "permission_response":
        if await _resolve_claude_permission(worker_id, control):
            return None
        if getattr(w.adapter, "name", "") == "claude":
            return "Claude permission request not found"

    if (w.process is None or w.process.returncode is not None
            or w.process.stdin is None):
        return "Worker process is not running"
    encode_control = getattr(w.adapter, "encode_control_message", None)
    if encode_control is None:
        return f"Adapter '{w.adapter.name}' does not support worker controls"
    try:
        stdin = runtime[3]
        wire = encode_control(control) + b"\n"
        # Encoding/permission handling must not retarget an earlier request.
        if not _control_runtime_matches(worker_id, runtime):
            return "Worker changed before control handoff"
        if (control.get("type") == "steer" and getattr(w.adapter, "steer_requires_run_id", False)
                and (w.status != "running" or w._terminal_handled or w.native_control_mode != "pan"
                     or control.get("expectedRequestId") != w._steer_run_id)):
            return "CBC turn changed before Steer handoff"
        outcome['status'] = 'unknown'  # Once attempted, prose cannot prove no handoff.
        written = stdin.write(wire)
        if isinstance(written, int) and written != len(wire):
            raise OSError("partial control write")
        if drain:
            await stdin.drain()
    except (BrokenPipeError, ConnectionError, OSError, RuntimeError, TypeError, ValueError):
        return "Worker control write failed"
    outcome['status'] = 'accepted'
    return None


async def send_control_message(worker_id: str, control: dict, *, _expected_runtime=None, outcome=None) -> str | None:
    """Send an out-of-band control, serializing Steer against queue edits.

    Native adapters may use this for controls that are not user turns, such
    as Codex app-server approval and user-input responses. The adapter owns
    wire encoding; workers without that optional capability keep existing
    behavior.
    """
    if outcome is None:
        outcome = {}
    outcome['status'] = 'rejected'
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    runtime = _expected_runtime or (
        w, getattr(w, "generation", None), w.process, getattr(w.process, "stdin", None))
    if not _control_runtime_matches(worker_id, runtime):
        return "Worker changed before control handoff"
    if isinstance(control, dict) and control.get("type") == "steer":
        s = _session(w)
        if s is not None:
            async with queue_lock(s.id):
                if not _control_runtime_matches(worker_id, runtime):
                    return "Worker changed before control handoff"
                if s.readonly_session:
                    return READONLY_SESSION_ERROR
                if s.queue_paused:
                    return "Pause everything blocks Steer"
                if any(queue_item_edit_locked(s, item_id)
                       for item_id in list(getattr(s, "queue_edit_locks", {}))):
                    return "Cannot Steer while a queued message is being edited"
                # Keep the lease check and stdin write in one critical section
                # so direct API/programmatic Steer cannot race edit acquisition.
                error = await _send_control_message_unlocked(
                    worker_id, control, drain=False, _expected_runtime=runtime, outcome=outcome)
                if error:
                    return error
                stdin = runtime[3]
            # Backpressure is after the synchronous handoff. New queue appends
            # and edit acquisition need not wait for this control's drain.
            try:
                outcome['status'] = 'unknown'
                await stdin.drain()
            except (BrokenPipeError, ConnectionError, OSError, RuntimeError, TypeError, ValueError):
                return "Worker control write failed"
            outcome['status'] = 'accepted'
            return None
    return await _send_control_message_unlocked(worker_id, control, _expected_runtime=runtime, outcome=outcome)


async def steer_worker(worker_id: str, text: str,
                       message_id: str | None = None, *, outcome=None) -> str | None:
    """Inject a follow-up instruction into a native running turn.

    Codex app-server's ``turn/steer`` changes the native thread, but it does
    not produce a normal Pan ``user_inject`` event. Persist the instruction
    only after the control write succeeds so Pan history stays aligned with
    the native conversation.
    """
    if outcome is None:
        outcome = {}
    outcome['status'] = 'rejected'
    text = str(text or "").strip()
    if not text:
        return "Steer text is required"
    if message_id is not None and (
        not isinstance(message_id, str)
        or not message_id.startswith("steer:")
        or len(message_id) > 128
    ):
        return "Invalid Steer message id"
    w = workers.get(worker_id)
    if w is None:
        return "Steer requires a running Worker"
    s = _session(w)
    prior = s.adapter_config.get("_steer_requests", {}).get(message_id) if s else None
    if prior and prior.get("sessionId") == s.id:
        if prior.get("bodyHash") != hashlib.sha256(text.encode()).hexdigest():
            return "Steer message id already belongs to different text"
        outcome['status'] = 'accepted' if prior.get('status') == 'accepted' else 'unknown'
        return None if outcome['status'] == 'accepted' else "Steer outcome unknown: original request retained; do not resend"
    if s is not None and message_id and any(
            row.get("clientMessageId") == message_id or row.get("messageId") == message_id
            for row in s.history if isinstance(row, dict)):
        outcome['status'] = 'accepted'
        return None
    if w.status != "running":
        return "Steer requires a running Worker"
    if not getattr(w.adapter, "supports_native_steer", False):
        return "Adapter does not support native Steer"
    if w._direct_steer_pending:
        outcome['status'] = 'unknown'
        return "Previous Steer is awaiting a provider receipt; do not resend"
    if s is not None and getattr(w.adapter, "steer_requires_run_id", False) and any(
            entry.get("sessionId") == s.id and entry.get("status") == "unknown"
            for entry in s.adapter_config.get("_steer_requests", {}).values() if isinstance(entry, dict)):
        outcome['status'] = 'unknown'
        return "Steer outcome unknown: previous request retained; do not resend"
    origin = _steer_origin(w) if getattr(w.adapter, "steer_requires_run_id", False) else None
    ledger = None
    request_id = uuid.uuid4().hex
    runtime = (w, w.generation, w.process, getattr(w.process, "stdin", None))
    if origin is not None:
        if s is None or s.readonly_session:
            return READONLY_SESSION_ERROR if s else "Session not found"
        ledger = s.adapter_config.setdefault("_steer_requests", {})
        identity = message_id or "steer:" + request_id
        try:
            w.adapter.encode_control_message(_prepare_steer_control(w, text, request_id, origin))
        except (ValueError, TypeError, RuntimeError):
            return "CBC Steer requires a confirmed active CLI session/run and supported stream protocol"
    w._direct_steer_pending = True
    if ledger is not None:
        durable = _sess.DurableOutcome()
        def reserve_direct():
            ledger[identity] = {"sessionId": s.id, "requestId": request_id, "status": "unknown",
                                "bodyHash": hashlib.sha256(text.encode()).hexdigest(), "origin": origin}
            _sess._save_body(s)
        try:
            await _sess._persist_async_outcome(s.id, reserve_direct, durable)
        except BaseException:
            # No sender exists yet, so cancellation/save failure proves no handoff.
            ledger.pop(identity, None)
            w._direct_steer_pending = False
            await asyncio.shield(_persist_queue_steer_state(w, s))
            raise
    async def settle_direct(status, error):
        if status == 'unknown':
            return
        w._direct_steer_pending = False
        if status == 'accepted':
            await _record_direct_steer(w, text, message_id, origin=origin,
                                       ledger_id=identity if ledger is not None else None)
        elif ledger is not None:
            ledger.pop(identity, None)  # Confirmed pre-handoff/provider rejection only.
            await _persist_queue_steer_state(w, s)
    async def complete_late(status, error):
        await settle_direct(status, error)
    err = await _send_steer_with_receipt(w, text, on_late_receipt=complete_late,
                                        origin=origin, request_id=request_id, runtime=runtime, outcome=outcome)
    await settle_direct(outcome['status'], err)
    return err


async def _record_direct_steer(w: Worker, text: str, message_id: str | None, *, origin=None, ledger_id=None):
    s = _session(w)
    if s is not None:
        if message_id and any(row.get("clientMessageId") == message_id or row.get("messageId") == message_id
                              for row in s.history if isinstance(row, dict)):
            return
        history_row = {"role": "user", "content": text, "source": "user"}
        if origin is not None:
            history_row["steer"] = dict(origin)
        if ledger_id is not None:
            s.adapter_config["_steer_requests"][ledger_id]["status"] = "accepted"
        if message_id:
            history_row["messageId"] = message_id
        _sess.append_history(s, history_row)
        # The native control has already been written successfully.  A
        # transient history-file failure must not make the caller retry Steer
        # (which would send the provider control a second time), so retry only
        # the failed persistence operation once.  This stays off the normal
        # delta/done path and remains bounded if the second write also fails.
        for attempt in range(2):
            try:
                await _sess.save_async(s)
                break
            except Exception:
                if attempt == 1:
                    _mark_history_dirty(w)
                    _log.exception("Steer accepted; history persistence pending session=%s", s.id)
                await asyncio.sleep(0)
        await _bcast({"type": "queue.item_delivered", "sessionId": s.id,
                      "queueItemIds": [], "messages": [history_row]})


async def _send_steer_with_receipt(w: Worker, text: str, *, on_late_receipt=None,
                                   origin=None, request_id=None, runtime=None, outcome=None) -> str | None:
    """Codex confirms turn/steer; Claude's documented boundary is stdin drain."""
    if outcome is None:
        outcome = {}
    outcome['status'] = 'rejected'
    send_outcome = {}
    request_id = request_id or uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    wants_receipt = getattr(w.adapter, "supports_steer_receipt", False)
    if wants_receipt:
        w._steer_receipts[request_id] = future
    late_receipt = False
    sender = None
    # Bind before the sender task gets its first scheduling opportunity.
    runtime = runtime or (w, w.generation, w.process, getattr(w.process, "stdin", None))
    w._steer_contexts[request_id] = {"runtime": runtime, "origin": origin or _steer_origin(w)}

    def retain_late_receipt():
        nonlocal late_receipt
        if on_late_receipt is None:
            return
        late_receipt = True
        def settle_late(done):
            w._steer_receipts.pop(request_id, None)
            w._steer_contexts.pop(request_id, None)
            if not done.cancelled():
                status, detail = _steer_receipt_result(done.result())
                error = "Steer outcome unknown: " + str(detail) if status == "unknown" else detail
                task = asyncio.create_task(on_late_receipt(status, error))
                task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        future.add_done_callback(settle_late)

    try:
        try:
            if not _control_runtime_matches(w.worker_id, runtime):
                return "Worker changed before control handoff"
            control = _prepare_steer_control(w, text, request_id, origin)
        except ValueError as exc:
            return str(exc)
        if not _process_alive(w):
            return "Worker process is not running"
        if not wants_receipt:
            error = await send_control_message(w.worker_id, control, _expected_runtime=runtime, outcome=send_outcome)
            outcome['status'] = send_outcome.get('status', 'unknown')
            return ("Steer outcome unknown: control write/drain failed"
                    if outcome['status'] == 'unknown' else error)
        sender = asyncio.create_task(send_control_message(w.worker_id, control, _expected_runtime=runtime, outcome=send_outcome))
        # Never infer acceptance from stdout activity, completion or stdin write.
        # A missing receipt is ambiguous and must not be retried automatically.
        deadline = time.monotonic() + 30
        while _process_alive(w) and time.monotonic() < deadline:
            waiting = {future} | ({sender} if not sender.done() else set())
            await asyncio.wait(waiting, timeout=min(1, max(0, deadline-time.monotonic())),
                               return_when=asyncio.FIRST_COMPLETED)
            if future.done():
                status, detail = _steer_receipt_result(future.result())
                outcome['status'] = status
                return ("Steer outcome unknown: " + str(detail) if status == "unknown" else detail)
            if sender.done():
                error = sender.result()
                if error and send_outcome.get('status') == 'rejected':
                    outcome['status'] = 'rejected'
                    return error
        retain_late_receipt()
        outcome['status'] = 'unknown'
        return "Steer outcome unknown: provider receipt missing; do not resend"
    except asyncio.CancelledError:
        outcome['status'] = 'unknown'
        if wants_receipt:
            retain_late_receipt()
        raise
    finally:
        if sender is not None:
            if not sender.done():
                sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        if not late_receipt:
            w._steer_receipts.pop(request_id, None)
            w._steer_contexts.pop(request_id, None)


def _queue_origin_metadata(items: list[dict]) -> dict:
    """Preserve Pan identity independently of the provider's user wire role."""
    envelopes = []
    for item in items:
        envelope = copy.deepcopy(item)
        for key in ("reservedBy", "reservedGeneration", "reservedAt", "queueSteerPending", "queueSteerRequestId",
                    "queueSteerWorkerId", "queueSteerGeneration", "queueSteerOrigin", "forceSendHeld"):
            envelope.pop(key, None)
        if isinstance(envelope.get("parts"), list):
            envelope["parts"] = public_message_parts(envelope["parts"])
        envelopes.append(envelope)
    first = items[0]
    kind = _queue_item_kind(first)
    metadata = {"kind": kind, "queueEnvelopes": envelopes,
                "source": (_task_source(first) if kind == "task" else
                           first.get("source") or kind)}
    for key in ("sourcePluginId", "autoReportAdmission", "sourceSessionId", "taskId", "taskIdSource", "clientMessageId",
                "eventId", "channel", "envelope", "noticeKind", "jobId"):
        if key in first:
            metadata[key] = copy.deepcopy(first[key])
    if isinstance(first.get("parts"), list):
        metadata["parts"] = public_message_parts(first["parts"])
    source_ids = sorted({item["sourceSessionId"] for item in items
                         if isinstance(item.get("sourceSessionId"), str) and item["sourceSessionId"]})
    if source_ids and (kind != "task" or len(source_ids) > 1):
        metadata["sourceSessionIds"] = source_ids
    return metadata


def _public_queue_receipt(value):
    """Strip internal paths recursively, including retained origin envelopes."""
    if isinstance(value, dict):
        return {key: _public_queue_receipt(part) for key, part in value.items() if key != "__serverPath"}
    if isinstance(value, list):
        return [_public_queue_receipt(part) for part in value]
    return value


def _queue_history_row(items: list[dict], text: str | None = None) -> dict:
    first = items[0]
    if text is None:
        text = first.get("text", "") if _queue_item_kind(first) == "task" else _format_report_batch(items)
    row = {"role": "user", "content": text, "queueItemIds": [_queue_item_id(item) for item in items],
           "delivered_keys": [_delivery_key(item) for item in items], **_queue_origin_metadata(items)}
    if isinstance(first.get("parts"), list):
        # Storage retains canonical attachment paths; public receipts strip them.
        row["parts"] = copy.deepcopy(first["parts"])
    source_ids = sorted({item["sourceSessionId"] for item in items
                         if isinstance(item.get("sourceSessionId"), str) and item["sourceSessionId"]})
    if len(source_ids) > 1:
        row["sourceSessionIds"] = source_ids
    return row


_queue_steer_tasks: dict[tuple[str, str], asyncio.Task] = {}
_queue_steer_owners: set[asyncio.Task] = set()


def _own_queue_steer_task(coroutine) -> asyncio.Task:
    task = asyncio.create_task(coroutine)
    _queue_steer_owners.add(task)
    def finished(done):
        _queue_steer_owners.discard(done)
        if not done.cancelled() and done.exception() is not None:
            _log.error("Queue Steer owner failed", exc_info=done.exception())
    task.add_done_callback(finished)
    return task


async def _persist_queue_steer_state(w: Worker, s) -> bool:
    """Retain ownership through cancellation and retry persistence, never control."""
    for attempt in range(2):
        outcome = _sess.DurableOutcome()
        try:
            await _save_receipt(s, outcome=outcome)
            return True
        except BaseException:
            if outcome.succeeded:
                return True
            if attempt == 1:
                _mark_history_dirty(w)
                _log.exception("Queue Steer persistence unresolved session=%s", s.id)
    return False


async def _queue_steer_bcast(event: dict) -> None:
    try:
        await _bcast(event)
    except Exception:
        _log.exception("Queue Steer broadcast failed; durable receipt remains authoritative")


def _steer_receipt_result(value) -> tuple[str, str | None]:
    """Keep structured provider rejection distinct from a bridge write failure."""
    if value is None:  # Legacy in-process accepted receipt.
        return "accepted", None
    if isinstance(value, dict):
        if value.get("ok") is True and value.get("outcome") in {None, "accepted"}:
            return "accepted", None
        detail = str(value.get("error") or "Provider did not accept Steer")
        if value.get("ok") is False and value.get("outcome") == "rejected":
            return "rejected", detail
        return "unknown", detail
    # Legacy untyped failures have no proof of rejection. Never parse prose.
    return "unknown", str(value)


async def steer_queue_item(session_id: str, item_id: str, expected_revision: int, *, outcome=None):
    """Use the original durable item; serialize controls with lifecycle and FIFO."""
    key = (session_id, item_id)
    if outcome is None:
        outcome = {}
    outcome["status"] = "rejected"  # No write until the synchronous boundary.
    if key in _queue_steer_tasks:
        outcome["status"] = "unknown"
        return "Queue Steer is already awaiting a provider receipt"
    task = asyncio.create_task(_steer_queue_item(session_id, item_id, expected_revision, outcome=outcome))
    _queue_steer_tasks[key] = task
    def finished(done):
        _queue_steer_tasks.pop(key, None)
        if not done.cancelled():
            done.exception()  # Retain exception ownership after an HTTP timeout.
    task.add_done_callback(finished)
    try:
        return await asyncio.wait_for(asyncio.shield(task), 10)
    except asyncio.TimeoutError:
        outcome["status"] = "unknown"  # The server owner may still attempt delivery.
        return "Steer awaiting provider receipt; item retained and blocked against duplicate delivery"


async def _steer_queue_item(session_id: str, item_id: str, expected_revision: int, *, outcome=None):
    if outcome is None:
        outcome = {"status": "rejected"}
    async with AsyncExitStack() as admission:
        lock = await _session_spawn_lock(session_id)
        async with lock:
            w = find_alive_worker_by_session(session_id)
            s = _get_session_shallow(session_id)
            if s is None:
                return "Session not found"
            # Match startup's spawn -> Session reservation order. Keep the
            # Action claim through the actual write/drain/receipt owner, while
            # releasing the spawn/queue locks before waiting for provider IO.
            from packages.plugins.lifecycle import session_claim, action_groups, pinned
            await admission.enter_async_context(session_claim(s))
            if set(pinned(s.id)[0]) - action_groups(s.adapter_config):
                request_delayed_restart(s.id)
                return "Loaded Action Group detached; restart Worker before queue Steer"
            if s.readonly_session:
                return READONLY_SESSION_ERROR
            if w is None or w.status != "running":
                return "Queue Steer requires an existing running Worker; idle restart has priority"
            if not getattr(w.adapter, "supports_native_steer", False):
                return "Adapter does not support native Steer"
            report_context = await _auto_report.context()
            async with queue_lock(session_id):
                if s.readonly_session:
                    return READONLY_SESSION_ERROR
                item = next((it for it in s.queue_pending if _queue_item_id(it) == item_id), None)
                if item is None or _delivery_state(item) != _DELIVERY_QUEUED:
                    return "Queue item is no longer queued"
                if item.get("revision", 1) != expected_revision:
                    return "Queue item revision conflict"
                if s.queue_paused:
                    return "Pause everything blocks queue Steer"
                eligible_item = dict(item) if item.get("forceSend") else item
                if item.get("forceSend"):
                    eligible_item.pop("forceSendHeld", None)
                if not queue_item_eligible(s, eligible_item):
                    return "Queue item is locked or awaiting Steer confirmation"
                if any(queue_item_edit_locked(s, identity) for identity in list(s.queue_edit_locks)):
                    return "Cannot Steer while a queued message is being edited"
                items = _report_consume_unit(s, item) if item.get("reportConsumeIds") else [item]
                if (not items or items[0] is not item
                        or (item.get("reportConsumeIds")
                            and any(not queue_item_eligible(s, row) for row in items))):
                    return "Report batch is unavailable"
                kind = _queue_item_kind(item)
                if kind == "task":
                    if not _is_valid_task_item(item) or _task_source(item) is None:
                        return "Task source/body cannot be preserved losslessly"
                    history_text = item["text"]
                    parts = item.get("parts")
                    if isinstance(parts, list) and any(
                            not isinstance(part, dict) or part.get("type") not in {"text", "attachment"}
                            or (part.get("type") == "text" and not isinstance(part.get("text", part.get("value")), str))
                            or (part.get("type") == "attachment" and not part.get("__serverPath"))
                            for part in parts):
                        return "Task parts cannot be steered losslessly; canonical attachment paths are required"
                    try:
                        native_text = project_message_parts(parts, history_text)
                    except ValueError as exc:
                        return f"Queue attachment cannot be steered: {exc}"
                elif kind in {"report", "qq", "wechat"}:
                    if any(row.get("parts") for row in items):
                        return "Structured report/channel attachments cannot be steered losslessly"
                    history_text = native_text = _format_report_batch(items)
                else:
                    return "Unknown queue kind cannot be steered losslessly"
                # Encode before reserving: encoding failure proves no provider write.
                request_id = uuid.uuid4().hex
                process, generation = w.process, w.generation
                stdin = getattr(process, "stdin", None)
                runtime = (w, generation, process, stdin)
                origin = _steer_origin(w) if getattr(w.adapter, "steer_requires_run_id", False) else None
                try:
                    wire = w.adapter.encode_control_message(
                        _prepare_steer_control(w, native_text, request_id, origin)) + b"\n"
                except (AttributeError, TypeError, ValueError, RuntimeError, OSError) as exc:
                    return f"Steer encoding failed before handoff: {exc}"
                admission_candidates = copy.deepcopy(items)
                _auto_report.admission(admission_candidates, *report_context)
                admission_execution_seq = w._execution_seq
                # Apply, write and failed rollback inside the same writer ticket.
                durable = _sess.DurableOutcome()
                def apply_reservation():
                    originals = [(row, dict(row)) for row in items]
                    old_revision = s.queue_revision
                    try:
                        for row, candidate in zip(items, admission_candidates):
                            row["autoReportAdmission"] = candidate["autoReportAdmission"]
                            row["autoReportExecutionSeq"] = admission_execution_seq
                            row["queueSteerPending"] = True
                            row["queueSteerRequestId"] = request_id
                            row["queueSteerWorkerId"] = w.worker_id
                            row["queueSteerGeneration"] = generation
                            if origin is not None:
                                row["queueSteerOrigin"] = origin
                        s.queue_revision += 1
                        _sess._save_body(s)
                    except BaseException:
                        for row, original in originals:
                            row.clear()
                            row.update(original)
                        s.queue_revision = old_revision
                        raise
                try:
                    await _sess._persist_async_outcome(s.id, apply_reservation, durable)
                except BaseException:
                    if not durable.succeeded:
                        raise
                    # Cancellation may follow a successful ticket. In either case
                    # no control has been written: durably release the reservation.
                    for row in items:
                        row.pop("queueSteerPending", None)
                        row.pop("queueSteerRequestId", None)
                        row.pop("queueSteerWorkerId", None)
                        row.pop("queueSteerGeneration", None)
                        row.pop("queueSteerOrigin", None)
                    s.queue_revision += 1
                    cleanup = _own_queue_steer_task(_persist_queue_steer_state(w, s))
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue
                    raise
                def still_allowed(row):
                    candidate = dict(row)
                    candidate.pop("queueSteerPending", None)
                    if candidate.get("forceSend"):
                        candidate.pop("forceSendHeld", None)
                    return queue_item_eligible(s, candidate)
                if (s.readonly_session or (item.get("reportConsumeIds") and _offline.is_illegal(s))
                        or any(not still_allowed(row) for row in items)
                        or any(queue_item_edit_locked(s, identity) for identity in list(s.queue_edit_locks))
                        or w.native_control_mode != "pan" or not _control_runtime_matches(w.worker_id, runtime)
                        or w.status != "running" or (origin is not None and
                        (w._terminal_handled or w._steer_run_id != origin["runId"]))
                        or process is None or process.returncode is not None or stdin is None):
                    for row in items:
                        row.pop("queueSteerPending", None)
                        row.pop("queueSteerRequestId", None)
                        row.pop("queueSteerWorkerId", None)
                        row.pop("queueSteerGeneration", None)
                        row.pop("queueSteerOrigin", None)
                    s.queue_revision += 1
                    await _persist_queue_steer_state(w, s)
                    return "Worker turn ended before Steer handoff"
                future = asyncio.get_running_loop().create_future()
                wants_receipt = getattr(w.adapter, "supports_steer_receipt", False)
                if wants_receipt:
                    w._steer_receipts[request_id] = future
                    w._steer_contexts[request_id] = {"runtime": runtime, "origin": origin or _steer_origin(w)}
                    def settle_receipt(done):
                        if w._steer_receipts.get(request_id) is done:
                            w._steer_receipts.pop(request_id, None)
                        w._steer_contexts.pop(request_id, None)
                        if not done.cancelled():
                            disposition, detail = _steer_receipt_result(done.result())
                            _own_queue_steer_task(_settle_queue_steer(
                                w, s, items, history_text, detail, request_id, generation,
                                disposition=disposition))
                    future.add_done_callback(settle_receipt)
                # This synchronous write is the pause/lease linearization boundary.
                # Once attempted, even a write/drain error is conservatively unknown.
                write_error = None
                outcome["status"] = "unknown"
                try:
                    written = stdin.write(wire)
                    if isinstance(written, int) and written != len(wire):
                        raise OSError("partial Steer control write")
                except (BrokenPipeError, ConnectionError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    write_error = str(exc)
                owner = _own_queue_steer_task(_finish_queue_steer(
                    w, s, items, history_text, request_id, generation, process,
                    future if wants_receipt else None, write_error, outcome=outcome, stdin=stdin))
        # No spawn/queue lock spans provider IO; Action reservation remains.
        return await _sess.await_persistence(owner)


async def _finish_queue_steer(w, s, item, text, request_id, generation, process, future, write_error, *, outcome=None, stdin=None):
    await _queue_steer_bcast({"type": "queue.snapshot", "sessionId": s.id,
                             "queueRevision": s.queue_revision})
    drain = asyncio.create_task((stdin if stdin is not None else process.stdin).drain()) if write_error is None else None
    error = "Steer outcome unknown: control write/drain failed"
    disposition = "unknown"
    deadline = asyncio.get_running_loop().time() + 30
    try:
        if drain is not None:
            waiting = {drain} | ({future} if future is not None else set())
            await asyncio.wait(waiting, timeout=30, return_when=asyncio.FIRST_COMPLETED)
        if future is not None:
            if not future.done():
                await asyncio.wait({future}, timeout=max(0, deadline-asyncio.get_running_loop().time()))
            if future.done() and not future.cancelled():
                disposition, error = _steer_receipt_result(future.result())
            else:
                error = "Steer outcome unknown: provider receipt missing; item retained, no automatic retry"
        elif drain is not None and drain.done() and not drain.cancelled() and drain.exception() is None:
            disposition = "accepted"
            error = None
        await _settle_queue_steer(w, s, item, text, error, request_id, generation, disposition=disposition)
        if outcome is not None:
            outcome["status"] = disposition
        return error
    finally:
        if drain is not None:
            if not drain.done():
                drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)


async def _settle_queue_steer(w: Worker, s, item: dict, history_text: str, error: str | None,
                            request_id: str, generation: int, *, disposition: str = "unknown"):
    """Apply an authoritative receipt, including one arriving after HTTP timeout."""
    items = item if isinstance(item, list) else [item]
    item = items[0]
    delivery = None
    async with queue_lock(s.id):
        if any(not any(candidate is row for candidate in s.queue_pending)
               or not row.get("queueSteerPending") or row.get("queueSteerRequestId") != request_id
               or row.get("queueSteerWorkerId") != w.worker_id or row.get("queueSteerGeneration") != generation
               for row in items):
            return
        if error:
            for row in items:
                if disposition == "rejected":
                    for key in ("queueSteerPending", "queueSteerRequestId", "queueSteerWorkerId",
                                "queueSteerGeneration", "queueSteerOrigin"):
                        row.pop(key, None)
                row["lastSteerError"] = error
            s.queue_revision += 1
            await _persist_queue_steer_state(w, s)
            if (not item.get("queueSteerPending") and workers.get(w.worker_id) is w
                    and w.generation == generation):
                _wake_after_queue_unit(w, s)
        else:
            origin = item.get("queueSteerOrigin")
            for member in items:
                for key in ("queueSteerPending", "queueSteerRequestId", "queueSteerWorkerId",
                            "queueSteerGeneration", "queueSteerOrigin"):
                    member.pop(key, None)
            # Settle original receipts even after retirement, but never attach an
            # old provider acceptance to another generation/execution's policy.
            additions = [member["autoReportAdmission"] for member in items
                         if member.get("autoReportExecutionSeq") == w._execution_seq
                         and isinstance(member.get("autoReportAdmission"), dict)]
            if (additions and workers.get(w.worker_id) is w
                    and w.generation == generation and not w._terminal_handled):
                previous = w._auto_report_admission or {"version": 1, "inputs": [], "enabled": False}
                w._auto_report_admission = {"version": 1,
                    "inputs": [*previous["inputs"], *copy.deepcopy(additions)],
                    "enabled": previous["enabled"] or any(d["enabled"] and d["origin"]["kind"] != "report" for d in additions)}
                if s.offline_run:
                    s.offline_run["autoReportAdmission"] = copy.deepcopy(w._auto_report_admission)
            row = _queue_history_row(items, history_text)
            if origin is not None:
                row["steer"] = dict(origin)
            _sess.append_history(s, row)
            # One native admission, one history row, one receipt per original ID.
            for member in items:
                _set_delivery_state(s, member, _DELIVERY_SENT)
                if origin is not None:
                    s.queue_delivery_ledger[_queue_item_id(member)]["steer"] = dict(origin)
            s.queue_revision += 1
            _remove_queue_items_by_identity(s, items)
            await _persist_queue_steer_state(w, s)
            delivery = {"type": "queue.item_delivered", "sessionId": s.id,
                          "queueItemIds": [_queue_item_id(member) for member in items], "queueRevision": s.queue_revision,
                          "messages": [_public_queue_receipt({**row, **_queue_origin_metadata(items)})]}
    if delivery is not None:
        await _queue_steer_bcast(delivery)
    await _queue_steer_bcast({"type": "queue.snapshot", "sessionId": s.id,
                  "queueRevision": s.queue_revision})

# ── Automatic Force send for newly enqueued structured sources ──
#
# App Settings → Behavior exposes three independent switches.  A newly
# enqueued item is classified from its durable source fields (never from the
# body text), then routed through the same capability path as an explicit
# Force send:
#   running + native Steer → steer the original item by id;
#   running + no Steer     → move the original item to the queue head and
#                            restart + send;
#   idle/no Worker/empty   → ordinary durable handoff (no restart).
# The queue item is never copied, so its queueItemId, source, task, parts and
# body identity stay authoritative.  Pause, read-only, Illegal, locks, edit
# leases, delayed restarts and an unconfirmed/rejected Steer retain the item
# and are never bypassed.

_auto_force_tasks: dict[tuple[str, str], asyncio.Task] = {}


def auto_force_settings() -> dict[str, bool]:
    """Read the three persisted switches strictly (missing/invalid = off)."""
    ui = load_config().get("ui") or {}
    raw = ui.get("autoForceSend")
    if not isinstance(raw, dict):
        raw = {}
    return {name: raw.get(name) is True for name in AUTO_FORCE_SOURCE_CLASSES}


def _is_manager_ancestor(source_session_id, target) -> bool:
    """Whether *source_session_id* is an ancestor in the target's manager chain.

    MA messages are structurally identified by the managedBy relation, not by
    the ``////by agent`` body prefix or by the current template/MCP mount.
    """
    if not isinstance(source_session_id, str) or not source_session_id:
        return False
    target_id = getattr(target, "id", None)
    if target is None or source_session_id == target_id:
        return False
    seen = {target_id}
    current = getattr(target, "managed_by", None)
    while isinstance(current, str) and current and current not in seen:
        if current == source_session_id:
            return True
        seen.add(current)
        manager = _get_session_shallow(current)
        current = getattr(manager, "managed_by", None) if manager is not None else None
    return False


def classify_auto_force_source(item: dict, target) -> str | None:
    """Classify one durable queue row as system(job) / ma / report / None.

    Only structured fields participate.  A report/notice row is a Job
    terminal only with the explicit ``noticeKind`` (or the legacy terminal
    envelope); other automation notices stay unclassified.  An agent task is
    an MA message only when its sender Session is an ancestor of the target
    and it is not a formal assign dispatch.
    """
    if not isinstance(item, dict) or target is None:
        return None
    kind = _queue_item_kind(item)
    if kind is None:
        return None
    source = _task_source(item) if kind == "task" else item.get("source")
    if kind == "task" and source == "report" and item.get("explicitReport"):
        return None if _is_formal_task_item(item) else "report"
    if kind == "report":
        if item.get("noticeKind") == "background_job_terminal":
            return "system"
        envelope = item.get("envelope") if isinstance(item.get("envelope"), dict) else {}
        event_id = item.get("eventId")
        legacy_job_terminal = (
            source == "automation"
            and (item.get("jobId") is not None
                 or envelope.get("jobId") is not None
                 or (isinstance(event_id, str) and event_id.endswith(":terminal")))
        )
        if legacy_job_terminal:
            return "system"
        if source == "report":
            return "report"
        if source not in {"agent", "meta-agent"}:
            return None
    elif kind == "task":
        if source not in {"agent", "meta-agent"}:
            return None
        # A formal assign owns taskId idempotency and is a task dispatch, not
        # a message that may be injected into a running turn.
        if item.get("taskIdSource") == "assign":
            return None
    else:
        return None
    if source == "meta-agent":
        return "ma"
    source_session_id = item.get("sourceSessionId")
    if isinstance(source_session_id, str) and _is_manager_ancestor(source_session_id, target):
        return "ma"
    return None


def _schedule_auto_force(session_id: str, item: dict) -> asyncio.Task | None:
    """Start one event-driven automatic Force attempt for a just-enqueued row."""
    target = _get_session_shallow(session_id)
    if (target is None or target.readonly_session
            or getattr(target, "queue_paused", False)
            or _offline.is_illegal(target)):
        return None
    source_class = classify_auto_force_source(item, target)
    if source_class is None or not auto_force_settings().get(source_class):
        return None
    item_id = _queue_item_id(item)
    key = (session_id, item_id)
    existing = _auto_force_tasks.get(key)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(_auto_force_item(session_id, item_id, source_class))
    _auto_force_tasks[key] = task

    def finished(done):
        if _auto_force_tasks.get(key) is done:
            _auto_force_tasks.pop(key, None)
        if not done.cancelled():
            exc = done.exception()
            if exc is not None:
                _log.warning(
                    "[Session %s] automatic Force attempt failed: %s",
                    session_id, exc)

    task.add_done_callback(finished)
    return task


async def wait_auto_force(session_id: str | None = None, timeout: float = 15.0) -> None:
    """Await in-flight automatic Force attempts (event-driven, no polling)."""
    tasks = [
        task for (queued_session_id, _), task in list(_auto_force_tasks.items())
        if session_id is None or queued_session_id == session_id
    ]
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


def _move_queue_item_to_front(s, item: dict) -> bool:
    """Move one existing row to the absolute head and renumber positions."""
    pending = list(s.queue_pending or [])
    index = next((position for position, queued in enumerate(pending)
                  if queued is item), None)
    if index is None or index == 0:
        return False
    pending.pop(index)
    pending.insert(0, item)
    s.queue_pending = pending
    for position, queued in enumerate(pending):
        if isinstance(queued, dict):
            queued["position"] = position
    return True


async def _auto_force_item(session_id: str, item_id: str,
                           source_class: str) -> dict:
    """Route one existing durable item through automatic Force semantics."""
    s = _get_session_shallow(session_id)
    if s is None:
        return {"status": "skipped", "reason": "session_missing"}
    if s.readonly_session:
        return {"status": "skipped", "reason": "readonly"}
    if getattr(s, "queue_paused", False):
        return {"status": "skipped", "reason": "paused"}
    if _offline.is_illegal(s):
        return {"status": "skipped", "reason": "illegal"}
    item = next((queued for queued in (s.queue_pending or [])
                 if isinstance(queued, dict)
                 and _queue_item_id(queued) == item_id), None)
    if item is None or item.get("reportConsumeIds"):
        return {"status": "skipped", "reason": "not_queued"}
    if not auto_force_settings().get(source_class):
        return {"status": "skipped", "reason": "disabled"}
    if classify_auto_force_source(item, s) != source_class:
        return {"status": "skipped", "reason": "source_changed"}
    # A prior attempt that reached the provider, or one awaiting a receipt,
    # is never repeated automatically.  A rejected/unknown row stays durable
    # for ordinary FIFO or an explicit caller retry.
    if item.get("queueSteerPending") or item.get("lastSteerError"):
        return {"status": "skipped", "reason": "previous_attempt"}
    w = find_alive_worker_by_session(session_id)
    if w is None:
        return {"status": "skipped", "reason": "no_live_worker"}
    route = steer_delivery_route(w, s)
    if route == "steer":
        disposition = {}
        error = await steer_queue_item(
            session_id, item_id, item.get("revision", 1), outcome=disposition)
        return {
            "status": disposition.get("status", "unknown" if error else "accepted"),
            "error": error,
            "route": "steer",
        }
    if route == "restart":
        return await _auto_force_restart_item(session_id, item_id)
    return {"status": "skipped", "reason": route}


async def _auto_force_restart_item(session_id: str, item_id: str) -> dict:
    """Move the original item to the head, then restart and send it once."""
    lock = await _session_spawn_lock(session_id)
    async with lock:
        s = _get_session_shallow(session_id)
        if s is None:
            return {"status": "skipped", "reason": "session_missing"}
        if s.readonly_session:
            return {"status": "skipped", "reason": "readonly"}
        if getattr(s, "queue_paused", False):
            return {"status": "skipped", "reason": "paused"}
        if _offline.is_illegal(s):
            return {"status": "skipped", "reason": "illegal"}
        w = find_alive_worker_by_session(session_id)
        if w is None or w.status != "running":
            return {"status": "skipped", "reason": "not_running"}
        if steer_delivery_route(w, s) != "restart":
            return {"status": "skipped", "reason": "route_changed"}
        async with queue_lock(s.id):
            if s.readonly_session or _offline.is_illegal(s):
                return {"status": "skipped", "reason": "illegal"}
            if getattr(s, "queue_paused", False):
                return {"status": "skipped", "reason": "paused"}
            item = next((queued for queued in (s.queue_pending or [])
                         if isinstance(queued, dict)
                         and _queue_item_id(queued) == item_id), None)
            if item is None or item.get("reportConsumeIds") or _delivery_state(item) != _DELIVERY_QUEUED:
                return {"status": "skipped", "reason": "not_queued"}
            if (queue_item_edit_locked(s, item_id)
                    or _queue_item_locked(item)
                    or not queue_item_eligible(s, item)
                    or any(queue_item_edit_locked(s, identity)
                           for identity in list(getattr(s, "queue_edit_locks", {})))):
                return {"status": "skipped", "reason": "locked"}
            old_pending = list(s.queue_pending or [])
            old_positions = {id(queued): queued.get("position")
                             for queued in old_pending if isinstance(queued, dict)}
            old_revision = s.queue_revision
            _move_queue_item_to_front(s, item)
            s.queue_revision = old_revision + 1
            try:
                await _save_receipt(s)
            except Exception:
                s.queue_pending = old_pending
                for queued in old_pending:
                    if isinstance(queued, dict):
                        if old_positions.get(id(queued)) is None:
                            queued.pop("position", None)
                        else:
                            queued["position"] = old_positions[id(queued)]
                s.queue_revision = old_revision
                raise
            queue_revision = s.queue_revision
        await _bcast({"type": "queue.snapshot", "sessionId": session_id,
                      "queueRevision": queue_revision})
        # The snapshot broadcast yields after ordering. A newly detected
        # incident still owns admission; automatic Force cannot recover it.
        await _prepare_offline_recovery(s)
        if _offline.is_illegal(s):
            return {"status": "skipped", "reason": "illegal"}
        if (s.readonly_session or getattr(s, "queue_paused", False)
                or find_alive_worker_by_session(session_id) is not w
                or steer_delivery_route(w, s) != "restart"
                or not queue_item_eligible(s, item)
                or any(queue_item_edit_locked(s, identity)
                       for identity in list(getattr(s, "queue_edit_locks", {})))):
            return {"status": "skipped", "reason": "gate_changed"}
        error = await _restart_worker_unlocked(w.worker_id)
        if error:
            return {"status": "error", "error": error, "route": "restart"}
        _recovery_required.discard(session_id)
        return {"status": "restarted", "route": "restart",
                "workerId": w.worker_id, "queueItemId": item_id}


async def _interrupt_worker_unlocked(worker_id: str) -> str | None:
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    if w.status != "running":
        return "Worker is not running"
    if w.adapter.name == "cbc" and w.native_control_enabled:
        receipt = await write_native_control(worker_id, {
            "type": "interrupt", "generation": w.native_control_generation})
        # Cancellation uncertainty must not secretly become kill/resume of
        # the resident CBC kernel. FinalStop will establish its actual result.
        return None if receipt.get("ok") is True else "CBC interruption was not confirmed"
    # Codex app-server can interrupt the active turn while preserving the
    # long-lived native thread and app-server process.  Fall back to the
    # established kill+resume path for adapters without this optional
    # capability or when the control write fails.
    if getattr(w.adapter, "supports_native_interrupt", False):
        # A previous turn may have left the event set during the tiny window
        # before this turn's consumer starts waiting.  Do not let that stale
        # completion suppress the native-interrupt fallback.
        if w._task_done is not None:
            w._task_done.clear()
        err = await send_control_message(worker_id, {"type": "interrupt"})
        if err is None:
            # A successful stdin write is not proof that the native provider
            # honoured the interrupt.  Keep its cheap in-process path, but
            # bound it: a stuck turn is recovered through the same restart
            # route as non-native adapters.
            generation = w.generation
            if w._interrupt_guard_task is not None:
                await _cancel_worker_task(w._interrupt_guard_task)
            w._interrupt_guard_task = asyncio.create_task(
                _restart_if_native_interrupt_stalls(w, generation))
            return None
        _log.warning("[Worker %s] native interrupt write failed: %s; restarting", worker_id, err)
    return await _restart_worker_unlocked(worker_id)


async def interrupt_worker(worker_id: str) -> str | None:
    """Serialized compatibility wrapper for worker-id callers."""
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    generation = w.generation
    lock = await _session_spawn_lock(w.session_id)
    async with lock:
        current = workers.get(worker_id)
        if current is not w or w.generation != generation:
            return None
        return await _interrupt_worker_unlocked(worker_id)


async def interrupt_session_worker(session_id: str) -> Worker | str | None:
    """Interrupt the live worker for a session, if present."""
    if _get_session_shallow(session_id) is None:
        return f"Session {session_id} not found"
    lock = await _session_spawn_lock(session_id)
    async with lock:
        w = find_alive_worker_by_session(session_id)
        if w is None:
            return None
        error = await _interrupt_worker_unlocked(w.worker_id)
        return error or w


_ACCEPTED_INPUT_ID_LIMIT = getattr(_sess, "ACCEPTED_INPUT_ID_MAX", 256)


def _has_accepted_input_id(s, client_message_id: str | None) -> bool:
    if not client_message_id:
        return False
    _ensure_idempotency_index(s)
    if client_message_id in (
            (getattr(s, "queue_idempotency_index", {}) or {})
            .get("clientMessageId", {})):
        return True
    return client_message_id in (getattr(s, "accepted_input_ids", []) or [])


def _durable_task_id_seen(s, task_id: str | None) -> bool:
    """Whether a task id already crossed or is waiting at the receipt boundary."""
    if not task_id or s is None:
        return False
    _ensure_idempotency_index(s)
    return task_id in (
        (getattr(s, "queue_idempotency_index", {}) or {}).get("taskId", {})
    )


async def _persist_task_item(s, text: str, source: str, seq: int | None,
                             task_id: str | None,
                             client_message_id: str | None,
                             source_session_id: str | None = None,
                             parts: list[dict] | None = None,
                             *, idempotent_task_id: bool = True,
                             activate_task: bool = False,
                             locked: bool = False, wake: bool = False,
                             auto_force: bool = True,
                             source_plugin_id: str | None = None) -> tuple[dict | None, str | None]:
    """Durably append one task, atomically with the browser receipt ledger.

    ``auto_force=False`` is used by the explicit Force branches, which already
    choose their delivery route; every ordinary enqueue schedules the
    automatic-source classifier after the durable write succeeds.
    """
    if _shutdown_started:
        return None, "Pan main service is shutting down"
    source_type, source_sid, source_error = _validate_source_metadata(
        s, source, source_session_id)
    if source_error:
        return None, source_error
    if source_plugin_id is not None and not any(p["id"] == source_plugin_id for p in (await _auto_report.context())[1]):
        return None, "Plugin has no registered message source capability"
    _ensure_idempotency_index(s)
    if client_message_id:
        existing = _find_queue_item_by_idempotency(
            s, client_message_id=client_message_id)
        if existing is not None and not existing.get("receiptOnly"):
            # A retry addresses the original durable receipt and may only
            # re-arm it; it must never create a second queue item.
            return existing, None
        if _has_accepted_input_id(s, client_message_id):
            return None, None
    # taskId is the durable idempotency key for orchestration callers.  The
    # in-memory registry is only an acceleration layer; queue/history checks
    # keep a retry after process restart or registry TTL expiry from running a
    # second provider turn.
    if idempotent_task_id and task_id:
        existing = _find_queue_item_by_idempotency(s, task_id=task_id)
        if existing is not None and not existing.get("receiptOnly"):
            return existing, None
        if _durable_task_id_seen(s, task_id):
            return None, None
    metadata = {}
    if seq is None:
        seq = s.task_seq + 1
        metadata["task_seq"] = seq
    item = {
        "type": "task",
        "kind": "task",
        "id": "q_" + uuid.uuid4().hex,
        "queueItemId": None,
        "text": text,
        "source": source_type,
        "seq": seq,
        "taskId": task_id,
        "deliveryState": _DELIVERY_QUEUED,
        "dispatchState": _DELIVERY_QUEUED,
        "revision": 1,
        "createdAt": time.time(),
    }
    if source_plugin_id is not None:
        item["sourcePluginId"] = source_plugin_id
    if source_type == "report" and source_sid:
        item["explicitReport"] = True
        if getattr(s, "agent_reports_paused", False):
            _message_locks.manual_change(item, None, True)
    if locked:
        _message_locks.set_lock(item, "manual")
    if task_id is not None:
        # An inherited id is reporting context only; an assign id is also the
        # durable idempotency key.
        item["taskIdSource"] = "assign" if idempotent_task_id else "active"
    item["queueItemId"] = item["id"]
    if source_sid is not None:
        item["sourceSessionId"] = source_sid
    if parts is not None:
        # The web boundary has already validated and canonicalized these parts.
        # Keep them beside the text fallback so history/retry/restart can restore
        # attachment identity and editor position even for text-only adapters.
        item["parts"] = [dict(part) for part in parts]
    if client_message_id:
        item["clientMessageId"] = client_message_id
        metadata["accepted_input_ids"] = (
            list(s.accepted_input_ids) + [client_message_id])[-_ACCEPTED_INPUT_ID_LIMIT:]
    if activate_task:
        metadata["active_task_id"] = task_id
    try:
        item = await _append_new_queue_item(s, item, wake=wake, metadata=metadata)
    except Exception as exc:
        # The ordered writer staged everything, including browser idempotency.
        return None, f"Failed to persist queued task: {exc}"
    _log.info("[Session %s] queued task id=%s source=%s sourceSessionId=%s",
              s.id, item["id"], source_type, source_sid)
    await _bcast({"type": "queue.item_added", "sessionId": s.id,
                  "queueItemId": item["id"],
                  "queueRevision": s.queue_revision,
                  "item": {
                      **item,
                      **({"parts": public_message_parts(item.get("parts"))}
                         if isinstance(item.get("parts"), list) else {}),
                  }})
    if auto_force:
        _schedule_auto_force(s.id, item)
    return item, None


def configured_wake_message() -> str:
    from .config import load_config
    raw = load_config().get("session_lifecycle", {})
    value = raw.get("wakeMessage") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value.strip() and len(value) <= 32000 else "继续"


async def wake_session(session_id: str, *, client_message_id: str | None = None,
                       source: str = "user", source_session_id: str | None = None) -> dict:
    """Explicit wake: enqueue, release only its Illegal lock and place first.

    Persist all three in one ordered write. A repeated receipt never moves or
    unlocks an existing item again. Actual successor readiness clears Illegal.
    """
    s = _get_session_shallow(session_id)
    if s is None:
        return {"status": "error", "result": f"Session {session_id} not found"}
    async with queue_lock(session_id):
        item, error = await _persist_task_item(s, configured_wake_message(), source,
            None, None, client_message_id, source_session_id, wake=True, auto_force=False)
        if error:
            return {"status": "error", "result": error}
        item_id = _queue_item_id(item) if item else None
    await _wake_worker(session_id, auto_spawn=False)
    return {"status": "queued", "sessionId": session_id, "queueItemId": item_id,
            "queuePaused": bool(s.queue_paused)}


async def enqueue_user_message(session_id: str, text: str,
                               client_message_id: str | None = None,
                               parts: list[dict] | None = None,
                               *, locked: bool = False) -> dict:
    """Canonical durable entry point for browser/user queue messages.

    The queue item and receipt are written before acknowledging the request.
    Retrying the same clientMessageId resolves the original pending item or
    durable ledger receipt and never creates a second queue item.
    """
    s = _get_session_shallow(session_id)
    if s is None:
        return {"status": "error", "result": f"Session {session_id} not found"}
    async with queue_lock(session_id):
        migrated = _recover_delivery_states(s)
        migrated = _sync_queued_ledger(s) or migrated
        prepared = _prepare_receipt_persistence(s)
        if migrated or prepared:
            await _save_receipt(s)
        existing = _find_queue_item_by_idempotency(
            s, client_message_id=client_message_id)
        if existing is not None:
            state = _delivery_state(existing)
            return {
                "status": "sent" if state == _DELIVERY_SENT else "queued",
                "sessionId": session_id,
                "workerId": None,
                "queueItemId": _queue_item_id(existing),
                "item": dict(existing),
                "duplicate": True,
            }
        if parts is None:
            item, error = await _persist_task_item(
                s, text, "user", None, None, client_message_id, None,
                locked=locked, auto_force=False)
        else:
            item, error = await _persist_task_item(
                s, text, "user", None, None, client_message_id, None, parts,
                locked=locked, auto_force=False)
        if error:
            return {"status": "error", "result": error}
        if item is None:
            existing = _find_queue_item_by_idempotency(
                s, client_message_id=client_message_id)
            if existing is None:
                return {"status": "error", "result": "queue idempotency lookup failed"}
            item = existing
        live = find_alive_worker_by_session(session_id)
        if live is not None and item in (s.queue_pending or []):
            if live.pending_signal is not None and _has_sendable_queued_items(s):
                await live.pending_signal.put({"type": "queue_signal"})
            else:
                _schedule_queue_retry(session_id)
        elif (live is None and item in (s.queue_pending or [])
              and _has_dispatchable_items(s)):
            _schedule_session_recovery(session_id)
        return {
            "status": "queued",
            "workerId": live.worker_id if live else None,
            "sessionId": session_id,
            "queueItemId": _queue_item_id(item),
            "item": dict(item),
            "duplicate": False,
        }


def _pending_item_id(item: dict) -> str:
    """Return the same stable id exposed by the queue API for any item."""
    if item.get("id"):
        return str(item["id"])
    identity = {k: v for k, v in item.items() if k not in {
        "deliveryState", "reservedBy", "reservedGeneration", "reservedAt",
        "deliveryAttempts", "nextAttemptAt", "lastDeliveryError",
    }}
    canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False, default=str)
    return "sha1:" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _worker_owns_queue_item(w: Worker | None, item: dict) -> bool:
    """Whether a live worker currently has this item in its execution context."""
    if w is None:
        return False
    if w._current_queue_item is item:
        return True
    return any(existing is item for existing in (w._current_report_items or []))


async def retry_pending_item(session_id: str, item_id: str) -> dict | str:
    """Retry the original queued receipt without creating a second item."""
    s = _get_session_shallow(session_id)
    if not s:
        return f"Session {session_id} not found"
    async with queue_lock(session_id):
        _recover_delivery_states(s)
        item = next((it for it in (s.queue_pending or [])
                     if isinstance(it, dict) and _queue_item_id(it) == str(item_id)), None)
        if item is None:
            record = _ledger_record(s, item_id)
            if record is None:
                return f"Queue item {item_id} not found"
            state = _delivery_state(record)
            if state == _DELIVERY_SENT:
                return {"status": "sent", "sessionId": session_id,
                        "queueItemId": str(item_id), "item": dict(record),
                        "duplicate": True}
            item = dict(record)
            item["deliveryState"] = _DELIVERY_QUEUED
            item["dispatchState"] = _DELIVERY_QUEUED
            s.queue_pending.append(item)
        if _delivery_state(item) != _DELIVERY_QUEUED:
            return {"status": _delivery_state(item),
                    "queueItemId": _queue_item_id(item)}
        item.pop("nextAttemptAt", None)
        item.pop("lastDeliveryError", None)
        _set_delivery_state(s, item, _DELIVERY_QUEUED)
        s.queue_revision = getattr(s, "queue_revision", 0) + 1
        await _save_receipt(s)
    w = find_alive_worker_by_session(session_id)
    if w and w.pending_signal is not None:
        w.pending_signal.put_nowait({"type": "queue_signal"})
    else:
        _schedule_session_recovery(session_id)
    return {"status": "queued", "sessionId": session_id,
            "queueItemId": _queue_item_id(item), "item": dict(item)}


async def _restart_if_native_interrupt_stalls(w: Worker, generation: int) -> None:
    """Fall back to restart when native interrupt has no terminal response."""
    event = w._task_done
    if event is None:
        return
    try:
        await asyncio.wait_for(event.wait(), timeout=10)
    except asyncio.TimeoutError:
        if workers.get(w.worker_id) is w and w.generation == generation and w.status == "running":
            _log.warning("[Worker %s] native interrupt timed out; forcing restart", w.worker_id)
            await restart_worker(w.worker_id)
    except asyncio.CancelledError:
        raise
    finally:
        if w._interrupt_guard_task is asyncio.current_task():
            w._interrupt_guard_task = None


async def send_task(worker_id: str, text: str, source: str = "agent",
                    seq: int | None = None, task_id: str | None = None,
                    client_message_id: str | None = None,
                    source_session_id: str | None = None,
                    idempotent_task_id: bool = True,
                    auto_force: bool = True) -> str | None:
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    if w.status == "held":
        return "Worker is held (takeover mode). Restart first."
    # In MCP mode, process is None (spawned per-task). Still allow signal queue.
    # 分配任务序号（result 与 task 配对用；外部可预分配传入，保证 item.seq 与期望一致）。
    # 计数器持久化在 session.task_seq 上，跨 worker respawn 保持单调递增；
    # 早期用 worker 实例属性 _task_counter，respawn 产生新 Worker 后从 1 重新计数。
    s = _session(w)
    if s is None:
        return f"Session {w.session_id} not found"
    _, source_sid, source_error = _validate_source_metadata(
        s, source, source_session_id)
    if source_error:
        return source_error
    if not _process_alive(w):
        return "Worker process dead"
    if w.pending_signal is None:
        return "Worker signal queue not ready"
    w.last_activity = time.monotonic()

    # 任务 item 持久化到 session.queue_pending（落盘真源）；pending_signal
    # 只放无正文 queue_signal，consumer 每次只看 FIFO 队首。worker 死亡/回收
    # 后由 create_worker / 全局 watchdog 自动恢复消费。
    async with queue_lock(s.id):
        item, persist_error = await _persist_task_item(
            s, text, source, seq, task_id, client_message_id, source_sid,
            idempotent_task_id=idempotent_task_id,
            activate_task=idempotent_task_id and task_id is not None,
            auto_force=auto_force)
    if persist_error:
        return persist_error
    if item is None:
        # Same browser message was durably accepted before an ack was lost.
        # Do not append another signal/task; the original is already pending
        # or completed and the caller can safely treat this as success.
        return None
    await w.pending_signal.put({"type": "queue_signal"})

    # queued：任务已入队、consumer 尚未取出。若队列前面还有任务，保持 queued 直到轮到它。
    if w.status in ("idle", "queued"):
        w.status = "queued"
        await _record_legal_worker_state(w, "queued", "task/queued")
        await _bcast({
            "type": "worker.status",
            "workerId": w.worker_id,
            "sessionId": w.session_id,
            "generation": w.generation,
            "status": "queued",
            "taskSeq": seq,
            "source": source,
            "sourceSessionId": source_sid,
        })
    return None


# ── 编排原语：assign / send（供 Meta-Agent 调用） ──


async def _ensure_worker(session_id: str) -> tuple[Worker | None, str | None]:
    """确保 session 有活的 worker。返回 (worker, None) 或 (None, error)。"""
    w = find_worker_by_session(session_id)
    if w is None or not _process_alive(w):
        target = _get_session_shallow(session_id)
        if target and _offline.outstanding(target):
            await _prepare_offline_recovery(target)
            if _offline.is_illegal(target):
                return None, "illegal_queue_hold"
        created = await create_worker(session_id)
        if isinstance(created, str):
            return None, created
        w = created
    return w, None


async def assign(session_id: str, text: str, source: str = "agent",
                 task_id: str | None = None,
                 source_session_id: str | None = None) -> dict:
    """异步分派：确保 worker 存在 → 发任务 → 立即返回 queued。

    完成时通过 worker.result 事件（配合 /ws/agent subscribe）回调。
    适用于并行 fan-out。

    task_id 幂等（内存注册表用于快速返回，Session.queue/history 作为持久兜底）：
    同一 taskId 重发不重复入队（防双跑）。若该 taskId 已存在：
    - 已完成（done/error/cancelled）→ 返回缓存结果（status/result）
    - 进行中 → 返回 {"status": "pending", "taskId": ...}，不重复入队
    用于超时后安全重试 / 并发去重。不带 task_id 行为不变。
    """
    if _shutdown_started:
        return {"status": "error", "result": "Pan main service is shutting down"}
    target = _get_session_shallow(session_id)
    if target is None:
        return {"status": "error", "result": f"Session {session_id} not found"}
    source_type, source_sid, source_error = _validate_source_metadata(
        target, source, source_session_id)
    if source_error:
        return {"status": "error", "result": source_error}
    if task_id is None:
        # An assign without a business id starts an unlabelled task and must
        # not accidentally inherit the previous completed assignment.
        target.active_task_id = None
    # 惰性清理过期条目（TTL），防止注册表长期运行无界增长（H2 泄漏）
    _prune_task_status()
    # Durable queue/history wins over the process-local registry.  A stale
    # registry error must not turn a task that already reached the CLI into a
    # second execution after the caller retries.
    if task_id is not None:
        durable_item = _find_queue_item_by_idempotency(target, task_id=task_id)
        if durable_item is not None:
            durable_state = _delivery_state(durable_item)
            if durable_state == _DELIVERY_SENT:
                return {
                    "status": "sent_to_cli",
                    "taskId": task_id,
                    "duplicate": True,
                    "receipt": dict(durable_item),
                }
            return {"status": "pending", "taskId": task_id,
                    "duplicate": True}

    # taskId 幂等检查
    if task_id is not None and task_id in _task_status:
        existing = _task_status[task_id]
        if existing["status"] in ("done", "error", "cancelled"):
            return dict(existing)
        return {"status": "pending", "taskId": task_id}
    # The registry is process-local and TTL-pruned.  Consult the session's
    # durable queue/history before spawning a worker so a late retry cannot
    # create a second execution after a Pan restart or TTL expiry.
    if task_id is not None:
        durable_session = _get_session_shallow(session_id)
        if _durable_task_id_seen(durable_session, task_id):
            return {"status": "pending", "taskId": task_id}

    # Reserve the id before the await below.  Without this synchronous
    # reservation, two concurrent assign calls with the same task_id can both
    # pass the check, then enqueue two distinct messages after _ensure_worker.
    task_reservation = None
    if task_id is not None:
        task_reservation = _task_status[task_id] = {
            "status": "pending", "workerId": None,
            "taskId": task_id, "ts": time.monotonic(),
        }

    if getattr(target, "queue_paused", False) or _offline.is_illegal(target):
        async with queue_lock(session_id):
            item, error = await _persist_task_item(
                target, text, source_type, None, task_id, None, source_sid,
                activate_task=True, auto_force=False)
        if error:
            if task_id is not None:
                _task_status.pop(task_id, None)
            return {"status": "error", "result": error}
        await _wake_worker(session_id, auto_spawn=False)
        return {"status": "queued", "workerId": None, "sessionId": session_id,
                "taskId": task_id, "queuePaused": bool(getattr(target, "queue_paused", False))}

    try:
        w, err = await _ensure_worker(session_id)
    except asyncio.CancelledError:
        if task_id is not None and _task_status.get(task_id) is task_reservation:
            _task_status.pop(task_id, None)
        raise
    if err == "illegal_queue_hold":
        async with queue_lock(session_id):
            item, error = await _persist_task_item(target, text, source_type, None,
                task_id, None, source_sid, activate_task=task_id is not None)
        if error and task_id is not None:
            _task_status.pop(task_id, None)
        return ({"status": "error", "result": error} if error else
                {"status": "queued", "sessionId": session_id, "taskId": task_id,
                 "queueItemId": _queue_item_id(item) if item else None})
    if err:
        if task_id is not None and _task_status.get(task_id) is task_reservation:
            task_reservation.update({
                "status": "error", "result": err, "ts": time.monotonic(),
            })
        return {"status": "error", "result": err}

    if task_id is not None:
        task_reservation["workerId"] = w.worker_id
        task_reservation["ts"] = time.monotonic()

    # A formal assign is a task dispatch, not a follow-up message: it must
    # queue behind the current turn instead of being injected as a Steer.
    send_kwargs = {"source": source_type, "task_id": task_id, "auto_force": False}
    if source_sid is not None:
        send_kwargs["source_session_id"] = source_sid
    send_err = await send_task(w.worker_id, text, **send_kwargs)
    if send_err:
        if task_id is not None:
            _task_status[task_id] = {"status": "error", "result": send_err,
                                     "taskId": task_id, "ts": time.monotonic()}
        return {"status": "error", "result": send_err}

    result = {"status": "queued", "workerId": w.worker_id, "sessionId": session_id}
    if task_id is not None:
        result["taskId"] = task_id
    return result


async def send(worker_id: str, text: str, source: str = "agent",
               source_session_id: str | None = None) -> dict:
    """向已有 worker 发消息（持续性多轮协作）。

    若 worker 已死返回 error（需先 spawn）。完成时通过 worker.result 事件回调。
    """
    w = workers.get(worker_id)
    if w is None:
        return {"status": "error", "result": "Worker not found"}
    if not _process_alive(w):
        return {"status": "error", "result": "Worker process dead"}
    s = _session(w)
    inherited_task_id = (
        getattr(s, "active_task_id", None)
        if source == "agent" and s is not None else None
    )
    send_err = await send_task(
        worker_id, text, source=source, task_id=inherited_task_id,
        source_session_id=source_session_id, idempotent_task_id=False)
    if send_err:
        return {"status": "error", "result": send_err}
    return {"status": "queued", "workerId": worker_id, "sessionId": w.session_id}


async def send_session(session_id: str, text: str, source: str = "agent",
                       force: bool = False,
                       client_message_id: str | None = None,
                       source_session_id: str | None = None) -> dict:
    """向 session（agent）发消息（阶段 6 寻址兼容）：编排对象是 agent，
    worker（CLI 进程）是顺带的。

    - 有活 worker（含 oneshot 注册，process 为 None）→ 常规投递；
      force=True 仅在 running 时按能力路由：支持原生 Steer 则按原队列项
      Steer；不支持才把原项提到队首后 restart+send。idle/queued/held 或
      空队列一律普通 handoff，不重启。
    - 无活 worker（从未 spawn / 进程已死）→ **不报错**：消息入该 session 的
      持久队列 queue_pending（type=task），由全局 watchdog（queue_pending
      非空 && 无活 worker → create_worker）spawn 后经 _recover_pending_signals
      补发无正文 queue_signal 分发——「send = 写给 agent」。
    - held（takeover 模式）→ 透传错误，不吞错不入队。

    Pan agent messages copy the Session's persisted active_task_id at enqueue
    time.  The copied value is report context only; it is not an assign
    idempotency key.  User-originated messages keep taskId=None.
    """
    if _shutdown_started:
        return {"status": "error", "result": "Pan main service is shutting down"}
    target = _get_session_shallow(session_id)
    if target is None:
        return {"status": "error", "result": f"Session {session_id} not found"}
    source_type, source_sid, source_error = _validate_source_metadata(
        target, source, source_session_id)
    if source_error:
        return {"status": "error", "result": source_error}
    # Capture the routing context before a possible force restart.  The value
    # is copied onto this message's durable queue row and is never recomputed
    # from a later/previous result.
    inherited_task_id = (
        getattr(target, "active_task_id", None)
        if source_type in {"agent", "report"} and (source_type == "agent" or source_sid) else None
    )
    def force_body_conflict(item):
        if item.get('text') != text:
            return {'status': 'rejected', 'outcome': 'rejected', 'retryable': False,
                    'sessionId': session_id, 'queueItemId': _queue_item_id(item),
                    'result': 'Force message id already belongs to different text'}
        return None
    if force and client_message_id:
        existing = _find_queue_item_by_idempotency(target, client_message_id=client_message_id)
        if existing is not None:
            conflict = force_body_conflict(existing)
            if conflict:
                return conflict
            pending = next((row for row in target.queue_pending if _queue_item_id(row) == _queue_item_id(existing)), None)
            if _delivery_state(existing) == _DELIVERY_SENT:
                return {"status": "accepted", "outcome": "accepted", "consumption": "unconfirmed",
                        "sessionId": session_id, "queueItemId": _queue_item_id(existing), "duplicate": True}
            if pending and pending.get("forceSend") and (pending.get("queueSteerPending") or not pending.get("lastSteerError")):
                return {"status": "unknown", "outcome": "unknown", "retryable": False,
                        "sessionId": session_id, "queueItemId": _queue_item_id(existing), "duplicate": True}
    w = find_alive_worker_by_session(session_id)
    alive = w is not None
    if not alive or getattr(target, "queue_paused", False):
        s = _get_session_shallow(session_id)
        if not s:
            return {"status": "error", "result": f"Session {session_id} not found"}
        async with queue_lock(session_id):
            item, persist_error = await _persist_task_item(
                s, text, source_type, None, inherited_task_id,
                client_message_id, source_sid,
                idempotent_task_id=False)
        if persist_error:
            return {"status": "error", "result": persist_error}
        # The queue write is the acknowledgement boundary.  Start recovery
        # now; watchdog is intentionally only the eventual retry path.
        paused = bool(getattr(s, "queue_paused", False))
        live = find_alive_worker_by_session(session_id)
        recovery = _schedule_session_recovery(session_id) if not paused and live is None else None
        if not paused and live is not None:
            await _wake_worker(session_id, auto_spawn=False)
        return {"status": "queued", "workerId": live.worker_id if live else None,
                "sessionId": session_id, "queuePaused": paused,
                "pendingSpawn": recovery is not None}
    if _offline.is_illegal(target):
        force = False
    route = steer_delivery_route(w, target) if force else "queue"
    if force and route == "steer":
        async with queue_lock(session_id):
            if _offline.is_illegal(target):
                # Illegal was discovered after routing. It owns the item and
                # forbids Force from clearing it; enqueue under the same lock.
                item, error = await _persist_task_item(
                    target, text, source_type, None, inherited_task_id,
                    client_message_id, source_sid, idempotent_task_id=False,
                    auto_force=False)
                if error:
                    return {"status": "error", "result": error}
                return {"status": "queued", "sessionId": session_id,
                        "queueItemId": _queue_item_id(item) if item else None}
            prior = _find_queue_item_by_idempotency(target, client_message_id=client_message_id)
            item = None
            if prior is not None:
                conflict = force_body_conflict(prior)
                if conflict:
                    return conflict
                state = _delivery_state(prior)
                pending = next((row for row in target.queue_pending if _queue_item_id(row) == _queue_item_id(prior)), None)
                if (pending and pending.get("forceSend") and pending.get("lastSteerError")
                        and not pending.get("queueSteerPending") and pending.get("text") == text):
                    item = pending  # An explicit retry after proved rejection retains the original item.
                else:
                    status = "accepted" if state == _DELIVERY_SENT else "unknown"
                    return {"status": status, "outcome": status, "consumption": "unconfirmed",
                            "queueItemId": _queue_item_id(prior), "sessionId": session_id,
                            "workerId": w.worker_id, "duplicate": True}
            if item is None:
                item, error = await _persist_task_item(target, text, source_type, None,
                    inherited_task_id, client_message_id, source_sid,
                    idempotent_task_id=False, locked=True, auto_force=False)
                if error:
                    return {"status": "rejected", "outcome": "rejected", "result": error}
            # The lock prevents ordinary FIFO from racing this immediate attempt.
            item["forceSend"] = True
            item["forceSendHeld"] = True
            if prior is None:
                _message_locks.set_lock(item, None)  # Replace only the just-created transaction hold.
            await _save_receipt(target)
        disposition = {}
        error = await steer_queue_item(session_id, _queue_item_id(item), item.get("revision", 1), outcome=disposition)
        status = disposition.get("status", "unknown") if error else "accepted"
        return {"status": status, "outcome": status, "consumption": "unconfirmed",
                "workerId": w.worker_id, "sessionId": session_id, "queueItemId": _queue_item_id(item),
                **({"result": error, "retryable": status == "rejected"} if error else {})}
    if force and route == "restart":
        # Preserve the original durable item: persist once, move that row to
        # the head so the replacement consumer delivers it first, then
        # restart.  Never create a second queue identity for this message.
        async with queue_lock(session_id):
            if _offline.is_illegal(target):
                item, error = await _persist_task_item(
                    target, text, source_type, None, inherited_task_id,
                    client_message_id, source_sid, idempotent_task_id=False,
                    auto_force=False)
                if error:
                    return {"status": "error", "result": error}
                return {"status": "queued", "sessionId": session_id,
                        "queueItemId": _queue_item_id(item) if item else None}
            prior = _find_queue_item_by_idempotency(
                target, client_message_id=client_message_id)
            item = None
            if prior is not None:
                conflict = force_body_conflict(prior)
                if conflict:
                    return conflict
                state = _delivery_state(prior)
                pending = next((row for row in target.queue_pending
                                if _queue_item_id(row) == _queue_item_id(prior)), None)
                if (pending and pending.get("forceSend")
                        and pending.get("lastSteerError")
                        and not pending.get("queueSteerPending")
                        and pending.get("text") == text):
                    item = pending
                    # A proved rejection may be retried through restart; the
                    # old Steer hold must not keep the row locked.  Clearing
                    # the error makes a later duplicate resolve as unknown
                    # instead of triggering another restart.
                    item.pop("forceSendHeld", None)
                    item.pop("queueSteerRequestId", None)
                    item.pop("queueSteerWorkerId", None)
                    item.pop("queueSteerGeneration", None)
                    item.pop("queueSteerOrigin", None)
                    item.pop("lastSteerError", None)
                else:
                    status = "accepted" if state == _DELIVERY_SENT else "unknown"
                    return {"status": status, "outcome": status,
                            "consumption": "unconfirmed",
                            "queueItemId": _queue_item_id(prior),
                            "sessionId": session_id, "workerId": w.worker_id,
                            "duplicate": True}
            if item is None:
                item, error = await _persist_task_item(
                    target, text, source_type, None, inherited_task_id,
                    client_message_id, source_sid, idempotent_task_id=False,
                    auto_force=False)
                if error:
                    return {"status": "rejected", "outcome": "rejected",
                            "result": error}
                item["forceSend"] = True
            # Re-check the gates after the durable write.  An idle transition,
            # Pause, read-only Session or an edit lease must not be bypassed
            # by a restart that was decided before the enqueue.
            live = find_alive_worker_by_session(session_id)
            blocked = (
                live is not None and live.status != "running"
            ) or target.readonly_session or getattr(target, "queue_paused", False) \
                or _offline.is_illegal(target) \
                or any(queue_item_edit_locked(target, identity)
                       for identity in list(getattr(target, "queue_edit_locks", {})))
            if live is None or blocked:
                await _save_receipt(target)
                if not (target.readonly_session
                        or getattr(target, "queue_paused", False)
                        or _offline.is_illegal(target)):
                    await _wake_worker(session_id, auto_spawn=False)
                return {"status": "queued", "sessionId": session_id,
                        "queueItemId": _queue_item_id(item),
                        "workerId": live.worker_id if live else None,
                        "queueRevision": target.queue_revision}
            old_pending = list(target.queue_pending or [])
            old_positions = {id(queued): queued.get("position")
                             for queued in old_pending if isinstance(queued, dict)}
            old_revision = target.queue_revision
            _move_queue_item_to_front(target, item)
            target.queue_revision = old_revision + 1
            try:
                await _save_receipt(target)
            except Exception as exc:
                target.queue_pending = old_pending
                for queued in old_pending:
                    if isinstance(queued, dict):
                        if old_positions.get(id(queued)) is None:
                            queued.pop("position", None)
                        else:
                            queued["position"] = old_positions[id(queued)]
                target.queue_revision = old_revision
                return {"status": "error",
                        "result": f"Failed to persist force queue order: {exc}"}
            queue_revision = target.queue_revision
            item_id = _queue_item_id(item)
        await _bcast({"type": "queue.snapshot", "sessionId": session_id,
                      "queueRevision": queue_revision})
        restarted = await restart_or_start_worker(session_id, allow_illegal_recovery=False)
        if restarted == "illegal_queue_hold":
            return {"status": "queued", "sessionId": session_id,
                    "queueItemId": item_id, "queueRevision": queue_revision}
        if isinstance(restarted, str):
            return {"status": "error", "result": restarted,
                    "queueItemId": item_id}
        return {"status": "queued", "workerId": restarted.worker_id,
                "sessionId": session_id, "queueItemId": item_id,
                "queueRevision": queue_revision}
    send_err = await send_task(w.worker_id, text, source=source_type,
                               task_id=inherited_task_id,
                               client_message_id=client_message_id,
                               source_session_id=source_sid,
                               idempotent_task_id=False)
    if send_err:
        return {"status": "error", "result": send_err}
    return {"status": "queued", "workerId": w.worker_id, "sessionId": session_id}


def get_worker(worker_id: str) -> Worker | None:
    return workers.get(worker_id)


def list_workers() -> list[Worker]:
    return list(workers.values())


def list_live_workers() -> list[Worker]:
    """Return only runtimes currently reusable by session controls."""
    return [w for w in workers.values() if find_alive_worker_by_session(w.session_id) is w]


def find_worker_by_session(session_id: str) -> Worker | None:
    indexed = _workers_by_session.get(session_id)
    if indexed is not None and workers.get(indexed.worker_id) is indexed:
        return indexed
    # Tests and legacy embedders may populate the public worker registry
    # directly.  Keep that compatibility while repairing the index lazily.
    for w in workers.values():
        if w.session_id == session_id:
            _register_worker(w)
            return w
    _workers_by_session.pop(session_id, None)
    return None


async def steer_session_worker(session_id: str, text: str,
                               message_id: str | None = None, *, outcome=None) -> Worker | str | None:
    """Steer the session's live worker without exposing its runtime id."""
    if outcome is None:
        outcome = {}
    outcome['status'] = 'rejected'
    session = _get_session_shallow(session_id)
    if session is None:
        return f"Session {session_id} not found"
    prior = session.adapter_config.get("_steer_requests", {}).get(message_id)
    if prior and prior.get("sessionId") == session_id and prior.get("status") != "accepted":
        outcome['status'] = 'unknown'
        return "Steer outcome unknown: original request retained; do not resend"
    if any(entry.get("sessionId") == session_id and entry.get("status") == "unknown"
           for entry in session.adapter_config.get("_steer_requests", {}).values() if isinstance(entry, dict)):
        outcome['status'] = 'unknown'
        return "Steer outcome unknown: previous request retained; do not resend"
    lock = await _session_spawn_lock(session_id)
    async with lock:
        w = find_alive_worker_by_session(session_id)
        if w is None:
            return None
        error = await steer_worker(w.worker_id, text, message_id, outcome=outcome)
        return error or w


async def send_session_control(session_id: str, control: dict) -> Worker | str | None:
    """Send an out-of-band control to the session's current worker."""
    if _get_session_shallow(session_id) is None:
        return f"Session {session_id} not found"
    lock = await _session_spawn_lock(session_id)
    async with lock:
        w = find_alive_worker_by_session(session_id)
        if w is None:
            return None
        error = await send_control_message(w.worker_id, control)
        return error or w


def find_alive_worker_by_session(session_id: str) -> Worker | None:
    """Like find_worker_by_session, but only returns live worker runtimes.

    Stream workers are checked by their OS process. CBC one-shot workers have no
    OS process (``process=None``), so their resident consumer task is the runtime
    liveness signal. ``status`` is deliberately not used: an idle worker is still
    alive and must be reused by the queue/watchdog.
    """
    w = find_worker_by_session(session_id)
    # A live CLI with a finished consumer cannot consume durable signals.  Do
    # not report it as reusable: callers will let create_worker replace the
    # generation instead of silently appending to a dead in-memory queue.
    consumer_alive = w is not None and (
        w._consume_task is None or not w._consume_task.done())
    if w and consumer_alive and _process_alive(w):
        return w
    return None


def find_alive_workers_by_session() -> dict[str, Worker]:
    """Snapshot live workers for a whole Session list in one registry walk."""
    candidates: dict[str, Worker] = {}
    for session_id, indexed in _workers_by_session.items():
        if workers.get(indexed.worker_id) is indexed:
            candidates[session_id] = indexed
    # Legacy embedders may populate the public registry without the index.
    for item in workers.values():
        candidates.setdefault(item.session_id, item)
    return {
        session_id: item for session_id, item in candidates.items()
        if (item._consume_task is None or not item._consume_task.done())
        and _process_alive(item)
    }


async def sync_legal_worker_state_to_runtime(session_id: str, *, source: str = "manual/mark-safe-offline") -> dict:
    """Compatibility entry point: explicitly cancel pending recovery/Illegal."""
    try:
        return await clear_session_illegal_for_startup(session_id, reconcile=True)
    except RuntimeError as exc:
        return {"sessionId": session_id, "status": "error", "error": str(exc)}


async def shutdown_all(
    *, recovery_drain_timeout: float = 10.0,
    mark_legal_offline: bool = False,
    preserve_legal_running_session_ids: list[str] | tuple[str, ...] = (),
    mark_legal_offline_session_ids: list[str] | tuple[str, ...] = (),
    legal_state_source: str = "pan/main-exit",
):
    """关闭所有 worker 的 cbc 进程树 + takeover 终端。

    使用 psutil 递归杀进程树（避免 node.exe 等孤儿进程）。
    """
    errors: list[str] = []
    preserve_ids = set(preserve_legal_running_session_ids)
    offline_ids = set(mark_legal_offline_session_ids)
    legal_state_source = str(legal_state_source or "pan/main-exit")

    for task in list(_queue_retry_tasks.values()):
        try:
            await _cancel_worker_task(task)
        except Exception as exc:
            errors.append(f"queue retry cancellation failed: {exc}")
    _queue_retry_tasks.clear()
    # Recovery tasks are handled by drain_recoveries' bounded wait (a task
    # cancelled mid real subprocess spawn may never finish on the Windows
    # Proactor loop; awaiting it unboundedly deadlocks the shutdown).  If the
    # caller skipped drain_recoveries, fall back to a best-effort bounded
    # drain here so shutdown can never block indefinitely on them.
    try:
        await drain_recoveries(timeout=recovery_drain_timeout)
    except Exception as exc:
        errors.append(f"recovery drain failed: {exc}")
    ids = list(workers.keys())
    for wid in ids:
        w = workers.get(wid)
        if not w:
            continue
        worker_errors_before = len(errors)
        if not mark_legal_offline or w.session_id in preserve_ids:
            try:
                await _detect_offline_exit(w, _offline.owner(w), legal_state_source + "/unauthorized-or-preserved")
            except Exception as exc:
                errors.append(f"Worker {wid} Illegal commit failed: {exc}")
        stop_steps = (
            (w._watchdog_task, "watchdog cancellation"),
            (w._consume_task, "consumer cancellation"),
            (w._stdout_task, "stdout cancellation"),
            (w._interrupt_guard_task, "interrupt-guard cancellation"),
        )
        for task, label in stop_steps:
            if task:
                try:
                    await _cancel_worker_task(task)
                except Exception as exc:
                    errors.append(f"Worker {wid} {label} failed: {exc}")
        try:
            await _stop_worker_tasks(w)
        except Exception as exc:
            errors.append(f"Worker {wid} task shutdown failed: {exc}")
        # A1 崩溃安全：关闭前 flush 防抖缓冲的流式块
        if w._hist_dirty:
            try:
                await _flush_history_now(w)
            except Exception as exc:
                errors.append(f"Worker {wid} history flush failed: {exc}")
        try:
            await _kill_process_tree(w)
        except Exception as exc:
            errors.append(f"Worker {wid} process stop failed: {exc}")
        try:
            await _kill_takeover_terminal(w)
        except Exception as exc:
            errors.append(f"Worker {wid} takeover terminal stop failed: {exc}")
        if (mark_legal_offline and w.session_id not in preserve_ids
                and _runtime_stopped(w) and len(errors) == worker_errors_before):
            try:
                await _finish_offline_stop(w, _offline.owner(w), legal_state_source)
            except Exception as exc:
                errors.append(f"Worker {wid} legality commit failed: {exc}")
        if (mark_legal_offline and w.session_id not in preserve_ids
                and _runtime_stopped(w)):
            if not await _record_legal_worker_state(w, "offline", legal_state_source):
                errors.append(f"Worker {wid} legal offline state was not persisted")

    workers.clear()
    _workers_by_session.clear()
    _worker_generations.clear()
    _recovery_required.clear()
    if errors:
        raise RuntimeError("; ".join(errors))

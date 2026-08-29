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
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

from . import session as _sess
from .adapters import get_adapter, CliAdapter, resolve_execution_mode
from .config import load_config

_log = logging.getLogger(__name__)


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
# 默认开启（保持既有行为）；设 false 可完全跳过 embedding 记忆注入，
# 避免首次加载 bge 模型 + huggingface 网络重试阻塞 worker 任务。

_MEMORY_ENABLED = True


def load_memory_config():
    """从 config.json 的 memory.enabled 读取记忆注入开关。

    config.json:
        "memory": { "enabled": false }
    """
    global _MEMORY_ENABLED
    _MEMORY_ENABLED = bool(load_config().get("memory", {}).get("enabled", True))


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
    status: str = "idle"      # idle | running | held | error | queued | zombie
    process: asyncio.subprocess.Process | None = None
    _mcp_proc: asyncio.subprocess.Process | None = None  # in-flight one-shot MCP process
    _stdout_task: asyncio.Task | None = None
    _consume_task: asyncio.Task | None = None
    _watchdog_task: asyncio.Task | None = None
    # 唤醒信号通道（内存，语义按立项 4.3/4.7/L4 收窄为"只唤醒、不承载正文"）：
    # 消息真源是落盘 Session.queue_pending，本队列只放信号——report_signal
    # （报告批量消费）与 task_signal（携带 item.id，正文按 id 从真源拉取）。
    pending_signal: asyncio.Queue | None = None
    _task_done: asyncio.Event | None = None  # stream 任务完成信号（_consumer_stream 等待，防多消息同时在 cbc 管道飞行）
    _replaying: bool = False  # 遗留：cbc --resume 的 stdout 重放标志（worker-resume-replay 结论：stdin 有 prompt 时 cbc 不重放，恒为 False；_read_stdout 的 replay 分支保留作 EOF 型重放的死代码兜底）
    takeover_pid: int | None = None  # PID of takeover PowerShell terminal
    pending_restart: bool = False  # 进程相关配置变更后待重启（idle 时自动 respawn）
    # ── 活性探测（watchdog 用）──
    last_activity: float = 0.0  # time.monotonic；stdout 有事件 / 新任务入队时刷新
    _task_started_at: float = 0.0  # time.monotonic；stream 任务开始处理（status→running）时记录，watchdog 据此判定「任务运行时长」超时
    # ── 任务序号（result 与 task 配对用）──
    _task_counter: int = 0   # 已分配的任务序号（send_task 入队时自增）
    _current_seq: int | None = None  # 正在处理的 item 序号（_consumer 取出时记录）
    _current_task_id: str | None = None  # 正在处理的 item 的 taskId（幂等用）
    _zombie_reported: bool = False  # 异常死亡 zombie 报告是否已推送（防 watchdog/EOF 双路径重复）
    # ── 流式块防抖落盘（A1）──
    _hist_dirty: bool = False          # 有未落盘的 history 块（append 只标记，不逐块 save）
    _hist_block_count: int = 0         # 本次批量累计的块数（达上限提前落盘）
    _hist_force_flush: bool = False    # result/退出路径强制立即落盘标记
    _hist_wake_count: int = 0          # 未消费的「提前落盘」唤醒计数（权威信号，防 ev.set 被 clear 冲掉）
    _hist_flush_event: asyncio.Event | None = None  # 防抖唤醒（仅作阻塞唤醒；计数为准）
    _hist_save_task: asyncio.Task | None = None     # 防抖落盘任务（单写者）


workers: dict[str, Worker] = {}

_broadcast: callable = None

# ── task 幂等注册表: taskId → {"status", "workerId", "result"}
# assign 生成 taskId 入队时登记，result 时标记完成；Meta-Agent 重发带同一
# taskId → 检测已存在则返回状态，不重复入队（防超时后双跑）
_task_status: dict[str, dict] = {}

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

    任务超时后任务仍在跑；若此时 worker 被杀/崩溃，taskId 若停留在 pending，
    同 taskId 重试会被幂等注册表永久拦截、永不执行。标 error 后重试拿到确定性
    失败（H2 卡死修复）。
    """
    now = time.monotonic()
    marked = 0
    for tid, entry in list(_task_status.items()):
        if entry.get("workerId") == worker_id and entry.get("status") == "pending":
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
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
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


def _mcp_configured(s: _sess.Session | None) -> bool:
    """Session 是否配置了 MCP 工具（mcp_servers 非空）。

    mcp_servers 是唯一事实源：非空即启用（单一事实源收敛立项）。
    MCP 是叠加属性：与 worker 的执行模式（output_mode）独立。
    """
    return bool(s and s.adapter_config.get("mcp_servers"))


# ── stdout reader ──


# stdout 分块读超时：仅用于周期性把事件循环交还（可取消、可重试），
# 属「cbc 静默」正常态 —— 超时不代表不活跃，不据此刷新 last_activity
# （刷新会导致 stream worker 的 idle 回收 / queued 静默超时永不触发，
# 回归来源 252c41d）。活性基准见 _read_stdout 的有效输出路径。
_STDOUT_READ_TIMEOUT_SEC: float = 60.0


async def _iter_stdout_lines(w: Worker):
    """分块读取 worker stdout，按换行切分产出完整行。

    替代 ``readline()``：Windows asyncio 管道上，超大单行（如 Read 大文件的
    tool result，几百 KB ~ MB）会让 readline 永久挂起（Pan 卡死根因）。分块
    read(65536) + 自切行不依赖单次读完一行；超过 _STDOUT_MAX_LINE 的行截断为
    占位，避免大行占满 history 上下文。
    """
    _STDOUT_MAX_LINE = 256 * 1024  # 单行上限 256KB，超出截断
    buf = b""
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
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if len(line) > _STDOUT_MAX_LINE:
                line = line[:_STDOUT_MAX_LINE] + b" ... [tool result truncated]"
            yield line
    if buf:
        yield buf  # 最后一行无换行（EOF 残留）


def _signal_task_done(w: Worker) -> None:
    """标记当前 stream 任务完成，唤醒 _consumer_stream 的等待。

    与 _consumer_stream 的 ev.wait() 配对：result 处理完成（或进程 EOF）时调用，
    让 consumer 知道当前任务已结束、可以推进下一个排队任务。
    """
    ev = getattr(w, "_task_done", None)
    if ev is not None:
        ev.set()


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


async def _flush_history_now(w: Worker) -> None:
    """立即落盘（result 处理 / worker 退出 / kill / 重启前调用），保证缓冲块不丢。

    单写者协作：若防抖任务在跑，置 force + 唤醒并 shield 等待其落完（由该任务完成
    落盘），避免与它并发写同一文件；无防抖任务则直接落盘。调用方被取消时 shield
    保护防抖任务继续落盘，取消仍向上传播（不吞 CancelledError）。
    """
    if w._hist_flush_event is None:
        w._hist_flush_event = asyncio.Event()
    w._hist_force_flush = True
    task = w._hist_save_task
    if task is not None and not task.done():
        w._hist_flush_event.set()
        await asyncio.shield(task)  # 防抖任务独立完成落盘；调用方取消照常传播
        return
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


async def _read_stdout(w: Worker):
    adapter = w.adapter
    s = None  # bound even if stdout yields no parseable event (EOF check below)
    # 分块读取 stdout（read 65536 + 按换行切分），避免大 tool result 单行在
    # Windows asyncio 管道上挂起（Pan 卡死根因）；大行截断占位（_iter_stdout_lines）。
    line_count = 0
    async for line in _iter_stdout_lines(w):
        line_count += 1
        line_str = line.decode("utf-8", errors="replace").rstrip("\n")
        if not line_str:
            continue
        event = adapter.parse_event(line_str)
        if event is None:
            continue

        # 活性探测：任何有效输出都刷新 last_activity（watchdog 据此判定卡死）
        w.last_activity = time.monotonic()

        # 提取 session_id + model 并写入 Session
        # 注意：stream 模式（--input-format stream-json）启动时无 init 事件，
        # spawn 即就绪，所以这里只提取元数据，不做状态转换（worker 初始即 idle）。
        if adapter.is_init_event(event):
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
                    s.history.append(b)
                # A1 防抖：append 只标记 dirty，由防抖任务批量落盘（不逐块全量 save）
                _mark_history_dirty(w)

        # 任务完成 → 保存 Session + last_result
        if adapter.is_result_event(event):
            s = _session(w)
            is_error = adapter.is_result_error(event)
            w.status = "error" if is_error else "done"

            # replay 结束：标记完成，不保存（history 无变化）
            if w._replaying:
                w._replaying = False
                w.status = "idle"
                _signal_task_done(w)
                _maybe_restart_pending(w)
                continue

            # taskSeq 统一用 _current_seq（_consumer 取出 item 时记录）。用
            # _result_count 会在中断/重启后与 _task_counter 错位，导致
            # result 与 task 配对错乱。
            task_seq = w._current_seq

            if s:
                result_text = adapter.extract_result_text(event)
                s.last_result = {
                    "status": w.status,
                    "result": result_text,
                    "cli_session_id": s.cli_session_id,
                    "timestamp": datetime.now().isoformat(),
                    "taskSeq": task_seq,
                }
                if isinstance(result_text, str) and result_text.strip():
                    last = s.history[-1] if s.history else None
                    if not (last and last.get("role") == "assistant"
                            and last.get("content") == result_text):
                        s.history.append({"role": "assistant", "content": result_text})
                # enrich: 从 CLI 原生存储获取消耗数据（如 raw_usage）
                enrichment = None
                try:
                    enrichment = adapter.enrich_after_result(s)
                except Exception:
                    pass
                if enrichment:
                    prev_total = s.total_usage
                    s.raw_usage = _sess.accumulate_raw_usage(s.raw_usage, enrichment)
                    s.total_usage = _sess.compute_total_usage(s.raw_usage)
                    prev_credit = prev_total.get("credit", 0) if prev_total else 0
                    new_credit = s.total_usage.get("credit", 0) if s.total_usage else 0
                    _log.info("credit: %.2f -> %.2f (+%.2f)", prev_credit, new_credit, new_credit - prev_credit)
                # A1 result 立即落盘：同时 flush 防抖缓冲的流式块 + last_result，
                # 由单写者防抖任务（若在跑）完成，避免双写竞态。
                await _flush_history_now(w)

            # taskSeq 已在上方（last_result 补存处）统一用 _current_seq。
            task_seq = w._current_seq
            result_text = adapter.extract_result_text(event)
            await _bcast({
                "type": "worker.result",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "status": w.status,
                "result": result_text,
                "taskSeq": task_seq,
            })
            # 订阅制报告：完成 → 若被订阅则 append 到 manager 的落盘队列（立项 4.3）
            await _enqueue_report(w.session_id, w.status, result_text, w._current_task_id, w.worker_id)
            # 幂等：完成对应 taskId（若有）
            if w._current_task_id and w._current_task_id in _task_status:
                _task_status[w._current_task_id] = {
                    "status": w.status,
                    "result": result_text,
                    "workerId": w.worker_id,
                    "taskId": w._current_task_id,
                    "ts": time.monotonic(),
                }
            w._current_task_id = None
            w.status = "idle"
            # A3：idle 过渡即时广播（前端此前靠 result 推断，存在延迟）
            await _bcast({
                "type": "worker.status",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "status": "idle",
            })
            _signal_task_done(w)
            _maybe_restart_pending(w)
            continue

        # replay 期间不广播 stream 事件
        if not w._replaying:
            await _bcast({
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "event": event,
            })

    # stdout EOF — 进程退出了
    code = w.process.returncode if w.process else "unknown"
    _log.info("[Worker %s] %s 进程退出，返回码 %s", w.worker_id, adapter.name, code)

    # B2: 进程退出检测路径 — 任务进行中（running/queued）异常退出/崩溃 → 向被管
    # manager 推送 zombie 报告。正常完成后的退出（status 已回 idle）由
    # _enqueue_zombie_report 内部判定跳过——done/error 报告完成时已推送。
    await _enqueue_zombie_report(w, f"process exited (returncode={code})")

    # 如果已经通过 result event 收到正常输出（last_result 有内容且非 error），
    # 不要覆盖。某些 CLI 退出时返回非零码（如 kimi 的 0xC0000409）但回复已完整。
    if code != 0 and w.status == "idle" and s and s.last_result and s.last_result.get("status") != "error":
        _log.info("[Worker %s] 已有正常结果，忽略非零退出码 %s", w.worker_id, code)
        w.status = "idle"
    else:
        w.status = "error"
        await _bcast({
            "type": "worker.crashed",
            "workerId": w.worker_id,
            "sessionId": w.session_id,
            "returncode": code,
        })
    # zombie：进程已死、尚未回收的瞬间状态。先广播再移除，让订阅方能观测到。
    w.status = "zombie"
    await _bcast({
        "type": "worker.zombie",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
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
    workers.pop(w.worker_id, None)


# ── consumer ──

async def _consumer(w: Worker):
    """Consumer loop. Two execution modes (selected by resolve_execution_mode):

    - Stream mode (default, no MCP): long-running cbc process with
      --input-format stream-json. Each message is written to stdin.
    - Stream + MCP mode: same long-running process, spawned with --mcp-config
      (cbc >= 2.137.0). Enabled via adapter_config.output_mode == "stream".
    - One-shot MCP mode: new cbc process per message with --mcp-config.
      Legacy path, used when output_mode is unset/oneshot.

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

        # 报告唤醒信号：正文在落盘 queue_pending，批量拉取拼接成一条消息处理
        if item.get("type") == "report_signal":
            s = _session(w)
            if s and s.queue_pending:
                await _consume_pending_reports(w, s)
            continue

        # 任务唤醒信号（L4 落盘）：正文在落盘 queue_pending，按 id 认领单个 task
        claimed = None
        if item.get("type") == "task_signal":
            claimed = await _claim_pending_task(w, item.get("id"))
            if claimed is None:
                continue  # 信号重复 / item 已被消费 / 会话消失 → 跳过
            text = claimed["text"]
            source = claimed.get("source", "agent")
            w._current_seq = claimed.get("seq")
            w._current_task_id = claimed.get("taskId")
        else:
            # 直接入队消息（无 type）：send_task 落盘迁移前的完整 item 形态
            # （兼容测试直连 pending_signal.put 的完整 item）
            text = item["text"]
            source = item.get("source", "agent")
            w._current_seq = item.get("seq")
            w._current_task_id = item.get("taskId")

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
                # 恢复对账（jsonl-先写崩溃窗口）：history 尾部已有该任务的投递
                # 标记 → 已投递过，只做队列清除收敛（幂等，防双跑），不重复执行。
                # 对账与进程死活无关，放在存活检查之前；只对认领项做——直接入队
                # 消息（claimed=None）不经队列，无对账可言。
                if claimed is not None and _delivery_mark_in_history(s, claimed):
                    _log.info(
                        "[Worker %s] task id=%s reconciliation: delivery mark already "
                        "in history, skipping execution and clearing from queue_pending",
                        w.worker_id, claimed.get("id"))
                    s.queue_pending = [it for it in s.queue_pending if it is not claimed]
                    try:
                        await _sess.save_async(s)
                    except Exception as e:
                        _log.warning(
                            "[Worker %s] queue_pending save after task reconciliation "
                            "failed: %s", w.worker_id, e)
                    continue
                injected_text = await _maybe_inject_memory(s, text)
                text = injected_text
                # 投递语义：注入期间进程死亡 → 中止，claimed item 留在
                # queue_pending（finally 释放标记），respawn 后由
                # _recover_pending_signals 重新分发。
                if claimed is not None and not _process_alive(w):
                    _log.warning(
                        "[Worker %s] task id=%s aborted: process dead, "
                        "kept in queue_pending", w.worker_id, claimed.get("id"))
                    continue
                hist_entry = {"role": "user", "content": injected_text}
                if claimed is not None:
                    # 投递标记记为 history 条目元数据（不进消息正文，恢复对账据此
                    # 去重）；直连消息（claimed=None）不经队列，无对账、不打标记
                    hist_entry["delivered_keys"] = [_delivery_key(claimed)]
                if claimed is None:
                    s.history.append(hist_entry)
                else:
                    # 原子消费（出队 = 移交成功的确认）：执行上下文记录
                    # （history append）+ 从 queue_pending 移除，改同一份内存态后
                    # **一次落盘**——崩溃在 save 前 = 两者都没写（重投）；save 后
                    # = 两者都写了。save 失败回滚内存态，item 留队列可重投。
                    old_queue = s.queue_pending
                    s.history.append(hist_entry)
                    s.queue_pending = [it for it in old_queue if it is not claimed]
                    try:
                        await _sess.save_async(s)
                    except Exception as e:
                        s.history.pop()
                        s.queue_pending = old_queue
                        _log.warning(
                            "[Worker %s] task id=%s atomic save failed, "
                            "kept in queue_pending: %s", w.worker_id,
                            claimed.get("id"), e)
                        continue
            # 用户消息落盘已下移到执行函数（_consumer_stream / _consumer_oneshot）：在
            # running 广播 + 写 cbc stdin 之后立即持久化，发送时指示灯不再被全量
            # O(history) 序列化阻塞（方案 1）。崩溃窗口 = 写 stdin → 落盘 毫秒级，
            # 最坏丢一条刚发送未落盘的用户消息（可接受范围）。

            # 选择执行模式：oneshot（每次任务新开进程）vs stream（长驻，可带 MCP）。
            # 由 adapter.execution_modes + session.output_mode 决定（去 cbc 化）。
            mode = resolve_execution_mode(w.adapter, s)

            if mode == "oneshot":
                await _consumer_oneshot(w, text, source, s)
            else:
                await _consumer_stream(w, text, source, s)
        finally:
            # 无论正常完成、中止还是异常/取消，都释放 in-flight 标记——item 是否
            # 留在队列由「是否已确认出队」决定，标记只负责运行期去重。
            if claimed is not None:
                _inflight_task_ids.discard(claimed.get("id"))


# ── 订阅制报告消费（立项 4.3）──

def _format_report_batch(reports: list[dict]) -> str:
    """积压报告拼接为可读文本：`@@@@by agent : {sessionId} | {title}` 抬头 + 每字段一行。

    报告形状：{"status","result","sessionId","taskId","workerId"}。
    title 取被管 session 的 name（`_sess.get(session_id).name`），session 不存在则回退 unknown。
    result 值单独成行、去引号、保留多行原文；None → null。
    """
    def _field_value(v) -> str:
        if v is None:
            return "null"
        return str(v)

    parts = []
    for r in reports:
        # QQ inbox 更新提醒（type=qq）：与 agent 汇报同通道（queue_pending +
        # report_signal），但抬头/字段不同。
        if r.get("type") == "qq":
            qq_target = r.get("qqTarget") or ""
            nickname = r.get("nickname") or ""
            bot_uin = str(r.get("botUin") or "")
            # 多账号：抬头带 bot 来源标识，agent 可见该会话由哪个 bot 收到
            header = f"@@@@by qq : {qq_target} | {nickname}"
            if bot_uin:
                header += f" | bot {bot_uin}"
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
        sid = r.get("sessionId") or ""
        title = "unknown"
        if sid:
            sess = _sess.get(sid)
            if sess and sess.name:
                title = sess.name
        src = sid or r.get("workerId") or "unknown"
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
            f"sessionId: {_field_value(r.get('sessionId'))}",
            f"taskId: {_field_value(r.get('taskId'))}",
            f"workerId: {_field_value(r.get('workerId'))}",
        ]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _process_alive(w: Worker) -> bool:
    """worker 进程存活检查：stream 模式看 cbc returncode；oneshot（process=None）视为存活。"""
    return w.process is None or w.process.returncode is None


# ── 投递标记与恢复对账（jsonl-先写顺序下的重复窗口封堵）──
#
# save 顺序：① jsonl(history) ② 主文件(queue_pending)。崩溃窗口：jsonl 已写、
# 主文件未写 → history 有注入消息、队列项还在 → 重启 _recover_pending_signals
# 重投 → 重复。兜底：注入时在 history 条目上记 `delivered_keys` 元数据（不进
# 消息正文），消费前对账 history 尾部——条目元数据已含该项 key → 已投递过，
# 跳过执行并从队列清除（收敛），未命中 → 正常注入执行。

# 对账扫描 history 尾部深度：标记只在注入时写入，重投对账发生在恢复后不久，
# 尾部窗口足够；深度内未命中一律按未投递处理（宁可重复不丢）。
_DELIVERY_SCAN_DEPTH: int = 50


def _delivery_key(item: dict) -> str:
    """队列项的投递标记 key。

    task 用 item.id；report/qq 用 taskId（保留可读性）+ 内容指纹（同 taskId
    不同内容不误判，taskId 缺失回退纯指纹）。指纹取排序 JSON 的 sha1 前 12 位，
    json 往返（磁盘重载）后内容一致 → 指纹一致。
    """
    digest = hashlib.sha1(
        json.dumps(item, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:12]
    if item.get("type") == "task":
        return f"task:{item.get('id')}:{digest}"
    tid = item.get("taskId")
    return f"report:{tid if tid else 'anon'}:{digest}"


def _delivery_mark_in_history(s, item: dict) -> bool:
    """history 尾部是否已有该队列项的投递标记（消费前对账，查条目元数据）。"""
    key = _delivery_key(item)
    for h in s.history[-_DELIVERY_SCAN_DEPTH:]:
        if key in (h.get("delivered_keys") or ()):
            return True
    return False


async def _consume_pending_reports(w: Worker, s):
    """从落盘 queue_pending 取全部积压报告，拼接为一条消息交给模型处理。

    投递语义（出队 = 移交成功的确认，原子落盘）：消费逻辑跑在 server 进程，
    构造注入文本 → history append + 从 queue_pending 移除这批报告 → **同一次
    save_async** 写盘（history 与 queue_pending 在同一 Session JSON，天然原子）。
    崩溃在 save 前 = 两者都没写、报告可重投；save 后 = 两者都写了——既不丢
    也不重复。save 失败（非崩溃）则回滚内存态，队列原样保留可重投。

    消费前确认 worker 进程存活（CLI 子进程死 → 中止保留队列，由全局 watchdog
    spawn 恢复后经 _recover_pending_signals 补发信号重投）。

    L4 落盘：任务消息（type=="task"）与报告共存于同一队列；此处**只消费报告**，
    task item 保留在队列中由 task_signal 按 id 消费，互不误删。

    恢复对账：save 顺序 jsonl-先写 → 崩溃窗口内 history 条目已带 delivered_keys
    元数据、队列项仍在。消费前逐条对账 history 尾部：已投递 → 跳过该条（防双跑）
    且从队列清除（收敛）；未投递 → 注入执行，注入条目记 delivered_keys 元数据
    （不进消息正文）。
    """
    reports = [it for it in s.queue_pending if it.get("type") != "task"]
    if not reports:
        # 队列里只剩 task item（报告已被其他信号消费）→ 不发空消息
        return
    undelivered = [it for it in reports if not _delivery_mark_in_history(s, it)]
    if not undelivered:
        # 全部已投递（jsonl-先写崩溃恢复）→ 只做队列清除收敛，不注入不执行
        _log.info("[Worker %s] report reconciliation: %d already-delivered report(s) "
                  "skipped and cleared from queue_pending", w.worker_id, len(reports))
        s.queue_pending = [it for it in s.queue_pending if it.get("type") == "task"]
        try:
            await _sess.save_async(s)
        except Exception as e:
            _log.warning("[Worker %s] queue_pending save after report reconciliation failed: %s",
                         w.worker_id, e)
        return
    text = _format_report_batch(undelivered)

    # 报告不是 assign 任务：无 seq 配对，清空当前配对上下文避免 last_result 错位
    w._current_seq = None
    w._current_task_id = None
    w._replaying = False

    injected_text = await _maybe_inject_memory(s, text)
    if not _process_alive(w):
        _log.warning(
            "[Worker %s] report consumption aborted: process dead, "
            "%d report(s) kept in queue_pending", w.worker_id, len(undelivered))
        return

    # 原子消费：history + 出队先改同一份内存态，再一次落盘；失败回滚内存态
    # （与磁盘保持一致，报告仍可重投）。本批报告全部移除：未投递项已注入
    # （出队 = 移交确认），对账命中的已投递项一并清除（收敛）。
    old_queue = s.queue_pending
    s.history.append({"role": "user", "content": injected_text,
                      "delivered_keys": [_delivery_key(it) for it in undelivered]})
    s.queue_pending = [it for it in s.queue_pending if it.get("type") == "task"]
    try:
        await _sess.save_async(s)
    except Exception as e:
        s.history.pop()
        s.queue_pending = old_queue
        _log.warning(
            "[Worker %s] report atomic save failed, %d report(s) kept in queue_pending: %s",
            w.worker_id, len(undelivered), e)
        return

    mode = resolve_execution_mode(w.adapter, s)
    if mode == "oneshot":
        await _consumer_oneshot(w, injected_text, "report", s)
    else:
        await _consumer_stream(w, injected_text, "report", s)


# 任务投递的 in-flight 标记（内存）：已认领、尚未确认出队的 task item id。
# 防同一 id 被重复认领（重复信号 / 新旧 worker 消费者交叠窗口）。进程重启即清空，
# 真源仍是落盘 queue_pending——标记只做运行期去重，不承载持久语义。
_inflight_task_ids: set[str] = set()


async def _claim_pending_task(w: Worker, task_id: str | None) -> dict | None:
    """在落盘 queue_pending 中按 id 认领一个任务 item（只标记、不移除）。

    投递语义（出队 = 移交成功的确认，原子落盘）：本函数只定位 item 并打
    in-flight 标记；实际出队在 _consumer 中与「执行上下文记录（history append）」
    合并为**同一次 save_async**（同一 Session JSON，天然原子）——崩溃在 save 前
    = 两者都没写、任务可重投；save 后 = 两者都写了。save 失败（非崩溃）回滚
    内存态，item 留在队列，respawn 后由 _recover_pending_signals 重新分发。

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
    for it in s.queue_pending:
        if it.get("type") == "task" and it.get("id") == task_id:
            if task_id in _inflight_task_ids:
                _log.warning(
                    "[Worker %s] task_signal: task id=%s already in-flight, "
                    "skip duplicate claim", w.worker_id, task_id)
                return None
            _inflight_task_ids.add(task_id)
            return it
    _log.warning("[Worker %s] task_signal: task id=%s not found in queue_pending (len=%d)",
                 w.worker_id, task_id, len(s.queue_pending))
    return None


async def _enqueue_report(session_id: str, status: str, result: str,
                          task_id: str | None, worker_id: str,
                          report_type: str | None = None):
    """订阅制报告入队：session 完成 → 若被其 managed_by 订阅，报告 append 到
    manager 的落盘队列 queue_pending，并唤醒 manager 的 consumer。

    **done / error 都入队（决策保留，遗留待办 L6）**：协调者（manager）需要
    知道失败——失败是编排的必要信息（重试/排查），不能只报成功。若后续只想报
    done，在此按 status 过滤即可。

    report_type：附加的语义标记（如 "zombie"）。非 None 时在报告 dict 里写入
    "type" 字段，供 manager 区分异常死亡与正常完成。

    未订阅 / 无 managed_by → 不 append（保留现有 worker.result 广播不变）。
    """
    s = _sess.get(session_id)
    if not s or not s.managed_by:
        return
    manager = _sess.get(s.managed_by)
    if not manager:
        return
    if session_id not in (manager.report_subscriptions or set()):
        return

    item = {
        "status": status,
        "result": result,
        "sessionId": session_id,
        "taskId": task_id,
        "workerId": worker_id,
    }
    if report_type is not None:
        item["type"] = report_type
    manager.queue_pending.append(item)
    await _sess.save_async(manager)

    # 唤醒 manager 的 consumer（若 worker 存活）——报告正文在落盘队列，
    # 信号只负责唤醒，不承载正文
    await _wake_worker(s.managed_by)


async def _wake_worker(session_id: str, auto_spawn: bool = False) -> None:
    """唤醒某 session 的 worker consumer（若存活）。

    信号只负责唤醒、不承载正文（立项 4.3/4.7：正文在落盘 queue_pending）。
    worker 死亡/未 spawn 时默认静默返回，由全局 watchdog 周期兜底 spawn 恢复
    （spawn 后若 queue_pending 非空自动补发信号）。

    auto_spawn=True：无活 worker 时**立即** create_worker（事件驱动恢复，不等
    watchdog tick）。create_worker 自带 per-session 锁防重复 spawn、并对已有
    活 worker 去重复用；spawn 失败返回错误串，此处仅打 warning 不抛出——消息
    已落盘 queue_pending，watchdog 下轮仍会兜底。oneshot worker（process 为
    None）满足可唤醒条件，不会误触发 spawn。
    """
    mw = find_worker_by_session(session_id)
    if (mw and mw.pending_signal is not None
            and not (mw.process is not None and mw.process.returncode is not None)):
        mw.last_activity = time.monotonic()
        await mw.pending_signal.put({"type": "report_signal"})
    elif auto_spawn:
        created = await create_worker(session_id)
        if isinstance(created, str):
            _log.warning(
                "[Pan] auto-spawn worker failed for session=%s: %s",
                session_id, created,
            )


async def enqueue_qq_reminder(target_type: str, target_id: str,
                              nickname: str = "", text: str = "",
                              time_str: str = "", bot_uin: str = "") -> int:
    """QQ inbox 更新提醒入队：所有订阅了该 QQ 会话的 session 各收到一条提醒。

    多账号（bot_uin 非空）：命中两类订阅——不区分 bot 的旧键 ``<type>:<id>``
    与精确键 ``<type>:<id>@<bot_uin>``；bot_uin 为空（旧来源）仅命中旧键。

    镜像 report 汇报链路：提醒项 append 到订阅者 session 的落盘 queue_pending，
    再唤醒其 worker consumer（report_signal）。无活 worker 时立即 auto_spawn
    恢复（事件驱动，消除 QQ 消息等待 watchdog tick 的最长 30s 延迟），spawn
    失败打日志、由全局 watchdog 兜底。返回投递的订阅者数量。

    提醒项格式：{"type": "qq", "qqTarget": "<scope>:<target_id>",
    "botUin": "<bot_uin>"?, ...}，_format_report_batch 按 type=qq 分支渲染为
    `@@@@by qq` 抬头（bot_uin 非空时抬头带 `| bot <uin>`）。
    """
    target_key = f"{target_type}:{target_id}"
    bot_key = f"{target_key}@{bot_uin}" if bot_uin else None
    item = {
        "type": "qq",
        "qqTarget": target_key,
        "targetType": target_type,
        "targetId": str(target_id),
        "nickname": nickname,
        "text": text,
        "time": time_str,
    }
    if bot_uin:
        item["botUin"] = str(bot_uin)
    delivered = 0
    for s in _sess.list_all():
        subs = s.qq_subscriptions or set()
        if target_key not in subs and not (bot_key and bot_key in subs):
            continue
        s.queue_pending.append(item)
        await _sess.save_async(s)
        await _wake_worker(s.id, auto_spawn=True)
        delivered += 1
    return delivered


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
    await _enqueue_report(w.session_id, "error", f"worker died: {reason}",
                          w._current_task_id, w.worker_id, report_type="zombie")


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
                _log.info(
                    "[Worker %s] watchdog kill: reason=idle_reclaim mode=mcp status=%s "
                    "idle_for=%.0fs idle_threshold=%.0fs branch=mcp_idle_reclaim",
                    w.worker_id, w.status, idle_for, _WORKER_IDLE_SEC,
                )
                await kill_worker(w.worker_id)
                return
            continue

        # stream 模式：running 用任务运行时长判定，queued 用静默时长判定，idle 空闲回收
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
                await kill_worker(w.worker_id)
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
            await kill_worker(w.worker_id)
            return
        if w.status == "idle" and idle_for > _WORKER_IDLE_SEC:
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
# queue_pending 非空但没有活 worker 的 session"，自动 create_worker 恢复。
# spawn 走 create_worker（自带防重复，立项 4.5），不会对同一 session 重复 spawn。

_GLOBAL_WATCHDOG_TICK_SEC: float = _WATCHDOG_TICK_SEC  # 沿用 worker 级间隔
_global_watchdog_task: asyncio.Task | None = None


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
    while True:
        await asyncio.sleep(_GLOBAL_WATCHDOG_TICK_SEC)
        try:
            await _global_watchdog_tick()
        except Exception as e:
            _log.warning("[Pan] Global watchdog tick error: %r", e)


async def _global_watchdog_tick():
    """单轮扫描：queue_pending 非空 && 无活 worker → create_worker 恢复。

    整个 tick 由 _global_watchdog 的 try/except 兜底，单个 session 的异常不会
    中断后续轮次。
    """
    for s in list(_sess.list_all()):
        if not s.queue_pending:
            continue
        if find_alive_worker_by_session(s.id) is not None:
            continue  # 已有活 worker → 正常
        # 无活 worker（从未 spawn，或注册了死进程尚未 pop）→ 交给 create_worker 自愈
        _log.info(
            "[Pan] Global watchdog recover: session=%s queue_pending=%d items, no live worker",
            s.id, len(s.queue_pending),
        )
        created = await create_worker(s.id)
        if isinstance(created, str):
            _log.warning(
                "[Pan] Global watchdog recover failed for session=%s: %s",
                s.id, created,
            )


async def _consumer_stream(w: Worker, text: str, source: str, s):
    """Stream mode: write to long-running cbc stdin."""
    if w.process is None or w.process.returncode is not None:
        if s:
            s.last_result = {
                "status": "error",
                "result": f"Worker process dead (returncode={w.process.returncode if w.process else 'none'})",
                "cli_session_id": s.cli_session_id,
                "timestamp": datetime.now().isoformat(),
            }
            await _sess.save_async(s)
        await _bcast({
            "type": "worker.result",
            "workerId": w.worker_id,
            "sessionId": w.session_id,
            "status": "error",
            "result": "Worker process dead",
            "taskSeq": w._current_seq,
        })
        return

    # 任务运行时长起算点：进入 running 即开始计时（watchdog 据此判定卡死）。
    # 区别于 last_activity（无输出时长）：长思考/大文件读取会长时间无 stdout，
    # 用「任务运行时长」作为 running 超时判据，避免误杀静默思考中的任务。
    w._task_started_at = time.monotonic()
    w.status = "running"

    data = w.adapter.encode_user_message(text)
    w.process.stdin.write(data + b"\n")
    await w.process.stdin.drain()

    await _bcast({
        "type": "worker.status",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "status": "running",
        "source": source,
    })

    # 用户消息后置落盘（方案 1）：先广播 running + 写 stdin，再全量落盘——指示灯
    # 与 cbc 消息送达不再被 O(history) 序列化阻塞。崩溃窗口 = 写 stdin → 落盘
    # 毫秒级，最坏丢一条刚发送未落盘的用户消息。重取 session 避免把已删除会话
    # 写回复活（#10 模式，同 _consumer_oneshot 的输出处理处）。
    sess = _session(w)
    if sess:
        await _sess.save_async(sess)

    # 等待当前任务完成（result 事件 → _read_stdout 置 idle 并 set _task_done）后再返回。
    # 不能轮询 status：status 在多协程间共享，result 处理中先置 done 后置 idle，旧任务
    # result 协程的 idle 会覆盖新任务已设的 running，导致等待提前退出、多条消息同时在
    # cbc 长驻进程管道里飞行，result 与 seq/taskId 错位、history 重复（实测复现）。
    # 用独立 Event 标记"本次任务完成"，_consumer 才能串行推进下一个排队任务。
    ev = getattr(w, "_task_done", None)
    if ev is not None:
        ev.clear()
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


async def _consumer_oneshot(w: Worker, text: str, source: str, s):
    """One-shot mode: 每任务 spawn 一个一次性进程（prompt 作末参）。

    argv 由 ``adapter.oneshot_args(s, text)`` 提供（cbc 实现；kimi/opencode 因
    ``execution_modes == ["stream"]`` 永不进入此路径）。stdout 收集后用既有
    ``adapter.parse_event`` 事件模型解析。取代旧 ``_consumer_mcp`` 的 cbc 特定
    拼装与 ``hasattr`` 探测（adapter-architecture P1 建议 4）。

    详见 docs/design/adapter-p1-oneshot.md §4。
    """
    w.status = "running"
    await _bcast({
        "type": "worker.status",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "status": "running",
        "source": source,
    })

    adapter = w.adapter

    # argv 全部来自 adapter（去 cbc 化）：无 --input-format stream-json，
    # --resume / --mcp-config / --system-prompt（仅首条）/ prompt 末参。
    args = adapter.oneshot_args(s, text) if hasattr(adapter, "oneshot_args") else []
    if not args:
        # 防御：adapter 不支持 oneshot 却进入此路径（不应发生，resolve 已 gate）。
        _log.error(
            "[Worker %s] oneshot_args 返回空 argv（adapter=%s 不支持 oneshot？）；跳过本次任务",
            w.worker_id, getattr(adapter, "name", "?"),
        )
        if s:
            s.last_result = {
                "status": "error",
                "result": "adapter 不支持 oneshot 执行模式",
                "timestamp": datetime.now().isoformat(),
            }
            await _sess.save_async(s)
        w.last_activity = time.monotonic()
        w.status = "idle"
        _maybe_restart_pending(w)
        return

    _log.info("[Worker %s] one-shot spawn (full args): %s", w.worker_id, " ".join(repr(a) for a in args))

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=s.workdir or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        _log.error("[Worker %s] one-shot spawn failed: %s", w.worker_id, e)
        if s:
            s.last_result = {"status": "error", "result": f"MCP spawn failed: {e}", "timestamp": datetime.now().isoformat()}
            await _sess.save_async(s)
        # M3: 置 idle 同步刷新活性时间，避免该 worker 刚忙完就被 watchdog 当空闲回收
        w.last_activity = time.monotonic()
        w.status = "idle"
        _maybe_restart_pending(w)
        return

    # Track in-flight process so kill_worker can terminate it (see #3).
    w._mcp_proc = proc

    # 用户消息后置落盘（方案 1）：one-shot 进程已携带 prompt spawn，视为送达，
    # 随后立即持久化——running 广播与 spawn 不被全量落盘阻塞；崩溃语义与 stream
    # 路径一致（最坏丢一条刚发送未落盘的用户消息）。重取 session 避免写回复活
    # 已删除会话（#10 模式）。
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
        # If cancelled (CancelledError) or the process is still alive after
        # wait(), kill it — prevents orphaned cbc/MCP processes (#3).
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        w._mcp_proc = None

    returncode = proc.returncode

    # DEBUG: save raw cbc output for inspection
    debug_path = os.path.join(s.workdir, ".pan-cbc-raw.jsonl") if s else None
    if debug_path:
        try:
            os.makedirs(s.workdir, exist_ok=True)
            with open(debug_path, "wb") as df:
                df.write(output)
        except Exception:
            pass

    # Parse stream-json output — collect first, apply to the session only
    # after confirming it still exists (#10).
    result_text = ""
    cli_session_id = None
    assistant_events: list[dict] = []  # raw assistant events, re-broadcast as worker.stream
    assistant_blocks: list[dict] = []  # extracted history blocks (assistant/thinking/tool)
    for line in output.decode(errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = event.get("type", "")
        if t == "result":
            # 走 adapter.extract_result_text 而非裸取 result 字段：claude 在该方法内
            # 把 result 事件的 usage+cost 暂存到 _PENDING_RESULT_USAGE（result 事件是
            # cost 唯一权威来源），供下方 enrich_after_result 取用。返回文本与裸取
            # 完全一致（cbc/claude 均返回 event.get("result")），不改变结果语义。
            result_text = adapter.extract_result_text(event) or ""
        elif t == "system" and event.get("subtype") == "init":
            cli_session_id = event.get("session_id")
        elif t == "assistant":
            # Extract blocks the same way stream mode's _read_stdout does, so
            # history format and the re-broadcast event match stream mode.
            blocks = adapter.extract_assistant_blocks(event)
            if blocks:
                assistant_events.append(event)
                assistant_blocks.extend(blocks)

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
    # Append extracted blocks (assistant/thinking/tool) — same as stream mode.
    for block in assistant_blocks:
        s.history.append(block)

    # Surface failures the user can actually see (#8 timeout, #9 non-zero exit).
    if timed_out and not result_text:
        status, result = (
            "error",
            f"Task timed out after {read_timeout:.0f}s (no output) and the process was killed",
        )
    elif not result_text and returncode not in (None, 0):
        tail = output.decode(errors="replace")[-2000:].strip()
        status, result = "error", f"cbc exited with code {returncode}:\n{tail}"
    elif not result_text and returncode == 0:
        # cbc exits 0 even when it fails (e.g. --resume targets a session that
        # no longer exists). Surface the structured error instead of a silent
        # "(no output)" so a broken cli_session_id binding is visible.
        # 错误提取可插拔：adapter 提供 extract_oneshot_error 则优先使用，
        # 否则回退 cbc 的通用启发式（adapter-architecture P1 建议 4 可选收尾）。
        extract_err = getattr(adapter, "extract_oneshot_error", _extract_cbc_error)
        cbc_error = extract_err(output)
        status, result = "error", cbc_error or "(no output)"
    else:
        status, result = (
            "done" if result_text else "error",
            result_text or "(no output)",
        )

    s.last_result = {
        "status": status,
        "result": result,
        "cli_session_id": s.cli_session_id,
        "timestamp": datetime.now().isoformat(),
        "taskSeq": w._current_seq,
    }
    # 用量/credit 落账：与 stream 路径（_read_stdout）同构——补调
    # adapter.enrich_after_result 读取 CLI 原生存储/缓存的本轮消耗并累加进 session。
    # oneshot 之前漏调，导致 cbc 不记 credit、claude（仅 oneshot）完全不记
    # usage/cost（result 事件的 usage 已在上方由 extract_result_text 暂存）。
    enrichment = None
    try:
        enrichment = adapter.enrich_after_result(s)
    except Exception:
        pass
    if enrichment:
        prev_total = s.total_usage
        s.raw_usage = _sess.accumulate_raw_usage(s.raw_usage, enrichment)
        s.total_usage = _sess.compute_total_usage(s.raw_usage)
        prev_credit = prev_total.get("credit", 0) if prev_total else 0
        new_credit = s.total_usage.get("credit", 0) if s.total_usage else 0
        _log.info("credit: %.2f -> %.2f (+%.2f)", prev_credit, new_credit, new_credit - prev_credit)
    await _sess.save_async(s)

    # Broadcast assistant events as worker.stream so the frontend displays the
    # reply in real-time — MCP mode otherwise only emits worker.result, which
    # both frontends render as a bare "[DONE]" system message (#stream-mcp).
    for event in assistant_events:
        await _bcast({
            "type": "worker.stream",
            "workerId": w.worker_id,
            "sessionId": w.session_id,
            "event": event,
        })

    # M3: 置 idle 同步刷新活性时间——MCP 任务全程不刷新 last_activity，若不在此
    # 重置，任务耗时会被算进 idle 时长，刚忙完就可能被 watchdog 立即回收。
    w.last_activity = time.monotonic()
    w.status = "idle"
    _maybe_restart_pending(w)
    task_seq = w._current_seq
    await _bcast({
        "type": "worker.result",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "status": status,
        "result": result,
        "taskSeq": task_seq,
    })
    # 订阅制报告：完成 → 若被订阅则 append 到 manager 的落盘队列（立项 4.3）
    await _enqueue_report(w.session_id, status, result, w._current_task_id, w.worker_id)
    # 幂等：完成对应 taskId（若有）
    if w._current_task_id and w._current_task_id in _task_status:
        _task_status[w._current_task_id] = {
            "status": status,
            "result": result,
            "workerId": w.worker_id,
            "taskId": w._current_task_id,
            "ts": time.monotonic(),
        }
    w._current_task_id = None


async def _consumer_mcp(w: Worker, text: str, source: str, s):
    """Deprecated alias for _consumer_oneshot.

    cbc 特定拼装已搬入 CbcAdapter.oneshot_args（adapter-architecture P1 建议 4）。
    保留别名以兼容既有测试；下个 PR 删除。
    """
    return await _consumer_oneshot(w, text, source, s)


# ── lifecycle ──

# spawn 防重复（立项 4.5）：同一 session 的并发 create_worker 通过 per-session
# lock 串行化，避免竞态双 spawn。dict 的 setdefault 是纯同步原子操作（无 await
# 临界区），事件循环内天然安全，不需要额外 guard lock。
_spawn_locks: dict[str, asyncio.Lock] = {}


async def _session_spawn_lock(session_id: str) -> asyncio.Lock:
    """获取该 session 的 spawn 锁（并发 create_worker 串行化）。"""
    return _spawn_locks.setdefault(session_id, asyncio.Lock())


def _recover_pending_signals(w: Worker, s) -> None:
    """spawn/重启后恢复消费：把落盘 queue_pending 的积压项转成唤醒信号。

    L4 落盘：任务与报告都持久化在 Session.queue_pending（落盘真源），worker
    死亡/回收后消息不丢。新 worker 的 consumer 启动后需要信号才知道有积压——
    每个 task item 发一个 task_signal（带 id），有 report item 再补发一个
    report_signal。pending_signal 是新队列，直接 put_nowait（无界队列不阻塞）。

    调用时机：create_worker 在 system_prompt 注入**之前**（避免对注入任务重复
    发信号）、_restart_tasks 重建 consumer 后。
    """
    pending = s.queue_pending
    if not pending:
        return
    has_report = False
    for it in pending:
        if it.get("type") == "task":
            w.pending_signal.put_nowait({"type": "task_signal", "id": it.get("id")})
        else:
            has_report = True
    if has_report:
        w.pending_signal.put_nowait({"type": "report_signal"})


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
    lock = await _session_spawn_lock(session_id)
    async with lock:
        return await _create_worker(session_id)


def _spawn_system_prompt_args(adapter, s, mcp_on: bool) -> list[str] | None:
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
        return ["--system-prompt", s.system_prompt]
    return None


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

    old = find_worker_by_session(session_id)
    if old:
        # 有注册但进程已死的 worker（_read_stdout 尚未 pop）→ 清理后重建。
        # Deliberately do NOT clear cli_session_id: resuming the cbc JSONL is
        # the intended context-continuity mechanism, and clearing it here would
        # force a cold start on every restart (#11, resolved by design).
        await kill_worker(old.worker_id)

    adapter = get_adapter(s.adapter)
    worker_id = await _next_worker_id()

    mcp_on = _mcp_configured(s)
    mode = resolve_execution_mode(adapter, s)

    if mode == "oneshot":
        # One-shot mode: no long-running process, consumer spawns per-task
        # (no stdin). See docs/design/adapter-p1-oneshot.md.
        proc = None
        spawn_injected = False
    else:
        # Stream mode: spawn long-running process.
        # If MCP is configured (mcp_on, output_mode="stream"), the process is
        # spawned with --mcp-config (build_spawn_args -> mcp_args) so the
        # long-running stream keeps MCP tools (cbc >= 2.137.0).
        extra_args = _spawn_system_prompt_args(adapter, s, mcp_on)
        spawn_injected = extra_args is not None
        proc = await _spawn_process(session_id, adapter=adapter, extra_args=extra_args)
        if isinstance(proc, str):
            return proc

    w = Worker(worker_id=worker_id, session_id=session_id,
               adapter=adapter,
               status="idle", process=proc, pending_signal=asyncio.Queue(),
               _task_done=asyncio.Event(),
               _hist_flush_event=asyncio.Event())
    w.last_activity = time.monotonic()
    workers[worker_id] = w

    if mode != "oneshot":
        w._stdout_task = asyncio.create_task(_read_stdout(w))
    # watchdog 两种模式都启用：stream 做超时+空闲回收，MCP 只做空闲回收
    if not _DEFAULTS_INITIALIZED:
        load_worker_config()
    w._watchdog_task = asyncio.create_task(_watchdog(w))
    w._consume_task = asyncio.create_task(_consumer(w))

    await _bcast({
        "type": "worker.spawned",
        "workerId": worker_id,
        "sessionId": session_id,
        "name": s.name,
        "status": w.status,
        "model": s.model or adapter.default_model,
    })

    await _sess.save_async(s)

    # L4 落盘恢复：把本次 spawn 前已积压的落盘 queue_pending 转成唤醒信号——
    # 每个 task item 发 task_signal（带 id），有 report item 再补发 report_signal。
    # 必须放在 system_prompt 注入**之前**：注入会往 queue_pending 追加新 task 并
    # 自带 task_signal，先做恢复可避免对同一 item 重复发信号。
    _recover_pending_signals(w, s)

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
        elif not spawn_injected:
            _log.info("[Worker %s] injecting system_prompt (%d chars)", worker_id, len(s.system_prompt))
            await send_task(worker_id, s.system_prompt, source="system_prompt")
        else:
            _log.info("[Worker %s] stream mode: system_prompt injected via --system-prompt", worker_id)

    return w


async def _kill_takeover_terminal(w: Worker) -> bool:
    """杀掉 takeover 模式打开的终端及子进程树。异步版，不阻塞事件循环。"""
    if not w.takeover_pid:
        return False
    pid = w.takeover_pid
    _log.info("[Worker %s] 杀 takeover 终端 PID=%s", w.worker_id, pid)
    try:
        await asyncio.to_thread(_kill_pid_tree, pid)
        _log.info("[Worker %s] takeover 终端已结束 PID=%s", w.worker_id, pid)
    except Exception as e:
        _log.warning("[Worker %s] 杀 takeover 终端异常: %s", w.worker_id, e)
    w.takeover_pid = None
    return True


async def _kill_process_tree(w: Worker) -> None:
    """杀 worker 的 CLI 子进程树。异步版，不阻塞事件循环。"""
    # Stream mode: w.process is the long-running cbc
    if w.process:
        pid = w.process.pid
        try:
            await asyncio.to_thread(_kill_pid_tree, pid)
        except Exception:
            try:
                w.process.kill()
            except (ProcessLookupError, Exception):
                pass

    # MCP mode: w._mcp_proc is the in-flight one-shot cbc (may be None)
    if w._mcp_proc:
        mpid = w._mcp_proc.pid
        try:
            await asyncio.to_thread(_kill_pid_tree, mpid)
        except Exception:
            try:
                w._mcp_proc.kill()
            except (ProcessLookupError, Exception):
                pass


async def kill_worker(worker_id: str) -> str | None:
    """Kill the Worker process. Does NOT touch the Session."""
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"

    # 若 kill_worker 由该 worker 自己的 watchdog 触发，不能 cancel 当前任务
    # （否则 kill 流程刚 cancel 就收到 CancelledError 被中断，进程杀不掉、
    #  worker 也不 pop）——让 watchdog 自然 return 即可。
    current = asyncio.current_task()
    if w._watchdog_task and w._watchdog_task is not current:
        _log.info("[Worker %s] kill_worker: cancelling watchdog task", worker_id)
        w._watchdog_task.cancel()
    elif w._watchdog_task is current:
        _log.info(
            "[Worker %s] kill_worker: skip watchdog self-cancel "
            "(kill triggered by watchdog itself)",
            worker_id,
        )
    if w._consume_task:
        w._consume_task.cancel()
    if w._stdout_task:
        w._stdout_task.cancel()
    await _kill_process_tree(w)
    await _kill_takeover_terminal(w)

    # H2: worker 被杀 → 名下 pending 的 taskId 标 error（防止幂等重试永久卡 pending）
    _mark_worker_tasks_error(worker_id, "worker killed")
    # A1 崩溃安全：kill 前 flush 防抖缓冲的流式块
    if w._hist_dirty:
        await _flush_history_now(w)

    workers.pop(worker_id, None)
    await _bcast({
        "type": "worker.destroyed",
        "workerId": worker_id,
        "sessionId": w.session_id,
    })
    return None


async def cleanup_worker_background(worker_id: str, session_id: str):
    """Background worker cleanup — always cleans up and broadcasts, even on failure.

    Call via ``asyncio.create_task()`` from delete-session flows to avoid
    blocking the HTTP response on process termination.
    """
    try:
        w = workers.get(worker_id)
        if not w:
            return
        if w._watchdog_task:
            w._watchdog_task.cancel()
        if w._consume_task:
            w._consume_task.cancel()
        if w._stdout_task:
            w._stdout_task.cancel()
        await _kill_process_tree(w)
        await _kill_takeover_terminal(w)
        # A1 崩溃安全：清理前 flush 防抖缓冲的流式块
        if w._hist_dirty:
            await _flush_history_now(w)
    except Exception as exc:
        _log.warning("[Worker %s] BG cleanup error: %r", worker_id, exc)
    finally:
        workers.pop(worker_id, None)
        # H2: worker 回收 → 名下 pending 的 taskId 标 error（与 kill_worker 一致）
        _mark_worker_tasks_error(worker_id, "worker cleanup")
        try:
            await _bcast({
                "type": "worker.destroyed",
                "workerId": worker_id,
                "sessionId": session_id,
            })
        except Exception as bcast_err:
            _log.warning("[Worker %s] BG cleanup bcast error: %r", worker_id, bcast_err)


def _maybe_restart_pending(w: Worker) -> None:
    """Worker 回到 idle 时若标记了 pending_restart，异步 respawn 让配置变更生效。

    在 worker 置 idle 的各路径调用；仅 pending_restart 时触发，其他情况为无操作。
    """
    if w.pending_restart and w.process is not None:
        w.pending_restart = False
        _log.info("[Worker %s] 配置变更：idle 后 respawn", w.worker_id)
        asyncio.create_task(_respawn_worker(w))


async def _respawn_worker(w: Worker) -> None:
    """kill 当前进程 + 重新 spawn（resume 上下文），用于进程相关配置变更后生效。"""
    sid, wid = w.session_id, w.worker_id
    try:
        await kill_worker(wid)
    except Exception as e:
        _log.warning("[Worker %s] respawn kill 异常: %s", wid, e)
    try:
        result = await create_worker(sid)
        if isinstance(result, Worker):
            _log.info("[Worker %s] respawn 完成 -> %s", wid, result.worker_id)
        else:
            _log.warning("[Worker %s] respawn 失败: %s", wid, result)
    except Exception as e:
        _log.error("[Worker %s] respawn 异常: %s", wid, e)


async def _spawn_process(session_id: str,
                         adapter: CliAdapter,
                         extra_args: list[str] | None = None
                         ) -> asyncio.subprocess.Process | str:
    s = _sess.get(session_id)
    if not s:
        return f"Session {session_id} not found"

    args = adapter.build_spawn_args(s, extra_args)

    try:
        return await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=s.workdir or None,
        )
    except FileNotFoundError:
        return f"CLI executable not found (adapter={adapter.name})"
    except OSError as e:
        return f"OS error spawning {adapter.name}: {e}"


async def _restart_tasks(w: Worker):
    # A1 崩溃安全：重启前先 flush 防抖缓冲的流式块（换进程后 resume 依赖已落盘内容）
    if w._hist_dirty:
        await _flush_history_now(w)
    if w._watchdog_task:
        w._watchdog_task.cancel()
    if w._stdout_task:
        w._stdout_task.cancel()
    if w._consume_task:
        w._consume_task.cancel()
    w.pending_signal = asyncio.Queue()
    w.last_activity = time.monotonic()
    if w.process is not None:
        w._stdout_task = asyncio.create_task(_read_stdout(w))
    w._consume_task = asyncio.create_task(_consumer(w))
    if not _DEFAULTS_INITIALIZED:
        load_worker_config()
    w._watchdog_task = asyncio.create_task(_watchdog(w))
    # L4 落盘恢复：新 consumer 的信号队列是新建的，旧信号已随旧队列丢弃——
    # 重新对落盘 queue_pending 积压发信号，避免重启/换进程时丢任务与报告。
    s = _session(w)
    if s:
        _recover_pending_signals(w, s)


async def restart_worker(worker_id: str) -> str | None:
    """Restart the cbc process for a Worker. Preserves session."""
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"

    # always clear held status
    w.status = "idle"

    # cancel stale tasks FIRST — before killing the process.
    # _read_stdout detects EOF on process death and calls workers.pop(),
    # which would remove the worker being restarted.  Cancelling first
    # means _read_stdout never sees the EOF.
    if w._consume_task:
        w._consume_task.cancel()
    if w._stdout_task:
        w._stdout_task.cancel()

    # kill takeover terminal if one was opened
    await _kill_takeover_terminal(w)

    # kill existing cbc process tree（psutil 递归杀，避免 node.exe 孤儿）
    await _kill_process_tree(w)
    w.process = None

    proc = await _spawn_process(w.session_id, adapter=w.adapter)
    if isinstance(proc, str):
        return f"Spawn failed ({w.session_id}): {proc}"
    w.process = proc
    w.status = "idle"
    # resume 不再置 _replaying（worker-resume-replay 结论）：cbc stdin 有 prompt
    # 时不重放 stdout 历史，首个 result 即任务结果；若仍置 True，_read_stdout
    # 会把首个 result 当 replay 结束丢弃（L507-512 continue）→ 任务永不完成。
    await _restart_tasks(w)

    s = _session(w)
    await _bcast({
        "type": "worker.restarted",
        "workerId": worker_id,
        "sessionId": w.session_id,
        "name": s.name if s else worker_id,
        "status": w.status,
    })
    return None


async def respawn_worker(worker_id: str, extra_args: list[str] | None = None) -> str | None:
    """Kill + re-spawn with extra args (model/mode switch)."""
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"

    # cancel stale tasks FIRST — same race as restart_worker:
    # if we kill before cancelling, _read_stdout sees EOF and
    # pops the worker from workers dict during spawn.
    if w._consume_task:
        w._consume_task.cancel()
    if w._stdout_task:
        w._stdout_task.cancel()

    await _kill_takeover_terminal(w)
    await _kill_process_tree(w)
    w.process = None

    proc = await _spawn_process(w.session_id, adapter=w.adapter, extra_args=extra_args)
    if isinstance(proc, str):
        return proc
    w.process = proc
    w.status = "idle"
    await _restart_tasks(w)

    await _bcast({
        "type": "worker.reconfigured",
        "workerId": worker_id,
        "sessionId": w.session_id,
        "status": "idle",
    })
    return None


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

    # inherit model/mode/thinking from original session
    orig = _sess.get(w.session_id)
    if orig:
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

    # branch needs --fork-session from adapter
    extra_args = w.adapter.fork_args(s)
    if not w.adapter.supports_fork or not extra_args:
        return f"Adapter '{w.adapter.name}' does not support fork"

    new_id = await _next_worker_id()
    proc = await _spawn_process(new_session_id, adapter=w.adapter, extra_args=extra_args)
    if isinstance(proc, str):
        return proc

    new_w = Worker(worker_id=new_id, session_id=new_session_id,
                   adapter=w.adapter,
                   status="idle", process=proc, pending_signal=asyncio.Queue(),
                   _task_done=asyncio.Event(),
                   _hist_flush_event=asyncio.Event())
    # 注意：branch 不设 _replaying（与 create_worker/restart_worker 一致，现全局恒
    # False）。原注释假设"cbc --resume --fork-session 会把父会话历史重放到 stdout
    # 供 branch 空 history 填充"——worker-resume-replay 实测 fork+prompt **不重放**
    # （stdin 有 prompt → ResumeReplay skipped），该假设不成立。父上下文实际由 cbc
    # 内部 fork 的 JSONL 承载（模型侧完整）；Pan 侧新 session 的 history 只记录新
    # 回合（父历史不回填 → 展示层缺父回合，属既有局限，非模型上下文缺陷）。
    workers[new_id] = new_w
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
        "name": s.name,
        "status": "idle",
        "parentWorkerId": worker_id,
    })
    return new_w


async def interrupt_worker(worker_id: str) -> str | None:
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    if w.status != "running":
        return "Worker is not running"
    return await restart_worker(worker_id)


async def send_task(worker_id: str, text: str, source: str = "agent",
                    seq: int | None = None, task_id: str | None = None) -> str | None:
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    if w.status == "held":
        return "Worker is held (takeover mode). Restart first."
    # In MCP mode, process is None (spawned per-task). Still allow signal queue.
    if w.process is not None and w.process.returncode is not None:
        return "Worker process dead"
    if w.pending_signal is None:
        return "Worker signal queue not ready"

    # 分配任务序号（result 与 task 配对用；外部可预分配传入，保证 item.seq 与期望一致）
    if seq is None:
        w._task_counter += 1
        seq = w._task_counter

    w.last_activity = time.monotonic()

    # L4 落盘改造：任务 item 持久化到 session.queue_pending（落盘真源），
    # pending_signal 只放 task_signal 唤醒信号（携带 item.id），正文由 _consumer
    # 按 id 从真源拉取——worker 死亡/回收后任务不丢，重启后由 create_worker /
    # 全局 watchdog 自动恢复消费。
    item = {
        "type": "task",
        "id": uuid.uuid4().hex,
        "text": text,
        "source": source,
        "seq": seq,
        "taskId": task_id,
    }
    s = _session(w)
    if s is None:
        return f"Session {w.session_id} not found"
    s.queue_pending.append(item)
    await _sess.save_async(s)
    await w.pending_signal.put({"type": "task_signal", "id": item["id"]})

    # queued：任务已入队、consumer 尚未取出。若队列前面还有任务，保持 queued 直到轮到它。
    if w.status in ("idle", "queued"):
        w.status = "queued"
        await _bcast({
            "type": "worker.status",
            "workerId": w.worker_id,
            "sessionId": w.session_id,
            "status": "queued",
        })
    return None


# ── 编排原语：assign / send（供 Meta-Agent 调用） ──


async def _ensure_worker(session_id: str) -> tuple[Worker | None, str | None]:
    """确保 session 有活的 worker。返回 (worker, None) 或 (None, error)。"""
    w = find_worker_by_session(session_id)
    if w is None or (w.process is not None and w.process.returncode is not None):
        created = await create_worker(session_id)
        if isinstance(created, str):
            return None, created
        w = created
    return w, None


async def assign(session_id: str, text: str, source: str = "agent",
                 task_id: str | None = None) -> dict:
    """异步分派：确保 worker 存在 → 发任务 → 立即返回 queued。

    完成时通过 worker.result 事件（配合 /ws/agent subscribe）回调。
    适用于并行 fan-out。

    task_id 幂等（复用 taskId 幂等注册表 _task_status + TTL 惰性清理）：
    同一 taskId 重发不重复入队（防双跑）。若该 taskId 已存在：
    - 已完成（done/error）→ 返回缓存结果（status/result）
    - 进行中 → 返回 {"status": "pending", "taskId": ...}，不重复入队
    用于超时后安全重试 / 并发去重。不带 task_id 行为不变。
    """
    # 惰性清理过期条目（TTL），防止注册表长期运行无界增长（H2 泄漏）
    _prune_task_status()
    # taskId 幂等检查
    if task_id is not None and task_id in _task_status:
        existing = _task_status[task_id]
        if existing["status"] in ("done", "error"):
            return dict(existing)
        return {"status": "pending", "taskId": task_id}

    w, err = await _ensure_worker(session_id)
    if err:
        return {"status": "error", "result": err}

    if task_id is not None:
        _task_status[task_id] = {"status": "pending", "workerId": w.worker_id,
                                 "taskId": task_id, "ts": time.monotonic()}

    send_err = await send_task(w.worker_id, text, source=source, task_id=task_id)
    if send_err:
        if task_id is not None:
            _task_status[task_id] = {"status": "error", "result": send_err,
                                     "taskId": task_id, "ts": time.monotonic()}
        return {"status": "error", "result": send_err}

    result = {"status": "queued", "workerId": w.worker_id, "sessionId": session_id}
    if task_id is not None:
        result["taskId"] = task_id
    return result


async def send(worker_id: str, text: str, source: str = "agent") -> dict:
    """向已有 worker 发消息（持续性多轮协作）。

    若 worker 已死返回 error（需先 spawn）。完成时通过 worker.result 事件回调。
    """
    w = workers.get(worker_id)
    if w is None:
        return {"status": "error", "result": "Worker not found"}
    if w.process is not None and w.process.returncode is not None:
        return {"status": "error", "result": "Worker process dead"}
    send_err = await send_task(worker_id, text, source=source)
    if send_err:
        return {"status": "error", "result": send_err}
    return {"status": "queued", "workerId": worker_id, "sessionId": w.session_id}


async def send_session(session_id: str, text: str, source: str = "agent",
                       force: bool = False) -> dict:
    """向 session（agent）发消息（阶段 6 寻址兼容）：编排对象是 agent，
    worker（CLI 进程）是顺带的。

    - 有活 worker（含 oneshot 注册，process 为 None）→ 常规投递；
      force=True 先 restart 再投递（worker_send_force 语义）。
    - 无活 worker（从未 spawn / 进程已死）→ **不报错**：消息入该 session 的
      持久队列 queue_pending（type=task），由全局 watchdog（queue_pending
      非空 && 无活 worker → create_worker）spawn 后经 _recover_pending_signals
      补发 task_signal 分发——「send = 写给 agent」。
    - held（takeover 模式）→ 透传错误，不吞错不入队。
    """
    w = find_worker_by_session(session_id)
    alive = w is not None and (w.process is None or w.process.returncode is None)
    if not alive:
        s = _sess.get(session_id)
        if not s:
            return {"status": "error", "result": f"Session {session_id} not found"}
        item = {
            "type": "task",
            "id": uuid.uuid4().hex,
            "text": text,
            "source": source,
            "seq": None,
            "taskId": None,
        }
        s.queue_pending.append(item)
        await _sess.save_async(s)
        return {"status": "queued", "workerId": None, "sessionId": session_id,
                "pendingSpawn": True}
    if force:
        err = await restart_worker(w.worker_id)
        if err:
            return {"status": "error", "result": err}
    send_err = await send_task(w.worker_id, text, source=source)
    if send_err:
        return {"status": "error", "result": send_err}
    return {"status": "queued", "workerId": w.worker_id, "sessionId": session_id}


def get_worker(worker_id: str) -> Worker | None:
    return workers.get(worker_id)


def list_workers() -> list[Worker]:
    return list(workers.values())


def find_worker_by_session(session_id: str) -> Worker | None:
    for w in workers.values():
        if w.session_id == session_id:
            return w
    return None


def find_alive_worker_by_session(session_id: str) -> Worker | None:
    """Like find_worker_by_session, but only returns workers whose OS process
    is still alive (returncode is None).

    A worker in the `workers` dict can have a dead process in the brief window
    between process exit and `_read_stdout` popping it. Also, `status` only
    equals "running" during active message processing — an idle worker (alive
    process, waiting for input) has status "idle" but should still be preserved
    by reimport. Checking `returncode is None` is the robust liveness test.
    """
    w = find_worker_by_session(session_id)
    if w and w.process and w.process.returncode is None:
        return w
    return None


async def shutdown_all():
    """关闭所有 worker 的 cbc 进程树 + takeover 终端。

    使用 psutil 递归杀进程树（避免 node.exe 等孤儿进程）。
    """
    ids = list(workers.keys())
    for wid in ids:
        w = workers.get(wid)
        if not w:
            continue
        if w._watchdog_task:
            w._watchdog_task.cancel()
        if w._consume_task:
            w._consume_task.cancel()
        if w._stdout_task:
            w._stdout_task.cancel()
        # A1 崩溃安全：关闭前 flush 防抖缓冲的流式块
        if w._hist_dirty:
            await _flush_history_now(w)
        await _kill_process_tree(w)
        await _kill_takeover_terminal(w)
    workers.clear()

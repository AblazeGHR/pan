"""Worker — runtime cbc process management.

Worker is ephemeral: kill it, the Worker is gone.
All persistent data lives in Session (session.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

from . import session as _sess
from .adapters import get_adapter, CliAdapter
from .config import load_config

_log = logging.getLogger(__name__)


# ── Worker 生命周期配置（启动时读取一次，缓存）──

_WORKER_TIMEOUT_SEC: float = 300.0  # 静默超时：无输出超过该值 → kill
_WORKER_IDLE_SEC: float = 300.0     # 空闲回收：idle 超时 → kill
_WATCHDOG_TICK_SEC: float = 30.0    # watchdog 检查间隔

# 可被 server 启动时覆盖（测试也可直接赋值）
_DEFAULTS_INITIALIZED = False


def load_worker_config():
    """从 config.json 读取 worker 生命周期配置并缓存。

    由 server 启动时调用一次（lifespan）；直接使用 core 的场景也会在
    首次 create_worker 前惰性初始化。
    """
    global _WORKER_TIMEOUT_SEC, _WORKER_IDLE_SEC, _DEFAULTS_INITIALIZED
    cfg = load_config().get("worker", {})
    _WORKER_TIMEOUT_SEC = float(cfg.get("timeout_sec", 300))
    _WORKER_IDLE_SEC = float(cfg.get("idle_sec", 300))
    _DEFAULTS_INITIALIZED = True


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
    worker_id: str
    session_id: str           # Session UUID (ses_<hex>)
    adapter: CliAdapter       # CLI tool adapter instance
    status: str = "idle"      # idle | running | held | error | queued | zombie
    process: asyncio.subprocess.Process | None = None
    _mcp_proc: asyncio.subprocess.Process | None = None  # in-flight one-shot MCP process
    _stdout_task: asyncio.Task | None = None
    _consume_task: asyncio.Task | None = None
    _watchdog_task: asyncio.Task | None = None
    # 唤醒信号通道（内存），语义按立项 4.3/4.7 收窄为"只唤醒、不承载正文"：
    # 消息真源是落盘 Session.queue_pending，本队列只放信号；真源迁移完成后
    # 普通任务也只放 item.id，正文由 _consumer 从真源按 id 拉取。
    pending_signal: asyncio.Queue | None = None
    _replaying: bool = False  # true during cbc --resume event replay
    takeover_pid: int | None = None  # PID of takeover PowerShell terminal
    # ── 活性探测（watchdog 用）──
    last_activity: float = 0.0  # time.monotonic；stdout 有事件 / 新任务入队时刷新
    # ── 任务序号（result 与 task 配对用）──
    _task_counter: int = 0   # 已分配的任务序号（send_task 入队时自增）
    _current_seq: int | None = None  # 正在处理的 item 序号（_consumer 取出时记录）
    _current_task_id: str | None = None  # 正在处理的 item 的 taskId（幂等用）


workers: dict[str, Worker] = {}

_broadcast: callable = None

# ── result waiters: 供 handoff（同步等待）在进程内捕获 worker.result ──
# worker_id → {seq: asyncio.Future}；result 的 taskSeq 匹配对应 seq 的 waiter 才 resolve。
# 多槽位（按 seq，A3 waiter 审查）：同一 worker 上可并发多个 handoff，各占一槽，
# 不再被单槽位覆盖。
_result_waiters: dict[str, dict[int, asyncio.Future]] = {}

# ── task 幂等注册表: taskId → {"status", "workerId", "result"}
# handoff 生成 taskId 入队时登记，result 时标记完成；Meta-Agent 重发带同一
# taskId → 检测已存在则返回状态，不重复入队（防超时后双跑）
_task_status: dict[str, dict] = {}

# task 幂等注册表条目 TTL：条目超过该时长（无论 pending 还是已完成）在下次
# handoff 访问注册表时被惰性清除，防止全局 dict 长期运行无界增长（H2 泄漏）。
_TASK_STATUS_TTL_SEC: float = 86400.0  # 24h


def _prune_task_status() -> None:
    """惰性清除 _task_status 中超过 TTL 的过期条目。

    注册表仅在 handoff 幂等检查处读取，故在 handoff 入口调用即可兜住泄漏。
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

    handoff 超时后任务仍在跑；若此时 worker 被杀/崩溃，taskId 若停留在 pending，
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


def _resolve_result_waiter(worker_id: str, status: str, result: str,
                           task_seq: int | None = None):
    """当 worker 产生 result 时，resolve 正在等待该 worker 的 handoff future。

    task_seq 传入时只匹配期望该序号的 waiter（避免拿到别的任务的结果）；
    为 None 时强制 resolve（worker 被杀/崩溃场景，该 worker 上所有 waiter 全 error）。
    """
    waiters = _result_waiters.get(worker_id)
    if not waiters:
        return
    if task_seq is None:
        # 强制 resolve：worker 被杀/崩溃 → 所有等待中的 handoff 全部结束
        _result_waiters.pop(worker_id, None)
        for fut in list(waiters.values()):
            if not fut.done():
                fut.set_result({"status": status, "result": result})
        return
    fut = waiters.pop(task_seq, None)
    if fut is None:
        return  # 不是等待中的任务结果，保留其他 waiter
    if not waiters:
        _result_waiters.pop(worker_id, None)
    if not fut.done():
        fut.set_result({"status": status, "result": result})


def _drop_result_waiter(worker_id: str, seq: int):
    """移除某个 (worker, seq) 的 waiter；该 worker 无剩余 waiter 时清理外层 key。"""
    waiters = _result_waiters.get(worker_id)
    if waiters:
        waiters.pop(seq, None)
        if not waiters:
            _result_waiters.pop(worker_id, None)


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
    """Session 是否配置了 MCP 工具（mcp_enabled 且 mcp_servers 非空）。

    MCP 是叠加属性：与 worker 的执行模式（output_mode）独立。
    """
    return bool(s and s.adapter_config.get("mcp_enabled") and s.adapter_config.get("mcp_servers"))


def _use_oneshot_mcp(s: _sess.Session | None) -> bool:
    """是否走 one-shot MCP 模式（每次任务新开 cbc 进程 + --mcp-config）。

    判定矩阵（三条通道）：
    - 无 MCP（mcp_enabled/mcp_servers 缺失）→ False：stream 长驻（无 MCP）
    - 有 MCP 且 output_mode 未设置 / == "stream" → False：stream 长驻 + MCP（cbc ≥ 2.137.0，默认）
    - 有 MCP 且 output_mode == "oneshot" → True：one-shot（显式指定才走）
    """
    if not _mcp_configured(s):
        return False
    return s.adapter_config.get("output_mode") == "oneshot"


# ── stdout reader ──

async def _read_stdout(w: Worker):
    adapter = w.adapter
    s = None  # bound even if stdout yields no parseable event (EOF check below)
    async for line in w.process.stdout:
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
                await _sess.save_async(s)

        # 任务完成 → 保存 Session + last_result
        if adapter.is_result_event(event):
            s = _session(w)
            is_error = adapter.is_result_error(event)
            w.status = "error" if is_error else "done"

            # replay 结束：标记完成，不保存（history 无变化）
            if w._replaying:
                w._replaying = False
                w.status = "idle"
                continue

            # taskSeq 统一用 _current_seq（_consumer 取出 item 时已从 handoff
            # 预分配的序号记录）。用 _result_count 会在中断/重启后与
            # _task_counter 错位，导致 handoff waiter 永远匹配不上而悬挂超时。
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
                await _sess.save_async(s)

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
            _resolve_result_waiter(w.worker_id, w.status, result_text, task_seq=task_seq)
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
    # 有 handoff 在等这个 worker → 立即 resolve 为错误，避免悬挂到超时
    _resolve_result_waiter(w.worker_id, "error", f"worker exited (returncode={code})")
    # H2: worker 退出 → 名下 pending 的 taskId 标 error（防止"超时+crash"组合
    # 让同 taskId 重试永久卡 pending）
    _mark_worker_tasks_error(w.worker_id, f"worker exited (returncode={code})")
    # 从 workers dict 移除尸体——否则 find_worker_by_session 会返回这个死 worker，
    # 后续 send_task 才报 'process dead'，晚了一步
    workers.pop(w.worker_id, None)


# ── consumer ──

async def _consumer(w: Worker):
    """Consumer loop. Three execution modes (selected by _use_oneshot_mcp):

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
    messages (handoff tasks / normal messages / system_prompt) stay single.
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

        text = item["text"]
        source = item.get("source", "agent")
        w._current_seq = item.get("seq")
        w._current_task_id = item.get("taskId")

        w._replaying = False

        s = _session(w)
        if s:
            injected_text = await _maybe_inject_memory(s, text)
            s.history.append({"role": "user", "content": injected_text})
            await _sess.save_async(s)
            text = injected_text

        # 选择执行模式：one-shot MCP（每次任务新开进程）vs stream（长驻，可带 MCP）
        use_mcp = _use_oneshot_mcp(s)

        if use_mcp:
            await _consumer_mcp(w, text, source, s)
        else:
            await _consumer_stream(w, text, source, s)


# ── 订阅制报告消费（立项 4.3）──

_REPORT_SEP = "─────"  # 报告拼接分隔线


def _format_report_batch(reports: list[dict]) -> str:
    """积压报告原样拼接：每条 report dict 序列化 + 显眼分隔线 + 来源标注。

    报告形状对齐 handoff：{"status","result","sessionId","taskId","workerId"}。
    """
    parts = []
    for r in reports:
        src = r.get("sessionId") or r.get("workerId") or "unknown"
        parts.append(
            f"{_REPORT_SEP} 子任务报告（来源 sessionId={src}）{_REPORT_SEP}\n"
            f"{json.dumps(r, ensure_ascii=False)}"
        )
    return "\n\n".join(parts)


async def _consume_pending_reports(w: Worker, s):
    """从落盘 queue_pending 取全部积压报告，拼接为一条消息交给模型处理。

    消费即删（清空后立即回写），与"落盘真源 + 内存信号"一致。
    """
    reports = s.queue_pending
    s.queue_pending = []
    await _sess.save_async(s)
    text = _format_report_batch(reports)

    # 报告不是 handoff 任务：无 seq 配对，清空当前配对上下文避免 last_result 错位
    w._current_seq = None
    w._current_task_id = None
    w._replaying = False

    # 复用普通消息处理路径：记忆注入 → history append → 执行
    injected_text = await _maybe_inject_memory(s, text)
    s.history.append({"role": "user", "content": injected_text})
    await _sess.save_async(s)

    use_mcp = _use_oneshot_mcp(s)
    if use_mcp:
        await _consumer_mcp(w, injected_text, "report", s)
    else:
        await _consumer_stream(w, injected_text, "report", s)


async def _enqueue_report(session_id: str, status: str, result: str,
                          task_id: str | None, worker_id: str):
    """订阅制报告入队：session 完成 → 若被其 managed_by 订阅，报告 append 到
    manager 的落盘队列 queue_pending，并唤醒 manager 的 consumer。

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

    manager.queue_pending.append({
        "status": status,
        "result": result,
        "sessionId": session_id,
        "taskId": task_id,
        "workerId": worker_id,
    })
    await _sess.save_async(manager)

    # 唤醒 manager 的 consumer（若 worker 存活）——报告正文在落盘队列，
    # 信号只负责唤醒，不承载正文
    mw = find_worker_by_session(s.managed_by)
    if (mw and mw.pending_signal is not None
            and not (mw.process is not None and mw.process.returncode is not None)):
        mw.last_activity = time.monotonic()
        # 信号只唤醒、不承载正文（立项 4.3/4.7：正文在落盘 queue_pending）
        await mw.pending_signal.put({"type": "report_signal"})


# ── watchdog：超时 / 空闲回收 ──


async def _watchdog(w: Worker):
    """周期性检查 worker 活性，超时/空闲则 kill。

    - stream 模式（w.process 非 None）：
      - running/queued：持续无输出超 _WORKER_TIMEOUT_SEC → 判定卡死 → kill
      - idle：任务完成且长时间无新任务 → 空闲回收 → kill
    - MCP one-shot 模式（w.process 为 None）：
      - 只做 idle 回收（超时已由 _consumer_mcp 读取超时承担，running 不干预）
    - held（takeover 模式）/ zombie：跳过，不回收
    触发 kill 时先 resolve 等待中的 handoff（error），再回收进程。

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

        # stream 模式：超时 + 空闲回收
        if w.status in ("running", "queued") and idle_for > _WORKER_TIMEOUT_SEC:
            _log.warning(
                "[Worker %s] watchdog kill: reason=timeout mode=stream status=%s "
                "idle_for=%.0fs timeout_threshold=%.0fs branch=stream_timeout",
                w.worker_id, w.status, idle_for, _WORKER_TIMEOUT_SEC,
            )
            await kill_worker(w.worker_id)
            return
        if w.status == "idle" and idle_for > _WORKER_IDLE_SEC:
            _log.info(
                "[Worker %s] watchdog kill: reason=idle_reclaim mode=stream status=%s "
                "idle_for=%.0fs idle_threshold=%.0fs branch=stream_idle_reclaim",
                w.worker_id, w.status, idle_for, _WORKER_IDLE_SEC,
            )
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
        # worker 已死，任何等待它的 handoff 都应立刻返回 error
        _resolve_result_waiter(w.worker_id, "error", "Worker process dead")
        return

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


async def _consumer_mcp(w: Worker, text: str, source: str, s):
    """One-shot MCP mode: spawn new cbc process per message with --mcp-config.

    Uses --resume to maintain conversation continuity across messages.
    The cbc process runs as a one-shot (-p mode) with the prompt as a CLI arg,
    which allows --mcp-config to work (incompatible with --input-format stream-json).
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

    # Build args without --input-format stream-json (required for MCP to work)
    args = adapter.base_args_stream() if hasattr(adapter, 'base_args_stream') else adapter.base_args()
    args.extend(adapter.model_args(s))
    args.extend(adapter.permission_mode_args(s))
    if hasattr(adapter, 'effort_args'):
        args.extend(adapter.effort_args(s))
    # NOTE: skip --settings in MCP mode (breaks MCP init)
    # see thinking_args() — skip it here since we're in base_args_stream() path

    # --resume: cbc natively maintains conversation history in
    # ~/.codebuddy/projects/d-project-Pan-memory/<session-id>.jsonl.
    # When cli_session_id is set (saved from init event), cbc picks up
    # the full context including MCP tool discovery state.
    if s.cli_session_id and adapter.supports_resume:
        args.extend(adapter.resume_args(s))

    # MCP config
    if hasattr(adapter, 'mcp_args'):
        args.extend(adapter.mcp_args(s))
    # No -d: create_subprocess_exec(cwd=s.workdir) below already makes cbc treat
    # the workdir as its project dir (JSONL + resume). MCP connection comes
    # from --mcp-config above (tested 2026-08-16: -d is redundant).

    # System prompt: pass via --system-prompt (override) so cbc injects it as a
    # real system message. --append-system-prompt is NOT honored by the model;
    # and text\n---\nprompt concatenation reads as ordinary user text.
    # Only inject on the first message of a session (before cli_session_id is
    # captured); --resume carries the model's context afterwards.
    if s.system_prompt and not s.cli_session_id:
        args.extend(["--system-prompt", s.system_prompt])
    # Prompt as last argument
    args.append(text)

    _log.info("[Worker %s] MCP one-shot spawn (full args): %s", w.worker_id, " ".join(repr(a) for a in args))

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=s.workdir or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        _log.error("[Worker %s] MCP spawn failed: %s", w.worker_id, e)
        if s:
            s.last_result = {"status": "error", "result": f"MCP spawn failed: {e}", "timestamp": datetime.now().isoformat()}
            await _sess.save_async(s)
        # M3: 置 idle 同步刷新活性时间，避免该 worker 刚忙完就被 watchdog 当空闲回收
        w.last_activity = time.monotonic()
        w.status = "idle"
        return

    # Track in-flight process so kill_worker can terminate it (see #3).
    w._mcp_proc = proc

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
            result_text = event.get("result", "")
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
        cbc_error = _extract_cbc_error(output)
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
    task_seq = w._current_seq
    await _bcast({
        "type": "worker.result",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "status": status,
        "result": result,
        "taskSeq": task_seq,
    })
    _resolve_result_waiter(w.worker_id, status, result, task_seq=task_seq)
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


# ── lifecycle ──

# spawn 防重复（立项 4.5）：同一 session 的并发 create_worker 通过 per-session
# lock 串行化，避免竞态双 spawn。dict 的 setdefault 是纯同步原子操作（无 await
# 临界区），事件循环内天然安全，不需要额外 guard lock。
_spawn_locks: dict[str, asyncio.Lock] = {}


async def _session_spawn_lock(session_id: str) -> asyncio.Lock:
    """获取该 session 的 spawn 锁（并发 create_worker 串行化）。"""
    return _spawn_locks.setdefault(session_id, asyncio.Lock())


async def create_worker(session_id: str) -> Worker | str:
    """Spawn a CLI process for the given Session UUID.

    Returns Worker on success, error string on failure.

    防重复 spawn（立项 4.5）：同一 session 的并发调用由 per-session lock 串行化；
    已有活 worker 时直接复用现有 Worker，不重复创建（任何 session 不应被重复
    spawn worker）。显式重启场景（/api/spawn 等）会先 kill 再进入本函数，不受影响。

    Three execution modes (see _use_oneshot_mcp):
    - Stream mode (default, no MCP): long-running process with --input-format stream-json.
    - Stream + MCP mode: long-running process spawned with --mcp-config
      (adapter_config.output_mode == "stream", requires cbc >= 2.137.0).
    - One-shot MCP mode: no long-running process. Each task spawns a one-shot
      cbc process. Used when MCP configured and output_mode unset/oneshot.
    """
    lock = await _session_spawn_lock(session_id)
    async with lock:
        return await _create_worker(session_id)


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
    use_mcp = _use_oneshot_mcp(s)

    if use_mcp:
        # One-shot MCP mode: no long-running process, consumer spawns per-task
        proc = None
        resuming = False
    else:
        # Stream mode: spawn long-running process.
        # If MCP is configured (mcp_on, output_mode="stream"), the process is
        # spawned with --mcp-config (build_spawn_args -> mcp_args) so the
        # long-running stream keeps MCP tools (cbc >= 2.137.0).
        extra_args = None
        if mcp_on and s.system_prompt and not s.cli_session_id:
            # stream+MCP: inject system_prompt via --system-prompt (same as
            # one-shot MCP) instead of a separate first message, avoiding the
            # roleplay trap (see cbc-mcp-踩坑记录.md #13).
            extra_args = ["--system-prompt", s.system_prompt]
        proc = await _spawn_process(session_id, adapter=adapter, extra_args=extra_args)
        if isinstance(proc, str):
            return proc
        resuming = bool(s.cli_session_id) and adapter.supports_resume

    w = Worker(worker_id=worker_id, session_id=session_id,
               adapter=adapter,
               status="idle", process=proc, pending_signal=asyncio.Queue(),
               _replaying=resuming)
    w.last_activity = time.monotonic()
    workers[worker_id] = w

    if not use_mcp:
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

    # Inject system_prompt
    # - Pure stream (no MCP): injected as a separate first message (existing).
    # - With MCP (one-shot or stream+MCP): skipped here — injected via
    #   --system-prompt at spawn / in _consumer_mcp, because a separate first
    #   message biases the LLM into pure roleplay and prevents it from
    #   discovering MCP tools via ToolSearch.
    if s.system_prompt and not mcp_on:
        _log.info("[Worker %s] injecting system_prompt (%d chars)", worker_id, len(s.system_prompt))
        await send_task(worker_id, s.system_prompt, source="system_prompt")
    elif s.system_prompt:
        _log.info("[Worker %s] MCP mode: system_prompt injected via --system-prompt", worker_id)

    # 订阅制报告：落盘 queue_pending 有积压（上次 worker 死亡/回收未消费）
    # → 发唤醒信号，_consumer 批量消费（立项 4.3 防死亡丢消息）
    if s.queue_pending:
        await w.pending_signal.put({"type": "report_signal"})

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

    # 有 handoff 在等这个 worker → 立即 resolve 为错误
    _resolve_result_waiter(worker_id, "error", "worker killed")
    # H2: worker 被杀 → 名下 pending 的 taskId 标 error（防止幂等重试永久卡 pending）
    _mark_worker_tasks_error(worker_id, "worker killed")

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
    # if session has cli_session_id, --resume was used → enter replay mode
    s = _sess.get(w.session_id)
    w._replaying = bool(s and s.cli_session_id) and w.adapter.supports_resume
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
                   status="idle", process=proc, pending_signal=asyncio.Queue())
    # 注意：branch 不设 _replaying（与 create_worker/restart_worker 不同）。
    # branch 的新 session history 为空，需要从 cbc --resume --fork-session
    # 的重放中填入历史，所以走正常 append 路径。主路径的 session 已有
    # 完整 history（磁盘 ground truth），replay 期间跳过 append 避免重复。
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

    # 分配任务序号（handoff 预分配后传入，保证 waiter 与 item 一致）
    if seq is None:
        w._task_counter += 1
        seq = w._task_counter

    w.last_activity = time.monotonic()
    # 唤醒信号：真源（落盘 queue_pending）迁移完成前暂以完整 item 入队
    # （_consumer 仍需 text/seq/taskId 配对）；迁移后本队列只放 item.id
    # （立项 4.3/4.7）。
    await w.pending_signal.put({"text": text, "source": source, "seq": seq, "taskId": task_id})

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


# ── 编排原语：handoff / assign / send（供 Meta-Agent 调用） ──


async def _ensure_worker(session_id: str) -> tuple[Worker | None, str | None]:
    """确保 session 有活的 worker。返回 (worker, None) 或 (None, error)。"""
    w = find_worker_by_session(session_id)
    if w is None or (w.process is not None and w.process.returncode is not None):
        created = await create_worker(session_id)
        if isinstance(created, str):
            return None, created
        w = created
    return w, None


async def handoff(session_id: str, text: str, source: str = "agent",
                  timeout: float = 600.0, task_id: str | None = None) -> dict:
    """[DEPRECATED] 同步阻塞：确保 worker 存在 → 发任务 → 等待该任务对应的 result。

    DEPRECATED（立项 4.7）：推荐改用 ``assign``（异步分派）+ 报告订阅消费。
    理由：若确实需要等，meta-agent 不应处于 busy 或可能被插队的状态——"等"应是
    meta-agent 的默认 idle 状态，而非一个阻塞调用动作。保留本函数仅服务确需
    严格阻塞同步返回值的场景；未来可能整体移除。
    标记粒度：代码注释 + MCP 工具 description，暂不加运行时警告。

    预分配任务序号：waiter 只匹配该序号的 result，避免拿到队列中
    其他任务的结果。超时（默认 10 分钟）返回 {"status": "error"}。
    waiter 按 (worker, seq) 多槽位（dict[seq, Future]）：同一 worker 上并发
    多个 handoff 互不覆盖，各自按自己的 seq resolve。

    task_id 幂等：同一 taskId 重发不重复入队。若该 taskId 已存在：
    - 已完成 → 返回已有结果
    - 进行中 → 返回 {"status": "pending", "taskId":...}
    用于超时后安全重试，避免双跑。
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

    # 预分配序号：send_task 不再自增，保证 item.seq == waiter 期望的 seq
    w._task_counter += 1
    seq = w._task_counter

    if task_id is not None:
        _task_status[task_id] = {"status": "pending", "workerId": w.worker_id,
                                 "taskId": task_id, "ts": time.monotonic()}

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _result_waiters.setdefault(w.worker_id, {})[seq] = fut

    send_err = await send_task(w.worker_id, text, source=source, seq=seq, task_id=task_id)
    if send_err:
        _drop_result_waiter(w.worker_id, seq)
        if task_id is not None:
            _task_status[task_id] = {"status": "error", "result": send_err,
                                     "taskId": task_id, "ts": time.monotonic()}
        return {"status": "error", "result": send_err}

    try:
        result = await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        _drop_result_waiter(w.worker_id, seq)
        # 超时：任务仍在队列中跑，返回 pending + taskId，Meta-Agent 稍后重试/查状态
        if task_id is not None:
            return {"status": "pending", "taskId": task_id,
                    "workerId": w.worker_id, "result": f"handoff timed out after {timeout:.0f}s"}
        return {"status": "error", "result": f"handoff timed out after {timeout:.0f}s"}
    result["workerId"] = w.worker_id
    if task_id is not None:
        result["taskId"] = task_id
        result["ts"] = time.monotonic()
        _task_status[task_id] = dict(result)
    return result


async def assign(session_id: str, text: str, source: str = "agent") -> dict:
    """异步分派：确保 worker 存在 → 发任务 → 立即返回 queued。

    完成时通过 worker.result 事件（配合 /ws/agent subscribe）回调。
    适用于并行 fan-out。
    """
    w, err = await _ensure_worker(session_id)
    if err:
        return {"status": "error", "result": err}

    send_err = await send_task(w.worker_id, text, source=source)
    if send_err:
        return {"status": "error", "result": send_err}
    return {"status": "queued", "workerId": w.worker_id, "sessionId": session_id}


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
        await _kill_process_tree(w)
        await _kill_takeover_terminal(w)
    workers.clear()

"""Worker — runtime cbc process management.

Worker is ephemeral: kill it, the Worker is gone.
All persistent data lives in Session (session.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

from . import session as _sess
from .adapters import get_adapter, CliAdapter

_log = logging.getLogger(__name__)


# ── Memory injection helper ──


async def _maybe_inject_memory(s, text: str) -> str:
    """If session has a character_id, search memory and inject context.

    Runs in a thread to avoid blocking the event loop (embedding is CPU-bound).
    On failure, returns the original text unchanged.
    """
    if not s.character_id:
        return text

    try:
        from .memory_context import search_and_format, inject_context

        # Resolve memory db dir from the project root (repo-relative), NOT
        # Path.cwd() — the server uses an absolute DATA_DIR and the two must
        # point at the same DB (#22).
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
        db_dir = str(_PROJECT_ROOT / "data" / "memory")

        ctx = await asyncio.to_thread(
            search_and_format,
            text,
            character_id=s.character_id,
            db_dir=db_dir,
        )
        if ctx.snippet_count > 0:
            return inject_context(text, ctx)
    except Exception:
        _log.warning("Memory injection failed for %s, using raw text", s.character_id)
    return text


@dataclass
class Worker:
    worker_id: str
    session_id: str           # Session UUID (ses_<hex>)
    adapter: CliAdapter       # CLI tool adapter instance
    status: str = "idle"      # idle | running | held | error | spawning | queued | zombie
    process: asyncio.subprocess.Process | None = None
    _mcp_proc: asyncio.subprocess.Process | None = None  # in-flight one-shot MCP process
    _stdout_task: asyncio.Task | None = None
    _consume_task: asyncio.Task | None = None
    queue: asyncio.Queue | None = None
    _replaying: bool = False  # true during cbc --resume event replay
    takeover_pid: int | None = None  # PID of takeover PowerShell terminal


workers: dict[str, Worker] = {}

_broadcast: callable = None


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
        print(f"[Worker] psutil kill tree failed for PID={pid}: {e}")


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

        # 提取 session_id + model 并写入 Session
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
            # spawning → idle：CLI 就绪，可以接收任务
            if w.status == "spawning":
                w.status = "idle"
                await _bcast({
                    "type": "worker.status",
                    "workerId": w.worker_id,
                    "sessionId": w.session_id,
                    "status": "idle",
                })

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

            if s:
                result_text = adapter.extract_result_text(event)
                s.last_result = {
                    "status": w.status,
                    "result": result_text,
                    "cli_session_id": s.cli_session_id,
                    "timestamp": datetime.now().isoformat(),
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

            await _bcast({
                "type": "worker.result",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "status": w.status,
                "result": adapter.extract_result_text(event),
            })
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
    print(f"[Worker {w.worker_id}] {adapter.name} 进程退出，返回码 {code}")

    # 如果已经通过 result event 收到正常输出（last_result 有内容且非 error），
    # 不要覆盖。某些 CLI 退出时返回非零码（如 kimi 的 0xC0000409）但回复已完整。
    if code != 0 and w.status == "idle" and s and s.last_result and s.last_result.get("status") != "error":
        print(f"[Worker {w.worker_id}] 已有正常结果，忽略非零退出码 {code}")
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
    # 从 workers dict 移除尸体——否则 find_worker_by_session 会返回这个死 worker，
    # 后续 send_task 才报 'process dead'，晚了一步
    workers.pop(w.worker_id, None)


# ── consumer ──

async def _consumer(w: Worker):
    """Consumer loop. Two modes:
    
    - Stream mode (default): long-running cbc process with --input-format stream-json.
      Each message is written to stdin, responses parsed from stdout.
    - One-shot MCP mode: new cbc process per message with --mcp-config.
      Used when session has mcp_servers configured (cbc MCP incompatible with stream-json).
    """
    while True:
        item = await w.queue.get()
        if item is None:
            break

        text = item["text"]
        source = item.get("source", "agent")

        w._replaying = False

        s = _session(w)
        if s:
            injected_text = await _maybe_inject_memory(s, text)
            s.history.append({"role": "user", "content": injected_text})
            await _sess.save_async(s)
            text = injected_text

        # Check if MCP mode (one-shot) or stream mode
        use_mcp = s and s.adapter_config.get("mcp_enabled") and s.adapter_config.get("mcp_servers")

        if use_mcp:
            await _consumer_mcp(w, text, source, s)
        else:
            await _consumer_stream(w, text, source, s)


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
        })
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
    # NOTE: pass -d <workdir> as arg (not cwd= to subprocess)
    # cbc treats cwd= as "random dir" but -d as "project directory",
    # and only -d registers the MCP servers as directly connected.
    if s.workdir:
        args.extend(["-d", s.workdir])

    # History replay: only needed when --resume is NOT available
    # (first message of a session, before cli_session_id is captured).
    if s.system_prompt and not s.cli_session_id:
        # First user message: user instruction first, system_prompt follows
        text = f"{text}\n\n---\n{s.system_prompt}"
    # Prompt as last argument
    args.append(text)

    _log.info("[Worker %s] MCP one-shot spawn (full args): %s", w.worker_id, " ".join(repr(a) for a in args))

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        _log.error("[Worker %s] MCP spawn failed: %s", w.worker_id, e)
        if s:
            s.last_result = {"status": "error", "result": f"MCP spawn failed: {e}", "timestamp": datetime.now().isoformat()}
            await _sess.save_async(s)
        w.status = "idle"
        return

    # Track in-flight process so kill_worker can terminate it (see #3).
    w._mcp_proc = proc

    # Collect output
    output = b""
    timed_out = False
    try:
        try:
            while True:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=120)
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
        w.status = "idle"
        return

    if cli_session_id:
        s.cli_session_id = cli_session_id
    # Append extracted blocks (assistant/thinking/tool) — same as stream mode.
    for block in assistant_blocks:
        s.history.append(block)

    # Surface failures the user can actually see (#8 timeout, #9 non-zero exit).
    if timed_out and not result_text:
        status, result = (
            "error",
            "Task timed out after 120s and the process was killed",
        )
    elif not result_text and returncode not in (None, 0):
        tail = output.decode(errors="replace")[-2000:].strip()
        status, result = "error", f"cbc exited with code {returncode}:\n{tail}"
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

    w.status = "idle"
    await _bcast({
        "type": "worker.result",
        "workerId": w.worker_id,
        "sessionId": w.session_id,
        "status": status,
        "result": result,
    })


# ── lifecycle ──

async def create_worker(session_id: str) -> Worker | str:
    """Spawn a CLI process for the given Session UUID.

    Returns Worker on success, error string on failure.

    Two modes:
    - Stream mode (default): long-running process with --input-format stream-json.
    - MCP mode: no long-running process. Each task spawns a one-shot cbc process.
      Used when session has mcp_servers configured.
    """
    s = _sess.get(session_id)
    if not s:
        return f"Session {session_id} not found"

    old = find_worker_by_session(session_id)
    if old:
        # Replace a live worker for this session. Deliberately do NOT clear
        # cli_session_id: resuming the cbc JSONL is the intended context-
        # continuity mechanism, and clearing it here would force a cold start
        # on every restart (#11, resolved by design).
        await kill_worker(old.worker_id)

    adapter = get_adapter(s.adapter)
    worker_id = await _next_worker_id()

    use_mcp = bool(s.adapter_config.get("mcp_enabled") and s.adapter_config.get("mcp_servers"))

    if use_mcp:
        # MCP mode: no long-running process, consumer spawns per-task
        proc = None
        resuming = False
    else:
        # Stream mode: spawn long-running process
        proc = await _spawn_process(session_id, adapter=adapter)
        if isinstance(proc, str):
            return proc
        resuming = bool(s.cli_session_id) and adapter.supports_resume

    w = Worker(worker_id=worker_id, session_id=session_id,
               adapter=adapter,
               status="idle" if use_mcp else "spawning", process=proc, queue=asyncio.Queue(),
               _replaying=resuming)
    workers[worker_id] = w

    if not use_mcp:
        w._stdout_task = asyncio.create_task(_read_stdout(w))
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

    # Inject system_prompt as first message if set
    # MCP mode: skip separate injection — system_prompt biases LLM into pure
    # roleplay, preventing it from discovering MCP tools via ToolSearch.
    if s.system_prompt and not use_mcp:
        _log.info("[Worker %s] injecting system_prompt (%d chars)", worker_id, len(s.system_prompt))
        await send_task(worker_id, s.system_prompt, source="system_prompt")
    elif s.system_prompt and use_mcp:
        _log.info("[Worker %s] MCP mode: system_prompt will be prepended to first user message", worker_id)
    
    return w


async def _kill_takeover_terminal(w: Worker) -> bool:
    """杀掉 takeover 模式打开的终端及子进程树。异步版，不阻塞事件循环。"""
    if not w.takeover_pid:
        return False
    pid = w.takeover_pid
    print(f"[Worker {w.worker_id}] 杀 takeover 终端 PID={pid}")
    try:
        await asyncio.to_thread(_kill_pid_tree, pid)
        print(f"[Worker {w.worker_id}] takeover 终端已结束 PID={pid}")
    except Exception as e:
        print(f"[Worker {w.worker_id}] 杀 takeover 终端异常: {e}")
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

    if w._consume_task:
        w._consume_task.cancel()
    if w._stdout_task:
        w._stdout_task.cancel()
    await _kill_process_tree(w)
    await _kill_takeover_terminal(w)

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
        if w._consume_task:
            w._consume_task.cancel()
        if w._stdout_task:
            w._stdout_task.cancel()
        await _kill_process_tree(w)
        await _kill_takeover_terminal(w)
    except Exception as exc:
        print(f"[Worker {worker_id}] BG cleanup error: {exc!r}")
    finally:
        workers.pop(worker_id, None)
        try:
            await _bcast({
                "type": "worker.destroyed",
                "workerId": worker_id,
                "sessionId": session_id,
            })
        except Exception as bcast_err:
            print(f"[Worker {worker_id}] BG cleanup bcast error: {bcast_err!r}")


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
    if w._stdout_task:
        w._stdout_task.cancel()
    if w._consume_task:
        w._consume_task.cancel()
    w.queue = asyncio.Queue()
    w._stdout_task = asyncio.create_task(_read_stdout(w))
    w._consume_task = asyncio.create_task(_consumer(w))


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
    w.status = "spawning"
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
    w.status = "spawning"
    await _restart_tasks(w)

    await _bcast({
        "type": "worker.reconfigured",
        "workerId": worker_id,
        "sessionId": w.session_id,
        "status": "spawning",
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
                   status="spawning", process=proc, queue=asyncio.Queue())
    # 注意：branch 不设 _replaying（与 create_worker/restart_worker 不同）。
    # branch 的新 session history 为空，需要从 cbc --resume --fork-session
    # 的重放中填入历史，所以走正常 append 路径。主路径的 session 已有
    # 完整 history（磁盘 ground truth），replay 期间跳过 append 避免重复。
    workers[new_id] = new_w
    new_w._stdout_task = asyncio.create_task(_read_stdout(new_w))
    new_w._consume_task = asyncio.create_task(_consumer(new_w))

    await _sess.save_async(s)

    await _bcast({
        "type": "worker.spawned",
        "workerId": new_id,
        "sessionId": new_session_id,
        "name": s.name,
        "status": "spawning",
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


async def send_task(worker_id: str, text: str, source: str = "agent") -> str | None:
    w = workers.get(worker_id)
    if not w:
        return "Worker not found"
    if w.status == "held":
        return "Worker is held (takeover mode). Restart first."
    # In MCP mode, process is None (spawned per-task). Still allow queue.
    if w.process is not None and w.process.returncode is not None:
        return "Worker process dead"
    if w.queue is None:
        return "Worker queue not ready"

    await w.queue.put({"text": text, "source": source})

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
        if w._consume_task:
            w._consume_task.cancel()
        if w._stdout_task:
            w._stdout_task.cancel()
        await _kill_process_tree(w)
        await _kill_takeover_terminal(w)
    workers.clear()

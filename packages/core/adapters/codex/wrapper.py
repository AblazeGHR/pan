"""Stable executable entry point for the Codex Pan bridge.

The adapter starts this file with ``--app-server`` so the default path is the
native long-lived ``codex app-server --stdio`` protocol implemented in
``app_server_wrapper.py``. The original ``codex exec`` loop remains below as
a compatibility fallback for direct/manual invocations that omit the flag.

Legacy exec fallback fixes (对齐 opencode/kimi wrapper）：
- codex 子进程显式 ``stdin=DEVNULL``，切断与 server 长驻管道的连接（prompt 来自 CLI
  参数，不依赖 stdin）；否则 codex 会读 stdin 等 EOF 而静默挂起 → 会话卡 running。
- 真实入口解析为 ``[node, codex.js]``（由 adapter 经 --codex-path/--node-path 传入），
  避开 npm .CMD shim 经 cmd.exe 的二次切分（中文参数乱码根因）。
- 子进程 cwd 设为 workdir（worker 已设 wrapper cwd；wrapper 再显式 ``-C`` 兜底），
  保证 codex 在正确工作区运行。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from queue import Queue


def _write_stdout_line(text: str) -> None:
    """以 UTF-8 写入 stdout 并换行，避免 Windows 管道默认编码问题。"""
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
    sys.stdout.buffer.flush()


def _filter_resume_opts(opts: list[str]) -> list[str]:
    """续接（resume）时保留 ``-c`` 配置覆盖与审批/权限 flag，丢弃其它一次性 flag。

    resume 沿用 thread 已存的 model / cwd 配置，无需重复传 ``--model``（已改为
    ``-c model=``）/ ``-C``；但审批 flag 必须重传：thread 存的 approval_mode="never"
    是 bypass flag 落库的结果，resume 不重传时 codex 以 never 策略**拒绝 MCP 工具
    调用**（实测报 "MCP tool call requires approval, but approval policy is never"）。
    实测 ``codex exec resume`` 接受 ``--dangerously-bypass-approvals-and-sandbox``
    与 ``--approve-for-me``（不可重复，故原样透传即可）。
    """
    out: list[str] = []
    i = 0
    while i < len(opts):
        a = opts[i]
        if a == "-c":
            out.append(a)
            if i + 1 < len(opts):
                out.append(opts[i + 1])
                i += 2
            else:
                i += 1
        elif a in ("--dangerously-bypass-approvals-and-sandbox", "--approve-for-me"):
            out.append(a)
            i += 1
        else:
            i += 1
    return out


def _system_prompt_opts(system_prompt: str | None) -> list[str]:
    """Encode Pan's system prompt as Codex developer instructions.

    ``codex exec`` has no public ``--system-prompt`` flag. The current native
    CLI accepts the ``developer_instructions`` config key, which keeps the
    prompt in the instruction/developer layer instead of sending it as an
    ordinary user turn.
    """
    if not system_prompt:
        return []
    return ["-c", f"developer_instructions={json.dumps(system_prompt, ensure_ascii=False)}"]


def _build_codex_args(node: str, codex_js: str, text: str,
                      thread_id: str | None, extra_opts: list[str],
                      cwd: str | None) -> list[str]:
    cmd = [node, codex_js] if node else [codex_js]
    cmd += ["exec"]
    if thread_id:
        # resume 续接同一 thread（沿用其已存 cwd/model/approval），仅透传 -c 类覆盖；
        # 注意 `codex exec resume` 不接受 `-C`（实测报 "unexpected argument '-C'"），
        # 也无需 -C（thread 已记住 cwd）。
        opts = _filter_resume_opts(extra_opts)
        cmd += ["resume", thread_id]
        cmd += opts
        cmd += [text, "--json", "--skip-git-repo-check"]
    else:
        cmd += list(extra_opts)
        cmd += [text, "--json"]
        if cwd:
            cmd += ["-C", cwd]
        cmd += ["--skip-git-repo-check"]
    return cmd


def _forward_and_collect(stdout, stderr, thread_id: str | None):
    """转发 codex stdout 每一行，同时提取 thread_id / 末条 assistant 文本 / usage / 错误。"""
    last_assistant_text: str | None = None
    last_usage: dict | None = None
    error_event: dict | None = None

    for line_b in stdout:
        line = line_b.decode("utf-8", errors="replace").rstrip("\n")
        if not line:
            continue
        _write_stdout_line(line)

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type")
        if etype == "thread.started":
            tid = event.get("thread_id")
            if tid:
                thread_id = tid
        elif etype == "item.completed":
            item = event.get("item", {}) or {}
            # live stdout 用 snake_case（agent_message），持久化 thread_items 用
            # camelCase（agentMessage），两者都兼容
            if item.get("type") in ("agent_message", "agentMessage"):
                txt = item.get("text")
                if txt:
                    last_assistant_text = txt
        elif etype == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                last_usage = usage
        elif etype == "error":
            error_event = event

    return thread_id, last_assistant_text, last_usage, error_event


def _stderr_pump(stderr, label: str) -> None:
    """将 codex 子进程的 stderr 透传到 wrapper 的 stderr（→ Pan 日志）。"""
    try:
        for line_b in stderr:
            text = line_b.decode("utf-8", errors="replace").rstrip("\n")
            if not text:
                continue
            sys.stderr.buffer.write(
                f"[codex stderr][{label}] {text}\n".encode("utf-8", errors="replace")
            )
            sys.stderr.buffer.flush()
    except (ValueError, OSError):
        pass


def _stdin_reader(message_queue: Queue, shutdown_event: threading.Event) -> None:
    """后台线程：从 stdin 读取 JSON 消息并入队（与 opencode wrapper 同形）。

    以二进制读取并按 UTF-8 显式解码，避免 Windows 下 TextIOWrapper 用系统 locale
    编码（如 cp936/GBK）解码 UTF-8 字节导致中文乱码、json.loads 失败。
    """
    f = sys.stdin
    buf = getattr(f, "buffer", None) or f
    while not shutdown_event.is_set():
        try:
            line_b = buf.readline()
        except (ValueError, OSError):
            break
        if not line_b:
            break  # stdin closed (EOF) — signal main loop to exit
        if isinstance(line_b, str):
            line_b = line_b.encode("utf-8")
        try:
            line = line_b.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("text"):
            message_queue.put(msg)
    message_queue.put(None)


def _main_loop(node: str, codex_js: str, extra_opts: list[str],
               initial_thread_id: str | None, cwd: str | None,
               system_prompt: str | None = None) -> int:
    thread_id = initial_thread_id
    message_queue: Queue = Queue()
    shutdown_event = threading.Event()

    reader_thread = threading.Thread(
        target=_stdin_reader, args=(message_queue, shutdown_event), daemon=True
    )
    reader_thread.start()

    try:
        while True:
            msg = message_queue.get()
            if msg is None:
                break
            text = msg.get("text", "")
            if not text:
                continue

            # Only the first fresh turn receives the developer instructions.
            # Once Codex has created a thread, resume carries that context.
            call_opts = extra_opts
            if not thread_id and system_prompt:
                call_opts = extra_opts + _system_prompt_opts(system_prompt)
            args = _build_codex_args(node, codex_js, text, thread_id, call_opts, cwd)

            try:
                # 关键修复：codex exec 不依赖 stdin（prompt 来自 CLI 参数），显式置
                # stdin=DEVNULL 切断与 server 长驻管道的连接，避免等 EOF 静默挂起。
                # close_fds 避免继承 server 的其它句柄（监听 socket 等）。
                proc = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd or None,
                    close_fds=True,
                )
            except FileNotFoundError:
                _write_stdout_line(json.dumps({
                    "type": "result", "is_error": True,
                    "result": f"Codex executable not found: {codex_js}",
                }, ensure_ascii=False))
                continue
            except OSError as e:
                _write_stdout_line(json.dumps({
                    "type": "result", "is_error": True,
                    "result": f"OS error spawning Codex: {e}",
                }, ensure_ascii=False))
                continue

            pump = threading.Thread(
                target=_stderr_pump, args=(proc.stderr, text[:20]), daemon=True
            )
            pump.start()

            new_thread_id, last_text, last_usage, error_event = _forward_and_collect(
                proc.stdout, proc.stderr, thread_id
            )
            thread_id = new_thread_id or thread_id
            _, stderr_bytes = proc.communicate()
            proc.wait()
            pump.join(timeout=1.0)

            if error_event is not None:
                err = error_event.get("error", {})
                message = (err.get("data") or {}).get("message") or err.get("name") or "Codex error"
                result_event = {
                    "type": "result", "is_error": True,
                    "result": f"[codex] {message}",
                }
            elif proc.returncode not in (None, 0) and not last_text:
                tail = (stderr_bytes or b"").decode("utf-8", errors="replace")[-2000:].strip()
                result_event = {
                    "type": "result", "is_error": True,
                    "result": f"codex exited with code {proc.returncode}:\n{tail}",
                }
            else:
                result_event = {
                    "type": "result", "is_error": False,
                    "result": last_text or "",
                    "usage": last_usage,
                }
            _write_stdout_line(json.dumps(result_event, ensure_ascii=False))
    except Exception:
        import traceback
        err = traceback.format_exc()
        sys.stderr.buffer.write(err.encode("utf-8", errors="replace"))
        sys.stderr.buffer.flush()
        raise
    finally:
        shutdown_event.set()
    return 0


def main() -> int:
    # The native app-server bridge keeps one Codex thread/turn protocol alive
    # and is the default path used by the adapter.  Keep this file as the
    # stable executable entry point so existing deployments and tests that
    # refer to ``wrapper.py`` continue to work; the old exec loop remains a
    # useful fallback for manual invocations without --app-server.
    if "--app-server" in sys.argv[1:]:
        from app_server_wrapper import main as app_server_main
        argv = [arg for arg in sys.argv[1:] if arg != "--app-server"]
        return app_server_main(argv)

    parser = argparse.ArgumentParser(description="OpenAI Codex persistent wrapper for Pan")
    parser.add_argument("--codex-path", required=True,
                        help="Path to codex.js (resolved real entry, not the .CMD shim)")
    parser.add_argument("--node-path", default="node",
                        help="node executable used to launch codex.js")
    parser.add_argument("--thread-id", default=None,
                        help="Initial thread id to resume (continuity across worker respawns)")
    parser.add_argument("--codex-extra-args", default="[]",
                        help="JSON list of codex-level option flags (model/permission/mcp/effort)")
    parser.add_argument("--system-prompt", default=None,
                        help="Pan system prompt, passed as Codex developer_instructions on the first fresh turn")
    args = parser.parse_args()

    cwd = os.environ.get("PAN_CODEX_CWD") or os.getcwd()
    try:
        extra_opts = json.loads(args.codex_extra_args)
        if not isinstance(extra_opts, list):
            extra_opts = []
    except json.JSONDecodeError:
        extra_opts = []
    return _main_loop(
        node=args.node_path,
        codex_js=args.codex_path,
        extra_opts=extra_opts,
        initial_thread_id=args.thread_id,
        cwd=cwd,
        system_prompt=args.system_prompt,
    )


if __name__ == "__main__":
    sys.exit(main())

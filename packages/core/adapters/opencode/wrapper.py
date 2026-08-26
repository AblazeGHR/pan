"""OpenCode CLI 长驻包装器。

OpenCode 的 `opencode run` 是一次性进程（非 stdin 流式），无法像 cbc 那样长期驻留。
本包装器作为 Pan Worker 的子进程长期运行，内部循环：

1. 从 stdin 读取一条 JSON 消息（由 OpencodeAdapter.encode_user_message 生成）。
2. 用当前 session_id、model、variant、thinking、auto 等参数调用
   `opencode run "<text>" --format json --no-replay [--session <id>]`。
3. 逐行转发 OpenCode 的 stdout（JSONL 事件）。
4. 从事件提取 sessionID，供下一轮复用（resume）。
5. OpenCode 子进程结束后，输出一条合成的 result 事件，让 worker.py 标记任务完成
   （OpenCode 的 --format json 没有原生 result 事件，完成由进程退出表征）。
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


def _forward_and_collect(stdout, session_id: str | None):
    """转发 stdout 每一行，同时提取 session_id 与最后一条 assistant 文本/错误。"""
    last_assistant_text: str | None = None
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
        # sessionID 出现在每个事件上
        sid = event.get("sessionID")
        if sid:
            session_id = sid

        if etype == "text":
            txt = (event.get("part") or {}).get("text")
            if txt:
                last_assistant_text = txt
        elif etype == "error":
            error_event = event

    return session_id, last_assistant_text, error_event


def _stderr_pump(stderr, label: str) -> None:
    """将 opencode 子进程的 stderr 透传到 wrapper 的 stderr（→ Pan 日志）。"""
    try:
        for line_b in stderr:
            text = line_b.decode("utf-8", errors="replace").rstrip("\n")
            if not text:
                continue
            sys.stderr.buffer.write(
                f"[opencode stderr][{label}] {text}\n".encode("utf-8", errors="replace")
            )
            sys.stderr.buffer.flush()
    except (ValueError, OSError):
        pass


def _stdin_reader(message_queue: Queue, shutdown_event: threading.Event) -> None:
    """后台线程：从 stdin 读取 JSON 消息并入队。

    stdin 以**二进制**读取并按 UTF-8 显式解码，避免 Windows 下 TextIOWrapper
    用系统 locale 编码（如 cp936/GBK）解码 UTF-8 字节，导致中文被乱码、
    json.loads 失败、消息被静默丢弃。
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
    # EOF or shutdown: let the main loop drain queued messages then exit.
    message_queue.put(None)


def _build_run_args(opencode_path: str, text: str, session_id: str | None,
                     model: str | None, variant: str | None,
                     thinking: bool, auto: bool) -> list[str]:
    args = [opencode_path, "run", text, "--format", "json", "--no-replay"]
    if session_id:
        args.extend(["--session", session_id])
    if model:
        args.extend(["--model", model])
    if variant:
        args.extend(["--variant", variant])
    if thinking:
        args.append("--thinking")
    if auto:
        args.append("--auto")
    return args


def _main_loop(opencode_path: str, model: str | None, variant: str | None,
               thinking: bool, auto: bool,
               initial_session_id: str | None, cwd: str | None) -> int:
    session_id = initial_session_id
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

            args = _build_run_args(
                opencode_path, text, session_id, model, variant, thinking, auto
            )

            try:
                # 关键修复：opencode 的 `--format json` 一次性模式仍会读取 stdin。
                # 若继承 wrapper 的 stdin（来自 server 的长驻管道且保持打开），opencode
                # 会一直等待 stdin EOF/输入而静默挂起——无任何 stdout/stderr 输出，表现为
                # 会话卡 running、60s 超时、takeover 报 "no CLI session yet"。
                # 显式置 stdin=DEVNULL 切断与 server 管道的连接（prompt 来自 CLI 参数，
                # 不依赖 stdin）；close_fds 避免继承 server 的其它句柄（监听 socket 等）。
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
                    "role": "result", "is_error": True,
                    "result": f"OpenCode executable not found: {opencode_path}",
                }, ensure_ascii=False))
                continue
            except OSError as e:
                _write_stdout_line(json.dumps({
                    "role": "result", "is_error": True,
                    "result": f"OS error spawning OpenCode: {e}",
                }, ensure_ascii=False))
                continue

            pump = threading.Thread(
                target=_stderr_pump, args=(proc.stderr, text[:20]), daemon=True
            )
            pump.start()

            new_session_id, last_text, error_event = _forward_and_collect(
                proc.stdout, session_id
            )
            session_id = new_session_id or session_id
            _, stderr_bytes = proc.communicate()
            proc.wait()
            pump.join(timeout=1.0)

            if error_event is not None:
                err = error_event.get("error", {})
                message = (err.get("data") or {}).get("message") or err.get("name") or "OpenCode error"
                result_event = {
                    "role": "result", "is_error": True,
                    "result": f"[opencode {err.get('name','error')}] {message}",
                }
            else:
                result_event = {
                    "role": "result", "is_error": False,
                    "result": last_text or "",
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
    parser = argparse.ArgumentParser(description="OpenCode persistent wrapper for Pan")
    parser.add_argument("--opencode-path", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--variant", default=None)
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args()

    cwd = os.environ.get("PAN_OPENCODE_CWD") or os.getcwd()
    return _main_loop(
        opencode_path=args.opencode_path,
        model=args.model,
        variant=args.variant,
        thinking=args.thinking,
        auto=args.auto,
        initial_session_id=args.session_id,
        cwd=cwd,
    )


if __name__ == "__main__":
    sys.exit(main())

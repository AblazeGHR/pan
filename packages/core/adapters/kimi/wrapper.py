"""Kimi Code CLI 长驻包装器。

Kimi 的 `-p/--prompt` 模式是一次性进程（非 stdin 流式），无法像 cbc 那样长期驻留。
本包装器作为 Pan Worker 的子进程长期运行，内部循环：

1. 从 stdin 读取一条 JSON 消息（由 KimiAdapter.encode_user_message 生成）。
2. 用当前 session_id、model 等参数调用 `kimi -p ... --output-format stream-json`。
3. 逐行转发 Kimi 的 stdout。
4. 从 meta/session.resume_hint 事件提取 session_id，供下一轮使用。
5. Kimi 子进程结束后，输出一条合成的 result 事件，让 worker.py 标记任务��成。
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


def _forward_and_collect(stdout, session_id: str | None) -> tuple[str | None, str | None]:
    """转发 stdout 每一行，同时提取 session_id 和最后一条 assistant 文本。"""
    last_assistant_text: str | None = None
    for line_b in stdout:
        line = line_b.decode("utf-8", errors="replace").rstrip("\n")
        if not line:
            continue
        # 原样转发给 Pan worker
        _write_stdout_line(line)

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        role = event.get("role")
        if role == "meta" and event.get("type") == "session.resume_hint":
            sid = event.get("session_id")
            if sid:
                session_id = sid
        elif role == "assistant" and event.get("content"):
            last_assistant_text = event["content"]

    return session_id, last_assistant_text


def _stdin_reader(message_queue: Queue, shutdown_event: threading.Event) -> None:
    """后台线程：从 stdin 读取 JSON 消息并入队。"""
    while not shutdown_event.is_set():
        try:
            line = sys.stdin.readline()
        except (ValueError, OSError):
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("text"):
            message_queue.put(msg)


def _main_loop(kimi_path: str, model: str | None,
               initial_session_id: str | None,
               cwd: str | None) -> int:
    """主循环：从队列取消息，逐条调用 Kimi。"""
    session_id = initial_session_id
    message_queue: Queue = Queue()
    shutdown_event = threading.Event()

    reader_thread = threading.Thread(
        target=_stdin_reader,
        args=(message_queue, shutdown_event),
        daemon=True,
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

            args = [kimi_path, "-p", text, "--output-format", "stream-json"]
            if model:
                args.extend(["-m", model])
            if session_id:
                args.extend(["-S", session_id])

            try:
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd or None,
                )
            except FileNotFoundError:
                _write_stdout_line(json.dumps({
                    "role": "result",
                    "is_error": True,
                    "result": f"Kimi executable not found: {kimi_path}",
                }, ensure_ascii=False))
                continue
            except OSError as e:
                _write_stdout_line(json.dumps({
                    "role": "result",
                    "is_error": True,
                    "result": f"OS error spawning Kimi: {e}",
                }, ensure_ascii=False))
                continue

            new_session_id, last_text = _forward_and_collect(proc.stdout, session_id)
            session_id = new_session_id or session_id
            _, stderr_bytes = proc.communicate()
            proc.wait()

            if proc.returncode != 0 and not last_text:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip() if stderr_bytes else ""
                msg = f"kimi exited with code {proc.returncode}"
                if stderr_text:
                    msg += f": {stderr_text}"
                result_event = {
                    "role": "result",
                    "is_error": True,
                    "result": msg,
                }
            else:
                if proc.returncode != 0:
                    print(f"[kimi wrapper] process exited {proc.returncode} but output collected, ignoring", file=sys.stderr)
                result_event = {
                    "role": "result",
                    "is_error": False,
                    "result": last_text or "",
                }
            _write_stdout_line(json.dumps(result_event, ensure_ascii=False))
    except Exception:
        # 输出异常信息到 stderr；注意 stderr 在 worker.py 中会被合并到 stdout，
        # 因此这里用 UTF-8 写入 stderr buffer 避免 Windows 编码问题。
        import traceback
        err = traceback.format_exc()
        sys.stderr.buffer.write(err.encode("utf-8", errors="replace"))
        sys.stderr.buffer.flush()
        raise

    shutdown_event.set()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kimi Code CLI persistent wrapper for Pan"
    )
    parser.add_argument("--kimi-path", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args()

    cwd = os.environ.get("PAN_KIMI_CWD") or os.environ.get("CLICONDUCTOR_KIMI_CWD") or os.getcwd()
    return _main_loop(
        kimi_path=args.kimi_path,
        model=args.model,
        initial_session_id=args.session_id,
        cwd=cwd,
    )


if __name__ == "__main__":
    sys.exit(main())

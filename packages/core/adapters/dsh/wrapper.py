"""Pan ↔ DSH (DeepSeek Harness) headless 长驻桥接。

Pan worker 只认"一个常驻进程 + stdin 逐条消息 + stdout 逐行 JSON 事件"的契约
（``execution_modes = ["stream"]``）。DSH headless 没有 stdin 长驻模式——它一次
调用跑完一个任务就退出——所以这里和 kimi/codex 走同一条路：**用 wrapper 冒充长驻
进程**，内部每收到一条消息就 spawn 一次 ``dsh headless --json``。

行为要点：

- 子进程 argv：``[<DeepSeek Harness.exe>, --expose-internals, <cli.js>,
  --profile <p>, (--patch <file>), --json, (--session-id <id>), -]``。
  ``-`` 表示任务从 stdin 读，prompt 因此不经过命令行长度/编码（中文、引号、
  多行都安全）。exe 直接交给 CreateProcess，避免 cmd.exe 的二次切分。
- ``ELECTRON_RUN_AS_NODE=1``：DSH 的 CLI 由 Electron 二进制当 node 跑，shim
  （``dsh.cmd``）自己设了这个变量，我们直接调 exe 时必须自己设。
- ``DSH_PERMISSION_MODE``：DSH 的 sandbox-policy / approval 行都读它。
- 子进程 stdout 的 NDJSON 由 ``events.map_event`` 翻成 Pan 事件；进程退出后按
  DSH 的官方成败规则（runner error / turn_end.reason / exit code）合成唯一一条
  ``result``——worker 每轮必须恰好收到一条。
- 子进程 stderr 用后台线程抽干（防管道写满死锁），前缀 ``[dsh]`` 转发到
  wrapper 的 stderr（worker 把 stderr 并入 stdout，非 JSON 行不会污染事件流）。

本文件是"稳定可执行入口"：worker argv 只认 ``wrapper.py`` 的 flag，升级 DSH
事件词表时改 ``events.py`` 即可。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

# wrapper 会以绝对路径从 Session workdir 启动，包导入需要仓库根在 sys.path。
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from packages.core.adapters.dsh.events import TurnState, map_event, terminal_result
from packages.jobs.window import command_window_options

# stderr 只保留尾部（错误诊断用），避免长 run 撑爆内存。
_STDERR_TAIL_LINES = 200


def _write_stdout_line(text: str) -> None:
    """以 UTF-8 写一行到 stdout（Windows 管道默认编码不可信）。"""
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
    sys.stdout.buffer.flush()


def _write_event(event: dict) -> None:
    _write_stdout_line(json.dumps(event, ensure_ascii=False))


def _child_env(permission_mode: str | None) -> dict:
    env = dict(os.environ)
    # DSH CLI 是 Electron 主二进制 + cli.js：必须以 node 模式运行。
    env["ELECTRON_RUN_AS_NODE"] = "1"
    if permission_mode:
        env["DSH_PERMISSION_MODE"] = permission_mode
    # 让子进程的 stdio 固定为 UTF-8，避免中文在 cp936 管道里变乱码。
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _build_command(opts: argparse.Namespace, session_id: str | None) -> list[str]:
    """构造一次 headless run 的 argv。

    ``session_id`` 为 None（首条消息且未指定 resume）时不带 ``--session-id``，
    DSH 会新建 ``session-<uuid>``；之后每条消息都续接捕获到的 id。
    """
    if opts.dsh_cli_js:
        cmd = [opts.dsh_path, "--expose-internals", opts.dsh_cli_js]
    else:
        cmd = [opts.dsh_path]
    cmd += ["--profile", opts.profile]
    if opts.patch:
        cmd += ["--patch", opts.patch]
    cmd += ["--json"]
    sid = session_id or opts.session_id
    if sid:
        cmd += ["--session-id", sid]
    # '-' = 任务从 stdin 读（prompt 不经过命令行）。
    cmd += ["-"]
    return cmd


def _drain_stderr(stream, tail: deque) -> None:
    """抽干子进程 stderr，保留尾部并转发（带前缀，避免与事件流混淆）。"""
    try:
        for raw in stream:
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not text:
                continue
            tail.append(text)
            sys.stderr.buffer.write(f"[dsh] {text}\n".encode("utf-8", errors="replace"))
            sys.stderr.buffer.flush()
    except (ValueError, OSError):
        pass


def _read_stdin_messages():
    """逐行读 worker 的 stdin JSON 帧（``{"text": ...}``）。"""
    stream = getattr(sys.stdin, "buffer", None) or sys.stdin
    while True:
        try:
            raw = stream.readline()
        except (ValueError, OSError):
            return
        if not raw:
            return
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        text = message.get("text")
        if isinstance(text, str) and text:
            yield text


def run_turn(opts: argparse.Namespace, text: str, session_id: str | None) -> str | None:
    """跑一条任务；返回本轮结束后的 session id（供后续消息续接）。"""
    state = TurnState(session_id=session_id or "")
    cmd = _build_command(opts, session_id)
    tail: deque = deque(maxlen=_STDERR_TAIL_LINES)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.getcwd(),
            env=_child_env(opts.permission_mode),
            close_fds=True,
            **command_window_options(),
        )
    except FileNotFoundError:
        _write_event({
            "type": "result", "is_error": True,
            "result": f"DSH executable not found: {opts.dsh_path}",
            "error": {"message": f"DSH executable not found: {opts.dsh_path}"},
            "error_source": "wrapper_exception",
        })
        return session_id
    except OSError as exc:
        _write_event({
            "type": "result", "is_error": True,
            "result": f"OS error spawning DSH: {exc}",
            "error": {"message": f"OS error spawning DSH: {exc}"},
            "error_source": "wrapper_exception",
        })
        return session_id

    pump = threading.Thread(target=_drain_stderr, args=(proc.stderr, tail), daemon=True)
    pump.start()

    try:
        # 任务正文（原文透传；DSH 会在末尾保留换行，语义无差别）。
        proc.stdin.write(text.encode("utf-8", errors="replace"))
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass

    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # 非 JSON（例如 loader 诊断）原样转发到 stderr，保持可诊断。
                sys.stderr.buffer.write(f"[dsh] {line}\n".encode("utf-8", errors="replace"))
                sys.stderr.buffer.flush()
                continue
            if not isinstance(event, dict):
                continue
            for mapped in map_event(event, state):
                _write_event(mapped)
    finally:
        returncode = proc.wait()
        pump.join(timeout=2.0)

    _write_event(terminal_result(state, returncode, "\n".join(tail)))
    return state.session_id or session_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DSH headless persistent wrapper for Pan")
    parser.add_argument("--dsh-path", required=True,
                        help="DeepSeek Harness executable (or dsh.cmd fallback)")
    parser.add_argument("--dsh-cli-js", default=None,
                        help="dsh desktop-host cli.js run by the Electron binary")
    parser.add_argument("--profile", default="headless", help="DSH profile name")
    parser.add_argument("--patch", default=None,
                        help="Session-scoped profile overlay written by Pan (model/effort/MCP)")
    parser.add_argument("--permission-mode", default=None,
                        help="DSH_PERMISSION_MODE: read-only | workspace-write | danger-full-access")
    parser.add_argument("--session-id", default=None,
                        help="Initial DSH session id to resume (continuity across worker respawns)")
    opts = parser.parse_args(argv)

    session_id = opts.session_id or None
    for text in _read_stdin_messages():
        session_id = run_turn(opts, text, session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())

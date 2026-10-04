"""Bounded, evidence-producing ConPTY probe for cbc's native rewind.

Stage 1/2 are non-destructive. Stage 3 is intentionally opt-in with
``--stage3`` and uses only a disposable work directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
CAPTURE = EVIDENCE / "rewind_probe_capture.txt"


def dump_json(name: str, value: dict[str, Any]) -> None:
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def append_capture(title: str, text: str) -> None:
    with CAPTURE.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n{'=' * 20} {title} {'=' * 20}\n{text}\n")


def version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def kill_tree(pid: int | None) -> list[int]:
    """Best-effort bounded process-tree cleanup for pywinpty's child PID."""
    if not pid:
        return []
    try:
        import psutil
        root = psutil.Process(pid)
        children = root.children(recursive=True)
    except Exception:
        return []
    victims = children + [root]
    pids = [p.pid for p in victims]
    for process in victims:
        try:
            process.kill()
        except Exception:
            pass
    try:
        _, alive = psutil.wait_procs(victims, timeout=2.0)
        for process in alive:
            try:
                process.kill()
            except Exception:
                pass
    except Exception:
        pass
    return pids


class PtySession:
    def __init__(self, argv: list[str], cwd: str, rows: int = 36, cols: int = 120):
        from pyte import Screen, Stream
        from winpty import PtyProcess

        self.screen = Screen(cols, rows)
        self.stream = Stream(self.screen)
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.proc = PtyProcess.spawn(
            argv, cwd=cwd, dimensions=(rows, cols),
            env={**os.environ, "TERM": "xterm-256color", "CI": "0"},
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        while True:
            try:
                if not self.proc.isalive():
                    self.queue.put(None)
                    return
                chunk = self.proc.read(4096)
                self.queue.put(chunk if chunk else None)
            except (EOFError, OSError, RuntimeError):
                self.queue.put(None)
                return

    def text(self) -> str:
        return "\n".join(self.screen.display).rstrip()

    def drain_until(self, deadline: float, stop_tokens: tuple[str, ...] = (), min_seconds: float = 0.8) -> str:
        last = self.text()
        started = time.monotonic()
        while time.monotonic() < deadline:
            try:
                chunk = self.queue.get(timeout=0.15)
            except queue.Empty:
                continue
            if chunk:
                self.stream.feed(chunk)
                last = self.text()
                if time.monotonic() - started >= min_seconds and stop_tokens and any(token.lower() in last.lower() for token in stop_tokens):
                    break
            elif not self.proc.isalive():
                break
        return last

    def send(self, text: str) -> None:
        self.proc.write(text)

    def close(self) -> dict[str, Any]:
        pid = getattr(self.proc, "pid", None)
        cleanup: dict[str, Any] = {"pid": pid, "terminate": None, "killed_pids": []}
        try:
            if self.proc.isalive():
                self.proc.write("\x03")
                time.sleep(0.2)
        except Exception as exc:
            cleanup["interrupt_error"] = repr(exc)
        done = threading.Event()

        def terminate() -> None:
            try:
                self.proc.terminate(force=True)
                cleanup["terminate"] = "returned"
            except Exception as exc:
                cleanup["terminate"] = f"error: {exc!r}"
            finally:
                done.set()

        threading.Thread(target=terminate, daemon=True).start()
        done.wait(1.5)
        if not done.is_set():
            cleanup["terminate"] = "timed_out"
        cleanup["killed_pids"] = kill_tree(pid)
        try:
            self.proc.close()
        except Exception as exc:
            cleanup["close_error"] = repr(exc)
        return cleanup


def resolve_commands() -> dict[str, Any]:
    shim = shutil.which("cbc")
    node = shutil.which("node")
    entry = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@tencent-ai" / "codebuddy-code" / "bin" / "codebuddy"
    return {
        "shim": [shim] if shim else None,
        "node": [node, str(entry)] if node and entry.is_file() else None,
        "shim_path": shim,
        "node_path": node,
        "entry_path": str(entry),
    }


def run_spawn(mode: str, argv: list[str], cwd: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {"spawn_mode": mode, "argv": argv, "started": False, "error": None}
    session: PtySession | None = None
    try:
        session = PtySession(argv, cwd)
        screen = session.drain_until(time.monotonic() + timeout, ("Do you trust the files", "Welcome", "What would you like to do", "❯"), min_seconds=1.5)
        result.update({
            "started": True,
            "seconds": round(time.monotonic() - started, 2),
            "initial_screen_digest": digest(screen),
            "initial_screen": screen,
            "trust_dialog": "do you trust the files" in screen.lower(),
        })
        append_capture(f"Stage 1 {mode} initial screen", screen)
    except Exception as exc:
        result.update({"seconds": round(time.monotonic() - started, 2), "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if session is not None:
            result["cleanup"] = session.close()
    return result


def stage1(commands: dict[str, Any], timeout: float) -> dict[str, Any]:
    results = []
    for mode, argv in (("cbc_shim", commands["shim"]), ("node_direct", commands["node"])):
        results.append(run_spawn(mode, argv + ["--permission-mode", "bypassPermissions"], str(ROOT), timeout) if argv else {"spawn_mode": mode, "started": False, "error": "command unavailable"})
    value = {"stage": 1, "python": sys.version.split()[0], "platform": sys.platform, "dependencies": {"pywinpty": version("pywinpty"), "pyte": version("pyte"), "psutil": version("psutil")}, "results": results}
    dump_json("rewind_probe_stage1.json", value)
    return value


def stage2(commands: dict[str, Any], timeout: float) -> dict[str, Any]:
    results = []
    for mode, argv in (("cbc_shim", commands["shim"]), ("node_direct", commands["node"])):
        item: dict[str, Any] = {"spawn_mode": mode, "started": False, "rewind_visible": False}
        if not argv:
            item["error"] = "command unavailable"
            results.append(item)
            continue
        session: PtySession | None = None
        try:
            session = PtySession(argv + ["--permission-mode", "bypassPermissions"], str(ROOT))
            initial = session.drain_until(time.monotonic() + timeout, ("Do you trust the files", "Welcome", "What would you like to do", "❯"), min_seconds=1.5)
            item.update({"started": True, "initial_screen_digest": digest(initial), "trust_dialog": "do you trust the files" in initial.lower()})
            if item["trust_dialog"]:
                item["error"] = "trust dialog appeared; probe deliberately did not accept it"
                item["screen"] = initial
                append_capture(f"Stage 2 {mode} trust dialog", initial)
            else:
                # Filter the interactive command palette without submitting a
                # command; this makes /rewind observable even when the list is
                # longer than the 36-row viewport.
                session.send("/rewind")
                help_screen = session.drain_until(time.monotonic() + min(timeout, 5.0))
                item.update({"screen": help_screen, "screen_digest": digest(help_screen), "rewind_visible": "/rewind" in help_screen.lower()})
                append_capture(f"Stage 2 {mode} after /help", help_screen)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if session is not None:
                item["cleanup"] = session.close()
        results.append(item)
    if any(item.get("rewind_visible") for item in results):
        conclusion = "TUI command list contains /rewind"
    elif any(item.get("trust_dialog") for item in results):
        conclusion = "blocked by trust dialog; no command-list assertion"
    else:
        conclusion = "TUI command list did not contain /rewind"
    value = {"stage": 2, "results": results, "conclusion": conclusion}
    dump_json("rewind_probe_stage2.json", value)
    return value


def find_recent_transcript(session_id: str) -> str | None:
    root = Path.home() / ".codebuddy" / "projects"
    direct = list(root.rglob(f"{session_id}.jsonl")) if root.is_dir() else []
    if direct:
        return str(direct[0])
    return None


def stage3_attempt(commands: dict[str, Any], attempt: int, timeout: float) -> dict[str, Any]:
    """Run one bounded end-to-end attempt in a disposable directory."""
    if not commands.get("shim"):
        return {"attempt": attempt, "status": "blocked", "error": "cbc shim unavailable"}
    started = time.monotonic()
    session: PtySession | None = None
    work = Path(tempfile.mkdtemp(prefix=f"pan-rewind-stage3-{attempt}-", dir=str(ROOT)))
    target = work / "rewind_probe_file.txt"
    sentinel = f"PAN_REWIND_SENTINEL_ATTEMPT_{attempt}"
    session_id = f"pan_rewind_probe_{int(time.time())}_{attempt}"
    item: dict[str, Any] = {
        "attempt": attempt, "workdir": str(work), "target": str(target),
        "session_id": session_id, "sentinel": sentinel,
        "status": "started", "keypresses": [], "screens": [],
    }
    try:
        argv = commands["shim"] + ["--permission-mode", "bypassPermissions", "--session-id", session_id]
        session = PtySession(argv, str(work))
        initial = session.drain_until(time.monotonic() + min(timeout, 15.0), ("Do you trust the files", "What would you like to do", "❯"), min_seconds=1.5)
        item["initial_screen"] = initial
        item["trust_dialog"] = "do you trust the files" in initial.lower()
        append_capture(f"Stage 3 attempt {attempt} initial", initial)
        if item["trust_dialog"]:
            item.update({"status": "blocked_trust_dialog", "error": "trust dialog appeared; no authorization key was sent"})
            return item

        prompt = (
            f"Use your native file editing tool, not bash or shell, to create exactly one file at "
            f"{target} with exactly this single-line content: {sentinel}. "
            "Do not modify any other file. After the edit, briefly confirm the file path and content."
        )
        # The Windows TUI documents Ctrl+J as the reliable line-feed/submit
        # sequence when terminal Enter bindings are not configured.
        session.send(prompt)
        session.proc.sendcontrol("j")
        item["keypresses"].append("<prompt> + Ctrl+J (submit)")
        file_deadline = time.monotonic() + timeout
        while time.monotonic() < file_deadline:
            session.drain_until(time.monotonic() + 0.5)
            if target.is_file() and target.read_text(encoding="utf-8", errors="replace").strip() == sentinel:
                break
        item["file_after_edit"] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        item["file_created"] = target.exists()
        after_edit = session.text()
        item["screen_after_edit"] = after_edit
        append_capture(f"Stage 3 attempt {attempt} after edit", after_edit)
        if not item["file_created"]:
            item.update({"status": "edit_failed", "error": "target file was not created with sentinel"})
            return item

        # Allow the assistant turn to settle before opening rewind.
        session.drain_until(time.monotonic() + 5.0)
        item["conversation_tail_before"] = session.text()[-4000:]
        item["transcript_path_before"] = find_recent_transcript(session_id)

        # Preferred documented path: empty input + two Esc presses.
        session.send("\x1b")
        time.sleep(0.15)
        session.send("\x1b")
        item["keypresses"].append("Esc, Esc")
        rewind_screen = session.drain_until(time.monotonic() + min(timeout, 8.0))
        item["screen_after_esc_esc"] = rewind_screen
        append_capture(f"Stage 3 attempt {attempt} after Esc Esc", rewind_screen)
        low = rewind_screen.lower()
        menu_detected = any(token in low for token in ("rewind", "restore", "checkpoint", "message"))
        item["rewind_menu_detected"] = menu_detected
        if not menu_detected:
            # Fallback: type /rewind and inspect, without submitting if no menu appears.
            session.send("/rewind")
            item["keypresses"].append("/rewind (not submitted)")
            rewind_screen = session.drain_until(time.monotonic() + min(timeout, 8.0))
            item["screen_after_slash_rewind"] = rewind_screen
            append_capture(f"Stage 3 attempt {attempt} after slash rewind", rewind_screen)
            menu_detected = "/rewind" in rewind_screen.lower() or "restore" in rewind_screen.lower()
            item["rewind_menu_detected"] = menu_detected
        if not menu_detected:
            item.update({"status": "rewind_menu_not_detected", "error": "neither Esc Esc nor /rewind exposed a rewind UI"})
            return item

        # The menu's default is the latest checkpoint. Select it, then choose
        # the combined code+conversation scope by visible text when possible.
        session.send("\r")
        item["keypresses"].append("Enter (latest checkpoint)")
        scope_screen = session.drain_until(time.monotonic() + min(timeout, 8.0))
        item["screen_after_checkpoint_enter"] = scope_screen
        append_capture(f"Stage 3 attempt {attempt} scope menu", scope_screen)
        scope_low = scope_screen.lower()
        combined = any(token in scope_low for token in ("code and conversation", "conversation and code", "both", "code + conversation", "code/conversation"))
        item["combined_scope_visible"] = combined
        if combined:
            # Usually combined scope is the first/default option; only press
            # Enter, avoiding guessed arrow counts.
            session.send("\r")
            item["keypresses"].append("Enter (combined code+conversation)")
        else:
            item.update({"status": "scope_menu_unrecognized", "error": "combined code+conversation option not visible"})
            return item
        completed = session.drain_until(time.monotonic() + min(timeout, 12.0))
        item["screen_after_rewind"] = completed
        append_capture(f"Stage 3 attempt {attempt} after rewind", completed)
        item["file_after_rewind"] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        item["file_rolled_back"] = not target.exists() or item["file_after_rewind"] != sentinel
        item["conversation_tail_after"] = session.text()[-4000:]
        item["transcript_path_after"] = find_recent_transcript(session_id)
        item["status"] = "success" if item["file_rolled_back"] else "file_not_rolled_back"
        return item
    except Exception as exc:
        item.update({"status": "exception", "error": f"{type(exc).__name__}: {exc}"})
        return item
    finally:
        if session is not None:
            item["cleanup"] = session.close()
        item["seconds"] = round(time.monotonic() - started, 2)
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception as exc:
            item["workdir_cleanup_error"] = repr(exc)


def stage3(commands: dict[str, Any], timeout: float, repeat: int) -> dict[str, Any]:
    attempts = []
    for attempt in range(1, repeat + 1):
        item = stage3_attempt(commands, attempt, timeout)
        attempts.append(item)
        dump_json(f"rewind_probe_stage3_attempt{attempt}.json", item)
        if item.get("status") in {"blocked_trust_dialog", "rewind_menu_not_detected", "scope_menu_unrecognized"}:
            # Do not blindly repeat a failed interaction pattern.
            break
    successes = [x for x in attempts if x.get("status") == "success"]
    value = {"stage": 3, "repeat_requested": repeat, "attempts": attempts, "success_count": len(successes), "conclusion": "feasible" if len(successes) == len(attempts) and attempts else "inconclusive"}
    dump_json("rewind_probe_stage3.json", value)
    return value


def headless_prepare(commands: dict[str, Any], work: Path, session_id: str, target: Path, sentinel: str, timeout: float) -> dict[str, Any]:
    """Create tracked file material without using PTY or shell file edits."""
    prompt = (
        f"Use the native Write or Edit file tool (not Bash, shell, or any command) to create exactly one file at {target} "
        f"with exactly this one-line content: {sentinel}. Do not modify any other file. After the tool call, briefly confirm it."
    )
    started = time.monotonic()
    cbc = commands.get("shim")
    result: dict[str, Any] = {"argv": None, "exit_code": None, "seconds": None, "stdout": "", "stderr": "", "prompt": prompt}
    if not cbc:
        result["error"] = "cbc shim unavailable"
        return result
    argv = cbc + ["-p", "--session-id", session_id, "--permission-mode", "bypassPermissions", "--output-format", "json", prompt]
    result["argv"] = argv
    try:
        proc = subprocess.run(argv, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
        result.update({"exit_code": proc.returncode, "stdout": proc.stdout[-12000:], "stderr": proc.stderr[-4000:]})
    except subprocess.TimeoutExpired as exc:
        result.update({"error": "headless timeout", "stdout": str(exc.stdout or "")[-12000:], "stderr": str(exc.stderr or "")[-4000:]})
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["seconds"] = round(time.monotonic() - started, 2)
    result["file_exists"] = target.is_file()
    result["file_content"] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
    return result


def transcript_info(session_id: str, target: Path) -> dict[str, Any]:
    root = Path.home() / ".codebuddy" / "projects"
    candidates = list(root.rglob(f"{session_id}.jsonl")) if root.is_dir() else []
    rows: list[dict[str, Any]] = []
    tool_rows: list[dict[str, Any]] = []
    for path in candidates:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows.append(row)
                text = json.dumps(row, ensure_ascii=False).lower()
                if "tool_use" in text or "tooluse" in text or "write" in text or "edit" in text:
                    if str(target).lower() in text or target.name.lower() in text:
                        tool_rows.append(row)
        except OSError:
            continue
    return {"paths": [str(p) for p in candidates], "message_count": len(rows), "file_tool_rows": tool_rows[-10:], "file_tool_use_found": bool(tool_rows)}


def stage3b_attempt(commands: dict[str, Any], attempt: int, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix=f"pan-rewind-stage3b-{attempt}-", dir=str(ROOT)))
    target = work / "rewind_probe_file.txt"
    sentinel = f"PAN_REWIND_STAGE3B_SENTINEL_{attempt}"
    session_id = f"pan_rewind_stage3b_{int(time.time())}_{attempt}"
    item: dict[str, Any] = {"attempt": attempt, "workdir": str(work), "target": str(target), "session_id": session_id, "sentinel": sentinel, "keypresses": [], "screens": []}
    session: PtySession | None = None
    try:
        item["headless"] = headless_prepare(commands, work, session_id, target, sentinel, min(timeout, 180.0))
        item["transcript_before"] = transcript_info(session_id, target)
        dump_json(f"rewind_probe_stage3b_attempt{attempt}_prepare.json", item)
        append_capture(f"Stage 3b attempt {attempt} headless prepare", json.dumps(item["headless"], ensure_ascii=False, indent=2))
        if not item["headless"].get("file_exists") or not item["transcript_before"].get("file_tool_use_found"):
            item["status"] = "prepare_failed_or_untracked"
            return item

        argv = commands["shim"] + ["-r", session_id, "--permission-mode", "bypassPermissions"]
        session = PtySession(argv, str(work))
        initial = session.drain_until(time.monotonic() + min(timeout, 20.0), ("Do you trust the files", "CodeBuddy Code", "❯"), min_seconds=2.0)
        item["initial_screen"] = initial
        append_capture(f"Stage 3b attempt {attempt} resumed initial", initial)
        if "do you trust the files" in initial.lower():
            item["status"] = "trust_dialog"
            item["error"] = "trust dialog appeared; did not accept it"
            return item
        item["input_empty_before"] = "\n>                                                                                                                       " in initial or initial.rstrip().endswith(">")
        session.send("\x1b")
        time.sleep(0.2)
        session.send("\x1b")
        item["keypresses"].append("Esc, wait 200ms, Esc")
        after = session.drain_until(time.monotonic() + min(timeout, 12.0))
        item["screen_after_esc_esc"] = after
        append_capture(f"Stage 3b attempt {attempt} after Esc Esc", after)
        low = after.lower()
        item["rewind_menu_detected"] = any(x in low for x in ("rewind", "restore", "checkpoint", "conversation"))
        item["status"] = "esc_esc_menu" if item["rewind_menu_detected"] else "esc_esc_no_menu"
        if item["rewind_menu_detected"]:
            # The menu initially selects the synthetic "current" row. Move to
            # the preceding user-message checkpoint, then inspect the scope
            # chooser before selecting anything else.
            session.send("\x1b[A")
            item["keypresses"].append("ArrowUp")
            checkpoint_screen = session.drain_until(time.monotonic() + 3.0)
            item["screen_after_arrow_up"] = checkpoint_screen
            append_capture(f"Stage 3b attempt {attempt} after ArrowUp", checkpoint_screen)
            session.send("\r")
            item["keypresses"].append("Enter (checkpoint)")
            scope_screen = session.drain_until(time.monotonic() + 5.0)
            item["screen_after_checkpoint_enter"] = scope_screen
            append_capture(f"Stage 3b attempt {attempt} scope chooser", scope_screen)
            scope_low = scope_screen.lower()
            item["combined_scope_visible"] = any(token in scope_low for token in ("code and conversation", "conversation and code", "both", "code + conversation", "restore code"))
            if item["combined_scope_visible"]:
                # In the observed UI the combined option is the default when
                # a checkpoint is selected; confirm it with Enter.
                session.send("\r")
                item["keypresses"].append("Enter (combined code+conversation default)")
                result_screen = session.drain_until(time.monotonic() + 12.0)
                item["screen_after_restore"] = result_screen
                append_capture(f"Stage 3b attempt {attempt} after restore", result_screen)
                time.sleep(1.0)
                item["file_after_restore"] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
                item["file_rolled_back"] = (not target.exists()) or item["file_after_restore"] != sentinel
                item["transcript_after"] = transcript_info(session_id, target)
                # Rewind forks the conversation; capture any newly-created
                # JSONL with the same cwd/session lineage while it is live.
                project_root = Path.home() / ".codebuddy" / "projects"
                recent = []
                if project_root.is_dir():
                    for path in project_root.rglob("*.jsonl"):
                        try:
                            if path.stat().st_mtime >= started - 5:
                                recent.append(str(path))
                        except OSError:
                            pass
                item["recent_transcripts"] = recent[-20:]
                item["status"] = "restore_submitted"
            else:
                item["status"] = "scope_chooser_unrecognized"
        dump_json(f"rewind_probe_stage3b_attempt{attempt}.json", item)
        return item
    except Exception as exc:
        item["status"] = "exception"
        item["error"] = f"{type(exc).__name__}: {exc}"
        return item
    finally:
        if session is not None:
            item["cleanup"] = session.close()
        item["seconds"] = round(time.monotonic() - started, 2)
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception as exc:
            item["workdir_cleanup_error"] = repr(exc)
        dump_json(f"rewind_probe_stage3b_attempt{attempt}.json", item)


def stage3b(commands: dict[str, Any], timeout: float, repeat: int) -> dict[str, Any]:
    attempts = []
    for attempt in range(1, max(1, min(repeat, 3)) + 1):
        item = stage3b_attempt(commands, attempt, timeout)
        attempts.append(item)
        dump_json(f"rewind_probe_stage3b_attempt{attempt}.json", item)
        if item.get("status") in {"prepare_failed_or_untracked", "trust_dialog", "exception"}:
            break
    value = {"stage": "3b", "attempts": attempts, "success_count": sum(x.get("status") == "esc_esc_menu" for x in attempts), "conclusion": "menu_reached" if any(x.get("status") == "esc_esc_menu" for x in attempts) else "blocked"}
    dump_json("rewind_probe_stage3b.json", value)
    return value


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("1", "2", "all"), default="all")
    parser.add_argument("--stage3", action="store_true")
    parser.add_argument("--stage3b", action="store_true")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    CAPTURE.write_text("cbc rewind PTY probe capture\n", encoding="utf-8")
    commands = resolve_commands()
    dump_json("rewind_probe_commands.json", commands)
    result: dict[str, Any] = {"commands": commands}
    if args.stage in ("1", "all"):
        result["stage1"] = stage1(commands, args.timeout)
    if args.stage in ("2", "all"):
        result["stage2"] = stage2(commands, args.timeout)
    if args.stage3:
        result["stage3"] = stage3(commands, args.timeout, max(1, min(args.repeat, 3)))
    if args.stage3b:
        result["stage3b"] = stage3b(commands, args.timeout, max(1, min(args.repeat, 3)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

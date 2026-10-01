"""Profile the end-to-end cbc PTY rewind interaction.

The probe uses a disposable session prepared headlessly, then measures only
the PTY resume/rewind path. All detailed evidence is UTF-8 JSON; stdout is a
single short summary to avoid Windows console encoding failures.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rewind_pty_probe import PtySession, resolve_commands

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)


def save(name: str, value: dict[str, Any]) -> None:
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def headless_prepare(cbc: list[str], work: Path, sid: str, target: Path, marker: str) -> dict[str, Any]:
    prompt = (
        f"Use the native Write or Edit file tool, not Bash or shell, to create exactly one file at {target} "
        f"with exactly this content: {marker}. Do not modify other files. Then confirm."
    )
    argv = cbc + ["-p", "--session-id", sid, "--permission-mode", "bypassPermissions", "--output-format", "json", prompt]
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False)
        return {"exit_code": p.returncode, "seconds": round(time.monotonic() - t0, 3), "file_exists": target.exists(), "file_content": target.read_text(encoding="utf-8", errors="replace") if target.exists() else None}
    except subprocess.TimeoutExpired:
        return {"error": "headless timeout", "seconds": round(time.monotonic() - t0, 3), "file_exists": target.exists()}


def wait_screen(session: PtySession, deadline: float, predicate: Callable[[str], bool], poll: float = 0.05) -> tuple[bool, str, float]:
    """Drain PTY output and return on a semantic screen predicate."""
    last = session.text()
    while time.monotonic() < deadline:
        chunk_deadline = min(deadline, time.monotonic() + poll)
        while time.monotonic() < chunk_deadline:
            try:
                chunk = session.queue.get(timeout=0.02)
            except Exception:
                break
            if chunk:
                session.stream.feed(chunk)
                last = session.text()
                if predicate(last):
                    return True, last, time.monotonic()
            elif not session.proc.isalive():
                return predicate(last), last, time.monotonic()
    return predicate(last), last, time.monotonic()


def run_once(run_id: int, timeout: float = 45.0) -> dict[str, Any]:
    commands = resolve_commands()
    cbc = commands.get("shim")
    if not cbc:
        return {"run": run_id, "status": "blocked", "error": "cbc unavailable"}
    work = Path(tempfile.mkdtemp(prefix=f"pan-rewind-timing-{run_id}-", dir=str(ROOT)))
    target = work / "rewind_timing_probe.txt"
    marker = f"PAN_REWIND_TIMING_{run_id}"
    sid = f"pan_rewind_timing_{int(time.time())}_{run_id}"
    result: dict[str, Any] = {"run": run_id, "session_id": sid, "workdir": str(work), "target": str(target), "marker": marker, "events": {}, "status": "started"}
    session: PtySession | None = None
    t0 = time.monotonic()
    try:
        result["prepare"] = headless_prepare(cbc, work, sid, target, marker)
        if not result["prepare"].get("file_exists"):
            result["status"] = "prepare_failed"
            return result
        spawn_t = time.monotonic()
        session = PtySession(cbc + ["-r", sid, "--permission-mode", "bypassPermissions"], str(work))
        result["events"]["spawn_start"] = spawn_t - t0
        ok, screen, ts = wait_screen(session, spawn_t + timeout, lambda s: "CodeBuddy Code" in s)
        result["events"]["tui_title"] = ts - t0
        result["checks"] = {"title_seen": ok, "title_digest": digest(screen)}
        if not ok:
            result["status"] = "tui_title_timeout"
            return result
        ok, screen, ts = wait_screen(session, spawn_t + timeout, lambda s: "\n>" in s or s.rstrip().endswith(">"))
        result["events"]["input_ready"] = ts - t0
        result["checks"]["input_ready"] = ok
        if not ok:
            result["status"] = "input_ready_timeout"
            return result
        session.send("\x1b")
        time.sleep(0.2)
        session.send("\x1b")
        result["events"]["esc_esc_sent"] = time.monotonic() - t0
        ok, screen, ts = wait_screen(session, time.monotonic() + timeout, lambda s: "Restore and fork the conversation" in s)
        result["events"]["rewind_menu"] = ts - t0
        result["checks"]["rewind_menu"] = ok
        if not ok:
            result["status"] = "rewind_menu_timeout"
            return result
        session.send("\x1b[A")
        result["events"]["arrow_up_sent"] = time.monotonic() - t0
        ok, screen, ts = wait_screen(session, time.monotonic() + 3.0, lambda s: "❯" in s and "rewind_timing_probe.txt" in s)
        result["events"]["checkpoint_selected"] = ts - t0
        result["checks"]["checkpoint_selected"] = ok
        session.send("\r")
        result["events"]["checkpoint_enter_sent"] = time.monotonic() - t0
        ok, screen, ts = wait_screen(session, time.monotonic() + timeout, lambda s: "Restore code and conversation" in s and "Never Mind" in s)
        result["events"]["confirm_page"] = ts - t0
        result["checks"]["confirm_page"] = ok
        if not ok:
            result["status"] = "confirm_page_timeout"
            return result
        session.send("\r")
        result["events"]["restore_enter_sent"] = time.monotonic() - t0
        ok, screen, ts = wait_screen(session, time.monotonic() + timeout, lambda s: not target.exists() and ("CodeBuddy Code" in s or "bypass permissions" in s))
        result["events"]["restore_complete"] = ts - t0
        result["checks"]["restore_complete"] = ok
        result["file_exists_after"] = target.exists()
        result["file_content_after"] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        result["final_screen_digest"] = digest(screen)
        result["status"] = "success" if ok and not target.exists() else "restore_not_confirmed"
    except Exception as exc:
        result["status"] = "exception"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if session is not None:
            t_cleanup = time.monotonic()
            result["cleanup"] = session.close()
            result["events"]["pty_reclaimed"] = time.monotonic() - t0
            result["events"]["cleanup_duration"] = time.monotonic() - t_cleanup
        result["total_seconds"] = time.monotonic() - t0
        shutil.rmtree(work, ignore_errors=True)
        save(f"rewind_timing_run{run_id}.json", result)
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    runs = [run_once(i) for i in range(1, max(1, min(args.repeat, 3)) + 1)]
    summaries = []
    for result in runs:
        events = result.get("events", {})
        summaries.append({"run": result.get("run"), "status": result.get("status"), "total": round(result.get("total_seconds", 0), 2), "events": {k: round(v, 2) for k, v in events.items() if isinstance(v, (int, float))}})
    save("rewind_timing_baseline.json", {"runs": summaries})
    print(f"rewind timing runs={len(runs)} success={sum(x['status'] == 'success' for x in summaries)} totals={[x['total'] for x in summaries]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

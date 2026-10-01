"""One-shot evidence probe for cbc rewind's fork semantics."""

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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rewind_pty_probe import PtySession, resolve_commands

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def project_slug(work: Path) -> str:
    # cbc's Windows project directory convention observed in prior probes.
    return str(work).replace("C:", "c-").replace("D:", "d-").replace("E:", "e-").replace("\\", "-").replace("/", "-")


def locate_project(work: Path, session_id: str) -> Path | None:
    root = Path.home() / ".codebuddy" / "projects"
    exact = project_slug(work)
    candidate = root / exact
    if candidate.is_dir() and any(candidate.glob(f"{session_id}.jsonl")):
        return candidate
    matches = list(root.rglob(f"{session_id}.jsonl")) if root.is_dir() else []
    return matches[0].parent if matches else None


def file_snapshot(project: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {"project": str(project) if project else None, "files": {}}
    if not project or not project.is_dir():
        return result
    for path in sorted(project.glob("*.jsonl")):
        try:
            data = path.read_bytes()
            result["files"][str(path)] = {
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "sha1": hashlib.sha1(data).hexdigest(),
                "lines": data.count(b"\n"),
            }
        except OSError as exc:
            result["files"][str(path)] = {"error": repr(exc)}
    return result


def session_ids(project: Path | None) -> list[str]:
    if not project or not project.is_dir():
        return []
    return sorted(path.stem for path in project.glob("*.jsonl"))


def prepare(cbc: list[str], work: Path, sid: str, target: Path, sentinel: str) -> dict[str, Any]:
    prompt = (
        f"Use the native Write or Edit file tool, not Bash or shell, to create exactly one file at {target} "
        f"with exactly this content: {sentinel}. Do not modify other files. Then confirm."
    )
    argv = cbc + ["-p", "--session-id", sid, "--permission-mode", "bypassPermissions", "--output-format", "json", prompt]
    started = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False)
        return {
            "argv": argv,
            "exit_code": p.returncode,
            "seconds": round(time.monotonic() - started, 2),
            "stdout_tail": p.stdout[-4000:],
            "stderr_tail": p.stderr[-2000:],
            "file_exists": target.exists(),
            "file_content": target.read_text(encoding="utf-8", errors="replace") if target.exists() else None,
        }
    except subprocess.TimeoutExpired as exc:
        return {"argv": argv, "error": "timeout", "seconds": round(time.monotonic() - started, 2), "stdout_tail": str(exc.stdout or "")[-4000:], "stderr_tail": str(exc.stderr or "")[-2000:]}


def resume_original(cbc: list[str], work: Path, sid: str) -> dict[str, Any]:
    argv = cbc + ["-r", sid, "-p", "--permission-mode", "bypassPermissions", "--output-format", "json", "Say hi and include the exact word PAN_REWIND_FORK_SIDE_TEST in your response."]
    try:
        p = subprocess.run(argv, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False)
        return {"argv": argv, "exit_code": p.returncode, "stdout_tail": p.stdout[-6000:], "stderr_tail": p.stderr[-2000:], "contains_original_sentinel": "PAN_REWIND_FORK_SEMANTICS" in p.stdout}
    except subprocess.TimeoutExpired as exc:
        return {"argv": argv, "error": "timeout", "stdout_tail": str(exc.stdout or "")[-6000:], "stderr_tail": str(exc.stderr or "")[-2000:]}


def main() -> int:
    commands = resolve_commands()
    cbc = commands.get("shim")
    if not cbc:
        write_json(EVIDENCE / "rewind_probe_fork_result.json", {"conclusion": "blocked", "error": "cbc shim unavailable"})
        print("fork probe blocked: cbc unavailable")
        return 2

    work = Path(tempfile.mkdtemp(prefix="pan-rewind-fork-", dir=str(ROOT)))
    sid = f"pan_rewind_fork_{int(time.time())}"
    target = work / "rewind_fork_probe.txt"
    sentinel = "PAN_REWIND_FORK_SEMANTICS"
    result: dict[str, Any] = {"session_a": sid, "workdir": str(work), "target": str(target), "sentinel": sentinel, "keypresses": ["Esc", "wait 200ms", "Esc", "ArrowUp", "Enter (select checkpoint)", "Enter (option 1)"]}
    pty: PtySession | None = None
    try:
        result["prepare"] = prepare(cbc, work, sid, target, sentinel)
        project = locate_project(work, sid)
        result["project_before"] = str(project) if project else None
        result["files_before"] = file_snapshot(project)
        result["session_ids_before"] = session_ids(project)
        if not result["prepare"].get("file_exists") or not project:
            result["conclusion"] = "blocked: preparation or transcript project missing"
            write_json(EVIDENCE / "rewind_probe_fork_result.json", result)
            print("fork probe blocked: headless preparation/transcript missing")
            return 1

        pty = PtySession(cbc + ["-r", sid, "--permission-mode", "bypassPermissions"], str(work))
        initial = pty.drain_until(time.monotonic() + 20, ("CodeBuddy Code", "❯"), min_seconds=2)
        result["initial_screen_digest"] = hashlib.sha1(initial.encode("utf-8", errors="replace")).hexdigest()[:16]
        if "do you trust the files" in initial.lower():
            result["conclusion"] = "blocked: trust dialog"
            write_json(EVIDENCE / "rewind_probe_fork_result.json", result)
            print("fork probe blocked: trust dialog")
            return 1
        pty.send("\x1b")
        time.sleep(0.2)
        pty.send("\x1b")
        menu = pty.drain_until(time.monotonic() + 12)
        result["screen_menu"] = menu
        pty.send("\x1b[A")
        checkpoint = pty.drain_until(time.monotonic() + 3)
        result["screen_checkpoint"] = checkpoint
        pty.send("\r")
        scope = pty.drain_until(time.monotonic() + 5)
        result["screen_scope"] = scope
        if "restore code and conversation" not in scope.lower():
            result["conclusion"] = "blocked: combined restore option missing"
            write_json(EVIDENCE / "rewind_probe_fork_result.json", result)
            print("fork probe blocked: combined restore option missing")
            return 1
        pty.send("\r")
        final = pty.drain_until(time.monotonic() + 15)
        result["screen_final"] = final
        time.sleep(1)
        result["file_exists_after"] = target.exists()
        result["file_content_after"] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        result["files_after_live"] = file_snapshot(project)
        result["session_ids_after_live"] = session_ids(project)
    finally:
        if pty is not None:
            result["pty_cleanup"] = pty.close()
        result["files_after_exit"] = file_snapshot(locate_project(work, sid))
        result["session_ids_after_exit"] = session_ids(locate_project(work, sid))
        result["resume_original"] = resume_original(cbc, work, sid)
        result["files_after_resume"] = file_snapshot(locate_project(work, sid))
        before = result.get("files_before", {}).get("files", {})
        after = result.get("files_after_exit", {}).get("files", {})
        result["original_file_comparisons"] = {
            path: {"same_sha1": before.get(path, {}).get("sha1") == meta.get("sha1"), "before": before.get(path), "after": meta}
            for path, meta in after.items() if Path(path).stem == sid
        }
        result["new_session_files"] = sorted(set(after) - set(before))
        result["original_session_unchanged"] = all(v.get("same_sha1") for v in result["original_file_comparisons"].values()) and bool(result["original_file_comparisons"])
        result["new_session_created"] = bool(result["new_session_files"])
        result["conclusion"] = "forked" if result["original_session_unchanged"] and result["new_session_created"] else "not proven"
        result["screen_fork_hint"] = any(token in result.get("screen_final", "").lower() for token in ("fork", "forked", "session"))
        result["cleanup_workdir"] = str(work)
        write_json(EVIDENCE / "rewind_probe_fork_result.json", result)
        (EVIDENCE / "rewind_probe_fork_screen.txt").write_text(
            "MENU\n" + result.get("screen_menu", "") + "\n\nCHECKPOINT\n" + result.get("screen_checkpoint", "") + "\n\nSCOPE\n" + result.get("screen_scope", "") + "\n\nFINAL\n" + result.get("screen_final", ""),
            encoding="utf-8",
        )
        shutil.rmtree(work, ignore_errors=True)

    print(f"fork probe complete: {result.get('conclusion')} original_unchanged={result.get('original_session_unchanged')} new_session={result.get('new_session_created')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

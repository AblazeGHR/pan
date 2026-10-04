"""Bounded file-level verification of cbc --fork-session."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)


def snap(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    st = path.stat()
    return {"path": str(path), "sha1": hashlib.sha1(data).hexdigest(), "lines": data.count(b"\n"), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def json_session_ids(text: str) -> list[str]:
    ids = []
    for match in re.finditer(r'"(?:session_id|sessionId)"\s*:\s*"([A-Za-z0-9_-]+)"', text):
        if match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def find_project(session_id: str) -> Path | None:
    root = Path.home() / ".codebuddy" / "projects"
    hits = list(root.rglob(f"{session_id}.jsonl")) if root.is_dir() else []
    return hits[0].parent if hits else None


def run(argv: list[str], cwd: Path, timeout: int = 180) -> dict[str, object]:
    started = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
        return {"argv": argv, "exit_code": p.returncode, "seconds": round(time.monotonic() - started, 2), "stdout": p.stdout[-12000:], "stderr": p.stderr[-4000:]}
    except subprocess.TimeoutExpired as exc:
        return {"argv": argv, "error": "timeout", "seconds": round(time.monotonic() - started, 2), "stdout": str(exc.stdout or "")[-12000:], "stderr": str(exc.stderr or "")[-4000:]}


def main() -> int:
    cbc = shutil.which("cbc")
    if not cbc:
        print("fork verification blocked: cbc unavailable")
        return 2
    work = Path(tempfile.mkdtemp(prefix="pan-cbc-fork-", dir=str(ROOT)))
    sid_a = f"pan_fork_verify_{int(time.time())}"
    marker = "PAN_FORK_VERIFY_MARKER"
    result: dict[str, object] = {"workdir": str(work), "session_a": sid_a, "marker": marker, "attempts": []}
    try:
        create = run([cbc, "-p", "--session-id", sid_a, "--permission-mode", "bypassPermissions", "--output-format", "json", f"Reply with exactly {marker}."], work)
        result["create"] = create
        project = find_project(sid_a)
        result["project"] = str(project) if project else None
        original = project / f"{sid_a}.jsonl" if project else None
        if not original or not original.exists():
            result["conclusion"] = "blocked: session A transcript missing"
            (EVIDENCE / "fork_session_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print("fork verification blocked: transcript missing")
            return 1
        before = snap(original)
        result["original_before"] = before

        commands = [
            [cbc, "-p", "--resume", sid_a, "--fork-session", "--permission-mode", "bypassPermissions", "--output-format", "json", "Reply with exactly PAN_FORK_VERIFY_FORKED."],
            [cbc, "-p", "--fork-session", "--resume", sid_a, "--permission-mode", "bypassPermissions", "--output-format", "json", "Reply with exactly PAN_FORK_VERIFY_FORKED."],
            [cbc, "--resume", sid_a, "--fork-session", "-p", "--permission-mode", "bypassPermissions", "--output-format", "json", "Reply with exactly PAN_FORK_VERIFY_FORKED."],
        ]
        for argv in commands:
            attempt = run(argv, work)
            attempt["session_ids_in_output"] = json_session_ids(str(attempt.get("stdout", "")))
            result["attempts"].append(attempt)
            time.sleep(0.5)
            files = sorted(project.glob("*.jsonl"))
            attempt["files_after"] = [snap(p) for p in files]
            attempt["new_files"] = [s for s in attempt["files_after"] if s["path"] != str(original)]
            if attempt.get("new_files"):
                break
        after = snap(original)
        result["original_after"] = after
        result["original_unchanged"] = before["sha1"] == after["sha1"] and before["lines"] == after["lines"]
        all_files = [snap(p) for p in sorted(project.glob("*.jsonl"))]
        result["all_files_final"] = all_files
        result["new_session_files"] = [s for s in all_files if s["path"] != str(original)]
        result["new_session_ids"] = [Path(s["path"]).stem for s in result["new_session_files"]]
        result["new_history_complete"] = bool(result["new_session_files"]) and all(s["lines"] == before["lines"] for s in result["new_session_files"])
        result["new_history_complete"] = False
        result["new_history_prefix_copy"] = False
        for item in result["new_session_files"]:
            new_path = Path(str(item["path"]))
            try:
                old_lines = original.read_text(encoding="utf-8").splitlines()
                new_lines = new_path.read_text(encoding="utf-8").splitlines()
                if len(new_lines) >= len(old_lines) and new_lines[:len(old_lines)] == old_lines:
                    result["new_history_prefix_copy"] = True
            except OSError:
                pass
        result["new_history_complete"] = result["new_history_prefix_copy"]
        result["conclusion"] = "成立" if result["original_unchanged"] and result["new_history_complete"] else "不成立"
        (EVIDENCE / "fork_session_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"fork verification: {result['conclusion']} original_unchanged={result['original_unchanged']} new_ids={result['new_session_ids']}")
        return 0 if result["conclusion"] == "成立" else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

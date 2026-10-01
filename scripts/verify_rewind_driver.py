"""Bounded end-to-end verification for the CBC rewind driver.

The script is disposable: it creates a temporary workdir, a cbc session A,
forks it to B, rewinds B through ConPTY, and writes UTF-8 evidence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

from packages.core.rewind import AnchorSpec, fork_session, rewind_in_pty


def snapshot(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": str(path),
        "sha1": hashlib.sha1(data).hexdigest(),
        "lines": data.count(b"\n"),
        "size": len(data),
    }


def find_transcript(session_id: str) -> Path | None:
    root = Path.home() / ".codebuddy" / "projects"
    hits = list(root.rglob(f"{session_id}.jsonl")) if root.is_dir() else []
    return hits[0] if hits else None


def main() -> int:
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="pan-rewind-e2e-", dir=str(ROOT)))
    target = work / "rewind_driver_probe.txt"
    sid_a = f"pan_rewind_driver_{int(time.time())}"
    marker = "PAN_REWIND_DRIVER_MARKER"
    prompt = (
        f"Use your native Write or Edit file tool, never Bash or shell, to create "
        f"exactly one file at {target} with exactly this content: {marker}. "
        "Then confirm the file was created."
    )
    evidence: dict[str, object] = {
        "workdir": str(work), "session_a": sid_a, "target": str(target),
        "marker": marker, "prompt": prompt,
    }
    try:
        cbc = shutil.which("cbc")
        if not cbc:
            evidence["status"] = "blocked"
            evidence["error"] = "cbc unavailable"
            return 2
        create = subprocess.run(
            [cbc, "-p", "--session-id", sid_a, "--permission-mode",
             "bypassPermissions", "--output-format", "json", prompt],
            cwd=str(work), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180, check=False,
        )
        evidence["create"] = {
            "exit_code": create.returncode,
            "stdout_tail": create.stdout[-4000:],
            "stderr_tail": create.stderr[-2000:],
        }
        evidence["file_after_create"] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        transcript_a = find_transcript(sid_a)
        evidence["transcript_a"] = str(transcript_a) if transcript_a else None
        if not target.exists() or not transcript_a:
            evidence["status"] = "prepare_failed"
            return 1
        transcript_text = transcript_a.read_text(encoding="utf-8", errors="replace")
        evidence["file_tool_seen"] = any(token in transcript_text for token in ("FileChange", "Write", "file_path"))
        before_a = snapshot(transcript_a)
        evidence["transcript_a_before"] = before_a
        fork = fork_session(sid_a, work)
        evidence["fork"] = fork.__dict__
        transcript_b = Path(fork.transcript_path) if fork.transcript_path else (find_transcript(fork.new_session_id or "") if fork.new_session_id else None)
        if not fork.new_session_id or not transcript_b or not fork.original_unchanged:
            evidence["status"] = "fork_failed"
            return 1
        before_b = snapshot(transcript_b)
        evidence["transcript_b_before"] = before_b
        stage_events: list[dict[str, object]] = []

        def on_stage(stage: str, details: dict[str, object]) -> None:
            stage_events.append({"stage": stage, "details": dict(details), "time": round(time.monotonic() - started, 3)})

        result = rewind_in_pty(
            fork.new_session_id, work, AnchorSpec(message_text=marker),
            expected_files={target: None}, timeout=35.0, on_stage=on_stage,
        )
        evidence["stages"] = stage_events
        evidence["rewind"] = result.__dict__
        evidence["file_after_rewind"] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        evidence["transcript_a_after"] = snapshot(transcript_a)
        evidence["transcript_b_after"] = snapshot(transcript_b) if transcript_b.exists() else None
        evidence["assertions"] = {
            "file_rolled_back": not target.exists(),
            "parent_unchanged": snapshot(transcript_a)["sha1"] == before_a["sha1"] and snapshot(transcript_a)["lines"] == before_a["lines"],
            "child_history_changed": bool(transcript_b.exists() and snapshot(transcript_b)["lines"] < before_b["lines"]),
            "pty_reclaimed": bool(result.cleanup),
        }
        evidence["status"] = "success" if result.success and all(evidence["assertions"].values()) else "failed"
        return 0 if evidence["status"] == "success" else 1
    except subprocess.TimeoutExpired:
        evidence["status"] = "timeout"
        evidence["error"] = "bounded subprocess timeout"
        return 1
    except Exception as exc:
        evidence["status"] = "exception"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (EVIDENCE / "rewind_driver_e2e.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        shutil.rmtree(work, ignore_errors=True)
        print(f"rewind driver e2e status={evidence.get('status')} elapsed={evidence['elapsed_seconds']}")


if __name__ == "__main__":
    raise SystemExit(main())

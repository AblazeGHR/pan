"""组合 r2 审查独立复跑器：13 项组合套件（直连 + uv 各一次），-o addopts= -q 单 q。

UTF-8 .txt（stdout/stderr 分流）+ summary.json（rc/耗时/汇总行/PASSED 计数）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REVIEW = HERE.parent.parent
REPO_ROOT = HERE
while not (REPO_ROOT / ".git").exists():
    REPO_ROOT = REPO_ROOT.parent

COMP_TESTS = "tests/test_terminal_composition.py"
UV_BASE = [
    "uv", "run", "--no-project", "--python", sys.executable,
    "--with-requirements", "minimal-requirements.txt",
    "--with", "pytest", "--with", "pytest-timeout",
]
TIMEOUT = 1800.0


def _run(argv: list[str], *, env: dict) -> dict:
    started = time.monotonic()
    completed = subprocess.run(
        argv, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=TIMEOUT,
    )
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _save(name: str, result: dict) -> dict:
    logs = REVIEW / "logs"
    (logs / f"{name}.out.txt").write_text(result["stdout"], encoding="utf-8")
    (logs / f"{name}.err.txt").write_text(result["stderr"], encoding="utf-8")
    summary_line = None
    for line in reversed(result["stdout"].splitlines()):
        if re.search(r"\d+ passed in", line):
            summary_line = line.strip()
            break
    return {
        "returncode": result["returncode"],
        "seconds": result["seconds"],
        "passed_lines": result["stdout"].count("PASSED "),
        "failed_lines": result["stdout"].count("FAILED "),
        "summary_line": summary_line,
        "stderr_len": len(result["stderr"]),
    }


def main() -> int:
    logs = REVIEW / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR"] = str(REVIEW / "evidence" / "direct13")
    summary: dict = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "executable": sys.executable,
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True
        ).stdout.strip(),
        "steps": {},
    }

    r = _run([sys.executable, "-m", "pytest", COMP_TESTS, "-o", "addopts=", "-q", "-rA"], env=env)
    summary["steps"]["direct13"] = _save("direct13", r)
    print("direct13", summary["steps"]["direct13"], flush=True)

    env_uv = dict(env)
    env_uv["PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR"] = str(REVIEW / "evidence" / "uv13")
    r = _run(
        UV_BASE + ["--", "python", "-m", "pytest", COMP_TESTS, "-o", "addopts=", "-q", "-rA"],
        env=env_uv,
    )
    summary["steps"]["uv13"] = _save("uv13", r)
    print("uv13", summary["steps"]["uv13"], flush=True)

    (logs / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    ok = all(step["returncode"] == 0 for step in summary["steps"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

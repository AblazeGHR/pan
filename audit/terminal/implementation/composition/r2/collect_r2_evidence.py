"""组合 r2 证据收集器（修正后真实验收：13 项直连 + uv 各一次）。

用法（树根）::

    E:/software/miniforge/python.exe audit/terminal/implementation/composition/r2/collect_r2_evidence.py

产物（本目录，UTF-8）：

- ``pytest_direct.txt`` / ``evidence_direct/``：直连 13 项 + 每用例 JSON；
- ``pytest_uv.txt`` / ``evidence_uv/``：uv 隔离同套件；
- ``uv_env.json`` / ``sidecar_pins.json`` / ``source_blobs.json`` / ``summary.json``（含残留扫描）。

按 MA 口径：只跑本组合套件；不跑旧 38/47/50 全量、11 首轮、48 emulator、core/全库/浏览器/长稳。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
R2_DIR = HERE.parent
AUDIT_DIR = R2_DIR.parent.parent
REPO_ROOT = AUDIT_DIR
while not (REPO_ROOT / ".git").exists():
    REPO_ROOT = REPO_ROOT.parent
SIDECAR_DIR = REPO_ROOT / "packages/core/terminal/emulator_sidecar"
COMPOSITION_TESTS = "tests/test_terminal_composition.py"
ANCHOR_FILES = [
    "tests/test_terminal_composition.py",
    "docs/design/PAN_TERMINAL_COMPOSITION_REVIEW_20261003_ROUND2.md",
    "audit/terminal/implementation/composition/r2/README.md",
]
UV_BASE = [
    "uv", "run", "--no-project", "--python", sys.executable,
    "--with-requirements", "minimal-requirements.txt",
    "--with", "pytest", "--with", "pytest-timeout",
]


def _run(argv: list[str], *, timeout: float, env: dict | None = None) -> dict:
    started = time.monotonic()
    completed = subprocess.run(
        argv, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=timeout
    )
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30
    ).stdout.strip()


def _scan_leftovers() -> dict:
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
        "'*packages.core.terminal*' -or $_.CommandLine -like '*emulator_sidecar*' } |"
        " Select-Object ProcessId,Name | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": type(exc).__name__}
    if completed.returncode != 0:
        return {"available": False, "returncode": completed.returncode}
    text = completed.stdout.strip()
    if not text:
        return {"available": True, "python_runners": 0, "node_sidecars": 0, "processes": []}
    parsed = json.loads(text)
    processes = parsed if isinstance(parsed, list) else [parsed]
    interesting = [
        item for item in processes
        if isinstance(item, dict) and (
            "python" in str(item.get("Name", "")).lower()
            or "node" in str(item.get("Name", "")).lower()
        )
    ]
    return {
        "available": True,
        "python_runners": sum(1 for p in interesting if "python" in str(p.get("Name", "")).lower()),
        "node_sidecars": sum(1 for p in interesting if "node" in str(p.get("Name", "")).lower()),
        "processes": interesting,
    }


def _aggregate_cleanup(evidence_dir: Path) -> dict:
    summary = {"sessions_with_cleanup": 0, "alive_before_cleanup": 0, "forced_terminations": 0}
    for path in sorted(evidence_dir.glob("composition_cleanup*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for trace in payload.values():
            if not isinstance(trace, dict):
                continue
            summary["sessions_with_cleanup"] += 1
            if trace.get("alive_before_cleanup"):
                summary["alive_before_cleanup"] += 1
            for key in ("runner", "sidecar", "shell"):
                item = trace.get(key)
                if isinstance(item, dict) and item.get("terminated") is True:
                    summary["forced_terminations"] += 1
    return summary


def _sidecar_pins() -> dict:
    package_json = SIDECAR_DIR / "package.json"
    lock = SIDECAR_DIR / "package-lock.json"
    payload = {
        "sidecar_dir": str(SIDECAR_DIR.relative_to(REPO_ROOT)),
        "node_modules_present": (SIDECAR_DIR / "node_modules/@xterm/headless/package.json").is_file(),
        "package_json_sha256": hashlib.sha256(package_json.read_bytes()).hexdigest(),
        "package_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
    }
    for dep in ("@xterm/headless", "@xterm/addon-serialize"):
        dep_pkg = SIDECAR_DIR / "node_modules" / dep / "package.json"
        payload[f"installed_{dep}"] = (
            json.loads(dep_pkg.read_text(encoding="utf-8")).get("version") if dep_pkg.is_file() else None
        )
    try:
        payload["node_version"] = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=20
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        payload["node_version"] = f"error:{type(exc).__name__}"
    return payload


def _staged_blobs() -> dict:
    ls = subprocess.run(
        ["git", "ls-files", "-s", *ANCHOR_FILES],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    blobs: dict[str, Any] = {}
    for line in ls.stdout.strip().splitlines():
        meta, path = line.split("\t", 1)
        parts = meta.split()
        blobs[path] = {"blob": parts[1], "mode": parts[0]}
    return blobs


def main() -> int:
    (R2_DIR / "evidence").mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "executable": sys.executable,
        "git_head": _git("rev-parse", "HEAD"),
        "steps": {},
    }

    env = dict(os.environ)
    env["PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR"] = str(R2_DIR / "evidence" / "direct")
    direct = _run([sys.executable, "-m", "pytest", COMPOSITION_TESTS, "-q", "-rA"], timeout=3600, env=env)
    (R2_DIR / "pytest_direct.txt").write_text(
        direct["stdout"] + ("\n[stderr]\n" + direct["stderr"] if direct["stderr"] else ""),
        encoding="utf-8",
    )
    summary["steps"]["pytest_direct"] = {
        "returncode": direct["returncode"],
        "seconds": direct["seconds"],
        "argv": direct["argv"],
        "tail": "\n".join(direct["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(R2_DIR / "evidence" / "direct"),
    }

    env_uv = dict(os.environ)
    env_uv["PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR"] = str(R2_DIR / "evidence" / "uv")
    uv_run = _run(
        UV_BASE + ["--", "python", "-m", "pytest", COMPOSITION_TESTS, "-q", "-rA"],
        timeout=3600, env=env_uv,
    )
    (R2_DIR / "pytest_uv.txt").write_text(
        uv_run["stdout"] + ("\n[stderr]\n" + uv_run["stderr"] if uv_run["stderr"] else ""),
        encoding="utf-8",
    )
    summary["steps"]["pytest_uv"] = {
        "returncode": uv_run["returncode"],
        "seconds": uv_run["seconds"],
        "argv": uv_run["argv"],
        "tail": "\n".join(uv_run["stdout"].strip().splitlines()[-2:]),
    }

    uv_version = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    (R2_DIR / "uv_env.json").write_text(
        json.dumps(
            {
                "uv_version": uv_version,
                "pytest_direct_argv": direct["argv"],
                "pytest_uv_argv": uv_run["argv"],
                "note": "修正后真实组合 13 项；不跑旧 38/47/50 全量、11 首轮、48 emulator、core/全库",
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (R2_DIR / "sidecar_pins.json").write_text(
        json.dumps(_sidecar_pins(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (R2_DIR / "source_blobs.json").write_text(
        json.dumps(
            {
                "parent_head": summary["git_head"],
                "anchor_kind": "git staged blob id（与本次提交内容逐字节一致；staged 前为旧提交 blob）",
                "blobs": _staged_blobs(),
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    summary["leftover_processes"] = _scan_leftovers()
    (R2_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if direct["returncode"] == 0 and uv_run["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

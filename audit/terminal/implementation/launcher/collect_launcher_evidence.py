"""Pan Terminal launcher 证据收集器。

跑：
- launcher 套件（tests/test_terminal_launcher.py）直连与 uv 隔离各一次；
- 13 组合相邻回归（tests/test_terminal_composition.py）直连与 uv 各一次。

产物（UTF-8 .txt 日志 + JSON）：pytest_launcher_{direct,uv}.txt、
pytest_composition_{direct,uv}.txt、evidence_launcher_{direct,uv}/、
evidence_composition_{direct,uv}/、summary.json、uv_env.json、pins.json。

纪律：只跑本文件列出的套件（不跑 50/48/core/全库/10 轮）；不触碰既有服务；
只读进程扫描（不杀进程）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / "audit/terminal/implementation/launcher"
SIDECAR_DIR = ROOT / "packages/core/terminal/emulator_sidecar"

LAUNCHER_TESTS = "tests/test_terminal_launcher.py"
COMPOSITION_TESTS = "tests/test_terminal_composition.py"

UV_BASE = [
    "uv", "run", "--no-project", "--python", sys.executable,
    "--with-requirements", "minimal-requirements.txt",
    "--with", "pytest", "--with", "pytest-timeout",
]


def _run(argv: list[str], *, env_extra: dict[str, str], timeout: float) -> dict:
    env = dict(os.environ)
    env.update(env_extra)
    started = time.monotonic()
    completed = subprocess.run(
        argv, cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    seconds = round(time.monotonic() - started, 3)
    return {
        "argv": argv,
        "returncode": int(completed.returncode),
        "seconds": seconds,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _write_log(name: str, run: dict) -> None:
    text = run["stdout"]
    if run["stderr"]:
        text += "\n[stderr]\n" + run["stderr"]
    (BASE / name).write_text(text, encoding="utf-8")


def _aggregate_cleanup(directory: Path) -> dict:
    sessions = 0
    alive_before = 0
    nonzero_exits: list[dict] = []
    leftovers: list[dict] = []
    if not directory.is_dir():
        return {"sessions": 0}
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or "alive_before_cleanup" not in data:
            continue
        sessions += 1
        if data.get("alive_before_cleanup"):
            alive_before += 1
        exit_code = data.get("exit_code")
        if exit_code not in (0, None):
            nonzero_exits.append({"file": path.name, "exit_code": exit_code})
        if data.get("sidecar_leftover") or data.get("shell_alive"):
            leftovers.append({"file": path.name,
                              "sidecar_leftover": data.get("sidecar_leftover"),
                              "shell_alive": data.get("shell_alive")})
    return {
        "sessions": sessions,
        "alive_before_cleanup": alive_before,
        "nonzero_exit_sessions": nonzero_exits,
        "resource_leftovers": leftovers,
    }


def _scan_leftovers() -> dict:
    try:
        import psutil
    except ImportError:
        return {"available": False}
    python_leftovers: list[int] = []
    node_leftovers: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(str(part) for part in (proc.info.get("cmdline") or []))
            if "python" in name and (
                "packages.core.terminal.launcher" in cmdline
                or "packages.core.terminal.runner" in cmdline
            ):
                python_leftovers.append(int(proc.info["pid"]))
            if "node" in name and "emulator_sidecar" in cmdline:
                node_leftovers.append(int(proc.info["pid"]))
        except Exception:  # noqa: BLE001 - 扫描尽力而为
            continue
    return {
        "available": True,
        "python_runner_leftovers": python_leftovers,
        "node_sidecar_leftovers": node_leftovers,
    }


def _pins() -> dict:
    def _node_version() -> str:
        try:
            return subprocess.run(
                ["node", "--version"], capture_output=True, text=True, timeout=30
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            return "unknown"

    def _pkg_version(rel: str) -> str:
        path = SIDECAR_DIR / rel
        try:
            return str(json.loads(path.read_text(encoding="utf-8")).get("version"))
        except Exception:  # noqa: BLE001
            return "unknown"

    import hashlib

    lock = SIDECAR_DIR / "package-lock.json"
    return {
        "node": _node_version(),
        "xterm_headless": _pkg_version("node_modules/@xterm/headless/package.json"),
        "xterm_addon_serialize": _pkg_version(
            "node_modules/@xterm/addon-serialize/package.json"
        ),
        "package_lock_sha256": (
            hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None
        ),
        "note": "sidecar 依赖仅在专属目录 npm ci（exact pin）；总依赖/锁未改",
    }


def main() -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True
        ).stdout.strip(),
        "steps": {},
        "pins": _pins(),
    }

    # ① launcher 套件：直连
    launcher_direct = _run(
        [sys.executable, "-m", "pytest", LAUNCHER_TESTS, "-q", "-rA"],
        env_extra={"PAN_TERMINAL_LAUNCHER_EVIDENCE_DIR": str(BASE / "evidence_launcher_direct")},
        timeout=1800,
    )
    _write_log("pytest_launcher_direct.txt", launcher_direct)
    summary["steps"]["launcher_direct"] = {
        "returncode": launcher_direct["returncode"],
        "seconds": launcher_direct["seconds"],
        "tail": "\n".join(launcher_direct["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(BASE / "evidence_launcher_direct"),
    }

    # ② launcher 套件：uv 隔离
    launcher_uv = _run(
        UV_BASE + ["--", "python", "-m", "pytest", LAUNCHER_TESTS, "-q", "-rA"],
        env_extra={"PAN_TERMINAL_LAUNCHER_EVIDENCE_DIR": str(BASE / "evidence_launcher_uv")},
        timeout=1800,
    )
    _write_log("pytest_launcher_uv.txt", launcher_uv)
    summary["steps"]["launcher_uv"] = {
        "returncode": launcher_uv["returncode"],
        "seconds": launcher_uv["seconds"],
        "tail": "\n".join(launcher_uv["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(BASE / "evidence_launcher_uv"),
    }

    # ③ 13 组合相邻回归：直连
    composition_direct = _run(
        [sys.executable, "-m", "pytest", COMPOSITION_TESTS, "-q"],
        env_extra={
            "PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR": str(BASE / "evidence_composition_direct")
        },
        timeout=1800,
    )
    _write_log("pytest_composition_direct.txt", composition_direct)
    summary["steps"]["composition_direct"] = {
        "returncode": composition_direct["returncode"],
        "seconds": composition_direct["seconds"],
        "tail": "\n".join(composition_direct["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(BASE / "evidence_composition_direct"),
    }

    # ④ 13 组合相邻回归：uv
    composition_uv = _run(
        UV_BASE + ["--", "python", "-m", "pytest", COMPOSITION_TESTS, "-q"],
        env_extra={
            "PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR": str(BASE / "evidence_composition_uv")
        },
        timeout=1800,
    )
    _write_log("pytest_composition_uv.txt", composition_uv)
    summary["steps"]["composition_uv"] = {
        "returncode": composition_uv["returncode"],
        "seconds": composition_uv["seconds"],
        "tail": "\n".join(composition_uv["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(BASE / "evidence_composition_uv"),
    }

    summary["leftover_processes"] = _scan_leftovers()
    (BASE / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    uv_version = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True, timeout=60
    ).stdout.strip()
    (BASE / "uv_env.json").write_text(
        json.dumps(
            {
                "uv_version": uv_version,
                "launcher_direct_argv": [sys.executable, "-m", "pytest", LAUNCHER_TESTS, "-q", "-rA"],
                "launcher_uv_argv": UV_BASE + ["--", "python", "-m", "pytest", LAUNCHER_TESTS, "-q", "-rA"],
                "composition_direct_argv": [sys.executable, "-m", "pytest", COMPOSITION_TESTS, "-q"],
                "composition_uv_argv": UV_BASE + ["--", "python", "-m", "pytest", COMPOSITION_TESTS, "-q"],
                "note": "argv 记录；日志见 pytest_*.txt",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:3500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

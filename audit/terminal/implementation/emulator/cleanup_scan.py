"""资源清理核验：确认 P1 emulator 测试/脚本未留下 sidecar/临时资源。

用法（从仓库根）：

    E:/software/miniforge/python.exe audit/terminal/implementation/emulator/cleanup_scan.py

只读扫描：枚举 node.exe 进程与命令行、枚举 %TEMP% 下 pan/emulator 相关目录、
枚举当前工作区内的残留文件。不杀任何进程、不清理任何既有资源。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _run_ps(command: str) -> str:
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", full],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return (proc.stdout or "").strip()


def _ps_json(command: str) -> list[dict]:
    raw = _run_ps(command)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [{"parse_error": raw[:400]}]
    if isinstance(data, dict):
        return [data]
    return list(data)


def node_processes() -> list[dict]:
    return _ps_json(
        "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )


def sidecar_like_processes() -> list[dict]:
    hits = []
    for item in node_processes():
        cmdline = str(item.get("CommandLine") or "")
        if "sidecar.mjs" in cmdline and "emulator_sidecar" in cmdline:
            hits.append(item)
    return hits


def python_emulator_processes() -> list[dict]:
    items = _ps_json(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    hits = []
    for item in items:
        cmdline = str(item.get("CommandLine") or "")
        if "test_terminal_emulator" in cmdline or "repro_ma_gaps" in cmdline:
            hits.append(item)
    return hits


def temp_leftovers() -> list[str]:
    temp = Path(tempfile.gettempdir())
    leftovers = []
    if temp.is_dir():
        for entry in temp.iterdir():
            name = entry.name.lower()
            if name.startswith(("pan-emulator", "pan-terminal-emulator", "pan_terminal_emulator")):
                leftovers.append(str(entry))
    return leftovers


def workspace_leftovers() -> list[str]:
    keep = {"node_modules", "__pycache__"}
    hits = []
    sidecar_dir = REPO_ROOT / "packages" / "core" / "terminal" / "emulator_sidecar"
    if sidecar_dir.is_dir():
        for entry in sidecar_dir.iterdir():
            if entry.name in keep:
                continue
            hits.append(str(entry.relative_to(REPO_ROOT)))
    return hits


def main() -> int:
    payload = {
        "generated_at": None,
        "node_processes": node_processes(),
        "sidecar_like_processes": sidecar_like_processes(),
        "python_emulator_processes": python_emulator_processes(),
        "temp_leftovers": temp_leftovers(),
        "emulator_sidecar_dir_entries": workspace_leftovers(),
    }
    import time

    payload["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    clean = (
        not payload["sidecar_like_processes"]
        and not payload["python_emulator_processes"]
        and not payload["temp_leftovers"]
    )
    payload["clean"] = clean
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())

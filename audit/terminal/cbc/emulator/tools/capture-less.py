#!/usr/bin/env python
"""Capture a real `less.exe` TUI byte stream through an own ConPTY.

Bounded, isolated, self-owned:
  * runs only a freshly created `less` process on a generated file in a temp dir;
  * captures raw bytes to ../evidence/captures/less-80x24.bin;
  * identity evidence: PID + raw creation FILETIME read from a handle;
  * cleanup via the retained handle + WaitForSingleObject(0) verification;
    never taskkill, never touches other processes;
  * no model calls, no auth, no existing services.

Run (uv with pywinpty+psutil, same discipline as the CBC probes):
  UV_CACHE_DIR=D:/tmp/uv-cache-pan-cbc uv run --no-project \
    --python E:/software/miniforge/python.exe \
    --with pywinpty==3.0.5 --with psutil \
    python audit/terminal/cbc/emulator/tools/capture-less.py
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
CAPTURES = HERE.parent / "evidence" / "captures"
LESS = Path(r"E:/Git/usr/bin/less.exe")
ROWS, COLS = 24, 80
CAPTURE_SECONDS = 3.0

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def raw_filetime(pid: int) -> int | None:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME(); exit_t = wintypes.FILETIME()
        k = wintypes.FILETIME(); u = wintypes.FILETIME()
        if not _kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t),
                                         ctypes.byref(k), ctypes.byref(u)):
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def wait_signaled(pid: int, timeout_ms: int = 5000) -> bool:
    """Same-handle wait: True when the process object is signaled (exited)."""
    handle = _kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return _kernel32.WaitForSingleObject(handle, timeout_ms) == _WAIT_OBJECT_0
    finally:
        _kernel32.CloseHandle(handle)


def main() -> int:
    from winpty import PtyProcess

    CAPTURES.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pan-less-capture-"))
    meta: dict = {"less_path": str(LESS), "rows": ROWS, "cols": COLS,
                  "capture_seconds": CAPTURE_SECONDS, "tmp": str(tmp)}
    proc = None
    try:
        content = tmp / "content.txt"
        lines = []
        for i in range(1, 400):
            lines.append(f"line {i:03d}  " + ("word%d " % (i % 7)) * (i % 13)
                         + ("END-COLOR" if i % 11 == 0 else ""))
        content.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        meta["content_bytes"] = content.stat().st_size

        proc = PtyProcess.spawn(
            [str(LESS), "-R", str(content)], cwd=str(tmp), dimensions=(ROWS, COLS),
            env={**os.environ, "TERM": "xterm-256color", "LESSCHARSET": "utf-8"})
        pid = proc.pid
        meta["pid"] = pid
        ft = raw_filetime(pid)
        meta["filetime"] = ft
        # 64-bit FILETIME exceeds JS Number.MAX_SAFE_INTEGER (2^53): transport it
        # as a string/hex so consumers cannot silently round the identity.
        meta["filetime_str"] = str(ft)
        meta["filetime_hex"] = format(ft, "016x") if ft is not None else None
        meta["create_time"] = psutil.Process(pid).create_time()

        chunks: list[bytes] = []
        stop = threading.Event()

        def reader() -> None:
            # pywinpty read() blocks; run it on a thread so the keystroke
            # schedule below is never blocked by an idle terminal.
            while not stop.is_set():
                try:
                    data = proc.read(4096)
                except (EOFError, OSError):
                    break
                except Exception:
                    break
                if not data:
                    break
                chunks.append(data if isinstance(data, bytes)
                              else data.encode("utf-8", "replace"))

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        schedule = [(0.6, "\r"), (0.4, "G"), (0.6, "g"), (0.5, "/word3\r"), (0.7, "q")]
        for wait_s, keys in schedule:
            time.sleep(wait_s)
            try:
                proc.write(keys)
            except Exception:
                break
        # bounded window for the last output, then a hard watchdog
        time.sleep(0.4)
        watchdog_forced = False
        if proc.isalive():
            proc.terminate(force=True)
            watchdog_forced = True
        stop.set()
        t.join(timeout=1.0)
        meta["watchdog_forced_kill"] = watchdog_forced

        raw = b"".join(chunks)
        out = CAPTURES / "less-80x24.bin"
        out.write_bytes(raw)
        meta["bytes_captured"] = len(raw)
        meta["contains_alt_screen"] = b"\x1b[?1049" in raw
        meta["contains_final_leave_alt"] = b"\x1b[?1049l" in raw
        meta["chunk_count"] = len(chunks)
        meta["output_space_used"] = sum(
            1 for b in raw if b >= 0x20)
        exited_naturally = not proc.isalive()
        meta["exited_naturally_after_q"] = exited_naturally
        if not exited_naturally:
            proc.terminate(force=True)
        time.sleep(0.3)
        meta["signaled_after_terminate"] = wait_signaled(pid, 3000)
        meta["alive_after"] = bool(psutil.pid_exists(pid))
        meta["out_file"] = str(out)
    finally:
        if proc is not None and proc.isalive():
            try:
                proc.terminate(force=True)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        meta["tmp_removed"] = not tmp.exists()
        (CAPTURES / "less-80x24.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: meta.get(k) for k in
                      ("pid", "filetime", "bytes_captured", "contains_alt_screen",
                       "exited_naturally_after_q", "signaled_after_terminate",
                       "alive_after", "tmp_removed")}, ensure_ascii=False))
    return 0 if meta.get("bytes_captured") else 1


if __name__ == "__main__":
    raise SystemExit(main())

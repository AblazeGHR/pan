#!/usr/bin/env python
"""Bounded feasibility probes for CBC (CodeBuddy CLI) terminal/attach/server capabilities.

Scope (see docs/design/PAN_TERMINAL_PTY_EXPLORATION_BRIEF_20261003.md):
  * never touches existing Pan/CLI sessions, workers, user config or auth;
  * every probe process is created by this script in a temp cwd + isolated
    CODEBUDDY_CONFIG_DIR, and is killed before the script returns;
  * model calls: none in `baseline`/`headless`/`bg`/`serve`/`acp` modes.
    (`bg-model` mode performs one tiny prompt and is opt-in.)

Modes:
  baseline   record installed version/help + empty registry/ps output
  headless   run the exact Pan stream-json argv; probe registry + native attach
  bg         run `--bg --exec` shell job; probe ps/logs/attach PID continuity
  bg-model   run `--bg` model job (tiny prompt) to observe the auth boundary
  serve      run `cbc --serve` on a self-chosen free loopback port; probe HTTP
             API (/health,/info,/workers,/sessions,/jobs,/pty) + SSE + Web UI
  acp        run `cbc --acp` (stdio) and record the initialize handshake

Run (from the worktree root):
  UV_CACHE_DIR=D:/tmp/uv-cache-pan-cbc uv run --no-project \
    --python E:/software/miniforge/python.exe \
    --with pywinpty==3.0.5 --with psutil \
    python audit/terminal/cbc/probe_cbc_feasibility.py <mode>
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
EVIDENCE_ROOT = HERE / "evidence"
CBC_ENTRY = Path(r"D:/node_npm/node_global/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy")
NODE = "node"
MODEL = "deepseek-v4.1-flash"
RUN_ID = datetime.now().strftime("%Y%m%d-%H%M%S")
RUN_START = time.time()
EVIDENCE = EVIDENCE_ROOT / f"{RUN_ID}"

# ---------------------------------------------------------------- utilities


def log(msg: str) -> None:
    print(f"[probe] {msg}", flush=True)


def iso_env(cfg_dir: Path, extra: dict | None = None) -> dict:
    """Environment with all inherited CODEBUDDY_*/WORKBUDDY_* entries removed.

    The probe must not inherit the orchestrating session's identity
    (CODEBUDDY_SESSION_ID, CODEBUDDY_MCP_CONFIG, service proxy, ...).
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("CODEBUDDY_", "WORKBUDDY_"))
    }
    env["CODEBUDDY_CONFIG_DIR"] = str(cfg_dir)
    env["DISABLE_TELEMETRY"] = "1"
    env["NO_COLOR"] = "1"
    if extra:
        env.update(extra)
    return env


def run_cli(args: list[str], env: dict, cwd: Path, timeout: float = 60.0) -> dict:
    t0 = time.time()
    try:
        cp = subprocess.run(
            [NODE, str(CBC_ENTRY), *args],
            env=env, cwd=str(cwd), capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return {
            "argv": ["node", str(CBC_ENTRY), *args],
            "returncode": cp.returncode,
            "stdout": cp.stdout,
            "stderr": cp.stderr,
            "seconds": round(time.time() - t0, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": ["node", str(CBC_ENTRY), *args],
            "returncode": None,
            "stdout": (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            "stderr": (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            "timeout": True,
            "seconds": round(time.time() - t0, 3),
        }


def spawn_cli(args: list[str], env: dict, cwd: Path, tag: str) -> dict:
    # NOTE: *.log is gitignored in this repo, so evidence uses .stdout.txt/.stderr.txt
    out_path = EVIDENCE / f"{tag}.stdout.txt"
    err_path = EVIDENCE / f"{tag}.stderr.txt"
    out_f = open(out_path, "w", encoding="utf-8")
    err_f = open(err_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [NODE, str(CBC_ENTRY), *args],
        env=env, cwd=str(cwd), stdin=subprocess.PIPE,
        stdout=out_f, stderr=err_f, text=True, encoding="utf-8", errors="replace",
    )
    return {"proc": proc, "stdout_path": out_path, "stderr_path": err_path,
            "argv": ["node", str(CBC_ENTRY), *args],
            "pid": proc.pid, "create_time": psutil.Process(proc.pid).create_time(),
            "filetime": (raw_identity(proc.pid) or {}).get("filetime")}


def proc_identity(pid: int) -> dict | None:
    try:
        p = psutil.Process(pid)
        return {
            "pid": pid,
            "create_time": p.create_time(),
            "filetime": (raw_identity(pid) or {}).get("filetime"),
            "name": p.name(),
            "cmdline": p.cmdline(),
            "cwd": p.cwd(),
            "alive": p.is_running(),
        }
    except psutil.Error:
        return None


# --------------------------------------------------------------------------
# Safety-reviewed process cleanup (probe scope only).
#
# A bare PID is not an identity: after the process exits the PID can be reused
# by an unrelated process. Every termination here therefore:
#   1. stores the RAW creation FILETIME (integer, GetProcessTimes units) when
#      the process is spawned / first collected — never a float round-trip;
#   2. at stop time opens ONE handle with
#      QUERY_LIMITED_INFORMATION|TERMINATE|SYNCHRONIZE;
#   3. asks that same handle for its process id and creation FILETIME and
#      requires an EXACT integer match (no tolerance) against the recorded raw
#      value; if the raw identity was never recorded, it refuses;
#   4. terminates through the verified handle and waits on the same handle
#      (handles refer to the process object, so PID reuse cannot redirect).
# Subtree handling additionally requires the descendant relationship at scan
# time and that the process command line or cwd references this run's isolated
# temp root; anything else is skipped and recorded as evidence.
#
# BOUNDARY (do not over-claim): this is cleanup for this probe's own processes,
# NOT a production tree guard. Residual races remain: (a) between psutil
# enumeration and OpenProcess the parent/child relation map can change, so a
# recorded "child" could have been reparented; the FILETIME equality check
# still prevents terminating a different process, but association cannot be
# re-verified afterwards; (b) no Windows Job Object is used here, so nothing
# re-parented out of the tree is re-collected by ancestry; (c) a refused or
# unverified entry is reported as residue and MUST NOT be counted as cleaned.
# --------------------------------------------------------------------------

import ctypes
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
_kernel32.GetProcessId.restype = wintypes.DWORD
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_FILETIME_UNIX_DELTA = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def _ft_value(ft) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _ft_to_unix(ft: int) -> float:
    """Display-only conversion; never used for identity comparison."""
    return ft / 10_000_000 - _FILETIME_UNIX_DELTA


def raw_identity(pid: int) -> dict | None:
    """Read the RAW creation FILETIME straight from a handle.

    Returns ``{"pid": int, "filetime": int}`` or ``None`` when the process
    cannot be opened/timed. Callers must store ``filetime`` verbatim; if it is
    missing, termination refuses (no fallback to wall-clock or psutil floats).
    """
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        if _kernel32.GetProcessId(handle) != pid:
            return None
        creation = wintypes.FILETIME(); exit_t = wintypes.FILETIME()
        ktime = wintypes.FILETIME(); utime = wintypes.FILETIME()
        if not _kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t),
                                         ctypes.byref(ktime), ctypes.byref(utime)):
            return None
        return {"pid": pid, "filetime": _ft_value(creation)}
    finally:
        _kernel32.CloseHandle(handle)


def protected_pids() -> set[int]:
    """This probe process plus every ancestor: never terminate these.

    A scope-marker scan can match the invoking shell (the marker often appears
    in its argv) or the harness above it; killing an ancestor would take down
    the probe itself or unrelated orchestration. Commands from other tools
    must never be able to terminate their own parent chain.
    """
    protected = {os.getpid()}
    try:
        for ancestor in psutil.Process(os.getpid()).parents():
            protected.add(ancestor.pid)
    except psutil.Error:
        pass
    return protected


def safe_stop(pid: int, expected_filetime: int | None, *, label: str = "",
              basis: str = "", popen: subprocess.Popen | None = None,
              wait_ms: int = 5000) -> dict:
    """Terminate *pid* only if the handle-confirmed RAW identity matches exactly.

    ``expected_filetime`` must be the raw FILETIME integer recorded at spawn or
    collection time. ``None`` or any mismatch refuses; an already exited
    process is only reported as not running, never terminated.
    """
    rec: dict = {"label": label, "pid": pid, "basis": basis,
                 "expected_filetime": expected_filetime,
                 "terminate_attempted": False}
    if pid in protected_pids():
        rec.update({"refused": True,
                    "reason": "self/ancestor protection: refusing to terminate this probe's own process chain"})
        return rec
    if expected_filetime is None:
        rec.update({"refused": True,
                    "reason": "no raw FILETIME identity recorded; refusing to terminate"})
        return rec
    if not psutil.pid_exists(pid):
        rec.update({"running": False, "gone": True,
                    "reason": "not running at stop time; no TerminateProcess attempted"})
        return rec

    handle = _kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
    if not handle:
        rec.update({"refused": True,
                    "reason": f"OpenProcess failed err={ctypes.get_last_error()}"})
        return rec
    try:
        handle_pid = _kernel32.GetProcessId(handle)
        creation = wintypes.FILETIME(); exit_t = wintypes.FILETIME()
        ktime = wintypes.FILETIME(); utime = wintypes.FILETIME()
        if not _kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t),
                                         ctypes.byref(ktime), ctypes.byref(utime)):
            rec.update({"refused": True,
                        "reason": f"GetProcessTimes failed err={ctypes.get_last_error()}"})
            return rec
        actual_ft = _ft_value(creation)
        rec["actual_filetime"] = actual_ft
        rec["handle_pid"] = handle_pid
        if handle_pid != pid:
            rec.update({"refused": True, "reason": "handle pid mismatch"})
            return rec
        if actual_ft != expected_filetime:
            rec.update({"refused": True,
                        "reason": "identity mismatch: raw creation FILETIME differs from the "
                                  "recorded identity (exact integer compare)"})
            return rec
        rec["identity_verified"] = True
        rec["terminate_attempted"] = True
        if not _kernel32.TerminateProcess(handle, 1):
            rec.update({"terminate_failed": True,
                        "reason": f"TerminateProcess err={ctypes.get_last_error()}"})
            return rec
        wait = _kernel32.WaitForSingleObject(handle, wait_ms)
        code = wintypes.DWORD()
        _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        rec.update({"terminated": True, "exit_code": code.value,
                    "handle_signaled": wait == _WAIT_OBJECT_0})
    finally:
        _kernel32.CloseHandle(handle)
    rec["gone"] = not psutil.pid_exists(pid)
    return rec


def _stop_outcome(rec: dict, pid: int) -> str:
    """Classify one stop record: 'gone' | 'refused' | 'residual'."""
    if rec.get("refused") or rec.get("terminate_failed"):
        return "refused"
    if rec.get("gone") or not psutil.pid_exists(pid):
        return "gone"
    return "residual"


def _scope_match(info: dict, scope_markers: list[str]) -> bool:
    blob = " ".join(info.get("cmdline") or []) + " " + str(info.get("cwd") or "")
    return any(m and m.lower() in blob.lower() for m in scope_markers)


def collect_own_descendants(root_pid: int, root_create_time: float,
                            scope_markers: list[str]) -> dict:
    """Enumerate descendants of *root_pid* that carry this run's scope marker.

    Each accepted entry stores the RAW creation FILETIME read at scan time;
    entries whose raw identity cannot be read are skipped (they would be
    unverifiable at stop time).
    """
    children: dict[int, list[int]] = {}
    infos: dict[int, dict] = {}
    protected = protected_pids()
    for p in psutil.process_iter(["pid", "ppid", "create_time", "cmdline", "cwd", "name"]):
        try:
            info = p.info
        except psutil.Error:
            continue
        infos[info["pid"]] = info
        children.setdefault(info["ppid"], []).append(info["pid"])
    owned: list[dict] = []
    skipped: list[dict] = []
    queue = list(children.get(root_pid, []))
    seen = set()
    while queue:
        pid = queue.pop()
        if pid in seen:
            continue
        seen.add(pid)
        info = infos.get(pid)
        if not info:
            skipped.append({"pid": pid, "reason": "process vanished during scan"})
            continue
        queue.extend(children.get(pid, []))
        if pid in protected:
            skipped.append({"pid": pid, "reason": "self/ancestor protected"})
            continue
        created = info.get("create_time") or 0.0
        if created and created < root_create_time - 1.0:
            skipped.append({"pid": pid, "reason": "created before root; refusing as foreign"})
            continue
        if not _scope_match(info, scope_markers):
            skipped.append({"pid": pid, "reason": "cmdline/cwd lacks this run's scope marker",
                            "cmdline": " ".join(info.get("cmdline") or [])[:200]})
            continue
        raw = raw_identity(pid)
        if not raw:
            skipped.append({"pid": pid, "reason": "raw FILETIME identity unreadable at scan"})
            continue
        owned.append({"pid": pid, "filetime": raw["filetime"],
                      "create_time": created,
                      "name": info.get("name"),
                      "cmdline": " ".join(info.get("cmdline") or [])[:200]})
    return {"owned": owned, "skipped": skipped}


def safe_stop_tree(root_pid: int, root_filetime: int | None, *, label: str = "",
                   scope_markers: list[str] | None = None,
                   popen: subprocess.Popen | None = None,
                   identity_overrides: dict[int, int] | None = None,
                   root_create_time: float | None = None) -> dict:
    """Stop this probe's own subtree: verified descendants first, then the root.

    ``identity_overrides`` exists solely for the cleanup self-test fault
    injection (a deliberately wrong raw FILETIME for one pid); no production
    call site passes it.

    ``all_gone`` is True only when every stop record is classified ``gone``;
    refused/unverified/residual entries keep ``all_gone`` False and are listed
    in ``refused_or_unverified`` / ``residual_pids``. This is probe-scope
    cleanup, not a production tree guard (see module note above the helpers).
    """
    markers = list(scope_markers or [])
    if root_create_time is None:
        ident = proc_identity(root_pid) or {}
        root_create_time = ident.get("create_time") or 0.0
    scan = collect_own_descendants(root_pid, root_create_time, markers)
    overrides = identity_overrides or {}
    stopped = []
    for child in sorted(scan["owned"], key=lambda c: c["create_time"], reverse=True):
        expected = overrides.get(child["pid"], child["filetime"])
        stopped.append(safe_stop(child["pid"], expected, label=f"{label}:child",
                                 basis="descendant of own root + scope marker + raw FILETIME at scan"))
    stopped.append(safe_stop(root_pid, root_filetime, label=label,
                             basis="own root, raw FILETIME recorded at spawn", popen=popen))
    outcomes = {s["pid"]: _stop_outcome(s, s["pid"]) for s in stopped}
    residual = [pid for pid, out in outcomes.items() if out != "gone"]
    refused = [s for s in stopped if outcomes[s["pid"]] == "refused"]
    return {"label": label, "root_pid": root_pid, "scan": scan, "stopped": stopped,
            "outcomes": outcomes,
            "residual_pids": residual,
            "refused_or_unverified": refused,
            "root_gone": outcomes.get(root_pid) == "gone",
            "all_gone": bool(outcomes) and all(out == "gone" for out in outcomes.values())
                        and not psutil.pid_exists(root_pid)}


def scan_own_leftovers(scope_markers: list[str], *, not_before: float) -> list[dict]:
    """Find (not kill) processes whose cmdline or cwd references this run's temp root."""
    found = []
    protected = protected_pids()
    for p in psutil.process_iter(["pid", "create_time", "cmdline", "name"]):
        try:
            cmdline = " ".join(p.info["cmdline"] or [])
            cwd = p.cwd()
        except psutil.Error:
            continue
        if p.info["pid"] in protected:
            continue
        blob = f"{cmdline} {cwd}"
        if any(m and m.lower() in blob.lower() for m in scope_markers):
            created = p.info.get("create_time") or 0.0
            raw = raw_identity(p.info["pid"])
            found.append({"pid": p.info["pid"], "name": p.info["name"],
                          "create_time": created,
                          "filetime": (raw or {}).get("filetime"),
                          "cmdline": cmdline[:300],
                          "cwd": cwd,
                          "created_during_run": bool(created >= not_before - 1.0)})
    return found


def safe_stop_leftovers(scope_markers: list[str], *, not_before: float,
                        tree_scope_markers: list[str] | None = None,
                        label: str = "leftover") -> dict:
    """Defensive fallback path: scan then identity-verify each candidate.

    A candidate is terminated only if (a) its cmdline/cwd references this run's
    unique temp root, (b) it was created during this run, and (c) the raw
    FILETIME read at scan time equals the value read from the terminating
    handle. Created-before-run candidates, unreadable raw identities and
    identity mismatches are reported as residue, never as "gone" —
    ``all_gone`` is False whenever any candidate was refused.
    """
    candidates = scan_own_leftovers(scope_markers, not_before=not_before)
    results = []
    for cand in candidates:
        if not cand["created_during_run"]:
            results.append({"pid": cand["pid"], "refused": True,
                            "reason": "created before this run started",
                            "terminate_attempted": False})
            continue
        results.append(safe_stop_tree(
            cand["pid"], cand.get("filetime"), label=f"{label}:{cand['pid']}",
            scope_markers=tree_scope_markers or scope_markers))
    leftovers = [r for r in results if not r.get("all_gone")]
    return {"candidates": candidates, "results": results,
            "leftovers": leftovers,
            "all_gone": not leftovers}


def sessions_dir(env: dict) -> Path:
    return Path(env["CODEBUDDY_CONFIG_DIR"]) / "sessions"


def registry_snapshot(env: dict) -> list[dict]:
    out = []
    d = sessions_dir(env)
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:  # pragma: no cover - evidence only
                data = {"_parse_error": str(exc)}
            out.append({"file": f.name, "data": data})
    return out


def free_loopback_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _jsonable(obj):
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def save(name: str, payload) -> Path:
    p = EVIDENCE / name
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return p


def new_workdirs(tag: str) -> tuple[Path, Path, Path]:
    import tempfile
    root = Path(tempfile.mkdtemp(prefix=f"pan-cbc-probe-{tag}-"))
    cfg = root / "cfg"
    ws = root / "ws"
    cfg.mkdir()
    ws.mkdir()
    return root, cfg, ws


# ---------------------------------------------------------------- PTY attach


def attach_in_pty(args: list[str], env: dict, cwd: Path, seconds: float,
                  keys: list[tuple[float, str]] | None = None,
                  dimensions=(30, 100)) -> dict:
    """Spawn `cbc attach ...` inside a real ConPTY, capture the screen text."""
    from winpty import PtyProcess

    chunks: list[str] = []
    p = PtyProcess.spawn(
        [NODE, str(CBC_ENTRY), *args],
        cwd=str(cwd), env=env, dimensions=dimensions,
    )
    pid = p.pid
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                data = p.read(4096)
            except EOFError:
                break
            except Exception as exc:  # pragma: no cover - evidence only
                chunks.append(f"\n<read-error {exc!r}>\n")
                break
            if not data:
                break
            chunks.append(data)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    identity_before = proc_identity(pid)
    schedule = keys or []
    t0 = time.time()
    for at, data in schedule:
        while time.time() - t0 < at:
            time.sleep(0.1)
        try:
            p.write(data)
        except Exception as exc:  # pragma: no cover - evidence only
            chunks.append(f"\n<write-error {exc!r}>\n")
            break
    while time.time() - t0 < seconds:
        time.sleep(0.2)
    alive_at_end = p.isalive()
    identity_after = proc_identity(pid)
    text = "".join(chunks)
    exit_code = None
    if not p.isalive():
        try:
            exit_code = p.exitstatus
        except Exception:
            exit_code = None
    try:
        if p.isalive():
            p.terminate(force=True)
    except Exception:
        pass
    stop.set()
    return {
        "argv": ["node", str(CBC_ENTRY), *args],
        "pty_pid": pid,
        "pty_identity_before": identity_before,
        "pty_alive_at_end": alive_at_end,
        "pty_exitstatus": exit_code,
        "pty_identity_after": identity_after,
        "screen_text": text,
        "screen_len": len(text),
        "seconds": round(time.time() - t0, 3),
    }


# ---------------------------------------------------------------- modes


def mode_baseline() -> dict:
    root, cfg, ws = new_workdirs("baseline")
    env = iso_env(cfg)
    res: dict = {"mode": "baseline", "run_id": RUN_ID, "root": str(root),
                 "cbc_entry": str(CBC_ENTRY), "model": MODEL}
    res["version"] = run_cli(["--version"], env, ws, timeout=30)
    res["help"] = run_cli(["--help"], env, ws, timeout=30)
    res["ps_json_empty"] = run_cli(["ps", "--json"], env, ws, timeout=30)
    res["registry_empty"] = registry_snapshot(env)
    res["cfg_dir_after"] = sorted(str(p.relative_to(cfg)) for p in cfg.rglob("*"))
    save("baseline.json", res)
    return res


def mode_headless() -> dict:
    """Pan's exact stream-json argv: does the process register? is it attachable?"""
    root, cfg, ws = new_workdirs("headless")
    env = iso_env(cfg)
    res: dict = {"mode": "headless", "run_id": RUN_ID, "root": str(root),
                 "argv_note": "cbc -p --output-format stream-json --input-format stream-json -y --model <model>"}
    args = ["-p", "--output-format", "stream-json", "--input-format", "stream-json",
            "-y", "--model", MODEL]
    handle = spawn_cli(args, env, ws, "headless")
    res["spawn"] = {k: v for k, v in handle.items() if k != "proc"}
    try:
        time.sleep(6)
        proc = handle["proc"]
        res["alive_after_6s"] = proc.poll() is None
        res["identity_after_6s"] = proc_identity(handle["pid"])
        res["registry_during"] = registry_snapshot(env)
        res["sessions_dir_exists"] = sessions_dir(env).exists()
        res["ps_json_during"] = run_cli(["ps", "--json"], env, ws, timeout=30)
        # native attach attempt against the running print-mode process
        res["attach_headless_pid"] = attach_in_pty(
            ["attach", str(handle["pid"])], env, ws, seconds=6.0)
        res["registry_after_attach"] = registry_snapshot(env)
    finally:
        res["cleanup"] = safe_stop_tree(handle["pid"], handle["filetime"],
                                        label="headless", scope_markers=[str(root)],
                                        popen=handle["proc"])
    res["stderr_after"] = handle["stderr_path"].read_text(encoding="utf-8", errors="replace")[:8000]
    res["stdout_after"] = handle["stdout_path"].read_text(encoding="utf-8", errors="replace")[:8000]
    res["registry_after_exit"] = registry_snapshot(env)
    save("headless.json", res)
    return res


def parse_bg_launch(text: str) -> dict:
    """Parse `cbc --bg` launcher output: job short id, PID, log path."""
    import re
    out: dict = {}
    m = re.search(r"backgrounded\s+·\s+([0-9a-f]+)\s+·\s+(.*?)\s+\(", text)
    if m:
        out["short_id"] = m.group(1)
        out["display_name"] = m.group(2)
    m = re.search(r"PID:\s+(\d+)", text)
    if m:
        out["pid"] = int(m.group(1))
    m = re.search(r"Log:\s+(.+)", text)
    if m:
        out["log_path"] = m.group(1).strip()
    return out


def mode_bg() -> dict:
    """`--bg --exec` shell job: ps/logs/attach continuity + cleanup."""
    root, cfg, ws = new_workdirs("bg")
    env = iso_env(cfg)
    name = f"panprobe{RUN_ID.replace('-', '')}"
    res: dict = {"mode": "bg", "run_id": RUN_ID, "root": str(root), "job_name": name}

    cmd = 'ping -n 120 127.0.0.1'
    launch = run_cli(["--bg", "--name", name, "--exec", cmd], env, ws, timeout=60)
    res["launch"] = launch
    ids = parse_bg_launch(launch["stdout"])
    res["parsed"] = ids
    short = ids.get("short_id")
    bg_pid = ids.get("pid")
    res["bg_pid"] = bg_pid
    res["registry_after_launch"] = registry_snapshot(env)
    res["ps_json"] = run_cli(["ps", "--json"], env, ws, timeout=30)
    res["bg_identity_after_launch"] = proc_identity(bg_pid) if bg_pid else None

    if short:
        res["logs_by_shortid"] = run_cli(["logs", short], env, ws, timeout=30)
        res["attach_shell_job"] = attach_in_pty(["attach", short], env, ws, seconds=8.0,
                                                keys=[(3.0, " ")])
        res["attach_screen"] = res["attach_shell_job"]["screen_text"]
        res["bg_identity_after_attach"] = proc_identity(bg_pid) if bg_pid else None
        res["registry_after_attach"] = registry_snapshot(env)
        res["ps_json_after_attach"] = run_cli(["ps", "--json"], env, ws, timeout=30)
        res["stop"] = run_cli(["stop", short], env, ws, timeout=30)
        time.sleep(2)
        res["bg_identity_after_stop"] = proc_identity(bg_pid) if bg_pid else None
        res["ps_json_after_stop"] = run_cli(["ps", "--json"], env, ws, timeout=30)
        res["registry_after_stop"] = registry_snapshot(env)

    if bg_pid and psutil.pid_exists(bg_pid):
        ident = res.get("bg_identity_after_launch") or proc_identity(bg_pid)
        res["cleanup"] = safe_stop_tree(
            bg_pid, (ident or {}).get("filetime"),
            label="bg-job", scope_markers=[str(root)])
    else:
        res["cleanup"] = {"skipped": True, "reason": "job PID not alive at cleanup time"}
    if ids.get("log_path"):
        lp = Path(ids["log_path"])
        res["log_file_size"] = lp.stat().st_size if lp.exists() else None
        if lp.exists():
            res["log_file_head"] = lp.read_text(encoding="utf-8", errors="replace")[:2000]
    job_dir = cfg / "jobs"
    if job_dir.exists():
        res["job_store_files"] = sorted(str(p.relative_to(cfg)) for p in job_dir.rglob("*"))
        for f in job_dir.rglob("state.json"):
            try:
                res.setdefault("job_state", json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    save("bg.json", res)
    return res


def mode_bg_lifetime() -> dict:
    """Sample `--bg --exec` job liveness across the launcher's own exit."""
    root, cfg, ws = new_workdirs("bglife")
    env = iso_env(cfg)
    name = f"panlife{RUN_ID.replace('-', '')}"
    res: dict = {"mode": "bg-lifetime", "run_id": RUN_ID, "root": str(root), "job_name": name}
    out_path = EVIDENCE / "bg-lifetime.launcher.txt"
    proc = subprocess.Popen(
        [NODE, str(CBC_ENTRY), "--bg", "--name", name, "--exec", "ping -n 90 127.0.0.1"],
        env=env, cwd=str(ws), stdin=subprocess.DEVNULL,
        stdout=open(out_path, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    res["launcher_pid"] = proc.pid
    res["launcher_create_time"] = psutil.Process(proc.pid).create_time()
    timeline = []
    ids: dict = {}
    t0 = time.time()
    attach_done = False
    while time.time() - t0 < 60:
        launcher_alive = proc.poll() is None
        if not ids and out_path.exists():
            ids = parse_bg_launch(out_path.read_text(encoding="utf-8", errors="replace"))
            if ids.get("short_id"):
                res["parsed"] = ids
        pid = ids.get("pid")
        job_alive = bool(pid and psutil.pid_exists(pid))
        timeline.append({"t": round(time.time() - t0, 2), "launcher_alive": launcher_alive,
                         "job_alive": job_alive})
        if not launcher_alive and not attach_done and ids.get("short_id"):
            attach_done = True
            res["launcher_exit_rc"] = proc.returncode
            res["launcher_exit_t"] = round(time.time() - t0, 2)
            # logs/attach attempted only while we can still see the job
            if job_alive:
                res["logs_after_launcher_exit"] = run_cli(
                    ["logs", ids["short_id"]], env, ws, timeout=20)
                res["attach_after_launcher_exit"] = attach_in_pty(
                    ["attach", ids["short_id"]], env, ws, seconds=6.0)
                res["attach_screen"] = res["attach_after_launcher_exit"]["screen_text"]
        if not launcher_alive and not job_alive and len(timeline) > 4:
            break
        time.sleep(0.5)
    res["timeline"] = timeline
    res["job_pid"] = ids.get("pid")
    res["job_identity_at_end"] = proc_identity(ids["pid"]) if ids.get("pid") else None
    if ids.get("pid") and psutil.pid_exists(ids["pid"]):
        ident = res.get("job_identity_at_end") or proc_identity(ids["pid"])
        res["cleanup"] = safe_stop_tree(
            ids["pid"], (ident or {}).get("filetime"),
            label="bg-lifetime", scope_markers=[str(root)])
    res["launcher_identity_at_end"] = proc_identity(proc.pid)
    job_dir = cfg / "jobs"
    if job_dir.exists():
        for f in job_dir.rglob("state.json"):
            try:
                res.setdefault("job_state", json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    save("bg-lifetime.json", res)
    return res


def mode_bg_model() -> dict:
    """`--bg` model job with an isolated config dir: observes the auth boundary."""
    root, cfg, ws = new_workdirs("bgmodel")
    env = iso_env(cfg)
    name = f"panprobeM{RUN_ID.replace('-', '')}"
    res: dict = {"mode": "bg-model", "run_id": RUN_ID, "root": str(root), "job_name": name}
    launch = run_cli(["--bg", "--name", name, MODEL and "--model", MODEL,
                      "Reply with the single word: ok"], env, ws, timeout=90)
    res["launch"] = launch
    time.sleep(6)
    res["registry"] = registry_snapshot(env)
    res["ps_json"] = run_cli(["ps", "--json"], env, ws, timeout=30)
    res["logs"] = run_cli(["logs", name], env, ws, timeout=30)
    for entry in res["registry"]:
        pid = entry["data"].get("pid")
        if pid and psutil.pid_exists(pid):
            ident = proc_identity(pid) or {}
            res.setdefault("cleanup", []).append(safe_stop_tree(
                pid, ident.get("filetime"), label=f"bg-model-{pid}",
                scope_markers=[str(root)]))
    res["kill_rc"] = run_cli(["kill", name], env, ws, timeout=30)
    save("bg-model.json", res)
    return res


def mode_serve() -> dict:
    """`cbc --serve` on our own free loopback port: HTTP API + Web UI + PTY."""
    import urllib.error
    import urllib.request

    root, cfg, ws = new_workdirs("serve")
    env = iso_env(cfg)
    port = free_loopback_port()
    res: dict = {"mode": "serve", "run_id": RUN_ID, "root": str(root),
                 "port": port, "port_free_before": not port_open(port)}
    args = ["--serve", "--host", "127.0.0.1", "--port", str(port), "--auth", "none",
            "--model", MODEL, "--permission-mode", "bypassPermissions"]
    handle = spawn_cli(args, env, ws, "serve")
    res["spawn"] = {k: v for k, v in handle.items() if k != "proc"}
    res["pid_create_time"] = handle["create_time"]

    def http(method: str, path: str, body=None, timeout=15.0, raw=False):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"X-CodeBuddy-Request": "1", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                text = data.decode("utf-8", "replace")
                if raw or "json" not in ctype:
                    return {"status": resp.status, "content_type": ctype,
                            "body_head": text[:2000], "body_len": len(text)}
                try:
                    return {"status": resp.status, "content_type": ctype,
                            "json": json.loads(text)}
                except json.JSONDecodeError:
                    return {"status": resp.status, "content_type": ctype,
                            "body_head": text[:2000]}
        except urllib.error.HTTPError as exc:
            return {"status": exc.code, "error": exc.read().decode("utf-8", "replace")[:2000]}
        except Exception as exc:
            return {"error": repr(exc)}

    try:
        deadline = time.time() + 40
        health = None
        while time.time() < deadline:
            health = http("GET", "/api/v1/health", timeout=3)
            if health.get("status") == 200:
                break
            time.sleep(1)
        res["health"] = health
        res["alive_after_health"] = handle["proc"].poll() is None
        res["info"] = http("GET", "/api/v1/info")
        res["workers"] = http("GET", "/api/v1/workers")
        res["sessions"] = http("GET", "/api/v1/sessions")
        res["jobs"] = http("GET", "/api/v1/jobs")
        res["root_html"] = http("GET", "/", raw=True, timeout=10)
        openapi = http("GET", "/api/openapi.json", raw=True, timeout=20)
        res["openapi"] = {k: v for k, v in openapi.items() if k != "body_head"}
        if "body_head" in openapi:
            try:
                spec = json.loads(openapi["body_head"] if openapi["body_len"] <= 2000 else "")
            except Exception:
                spec = None
            res["openapi_note"] = "truncated in evidence; see openapi.json"
            # refetch fully for evidence
            try:
                with urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/openapi.json",
                        headers={"X-CodeBuddy-Request": "1"}), timeout=20) as r:
                    save("openapi.json", json.loads(r.read().decode("utf-8")))
            except Exception as exc:
                res["openapi_fetch_error"] = repr(exc)

        # --- PTY endpoint: create -> input -> SSE output -> resize -> delete
        pty = http("POST", "/api/v1/pty", {"cols": 120, "rows": 40})
        res["pty_create"] = pty
        sid = None
        if isinstance(pty.get("json"), dict):
            data = pty["json"].get("data") or {}
            sid = data.get("id") or data.get("sessionId") or data.get("ptyId")
        res["pty_session_id"] = sid
        if sid:
            marker = f"PAN-CBC-PROBE-{RUN_ID}"
            res["pty_input"] = http("POST", f"/api/v1/pty/{sid}/input/send",
                                    {"data": f"echo {marker}\r\n"})
            sse_chunks = []
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/v1/pty/{sid}/output",
                    headers={"X-CodeBuddy-Request": "1", "Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    t_end = time.time() + 6
                    while time.time() < t_end:
                        line = resp.readline()
                        if not line:
                            break
                        sse_chunks.append(line.decode("utf-8", "replace"))
                        if marker in "".join(sse_chunks):
                            break
            except Exception as exc:
                sse_chunks.append(f"<sse-error {exc!r}>")
            res["pty_sse_head"] = "".join(sse_chunks)[:4000]
            res["pty_sse_marker_found"] = marker in "".join(sse_chunks)
            res["pty_resize"] = http("POST", f"/api/v1/pty/{sid}/resize",
                                     {"cols": 200, "rows": 50})
            res["pty_list"] = http("GET", "/api/v1/pty")
            res["pty_delete"] = http("DELETE", f"/api/v1/pty/{sid}")
            res["pty_list_after_delete"] = http("GET", "/api/v1/pty")
        res["cfg_dir_after"] = sorted(str(p.relative_to(cfg)) for p in cfg.rglob("*"))[:80]
    finally:
        res["cleanup"] = safe_stop_tree(handle["pid"], handle["filetime"],
                                        label="serve", scope_markers=[str(root)],
                                        popen=handle["proc"])
        time.sleep(1)
        res["port_free_after"] = not port_open(port, timeout=1.0)
    save("serve.json", res)
    return res


def mode_acp() -> dict:
    """`cbc --acp` stdio: record the initialize handshake (model-free)."""
    root, cfg, ws = new_workdirs("acp")
    env = iso_env(cfg)
    res: dict = {"mode": "acp", "run_id": RUN_ID, "root": str(root)}
    proc = subprocess.Popen(
        [NODE, str(CBC_ENTRY), "--acp"],
        env=env, cwd=str(ws), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
    )
    res["pid"] = proc.pid
    res["create_time"] = psutil.Process(proc.pid).create_time()
    res["filetime"] = (raw_identity(proc.pid) or {}).get("filetime")
    out_lines: list[str] = []
    err_lines: list[str] = []

    def pump(stream, sink):
        try:
            for line in stream:
                sink.append(line.rstrip("\n"))
        except Exception:
            pass

    t_out = threading.Thread(target=pump, args=(proc.stdout, out_lines), daemon=True)
    t_err = threading.Thread(target=pump, args=(proc.stderr, err_lines), daemon=True)
    t_out.start(); t_err.start()
    req = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": 1,
                   "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True},
                                          "terminal": False}},
    }
    try:
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        time.sleep(8)
        res["alive_after_8s"] = proc.poll() is None
    finally:
        res["cleanup"] = safe_stop_tree(proc.pid, res["filetime"],
                                        label="acp", scope_markers=[str(root)],
                                        popen=proc)
    res["stdout_lines"] = out_lines[:40]
    res["stderr_lines"] = err_lines[:40]
    save("acp.json", res)
    return res


def mode_bg_observe() -> dict:
    """Poll the reported job PID directly while the launcher is alive/exiting."""
    root, cfg, ws = new_workdirs("bgobs")
    env = iso_env(cfg)
    marker = f"panobs{RUN_ID.replace('-', '')}"
    name = marker
    res: dict = {"mode": "bg-observe", "run_id": RUN_ID, "root": str(root),
                 "job_name": name, "marker": marker}
    out_path = EVIDENCE / "bg-observe.launcher.txt"
    proc = subprocess.Popen(
        [NODE, str(CBC_ENTRY), "--bg", "--name", name, "--exec", "ping -n 300 127.0.0.1"],
        env=env, cwd=str(ws), stdin=subprocess.DEVNULL,
        stdout=open(out_path, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    res["launcher_pid"] = proc.pid
    timeline = []
    ids: dict = {}
    seen: dict[int, dict] = {}
    t0 = time.time()
    while time.time() - t0 < 20:
        if not ids:
            ids = parse_bg_launch(out_path.read_text(encoding="utf-8", errors="replace"))
            if ids.get("pid"):
                res["parsed"] = ids
        pid = ids.get("pid")
        alive = bool(pid and psutil.pid_exists(pid))
        if alive:
            if pid not in seen:
                ident = proc_identity(pid)
                if ident:
                    seen[pid] = dict(ident, first_seen_t=round(time.time() - t0, 2))
            seen[pid]["last_seen_t"] = round(time.time() - t0, 2)
        timeline.append({"t": round(time.time() - t0, 2),
                         "launcher_alive": proc.poll() is None,
                         "job_pid": pid, "job_alive": alive})
        if not proc.poll() is None and not alive and ids:
            break
        time.sleep(0.2)
    res["timeline"] = timeline
    res["processes_seen"] = list(seen.values())
    res["launcher_exit_rc"] = proc.returncode
    res["launcher_alive_at_end"] = proc.poll() is None
    res["job_identity_at_end"] = proc_identity(ids["pid"]) if ids.get("pid") else None
    if ids.get("pid") and psutil.pid_exists(ids["pid"]):
        ident = res.get("job_identity_at_end") or proc_identity(ids["pid"]) or {}
        res["cleanup"] = safe_stop_tree(ids["pid"], ident.get("filetime"),
                                        label="bg-observe", scope_markers=[str(root)])
    log_path = ids.get("log_path")
    if log_path and Path(log_path).exists():
        res["log_size_at_end"] = Path(log_path).stat().st_size
        res["log_head_at_end"] = Path(log_path).read_text(
            encoding="utf-8", errors="replace")[:1000]
    save("bg-observe.json", res)
    return res


def mode_daemon() -> dict:
    """Daemon-owned shell job via HTTP API: runtime, SSE, stop, cleanup."""
    import urllib.error
    import urllib.request

    root, cfg, ws = new_workdirs("daemon")
    env = iso_env(cfg)
    port = free_loopback_port()
    res: dict = {"mode": "daemon", "run_id": RUN_ID, "root": str(root),
                 "port": port, "port_free_before": not port_open(port)}
    start = run_cli(["daemon", "start", "--port", str(port)], env, ws, timeout=90)
    res["start"] = start
    time.sleep(3)
    res["status"] = run_cli(["daemon", "status"], env, ws, timeout=30)
    res["ps_json"] = run_cli(["ps", "--json"], env, ws, timeout=30)
    res["registry_after_start"] = registry_snapshot(env)

    def http(method: str, path: str, body=None, timeout=15.0):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"X-CodeBuddy-Request": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {"status": resp.status,
                        "json": json.loads(resp.read().decode("utf-8", "replace"))}
        except urllib.error.HTTPError as exc:
            return {"status": exc.code,
                    "error": exc.read().decode("utf-8", "replace")[:1500]}
        except Exception as exc:
            return {"error": repr(exc)}

    try:
        deadline = time.time() + 40
        health = None
        while time.time() < deadline:
            health = http("GET", "/api/v1/health", timeout=3)
            if health.get("status") == 200:
                break
            time.sleep(1)
        res["health"] = health
        res["daemon_status_api"] = http("GET", "/api/v1/daemon/status")
        res["workers"] = http("GET", "/api/v1/workers")
        res["jobs_before"] = http("GET", "/api/v1/jobs")

        # daemon-owned shell job (bash=true -> no model call, no auth needed)
        disp = http("POST", "/api/v1/jobs", {
            "prompt": "ping -n 600 127.0.0.1",
            "bash": True,
            "name": f"panDaemonJob{RUN_ID.replace('-', '')}",
        })
        res["job_dispatch"] = disp
        jid = None
        if isinstance(disp.get("json"), dict):
            jid = (disp["json"].get("data") or {}).get("id")
        res["job_id"] = jid
        time.sleep(3)
        res["jobs_after_dispatch"] = http("GET", "/api/v1/jobs")
        res["job_detail"] = http("GET", f"/api/v1/jobs/{jid}") if jid else None
        job_pid = None
        detail = (res["job_detail"] or {}).get("json", {}).get("data", {}) if res["job_detail"] else {}
        job_pid = detail.get("pid") if isinstance(detail, dict) else None
        res["job_pid"] = job_pid
        res["job_identity"] = proc_identity(job_pid) if job_pid else None

        # SSE stream (bounded)
        sse = []
        if jid:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/v1/jobs/{jid}/stream",
                    headers={"X-CodeBuddy-Request": "1", "Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    t_end = time.time() + 6
                    while time.time() < t_end:
                        line = resp.readline()
                        if not line:
                            break
                        sse.append(line.decode("utf-8", "replace"))
            except Exception as exc:
                sse.append(f"<sse-error {exc!r}>")
        res["job_sse_head"] = "".join(sse)[:6000]
        res["job_sse_event_lines"] = len([l for l in sse if l.strip()])

        # native attach to the daemon-owned job through a real PTY
        if jid:
            res["attach_daemon_job"] = attach_in_pty(["attach", jid], env, ws,
                                                     seconds=8.0, keys=[(3.0, " ")])
            res["attach_screen"] = res["attach_daemon_job"]["screen_text"]
        res["job_identity_after_attach"] = proc_identity(job_pid) if job_pid else None

        if jid:
            res["job_stop"] = http("POST", f"/api/v1/jobs/{jid}/stop")
        time.sleep(2)
        res["job_identity_after_stop"] = proc_identity(job_pid) if job_pid else None
        res["jobs_after_stop"] = http("GET", "/api/v1/jobs?all=1")
    finally:
        res["stop_cli"] = run_cli(["daemon", "stop"], env, ws, timeout=60)
        time.sleep(3)
        res["port_free_after_stop"] = not port_open(port)
        res["status_after_stop"] = run_cli(["daemon", "status"], env, ws, timeout=30)
        res["ps_json_after_stop"] = run_cli(["ps", "--json"], env, ws, timeout=30)
        res["registry_after_stop"] = registry_snapshot(env)
        time.sleep(1)
        left = [
            {"pid": p.pid, "cmdline": " ".join(p.info["cmdline"] or [])[:300]}
            for p in psutil.process_iter(["pid", "cmdline"])
            if (p.info["cmdline"] or []) and str(cfg) in " ".join(p.info["cmdline"])
        ]
        res["processes_referencing_isolated_cfg"] = left
        res["cleanup"] = safe_stop_leftovers([str(root)], not_before=RUN_START,
                                             tree_scope_markers=[str(root)],
                                             label="daemon")
    save("daemon.json", res)
    return res


def mode_daemon_job() -> dict:
    """Daemon-owned shell job: real process identity, files, SSE, reply, cleanup."""
    import urllib.error
    import urllib.request

    root, cfg, ws = new_workdirs("daemonjob")
    env = iso_env(cfg)
    port = free_loopback_port()
    marker = f"PANMARK-{RUN_ID}"
    res: dict = {"mode": "daemon-job", "run_id": RUN_ID, "root": str(root),
                 "port": port, "marker": marker}

    def http(method: str, path: str, body=None, timeout=15.0):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"X-CodeBuddy-Request": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {"status": resp.status,
                        "json": json.loads(resp.read().decode("utf-8", "replace"))}
        except urllib.error.HTTPError as exc:
            return {"status": exc.code,
                    "error": exc.read().decode("utf-8", "replace")[:1500]}
        except Exception as exc:
            return {"error": repr(exc)}

    start = run_cli(["daemon", "start", "--port", str(port)], env, ws, timeout=90)
    res["daemon_start"] = start
    daemon_pid = None
    import re as _re
    m = _re.search(r"PID:\s*(\d+)", start["stdout"])
    if m:
        daemon_pid = int(m.group(1))
    res["daemon_pid"] = daemon_pid
    res["daemon_identity"] = proc_identity(daemon_pid) if daemon_pid else None
    try:
        deadline = time.time() + 40
        while time.time() < deadline and http("GET", "/api/v1/health", timeout=3).get("status") != 200:
            time.sleep(1)
        res["health"] = http("GET", "/api/v1/health")

        disp = http("POST", "/api/v1/jobs", {
            "prompt": f'cmd /c "echo {marker} > panmark.txt & ping -n 300 127.0.0.1"',
            "bash": True, "name": f"panDaemonJob{RUN_ID.replace('-', '')}",
        })
        res["dispatch"] = disp
        jid = None
        if isinstance(disp.get("json"), dict):
            jid = (disp["json"].get("data") or {}).get("id")
        res["job_id"] = jid

        samples = []
        job_pid = None
        t0 = time.time()
        for _ in range(30):
            detail = http("GET", f"/api/v1/jobs/{jid}") if jid else {"error": "no id"}
            data = ((detail.get("json") or {}).get("data") or {}).get("job") or {}
            if not job_pid:
                job_pid = data.get("pid")
            samples.append({
                "t": round(time.time() - t0, 2),
                "api_alive": data.get("alive"), "api_state": data.get("state"),
                "api_pid": data.get("pid"),
                "pid_exists": bool(job_pid and psutil.pid_exists(job_pid)),
                "marker_file": (ws / "panmark.txt").exists(),
                "exec_log_size": (Path(data["logPath"]).stat().st_size
                                  if data.get("logPath") and Path(data["logPath"]).exists() else None),
            })
            if (ws / "panmark.txt").exists() and len(samples) > 4:
                break
            time.sleep(1)
        res["job_samples"] = samples
        res["job_pid"] = job_pid
        res["job_identity"] = proc_identity(job_pid) if job_pid else None
        res["daemon_identity_after_job"] = proc_identity(daemon_pid) if daemon_pid else None
        if (ws / "panmark.txt").exists():
            res["marker_file_content"] = (ws / "panmark.txt").read_text(
                encoding="utf-8", errors="replace")[:200]

        if jid:
            res["logs_cli"] = run_cli(["logs", jid], env, ws, timeout=30)
            res["attach_pty"] = attach_in_pty(["attach", jid], env, ws, seconds=8.0,
                                              keys=[(3.0, " ")])
            res["attach_screen"] = res["attach_pty"]["screen_text"]
            sse = []
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/v1/jobs/{jid}/stream",
                    headers={"X-CodeBuddy-Request": "1", "Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    t_end = time.time() + 6
                    while time.time() < t_end:
                        line = resp.readline()
                        if not line:
                            break
                        sse.append(line.decode("utf-8", "replace"))
            except Exception as exc:
                sse.append(f"<sse-error {exc!r}>")
            res["stream_head"] = "".join(sse)[:4000]
            res["reply"] = http("POST", f"/api/v1/jobs/{jid}/reply",
                                {"text": f'cmd /c "echo replied > replied.txt"', "bash": True})
            time.sleep(3)
            res["replied_file"] = (ws / "replied.txt").exists()
            res["job_identity_after_reply"] = proc_identity(job_pid) if job_pid else None
            res["stop"] = http("POST", f"/api/v1/jobs/{jid}/stop")
            time.sleep(2)
            res["job_pid_exists_after_stop"] = bool(job_pid and psutil.pid_exists(job_pid))
            res["job_detail_after_stop"] = http("GET", f"/api/v1/jobs/{jid}?all=1")
    finally:
        res["daemon_stop_cli"] = run_cli(["daemon", "stop"], env, ws, timeout=60)
        time.sleep(3)
        res["port_free_after"] = not port_open(port)
        res["daemon_pid_exists_after_stop"] = bool(daemon_pid and psutil.pid_exists(daemon_pid))
        res["cleanup"] = safe_stop_leftovers([str(root)], not_before=RUN_START,
                                             tree_scope_markers=[str(root)],
                                             label="daemon-job")
        res["leftover_processes"] = res["cleanup"]["candidates"]
        res["ps_after"] = run_cli(["ps", "--json"], env, ws, timeout=30)
        res["registry_after"] = registry_snapshot(env)
    save("daemon-job.json", res)
    return res


def mode_acp_http() -> dict:
    """ACP over HTTP/SSE on a self-owned `--serve` process (structured bypass)."""
    import urllib.error
    import urllib.request

    root, cfg, ws = new_workdirs("acphttp")
    env = iso_env(cfg)
    port = free_loopback_port()
    res: dict = {"mode": "acp-http", "run_id": RUN_ID, "root": str(root), "port": port}
    handle = spawn_cli(["--serve", "--host", "127.0.0.1", "--port", str(port),
                        "--auth", "none"], env, ws, "acp-http")
    res["pid"] = handle["pid"]
    res["create_time"] = handle["create_time"]

    def http(method: str, path: str, body=None, timeout=15.0, headers=None):
        hdr = {"X-CodeBuddy-Request": "1", "Content-Type": "application/json"}
        if headers:
            hdr.update(headers)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {"status": resp.status,
                        "body": resp.read().decode("utf-8", "replace")[:3000]}
        except urllib.error.HTTPError as exc:
            return {"status": exc.code, "error": exc.read().decode("utf-8", "replace")[:1500]}
        except Exception as exc:
            return {"error": repr(exc)}

    def sse_request(path: str, body, headers=None, seconds=8.0):
        hdr = {"X-CodeBuddy-Request": "1", "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
        if headers:
            hdr.update(headers)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method="POST",
            data=json.dumps(body).encode("utf-8"), headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=seconds + 4) as resp:
                lines = []
                t_end = time.time() + seconds
                while time.time() < t_end:
                    line = resp.readline()
                    if not line:
                        break
                    lines.append(line.decode("utf-8", "replace"))
                return {"status": resp.status, "lines": lines}
        except urllib.error.HTTPError as exc:
            return {"status": exc.code, "error": exc.read().decode("utf-8", "replace")[:1500]}
        except Exception as exc:
            return {"error": repr(exc)}

    try:
        deadline = time.time() + 40
        while time.time() < deadline and http("GET", "/api/v1/health", timeout=3).get("status") != 200:
            time.sleep(1)
        res["health"] = http("GET", "/api/v1/health")
        res["web_ui_served"] = http("GET", "/")["status"] if "status" in http("GET", "/") else None
        conn = http("POST", "/api/v1/acp/connect", {})
        conn_id = None
        try:
            parsed = json.loads(conn.get("body", "{}"))
            conn_id = parsed.get("connectionId") or (parsed.get("data") or {}).get("connectionId")
            # never persist the session token itself
            res["acp_connect"] = {
                "status": conn.get("status"),
                "connectionId_present": bool(conn_id),
                "sessionToken_present": bool(parsed.get("sessionToken")
                                             or (parsed.get("data") or {}).get("sessionToken")),
                "note": "sessionToken value intentionally not recorded",
            }
        except Exception as exc:
            res["acp_connect"] = {"status": conn.get("status"), "parse_error": repr(exc),
                                  "body_head": conn.get("body", "")[:200]}
        res["connection_id_present"] = bool(conn_id)
        if conn_id:
            init = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": 1, "clientInfo": {"name": "pan-probe", "version": "0"},
                           "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True},
                                                  "terminal": False}},
            }
            res["acp_initialize"] = sse_request(
                "/api/v1/acp", init, headers={"acp-connection-id": conn_id}, seconds=8)
            new = {
                "jsonrpc": "2.0", "id": 2, "method": "session/new",
                "params": {"cwd": str(ws), "mcpServers": []},
            }
            res["acp_session_new"] = sse_request(
                "/api/v1/acp", new, headers={"acp-connection-id": conn_id}, seconds=10)
            res["acp_disconnect"] = http("DELETE", "/api/v1/acp",
                                         headers={"acp-connection-id": conn_id})
        res["alive_at_end"] = handle["proc"].poll() is None
    finally:
        res["cleanup"] = safe_stop_tree(handle["pid"], handle["filetime"],
                                        label="acp-http", scope_markers=[str(root)],
                                        popen=handle["proc"])
        time.sleep(1)
        res["port_free_after"] = not port_open(port)
        res["stderr_tail"] = handle["stderr_path"].read_text(
            encoding="utf-8", errors="replace")[-2000:]
    save("acp-http.json", res)
    return res


def mode_selftest_cleanup() -> dict:
    """Fault tests for the identity-verified cleanup path (no CBC involved).

    T1  wrong raw identity (recorded FILETIME + 1) -> must refuse, process alive
    T2  exact raw identity                       -> terminates via verified handle
    T3  already-exited process                   -> reported "not running" only,
                                                    no TerminateProcess attempted
    T4  own subtree                              -> descendants need ancestry +
                                                    scope marker + raw identity
    T5  foreign same-marker decoy                -> NOT collected by root scan;
                                                    stopped only by its own identity
    T6  timeout path                             -> subprocess.run(timeout=) ends
                                                    its own child via retained handle
    T7  leftover scan + verified stop            -> all_gone True
    T8  self/ancestor guard                      -> refuses this probe's own chain
    T9  marker-in-argv scenario                  -> cleanup-leftovers subprocess whose
                                                    own argv contains the marker exits
                                                    cleanly (self-kill regression)
    T10 refused child negative                   -> identity_overrides(+1) forces a
                                                    refused child; all_gone MUST be
                                                    False and the child listed as
                                                    residual; then truly cleaned
    """
    root, cfg, ws = new_workdirs("selftest")
    env = iso_env(cfg)
    res: dict = {"mode": "selftest-cleanup", "run_id": RUN_ID, "root": str(root),
                 "note": "all processes here are created by this probe; no CBC calls",
                 "identity_comparison": "exact raw FILETIME integer, no tolerance"}
    started = time.time()
    spawned: list[subprocess.Popen] = []
    try:
        # --- T1/T2: decoy with wrong (raw + 1), then exact raw identity
        decoy = subprocess.Popen(["ping", "-n", "60", "127.0.0.1"], cwd=str(ws),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spawned.append(decoy)
        decoy_ft = (raw_identity(decoy.pid) or {}).get("filetime")
        t1 = safe_stop(decoy.pid, None if decoy_ft is None else decoy_ft + 1,
                       label="decoy-wrong-raw",
                       basis="fault test: recorded raw FILETIME + 1")
        alive_after_t1 = psutil.pid_exists(decoy.pid)
        t2 = safe_stop(decoy.pid, decoy_ft, label="decoy-correct-raw",
                       basis="raw FILETIME recorded at spawn")
        res["T1_wrong_identity"] = {"stop_result": t1, "alive_after_refusal": alive_after_t1}
        res["T2_correct_identity"] = {"stop_result": t2,
                                      "gone_after": not psutil.pid_exists(decoy.pid)}

        # --- T3: short-lived process, identity recorded, then gone
        short = subprocess.Popen(["cmd", "/c", "exit", "0"], cwd=str(ws))
        short_ft = (raw_identity(short.pid) or {}).get("filetime")
        short.wait(timeout=30)
        t3 = safe_stop(short.pid, short_ft, label="short-lived",
                       basis="already exited naturally")
        res["T3_already_gone"] = t3

        # --- T4/T5: root with a child (own subtree) + a foreign same-marker decoy
        marker = f"selftest{RUN_ID.replace('-', '')}"
        root_proc = subprocess.Popen(
            ["cmd", "/c", f"echo {marker} & ping -n 60 127.0.0.1"],
            cwd=str(ws), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spawned.append(root_proc)
        time.sleep(1.5)
        root_ident = proc_identity(root_proc.pid)
        root_ft = (root_ident or {}).get("filetime")
        foreign = subprocess.Popen(["ping", "-n", "60", "127.0.0.1"], cwd=str(ws),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spawned.append(foreign)
        foreign_ft = (raw_identity(foreign.pid) or {}).get("filetime")
        time.sleep(1.0)
        scan_root = collect_own_descendants(root_proc.pid, root_ident["create_time"],
                                            scope_markers=[str(root)])
        t4 = safe_stop_tree(root_proc.pid, root_ft, label="selftest-root",
                            scope_markers=[str(root)], popen=root_proc)
        res["T4_own_subtree"] = {
            "scan": scan_root, "stop": t4,
            "root_gone": not psutil.pid_exists(root_proc.pid),
            "children_gone": [c["pid"] for c in scan_root["owned"]
                              if not psutil.pid_exists(c["pid"])],
            "all_gone": t4["all_gone"],
            "foreign_still_alive": psutil.pid_exists(foreign.pid),
        }
        t5 = safe_stop(foreign.pid, foreign_ft, label="foreign-decoy",
                       basis="own decoy, separate raw identity")
        res["T5_foreign_not_collected"] = {
            "foreign_collected_by_root_scan": any(c["pid"] == foreign.pid for c in scan_root["owned"]),
            "stop_result": t5, "gone_after": not psutil.pid_exists(foreign.pid)}

        # --- T6: timeout path (subprocess.run timeout uses the retained handle)
        t6_proc = subprocess.Popen(["ping", "-n", "60", "127.0.0.1"], cwd=str(ws),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spawned.append(t6_proc)
        timed_out = False
        try:
            subprocess.run(["ping", "-n", "60", "127.0.0.1"], cwd=str(ws),
                           capture_output=True, timeout=1.5)
        except subprocess.TimeoutExpired:
            timed_out = True
        time.sleep(0.5)
        res["T6_timeout_path"] = {
            "timeout_raised": timed_out,
            "note": "subprocess.run ended its own child through the retained Popen handle",
            "own_unrelated_decoy_untouched": psutil.pid_exists(t6_proc.pid),
        }
        if psutil.pid_exists(t6_proc.pid):
            ft6 = (raw_identity(t6_proc.pid) or {}).get("filetime")
            res["T6_timeout_path"]["decoy_cleanup"] = safe_stop(
                t6_proc.pid, ft6, label="t6-decoy", basis="own decoy after timeout test")

        # --- T8: self / ancestor protection
        own_ft = (raw_identity(os.getpid()) or {}).get("filetime")
        parent = None
        try:
            parents = psutil.Process(os.getpid()).parents()
            if parents:
                parent = parents[0]
        except psutil.Error:
            parent = None
        t8_self = safe_stop(os.getpid(), own_ft, label="self",
                            basis="fault test: must refuse self")
        parent_ft = (raw_identity(parent.pid) or {}).get("filetime") if parent else None
        t8_parent = (safe_stop(parent.pid, parent_ft, label="parent",
                               basis="fault test: must refuse ancestor")
                     if parent else {"skipped": "no parent resolved"})
        res["T8_self_ancestor_protection"] = {
            "self_stop": t8_self, "parent_stop": t8_parent,
            "parent_still_alive": bool(parent and psutil.pid_exists(parent.pid)),
        }

        # --- T7: leftover scan + identity-verified stop
        leftover = subprocess.Popen(
            ["cmd", "/c", f"echo {marker} & ping -n 60 127.0.0.1"], cwd=str(ws),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spawned.append(leftover)
        time.sleep(1.0)
        scan = scan_own_leftovers([marker], not_before=started)
        t7 = safe_stop_leftovers([marker], not_before=started,
                                 tree_scope_markers=[str(root)],
                                 label="selftest-leftover")
        res["T7_leftover_scan_stop"] = {
            "scan_found": scan, "stop": t7,
            "alive_after": psutil.pid_exists(leftover.pid)}

        # --- T9: marker-in-argv regression (the child's own argv has the marker)
        scenario_marker = f"pan-selfscenario-{RUN_ID}"
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "cleanup-leftovers", scenario_marker],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent))
        res["T9_marker_in_argv"] = {
            "child_rc": child.returncode,
            "child_stdout_tail": (child.stdout or "")[-300:],
            "child_stderr_tail": (child.stderr or "")[-500:],
            "parent_alive_after": bool(psutil.Process(os.getpid()).is_running()),
            "note": "child argv contained the marker; self/ancestor protection must prevent self-kill",
        }

        # --- T10: refused-child negative -> all_gone must be False
        neg_root = subprocess.Popen(
            ["cmd", "/c", f"echo {marker} & ping -n 60 127.0.0.1"],
            cwd=str(ws), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spawned.append(neg_root)
        time.sleep(1.5)
        neg_scan = collect_own_descendants(neg_root.pid,
                                           (proc_identity(neg_root.pid) or {}).get("create_time", 0.0),
                                           scope_markers=[str(root)])
        neg_root_ft = (raw_identity(neg_root.pid) or {}).get("filetime")
        overrides = {c["pid"]: c["filetime"] + 1 for c in neg_scan["owned"]}
        t10 = safe_stop_tree(neg_root.pid, neg_root_ft, label="selftest-refused-child",
                             scope_markers=[str(root)], popen=neg_root,
                             identity_overrides=overrides)
        residual_child = [c["pid"] for c in neg_scan["owned"]]
        res["T10_refused_child_negative"] = {
            "injected_overrides": {str(k): v for k, v in overrides.items()},
            "stop": t10,
            "all_gone_must_be_false": t10["all_gone"] is False,
            "residual_pids": t10["residual_pids"],
            "refused_entries": [r.get("pid") for r in t10["refused_or_unverified"]],
            "child_still_alive_after": [pid for pid in residual_child if psutil.pid_exists(pid)],
        }
        # then clean the residual child with its TRUE raw identity
        cleaned = []
        for pid in residual_child:
            if psutil.pid_exists(pid):
                ft = (raw_identity(pid) or {}).get("filetime")
                cleaned.append(safe_stop(pid, ft, label=f"t10-cleanup-{pid}",
                                         basis="true raw identity after negative case"))
        res["T10_refused_child_negative"]["post_cleanup"] = cleaned
        res["T10_refused_child_negative"]["residual_gone_after_cleanup"] = [
            pid for pid in residual_child if not psutil.pid_exists(pid)]
    finally:
        for p in spawned:
            if p.poll() is None:
                ft = (raw_identity(p.pid) or {}).get("filetime")
                res.setdefault("final_cleanup", []).append(safe_stop(
                    p.pid, ft, label=f"final-{p.pid}", basis="own spawned process"))
        # scope-based fallback for anything this mode created
        res.setdefault("final_scope_cleanup", safe_stop_leftovers(
            [str(root)], not_before=RUN_START, label="selftest-scope"))
    save("selftest-cleanup.json", res)
    return res


def mode_cleanup_leftovers() -> dict:
    """Manual recovery: identity-verified cleanup of processes under a given
    temp-root substring (e.g. from an interrupted earlier run)."""
    marker = sys.argv[2] if len(sys.argv) > 2 else ""
    res: dict = {"mode": "cleanup-leftovers", "run_id": RUN_ID, "marker": marker}
    if not marker:
        res["error"] = "usage: probe_cbc_feasibility.py cleanup-leftovers <path-substring>"
        return res
    res["scan"] = scan_own_leftovers([marker], not_before=0.0)
    res["stop"] = safe_stop_leftovers([marker], not_before=0.0,
                                      tree_scope_markers=[marker], label="manual")
    save("cleanup-leftovers.json", res)
    return res


MODES = {
    "baseline": mode_baseline,
    "headless": mode_headless,
    "bg": mode_bg,
    "bg-lifetime": mode_bg_lifetime,
    "bg-observe": mode_bg_observe,
    "bg-model": mode_bg_model,
    "serve": mode_serve,
    "daemon": mode_daemon,
    "daemon-job": mode_daemon_job,
    "acp": mode_acp,
    "acp-http": mode_acp_http,
    "selftest-cleanup": mode_selftest_cleanup,
    "cleanup-leftovers": mode_cleanup_leftovers,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in MODES or (
            sys.argv[1] == "cleanup-leftovers" and len(sys.argv) < 3):
        print(f"usage: {sys.argv[0]} [{'|'.join(MODES)}]"
              " (cleanup-leftovers needs a path substring)", file=sys.stderr)
        return 2
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1]
    log(f"run_id={RUN_ID} mode={mode} evidence={EVIDENCE}")
    out = MODES[mode]()
    log(json.dumps({k: v for k, v in out.items()
                    if k in ("mode", "pid", "bg_pid", "job_pid", "job_name", "port",
                             "alive_after_6s", "pty_session_id",
                             "pty_sse_marker_found", "port_free_after",
                             "launcher_exit_rc", "launcher_exit_t")},
                   ensure_ascii=False))
    print(str(EVIDENCE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

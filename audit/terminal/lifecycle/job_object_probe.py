"""Job Object ownership-layout evidence for the Terminal lifecycle decision.

Sub-probes (all children are self-built probe processes):

  A no_extraction            assign child to KILL_ON_JOB_CLOSE job K; assign it
                             to a second job X; closing K's handle still kills it
                             -> membership can be added but never removed
  B breakaway_birth_only     a child created with CREATE_BREAKAWAY_FROM_JOB by a
                             holder inside a BREAKAWAY_OK job escapes that job at
                             birth; there is no assignment-time equivalent
  C owner_death_kills_tree   holder creates KILL_ON_JOB_CLOSE job, assigns a
                             child, exits hard -> kernel closes handles, child
                             dies (crash-safe cleanup without graceful code)
  D takeover_job_production  the production ``packages.core.takeover_job`` class
                             exercised read-only with probe children: normal
                             stop() and holder-crash paths both kill the tree
  E api_surface              kernel32/kernelbase export scan: which Job APIs
                             exist, and that no "remove from job" API exists

Usage: <venv-python> job_object_probe.py --out evidence/job_object_probe.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import probe_lib as lib  # noqa: E402

PYTHON, PY_ENV = lib.probe_python()
TEMP = Path(tempfile.gettempdir()) / f"pan-term-jobs-{os.getpid()}"
SLEEP_CODE = "import time; time.sleep(120)"
results: list[dict] = []


def record(name: str, passed: bool, facts: dict) -> None:
    results.append({"name": name, "pass": bool(passed), "facts": facts})
    print(f"[{'PASS' if passed else 'FAIL'}] {name} :: "
          f"{json.dumps(facts, ensure_ascii=False)[:400]}", flush=True)


def spawn_sleeper(extra_flags: int = 0) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, "-c", SLEEP_CODE], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=extra_flags, env=PY_ENV)


def cleanup_pids(pids: list[int]) -> None:
    for pid in pids:
        info = lib.identity(pid)
        if info:
            lib.kill_verified(pid, info["createTimeFiletime"])


# ── A: no extraction ─────────────────────────────────────────────────────────

def prob_no_extraction() -> None:
    child = spawn_sleeper()
    time.sleep(0.3)
    child_info = lib.identity(child.pid)
    job_k = lib.Job.create(kill_on_close=True)
    job_x = lib.Job.create(kill_on_close=False)
    facts = {
        "child": child_info,
        "ambientJob": lib.process_in_any_job(child.pid),
    }
    try:
        ok_k, err_k = job_k.assign(child.pid)
        facts["assignToKillOnCloseJob"] = ok_k
        facts["assignToKillOnCloseJobError"] = err_k
        facts["inK"] = job_k.is_member(child.pid)
        ok_x, err_x = job_x.assign(child.pid)
        facts["assignToSecondJob"] = ok_x
        facts["assignToSecondJobError"] = err_x
        facts["inXAfterSecondAssign"] = job_x.is_member(child.pid)
        facts["inKAfterSecondAssign"] = job_k.is_member(child.pid)
        job_k.close()  # the decisive step: close the kill-on-close job handle
        died, waited = lib.wait_until(lambda: not lib.identity_matches(
            child.pid, child_info["createTimeFiletime"]), timeout=4)
        facts["childDiedAfterClosingKHandle"] = died
        facts["childDiedAfterSeconds"] = round(waited, 3)
        facts["secondJobStillOpen"] = not job_x.closed
        passed = (facts["assignToKillOnCloseJob"] and facts["assignToSecondJob"]
                  and facts["inK"] is True and died)
    finally:
        cleanup_pids([child.pid])
        job_x.close()
        if not job_k.closed:
            job_k.close()
    record("A_no_extraction", passed, facts)


# ── B: breakaway only at birth ───────────────────────────────────────────────

HOLDER_BREAKAWAY = r'''
import subprocess, sys, time
PYEXE = getattr(sys, "_base_executable", None) or sys.executable
print("HOLDER_READY", flush=True)
line = sys.stdin.readline()
if line.strip() == "GO":
    p = subprocess.Popen([PYEXE, "-c", "import time;time.sleep(120)"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=0x01000000)
    print("CHILD", p.pid, flush=True)
    time.sleep(120)
'''


def prob_breakaway_birth_only() -> None:
    job = lib.Job.create(kill_on_close=False, breakaway_ok=True)
    holder = subprocess.Popen(
        [PYTHON, "-c", HOLDER_BREAKAWAY], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=PY_ENV)
    child_pid = None
    facts: dict = {}
    try:
        assert holder.stdout
        line = holder.stdout.readline().strip()
        facts["holderReadyLine"] = line
        ok_assign, err = job.assign(holder.pid)
        facts["assignHolderToBreakawayOkJob"] = ok_assign
        facts["assignError"] = err
        facts["holderInJob"] = job.is_member(holder.pid)
        holder.stdin.write("GO\n")
        holder.stdin.flush()
        line = holder.stdout.readline().strip()
        facts["childLine"] = line
        for _ in range(50):
            if line.startswith("CHILD"):
                break
            line = holder.stdout.readline().strip()
        child_pid = int(line.split()[1])
        child_info = lib.identity(child_pid)
        facts["child"] = child_info
        facts["childInBreakawayJob"] = job.is_member(child_pid)
        facts["childInAnyJob"] = lib.process_in_any_job(child_pid)
        passed = (facts["childInBreakawayJob"] is False)
        # A control: assigning (not creating) cannot produce the same escape.
        probe_child = spawn_sleeper()
        time.sleep(0.3)
        ok_move, move_err = job.assign(probe_child.pid)
        facts["assignExistingChildToSameJob"] = ok_move
        facts["assignExistingChildError"] = move_err
        cleanup_pids([probe_child.pid])
    finally:
        cleanup_pids([p for p in (child_pid, holder.pid) if p])
        if holder.poll() is None:
            holder.kill()
        job.close()
    record("B_breakaway_birth_only", passed, facts)


# ── C: owner death kills the tree ────────────────────────────────────────────

HOLDER_OWNER_DEATH = r'''
import ctypes, os, subprocess, sys
from ctypes import wintypes
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateJobObjectW.restype = wintypes.HANDLE
k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
class Basic(ctypes.Structure):
    _fields_ = [("a", ctypes.c_longlong), ("b", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("c", ctypes.c_size_t),
                ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]
class Ext(ctypes.Structure):
    _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6), ("mem", ctypes.c_size_t * 4)]
PYEXE = getattr(sys, "_base_executable", None) or sys.executable
job = k32.CreateJobObjectW(None, None)
lim = Ext(); lim.basic.flags = 0x2000
k32.SetInformationJobObject(job, 9, ctypes.byref(lim), ctypes.sizeof(lim))
p = subprocess.Popen([PYEXE, "-c", "import time;time.sleep(120)"], creationflags=4)
k32.AssignProcessToJobObject(job, int(p._handle))
print("GRANDCHILD", p.pid, flush=True)
os._exit(0)
'''


def prob_owner_death_kills_tree() -> None:
    holder = subprocess.Popen(
        [PYTHON, "-c", HOLDER_OWNER_DEATH], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=PY_ENV)
    facts: dict = {}
    child_pid = None
    try:
        assert holder.stdout
        line = holder.stdout.readline().strip()
        facts["holderLine"] = line
        child_pid = int(line.split()[1])
        child_info = lib.identity(child_pid)
        facts["child"] = child_info
        holder.wait(timeout=10)
        facts["holderExitCode"] = holder.returncode
        died, waited = lib.wait_until(lambda: not lib.identity_matches(
            child_pid, child_info["createTimeFiletime"]), timeout=4)
        facts["childDiedAfterHolderExit"] = died
        facts["childDiedAfterSeconds"] = round(waited, 3)
        passed = died and holder.returncode == 0
    finally:
        cleanup_pids([p for p in (child_pid, holder.pid) if p])
    record("C_owner_death_kills_tree", passed, facts)


# ── D: production TakeoverJob reuse ──────────────────────────────────────────

HOLDER_TAKEOVER_STOP = r'''
import subprocess, sys, time
PYEXE = getattr(sys, "_base_executable", None) or sys.executable
sys.path.insert(0, {repo_root!r})
from packages.core.takeover_job import TakeoverJob
job = TakeoverJob()
pid = job.launch([PYEXE, "-c", "import time;time.sleep(60)"],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
print("TAKEOVER_CHILD", pid, flush=True)
time.sleep(0.5)
job.stop()
print("STOPPED", flush=True)
time.sleep(0.2)
'''

HOLDER_TAKEOVER_CRASH = r'''
import os, subprocess, sys, time
PYEXE = getattr(sys, "_base_executable", None) or sys.executable
sys.path.insert(0, {repo_root!r})
from packages.core.takeover_job import TakeoverJob
job = TakeoverJob()
pid = job.launch([PYEXE, "-c", "import time;time.sleep(60)"],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
print("TAKEOVER_CHILD", pid, flush=True)
time.sleep(0.5)
os._exit(0)
'''


def run_takeover_case(name: str, source: str, expect_stopped_line: bool) -> None:
    holder = subprocess.Popen(
        [PYTHON, "-c", source.format(repo_root=str(REPO_ROOT))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=PY_ENV)
    facts: dict = {}
    child_pid = None
    try:
        assert holder.stdout
        line = holder.stdout.readline().strip()
        facts["holderLine"] = line
        child_pid = int(line.split()[1])
        child_info = lib.identity(child_pid)
        facts["child"] = child_info
        if expect_stopped_line:
            stopped_line = holder.stdout.readline().strip()
            facts["stoppedLine"] = stopped_line
            facts["inJobBeforeStop"] = lib.process_in_any_job(child_pid)
        holder.wait(timeout=10)
        facts["holderExitCode"] = holder.returncode
        died, waited = lib.wait_until(lambda: not lib.identity_matches(
            child_pid, child_info["createTimeFiletime"]), timeout=5)
        facts["childDied"] = died
        facts["childDiedAfterSeconds"] = round(waited, 3)
        passed = died and holder.returncode == 0
    finally:
        cleanup_pids([p for p in (child_pid, holder.pid) if p])
    record(name, passed, facts)


def prob_api_surface() -> None:
    facts: dict = {}
    try:
        names = list_exports(Path(os.environ.get("SystemRoot", r"C:\Windows"))
                             / "System32" / "kernel32.dll")
        facts["kernel32ExportCount"] = len(names)
        job_names = sorted(n for n in names if "job" in n.lower())
        facts["kernel32JobExports"] = job_names
        removal = [n for n in job_names
                   if "remove" in n.lower() or "detach" in n.lower()]
        facts["removalLikes"] = removal
        facts["assignPresent"] = any(n == "AssignProcessToJobObject" for n in names)
        facts["isInJobPresent"] = any(n == "IsProcessInJob" for n in names)
        facts["terminateJobPresent"] = any(n == "TerminateJobObject" for n in names)
        facts["openJobPresent"] = any(n == "OpenJobObjectW" for n in names)
        passed = (not removal and facts["assignPresent"]
                  and facts["isInJobPresent"] and facts["terminateJobPresent"])
    except Exception as exc:
        facts["error"] = f"{type(exc).__name__}: {exc}"
        passed = False
    record("E_api_surface", passed, facts)


def list_exports(path: Path) -> list[str]:
    data = path.read_bytes()
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError("not a PE file")
    coff = e_lfanew + 4
    num_sections = int.from_bytes(data[coff + 2:coff + 4], "little")
    opt_size = int.from_bytes(data[coff + 16:coff + 18], "little")
    opt = coff + 20
    magic = int.from_bytes(data[opt:opt + 2], "little")
    dd_off = opt + (112 if magic == 0x20B else 96)
    export_rva = int.from_bytes(data[dd_off:dd_off + 4], "little")
    sections = []
    sec_off = opt + opt_size
    for index in range(num_sections):
        base = sec_off + index * 40
        sections.append((
            int.from_bytes(data[base + 12:base + 16], "little"),
            int.from_bytes(data[base + 8:base + 12], "little"),
            int.from_bytes(data[base + 20:base + 24], "little"),
            int.from_bytes(data[base + 16:base + 20], "little"),
        ))

    def rva_to_off(rva: int) -> int | None:
        for vaddr, vsize, raw_ptr, raw_size in sections:
            if vaddr <= rva < vaddr + max(vsize, raw_size):
                return raw_ptr + (rva - vaddr)
        return None

    exp_off = rva_to_off(export_rva)
    if exp_off is None:
        return []
    name_count = int.from_bytes(data[exp_off + 24:exp_off + 28], "little")
    names_rva = int.from_bytes(data[exp_off + 32:exp_off + 36], "little")
    names_off = rva_to_off(names_rva)
    names = []
    for index in range(name_count):
        rva = int.from_bytes(data[names_off + index * 4:names_off + index * 4 + 4],
                             "little")
        off = rva_to_off(rva)
        end = data.index(b"\x00", off)
        names.append(data[off:end].decode("ascii", "replace"))
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "evidence" / "job_object_probe.json"))
    args = parser.parse_args()
    TEMP.mkdir(parents=True, exist_ok=True)
    prob_no_extraction()
    prob_breakaway_birth_only()
    prob_owner_death_kills_tree()
    run_takeover_case("D1_takeover_production_stop", HOLDER_TAKEOVER_STOP, True)
    run_takeover_case("D2_takeover_production_holder_crash",
                      HOLDER_TAKEOVER_CRASH, False)
    prob_api_surface()
    report = {
        "probe": "job_object_probe",
        "python": sys.version,
        "repoRoot": str(REPO_ROOT),
        "startedAt": time.time(),
        "results": results,
        "passed": all(item["pass"] for item in results),
    }
    lib.write_json(args.out, report)
    shutil.rmtree(TEMP, ignore_errors=True)
    print(json.dumps({"passed": report["passed"],
                      "results": [r["name"] for r in results]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

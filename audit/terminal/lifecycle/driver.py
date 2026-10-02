"""Scenario driver for the Terminal lifecycle / detach probe.

Runs five end-to-end scenarios with self-built processes only:

  s1_default_normal_exit   default runtime dies on host *normal* exit
  s2_default_crash         default runtime dies on host *crash* (lease)
  s3_detach_normal_exit    detached runtime survives host normal exit;
                           reconnect (new controller process) keeps PID,
                           variables, and the running program
  s4_controller_churn      browser disconnects change nothing; output and
                           execution continue across connection gaps
  s5_detach_crash_tree     detached runtime survives host crash; hard-killing
                           the runner still cleans the whole PTY tree
                           (runner-owned KILL_ON_JOB_CLOSE job)

Every scenario records PID + creation time identity (exact 100ns FILETIME) and
verifies cleanup in a finally-style sweep.  Evidence JSON is written to
``evidence/``.  All runtime state lives under the OS temp directory.

Usage:
  <venv-python> driver.py --scenario all
  <venv-python> driver.py --scenario s3_detach_normal_exit --keep
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_lib as lib  # noqa: E402

PYTHON, PY_ENV = lib.probe_python()
SUPERVISOR = HERE / "supervisor.py"
CONTROLLER = HERE / "controller.py"
TICK = HERE / "tick.py"
EVIDENCE = HERE / "evidence"


class Scenario:
    def __init__(self, name: str, keep: bool = False, evidence_dir: Path | None = None):
        self.name = name
        self.keep = keep
        self.evidence_dir = Path(evidence_dir) if evidence_dir else EVIDENCE
        run_id = time.strftime("%Y%m%d-%H%M%S")
        self.root = Path(tempfile.gettempdir()) / f"pan-term-lifecycle-{run_id}" / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.checks: list[dict] = []
        self.all_recorded: dict[int, dict] = {}
        self.trees: dict[str, dict] = {}
        self.notes: dict = {}
        self.supervisor: subprocess.Popen | None = None
        self.supervisor_log = None
        self.known: dict[str, dict] = {}
        self.tick_file = self.root / "tick.txt"
        self.evidence: dict = {
            "scenario": name,
            "startedAt": time.time(),
            "root": str(self.root),
            "checks": self.checks,
        }

    # ── assertions ──────────────────────────────────────────────────────────
    def check(self, name: str, ok: bool, detail=None) -> bool:
        entry = {"name": name, "ok": bool(ok), "detail": detail}
        self.checks.append(entry)
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name}" + ("" if detail is None else f" :: {detail}"),
              flush=True)
        return bool(ok)

    # ── identity tracking ───────────────────────────────────────────────────
    def record(self, role: str, pid: int | None, extra: dict | None = None) -> dict | None:
        info = lib.identity(pid)
        if info is None:
            return None
        if extra:
            info.update(extra)
        self.known[role] = info
        self.all_recorded[pid] = {k: info[k] for k in ("pid", "createTimeFiletime")}
        return info

    def capture_tree(self, label: str, roots: list[int]) -> dict:
        tree = lib.tree_identity(roots)
        for pid, info in tree.items():
            self.all_recorded.setdefault(pid, {
                "pid": pid, "createTimeFiletime": info["createTimeFiletime"]})
        self.trees[label] = tree
        return tree

    def alive_same(self, role: str) -> bool:
        info = self.known.get(role)
        if not info:
            return False
        return lib.identity_matches(info["pid"], info["createTimeFiletime"])

    def dead_same(self, role: str) -> bool:
        return not self.alive_same(role)

    # ── process helpers ─────────────────────────────────────────────────────
    def spawn_supervisor(self, tag: str, lease_grace: float = 2.0,
                         extra_args: list[str] | None = None) -> subprocess.Popen:
        self.supervisor_log = open(self.root / "supervisor_console.log", "wb")
        self.supervisor = subprocess.Popen(
            [PYTHON, str(SUPERVISOR), "--data-root", str(self.root), "--tag", tag,
             "--lease-grace", str(lease_grace), *(extra_args or [])],
            stdin=subprocess.PIPE, stdout=self.supervisor_log,
            stderr=subprocess.STDOUT, cwd=str(HERE), env=PY_ENV)
        self.record("supervisor", self.supervisor.pid)
        return self.supervisor

    def wait_supervisor_ready(self, timeout: float = 20.0) -> dict:
        path = self.root / "supervisor_ready.json"
        ok, waited = lib.wait_until(path.exists, timeout=timeout)
        if not ok:
            raise TimeoutError(f"supervisor not ready: {path}")
        ready = lib.read_json(path)
        endpoint = ready["endpoint"]
        self.record("runner", endpoint["pid"])
        self.all_recorded[endpoint["pid"]] = {
            "pid": endpoint["pid"],
            "createTimeFiletime": endpoint["processCreatedAtFiletime"],
        }
        self.known["pty_root"] = {
            "pid": endpoint["pty"]["rootPid"],
            "createTimeFiletime": endpoint["pty"]["rootCreatedAtFiletime"],
            "createTime": endpoint["pty"]["rootCreatedAt"],
            "image": None,
        }
        self.all_recorded[endpoint["pty"]["rootPid"]] = {
            "pid": endpoint["pty"]["rootPid"],
            "createTimeFiletime": endpoint["pty"]["rootCreatedAtFiletime"],
        }
        self.endpoint = endpoint
        self.evidence["runtimeEndpoint"] = {
            k: v for k, v in endpoint.items() if k != "token"}
        self.notes["supervisorReadyAfterSeconds"] = round(waited, 3)
        return ready

    def supervisor_command(self, command: str) -> None:
        assert self.supervisor and self.supervisor.stdin
        self.supervisor.stdin.write((command + "\n").encode())
        self.supervisor.stdin.flush()

    def wait_supervisor_exit(self, timeout: float = 20.0) -> int | None:
        assert self.supervisor
        try:
            return self.supervisor.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def run_controller(self, label: str, ops: list[dict]) -> dict:
        out = self.root / f"controller_{label}.json"
        ops_file = self.root / f"ops_{label}.json"
        lib.write_json(ops_file, ops)
        completed = subprocess.run(
            [PYTHON, str(CONTROLLER), "--data-root", str(self.root),
             "--ops", f"@{ops_file}", "--out", str(out),
             "--client-id", f"ctl-{label}"],
            capture_output=True, text=True, timeout=180, env=PY_ENV)
        data = lib.read_json(out) if out.exists() else {
            "ok": False, "error": f"controller output missing; rc={completed.returncode}"}
        data["_label"] = label
        data["_returncode"] = completed.returncode
        data["_stderr"] = completed.stderr[-2000:]
        self.notes.setdefault("controllers", {})[label] = {
            "ok": data.get("ok"), "pid": data.get("pid"),
            "error": data.get("error"), "returncode": completed.returncode,
        }
        return data

    # ── tick program helpers ────────────────────────────────────────────────
    def read_tick(self) -> dict:
        if not self.tick_file.exists():
            return {"exists": False}
        lines = self.tick_file.read_text(encoding="utf-8", errors="replace").splitlines()
        started = None
        last = None
        for line in lines:
            parts = line.split()
            if parts and parts[0] == "STARTED":
                started = int(parts[1])
            if parts and parts[0] == "TICK":
                last = {"pid": int(parts[1]), "n": int(parts[2]), "t": float(parts[3])}
        return {"exists": True, "lines": len(lines), "startedPid": started, "last": last}

    def start_tick_ops(self, marker_value: str) -> list[dict]:
        # Spawn tick.py inside the PTY via `start /b` (child of cmd.exe; inherits
        # the runner-owned guard job, so tree cleanup covers it too).
        cmd = (f'start "" /b "{PYTHON}" "{TICK}" "{self.tick_file}"\r\n')
        return [
            {"op": "input", "data": f"set PXS={marker_value}\r\n"},
            {"op": "input", "data": "echo SET-DONE:%PXS%\r\n"},
            {"op": "wait", "marker": f"SET-DONE:{marker_value}", "timeout": 15},
            {"op": "input", "data": cmd},
            {"op": "sleep", "seconds": 0.8},
        ]

    def wait_tick_started(self, timeout: float = 10.0) -> int | None:
        ok, _ = lib.wait_until(
            lambda: self.read_tick().get("startedPid") is not None, timeout=timeout)
        if not ok:
            return None
        started = self.read_tick()["startedPid"]
        self.record("tick", started)
        return started

    # ── cleanup ─────────────────────────────────────────────────────────────
    def cleanup(self) -> dict:
        """Identity-verified kill of everything this scenario ever recorded."""
        killed, survivors = [], []
        for pid, info in sorted(self.all_recorded.items(), key=lambda kv: kv[0]):
            if lib.identity_matches(pid, info["createTimeFiletime"]):
                ok = lib.kill_verified(pid, info["createTimeFiletime"])
                killed.append({"pid": pid, "killed": ok})
        if self.supervisor and self.supervisor.poll() is None:
            self.supervisor.kill()
            killed.append({"pid": self.supervisor.pid, "killed": True, "role": "supervisor"})
        time.sleep(0.5)
        for pid, info in sorted(self.all_recorded.items(), key=lambda kv: kv[0]):
            if lib.identity_matches(pid, info["createTimeFiletime"]):
                survivors.append(pid)
        if self.supervisor_log is not None:
            try:
                self.supervisor_log.close()
            except OSError:
                pass
            self.supervisor_log = None
        report = {"killed": killed, "survivors": survivors}
        self.evidence["cleanup"] = report
        return report

    def finalize(self, passed: bool) -> dict:
        self.evidence["endedAt"] = time.time()
        self.evidence["passed"] = passed
        self.evidence["knownProcesses"] = self.known
        self.evidence["trees"] = self.trees
        self.evidence["notes"] = self.notes
        self.evidence["failedChecks"] = [
            c for c in self.checks if not c["ok"]]
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        lib.write_json(self.evidence_dir / f"{self.name}.json", self.evidence)
        if not self.keep:
            shutil.rmtree(self.root.parent, ignore_errors=True)
        passed = passed and all(c["ok"] for c in self.checks)
        flag = "PASS" if passed else "FAIL"
        print(f"== {self.name}: {flag} ({sum(1 for c in self.checks if c['ok'])}"
              f"/{len(self.checks)} checks)", flush=True)
        return self.evidence


def env_facts() -> dict:
    import winpty
    facts = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "win32_ver": platform.win32_ver(),
        "pywinpty": getattr(winpty, "__version__", None),
        "driverInAnyJob": lib.process_in_any_job(os.getpid()),
    }
    return facts


# ── scenarios ────────────────────────────────────────────────────────────────

def scenario_default_normal_exit(keep: bool = False, evidence_dir: Path | None = None) -> dict:
    sc = Scenario("s1_default_normal_exit", keep=keep, evidence_dir=evidence_dir)
    try:
        sc.spawn_supervisor("s1")
        sc.wait_supervisor_ready()
        nonce = secrets.token_hex(3)
        marker = f"MX-{nonce}"
        result = sc.run_controller("setup", [
            *sc.start_tick_ops(marker),
            {"op": "state", "save": "after_setup"},
            {"op": "disconnect"},
        ])
        sc.check("s1 setup controller ok", result.get("ok"), result.get("error"))
        tick_pid = sc.wait_tick_started()
        sc.check("s1 tick program started in PTY", tick_pid is not None, tick_pid)
        sc.capture_tree("running", [sc.known["runner"]["pid"]])

        # Controller is gone; the program must keep running on its own.
        first = sc.read_tick()
        time.sleep(1.2)
        second = sc.read_tick()
        sc.check("s1 runtime keeps running while no controller is connected",
                 second.get("last") and first.get("last")
                 and second["last"]["n"] > first["last"]["n"],
                 {"first": first.get("last"), "second": second.get("last")})

        # A fresh controller process reconnects; the shell variable is intact.
        result = sc.run_controller("reconnect", [
            {"op": "input", "data": "echo CHURN:%PXS%\r\n"},
            {"op": "wait", "marker": f"CHURN:{marker}", "timeout": 10},
            {"op": "state", "save": "reconnect"},
            {"op": "disconnect"},
        ])
        sc.check("s1 reconnect sees same shell variable",
                 result.get("ok") and any(
                     r.get("op") == "wait" and r.get("found")
                     for r in result.get("results", [])),
                 result.get("error") or f"marker CHURN:{marker}")
        sc.check("s1 identities unchanged across controller gap",
                 sc.alive_same("runner") and sc.alive_same("pty_root")
                 and sc.alive_same("tick"))

        # Close the whole tree through the runtime API (not by killing PIDs).
        sc.supervisor_command("STOP_AND_EXIT")
        rc = sc.wait_supervisor_exit()
        sc.check("s1 supervisor graceful exit rc=0", rc == 0, rc)
        ok, waited = lib.wait_until(lambda: sc.dead_same("runner"), timeout=10)
        sc.check("s1 runner stopped by host shutdown", ok, f"{waited:.2f}s")
        ok2, waited2 = lib.wait_until(
            lambda: sc.dead_same("pty_root") and sc.dead_same("tick"), timeout=10)
        sc.check("s1 PTY root and tick program are gone", ok2, f"{waited2:.2f}s")
        port_ok, port_waited = lib.wait_port_closed(sc.endpoint["port"], timeout=10)
        sc.check("s1 control port closed", port_ok, f"{port_waited:.2f}s")
        exit_file = sc.root / "supervisor_exit.json"
        sc.evidence["supervisorExit"] = lib.read_json(exit_file) if exit_file.exists() else None
        final_file = sc.root / "runner_final.json"
        final = lib.read_json(final_file) if final_file.exists() else None
        sc.evidence["runnerFinal"] = final
        sc.check("s1 runner reported explicit stop", final
                 and final.get("stopReason") == "explicit_stop",
                 final.get("stopReason") if final else "missing runner_final.json")
    except Exception as exc:
        sc.check("scenario raised an unexpected exception", False,
                 f"{type(exc).__name__}: {exc}")
    except BaseException:
        sc.cleanup()
        raise
    cleanup = sc.cleanup()
    passed = all(c["ok"] for c in sc.checks) and not cleanup["survivors"]
    sc.check("s1 cleanup left no owned process alive", not cleanup["survivors"],
             cleanup["survivors"])
    return sc.finalize(passed)


def scenario_default_crash(keep: bool = False, evidence_dir: Path | None = None) -> dict:
    sc = Scenario("s2_default_crash", keep=keep, evidence_dir=evidence_dir)
    try:
        sc.spawn_supervisor("s2")
        sc.wait_supervisor_ready()
        nonce = secrets.token_hex(3)
        marker = f"MX-{nonce}"
        result = sc.run_controller("setup", [
            *sc.start_tick_ops(marker),
            {"op": "disconnect"},
        ])
        sc.check("s2 setup controller ok", result.get("ok"), result.get("error"))
        tick_pid = sc.wait_tick_started()
        sc.check("s2 tick program started in PTY", tick_pid is not None, tick_pid)
        sc.capture_tree("running", [sc.known["runner"]["pid"]])

        sc.supervisor_command("CRASH")
        rc = sc.wait_supervisor_exit()
        sc.check("s2 supervisor crashed hard (rc=17)", rc == 17, rc)
        crash_time = time.time()
        ok, waited = lib.wait_until(lambda: sc.dead_same("runner"), timeout=8)
        sc.notes["leaseExpiryObservedSeconds"] = round(waited, 3)
        sc.check("s2 default runtime self-terminates after lease loss",
                 ok, f"{waited:.2f}s (grace={sc.endpoint['leaseGraceSeconds']}s)")
        ok2, waited2 = lib.wait_until(
            lambda: sc.dead_same("pty_root") and sc.dead_same("tick"), timeout=8)
        sc.check("s2 whole PTY tree gone after lease expiry", ok2, f"{waited2:.2f}s")
        port_ok, port_waited = lib.wait_port_closed(sc.endpoint["port"], timeout=10)
        sc.check("s2 control port closed", port_ok, f"{port_waited:.2f}s")
        sc.notes["crashToAllDeadSeconds"] = round(time.time() - crash_time, 3)
        final_file = sc.root / "runner_final.json"
        final = lib.read_json(final_file) if final_file.exists() else None
        sc.evidence["runnerFinal"] = final
        sc.check("s2 stop reason recorded as lease_expired", final
                 and final.get("stopReason") == "lease_expired",
                 final.get("stopReason") if final else "missing runner_final.json")
    except Exception as exc:
        sc.check("scenario raised an unexpected exception", False,
                 f"{type(exc).__name__}: {exc}")
    except BaseException:
        sc.cleanup()
        raise
    cleanup = sc.cleanup()
    passed = all(c["ok"] for c in sc.checks) and not cleanup["survivors"]
    sc.check("s2 cleanup left no owned process alive", not cleanup["survivors"],
             cleanup["survivors"])
    return sc.finalize(passed)


def scenario_detach_normal_exit(keep: bool = False, evidence_dir: Path | None = None) -> dict:
    sc = Scenario("s3_detach_normal_exit", keep=keep, evidence_dir=evidence_dir)
    try:
        sc.spawn_supervisor("s3")
        sc.wait_supervisor_ready()
        nonce = secrets.token_hex(3)
        marker = f"MX-{nonce}"
        result = sc.run_controller("setup", [
            *sc.start_tick_ops(marker),
            {"op": "detach"},
            {"op": "disconnect"},
        ])
        sc.check("s3 setup+detach controller ok", result.get("ok"), result.get("error"))
        tick_pid = sc.wait_tick_started()
        sc.check("s3 tick program started in PTY", tick_pid is not None, tick_pid)
        sc.capture_tree("running", [sc.known["runner"]["pid"]])
        endpoint = lib.read_endpoint(sc.root)
        sc.check("s3 endpoint file records detached=true",
                 endpoint.get("detached") is True, endpoint.get("detached"))

        # Host exits *normally* without stopping the detached runtime.
        sc.supervisor_command("EXIT_KEEP")
        rc = sc.wait_supervisor_exit()
        sc.check("s3 supervisor exited normally (rc=0)", rc == 0, rc)
        deadline = time.time() + sc.endpoint["leaseGraceSeconds"] + 3.0
        sc.check("s3 supervisor process is gone", not sc.alive_same("supervisor"))
        time.sleep(max(0.0, deadline - time.time()))

        before = sc.read_tick()
        sc.check("s3 detached runtime survived host exit",
                 sc.alive_same("runner") and sc.alive_same("pty_root")
                 and sc.alive_same("tick"))
        sc.check("s3 identity (pid + create time) unchanged after host exit",
                 all(sc.alive_same(role) for role in ("runner", "pty_root", "tick")))
        time.sleep(1.5)
        after = sc.read_tick()
        sc.check("s3 running program continued without any controller connected",
                 after.get("last") and before.get("last")
                 and after["last"]["n"] > before["last"]["n"],
                 {"before": before.get("last"), "after": after.get("last")})

        # New controller process reconnects to the same PTY and same PIDs.
        tasklist = subprocess.run(
            ["tasklist", "/FI", f"PID eq {tick_pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30)
        sc.evidence["tasklistTick"] = tasklist.stdout.strip()
        result = sc.run_controller("reconnect", [
            {"op": "input", "data": "echo RESULT:%PXS%\r\n"},
            {"op": "wait", "marker": f"RESULT:{marker}", "timeout": 10},
            {"op": "state", "save": "after_reconnect"},
            {"op": "stop"},
        ])
        sc.check("s3 reconnect sees original shell variable",
                 result.get("ok") and any(
                     r.get("op") == "wait" and r.get("found")
                     for r in result.get("results", [])),
                 result.get("error") or f"marker RESULT:{marker}")
        sc.check("s3 tick program still listed by tasklist after reconnect",
                 str(tick_pid) in tasklist.stdout, tasklist.stdout.strip()[:200])
        state = (result.get("saved") or {}).get("after_reconnect") or {}
        same_runner = (state.get("pid") == sc.known["runner"]["pid"])
        same_pty = ((state.get("pty") or {}).get("rootPid") == sc.known["pty_root"]["pid"])
        sc.check("s3 reconnected to the same runner and PTY PID",
                 same_runner and same_pty, {"runner": same_runner, "pty": same_pty})

        ok, waited = lib.wait_until(lambda: sc.dead_same("runner"), timeout=10)
        sc.check("s3 explicit stop ends the detached runtime", ok, f"{waited:.2f}s")
        ok2, waited2 = lib.wait_until(
            lambda: sc.dead_same("pty_root") and sc.dead_same("tick"), timeout=10)
        sc.check("s3 whole PTY tree cleaned after stop", ok2, f"{waited2:.2f}s")
        port_ok, _ = lib.wait_port_closed(sc.endpoint["port"], timeout=10)
        sc.check("s3 control port closed after stop", port_ok)
        final_file = sc.root / "runner_final.json"
        final = lib.read_json(final_file) if final_file.exists() else None
        sc.evidence["runnerFinal"] = final
        sc.check("s3 stop reason is explicit_stop after detach",
                 final and final.get("stopReason") == "explicit_stop",
                 final.get("stopReason") if final else None)
    except Exception as exc:
        sc.check("scenario raised an unexpected exception", False,
                 f"{type(exc).__name__}: {exc}")
    except BaseException:
        sc.cleanup()
        raise
    cleanup = sc.cleanup()
    passed = all(c["ok"] for c in sc.checks) and not cleanup["survivors"]
    sc.check("s3 cleanup left no owned process alive", not cleanup["survivors"],
             cleanup["survivors"])
    return sc.finalize(passed)


def scenario_controller_churn(keep: bool = False, evidence_dir: Path | None = None) -> dict:
    sc = Scenario("s4_controller_churn", keep=keep, evidence_dir=evidence_dir)
    try:
        sc.spawn_supervisor("s4")
        sc.wait_supervisor_ready()
        nonce = secrets.token_hex(3)
        marker = f"MX-{nonce}"
        result = sc.run_controller("setup", [
            *sc.start_tick_ops(marker),
            {"op": "state", "save": "baseline"},
            {"op": "disconnect"},
        ])
        sc.check("s4 setup controller ok", result.get("ok"), result.get("error"))
        tick_pid = sc.wait_tick_started()
        sc.check("s4 tick program started in PTY", tick_pid is not None, tick_pid)
        sc.capture_tree("running", [sc.known["runner"]["pid"]])
        baseline_state = (result.get("saved") or {}).get("baseline") or {}
        baseline_ct = sc.read_tick()
        baseline_connects = int(baseline_state.get("controllerConnects") or 0)

        # Four short-lived controller processes; three end with a wait, one
        # disconnects in the middle of a slow command.
        for index in range(1, 4):
            result = sc.run_controller(f"churn{index}", [
                {"op": "input", "data": f"echo C{index}:%PXS%\r\n"},
                {"op": "wait", "marker": f"C{index}:{marker}", "timeout": 10},
                {"op": "state", "save": f"after_churn{index}"},
                {"op": "disconnect"},
            ])
            sc.check(f"s4 churn {index} roundtrip ok", result.get("ok"),
                     result.get("error") or f"C{index}:{marker}")

        slow_marker = f"AFTER-SLOW-{nonce}"
        result = sc.run_controller("midflight", [
            {"op": "input", "data":
                f"ping -n 3 127.0.0.1 >nul & echo {slow_marker}\r\n"},
            {"op": "disconnect"},
        ])
        sc.check("s4 mid-flight controller exited without waiting (disconnect)",
                 result.get("ok"), result.get("error"))
        result = sc.run_controller("midflight_reconnect", [
            {"op": "wait", "marker": slow_marker, "timeout": 20},
            {"op": "state", "save": "final"},
            {"op": "disconnect"},
        ])
        sc.check("s4 slow command completed during the connection gap",
                 result.get("ok"), result.get("error") or slow_marker)

        final_state = (result.get("saved") or {}).get("final") or {}
        final_connects = int(final_state.get("controllerConnects") or 0)
        sc.check("s4 no runtime identity changed across churn",
                 sc.alive_same("runner") and sc.alive_same("pty_root")
                 and sc.alive_same("tick"))
        last = sc.read_tick()
        sc.check("s4 background program kept running across churn",
                 last.get("last") and baseline_ct.get("last")
                 and last["last"]["n"] > baseline_ct["last"]["n"],
                 {"baseline": baseline_ct.get("last"), "final": last.get("last")})
        sc.check("s4 runner saw every controller connection",
                 final_connects >= baseline_connects + 5,
                 {"baseline": baseline_connects, "final": final_connects})
        exit_state = final_state.get("pty") or {}
        sc.check("s4 PTY still reports the original root PID",
                 exit_state.get("rootPid") == sc.known["pty_root"]["pid"],
                 exit_state)
        sc.evidence["baselineState"] = baseline_state
        sc.evidence["finalState"] = final_state

        sc.supervisor_command("STOP_AND_EXIT")
        rc = sc.wait_supervisor_exit()
        sc.check("s4 supervisor graceful exit rc=0", rc == 0, rc)
        ok, waited = lib.wait_until(
            lambda: sc.dead_same("runner") and sc.dead_same("pty_root")
            and sc.dead_same("tick"), timeout=10)
        sc.check("s4 runtime and tree stopped by host shutdown", ok, f"{waited:.2f}s")
    except Exception as exc:
        sc.check("scenario raised an unexpected exception", False,
                 f"{type(exc).__name__}: {exc}")
    except BaseException:
        sc.cleanup()
        raise
    cleanup = sc.cleanup()
    passed = all(c["ok"] for c in sc.checks) and not cleanup["survivors"]
    sc.check("s4 cleanup left no owned process alive", not cleanup["survivors"],
             cleanup["survivors"])
    return sc.finalize(passed)


def scenario_detach_crash_tree(keep: bool = False, evidence_dir: Path | None = None) -> dict:
    sc = Scenario("s5_detach_crash_tree", keep=keep, evidence_dir=evidence_dir)
    try:
        sc.spawn_supervisor("s5")
        sc.wait_supervisor_ready()
        nonce = secrets.token_hex(3)
        marker = f"MX-{nonce}"
        result = sc.run_controller("setup", [
            *sc.start_tick_ops(marker),
            {"op": "detach"},
            {"op": "disconnect"},
        ])
        sc.check("s5 setup+detach controller ok", result.get("ok"), result.get("error"))
        tick_pid = sc.wait_tick_started()
        sc.check("s5 tick program started in PTY", tick_pid is not None, tick_pid)
        sc.capture_tree("running", [sc.known["runner"]["pid"]])

        sc.supervisor_command("CRASH")
        rc = sc.wait_supervisor_exit()
        sc.check("s5 supervisor crashed hard (rc=17)", rc == 17, rc)
        time.sleep(sc.endpoint["leaseGraceSeconds"] + 2.5)
        sc.check("s5 detached runtime survives host crash",
                 sc.alive_same("runner") and sc.alive_same("pty_root")
                 and sc.alive_same("tick"))
        tree_before = sc.capture_tree("detached", [sc.known["runner"]["pid"]])

        # Hard-kill the runner itself (simulated runner crash, identity-checked).
        killed = lib.kill_verified(
            sc.known["runner"]["pid"], sc.known["runner"]["createTimeFiletime"])
        sc.check("s5 runner hard-killed with verified identity", killed)
        kill_time = time.time()
        ok, waited = lib.wait_until(lambda: sc.dead_same("pty_root"), timeout=6)
        sc.check("s5 PTY root dies with the runner (runner-owned job)", ok,
                 f"{waited:.2f}s")
        ok2, waited2 = lib.wait_until(lambda: sc.dead_same("tick"), timeout=6)
        sc.check("s5 grandchild tick program dies with the runner", ok2,
                 f"{waited2:.2f}s")
        port_ok, _ = lib.wait_port_closed(sc.endpoint["port"], timeout=10)
        sc.check("s5 control port closed when runner died", port_ok)
        sc.notes["runnerHardKillToTreeDeadSeconds"] = round(time.time() - kill_time, 3)
        sc.evidence["treeAtDetach"] = tree_before
        sc.evidence["runnerHardKillReason"] = (
            "kernel closes the runner-held KILL_ON_JOB_CLOSE job handle")
    except Exception as exc:
        sc.check("scenario raised an unexpected exception", False,
                 f"{type(exc).__name__}: {exc}")
    except BaseException:
        sc.cleanup()
        raise
    cleanup = sc.cleanup()
    passed = all(c["ok"] for c in sc.checks) and not cleanup["survivors"]
    sc.check("s5 cleanup left no owned process alive", not cleanup["survivors"],
             cleanup["survivors"])
    return sc.finalize(passed)


def scenario_startup_assign_failure(keep: bool = False,
                                    evidence_dir: Path | None = None) -> dict:
    """Injected guard-job assignment failure must fail closed.

    The runner is told to simulate AssignProcessToJobObject failing for the
    PTY root (cmd.exe).  Expected: no running endpoint is published, the host
    never attaches/leases, the runner exits non-zero, and only probe-owned
    descendants are cleaned up.
    """
    sc = Scenario("s6_startup_assign_failure", keep=keep, evidence_dir=evidence_dir)
    try:
        sc.spawn_supervisor("s6", extra_args=["--fail-inject-assign", "cmd.exe"])
        rc = sc.wait_supervisor_exit(timeout=40)
        sc.check("s6 supervisor reports startup failure (rc=4)", rc == 4, rc)
        exit_file = sc.root / "supervisor_exit.json"
        sup_exit = lib.read_json(exit_file) if exit_file.exists() else None
        sc.evidence["supervisorExit"] = sup_exit
        sc.check("s6 supervisor exit mode is startup_failed_assign",
                 bool(sup_exit) and sup_exit.get("mode") == "startup_failed_assign",
                 sup_exit)
        sc.check("s6 host never reached ready/lease state",
                 not (sc.root / "supervisor_ready.json").exists())
        endpoint = lib.read_json(sc.root / "runtime.json")
        sc.evidence["runtimeEndpoint"] = endpoint
        sc.check("s6 no running endpoint was published",
                 endpoint.get("status") == "startup_failed"
                 and endpoint.get("port") is None,
                 {"status": endpoint.get("status"), "port": endpoint.get("port")})
        failures = endpoint.get("failures") or []
        sc.check("s6 failure record names the injected member",
                 any("cmd.exe" in str(item.get("exe", "")).lower() for item in failures),
                 failures)
        runner_pid = endpoint.get("pid")
        sc.known["runner"] = {
            "pid": runner_pid,
            "createTimeFiletime": endpoint.get("processCreatedAtFiletime"),
        }
        sc.all_recorded[runner_pid] = {
            "pid": runner_pid,
            "createTimeFiletime": endpoint.get("processCreatedAtFiletime"),
        }
        ok, waited = lib.wait_until(lambda: not sc.alive_same("runner"), timeout=10)
        sc.check("s6 runner exited after failing closed", ok, f"{waited:.2f}s")
        pty_root = (endpoint.get("pty") or {}).get("rootPid")
        sc.check("s6 PTY root process is gone", not lib.process_running(pty_root),
                 pty_root)
        final_file = sc.root / "runner_startup_failure_final.json"
        final = lib.read_json(final_file) if final_file.exists() else None
        sc.evidence["runnerStartupFailureFinal"] = final
        sc.check("s6 cleanup touched only runner-owned descendants and left none",
                 bool(final) and not final.get("leftoverAfterSweep"),
                 final)
    except Exception as exc:
        sc.check("scenario raised an unexpected exception", False,
                 f"{type(exc).__name__}: {exc}")
    except BaseException:
        sc.cleanup()
        raise
    cleanup = sc.cleanup()
    passed = all(c["ok"] for c in sc.checks) and not cleanup["survivors"]
    sc.check("s6 cleanup left no owned process alive", not cleanup["survivors"],
             cleanup["survivors"])
    return sc.finalize(passed)


SCENARIOS = {
    "s1_default_normal_exit": scenario_default_normal_exit,
    "s2_default_crash": scenario_default_crash,
    "s3_detach_normal_exit": scenario_detach_normal_exit,
    "s4_controller_churn": scenario_controller_churn,
    "s5_detach_crash_tree": scenario_detach_crash_tree,
    "s6_startup_assign_failure": scenario_startup_assign_failure,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="all",
                        choices=["all", *SCENARIOS.keys()])
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--evidence-dir", default=str(EVIDENCE),
                        help="write evidence here (use a fresh directory to "
                             "avoid overwriting an earlier committed run)")
    args = parser.parse_args()
    evidence_dir = Path(args.evidence_dir)

    evidence_dir.mkdir(parents=True, exist_ok=True)
    env = env_facts()
    print(json.dumps({"env": env}, indent=2), flush=True)
    lib.write_json(evidence_dir / "environment.json", env)

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = {}
    for name in names:
        print(f"--- scenario {name}", flush=True)
        evidence = SCENARIOS[name](keep=args.keep, evidence_dir=evidence_dir)
        results[name] = {
            "passed": evidence.get("passed"),
            "checks": len(evidence.get("checks", [])),
            "failed": len(evidence.get("failedChecks", [])),
        }
    lib.write_json(evidence_dir / "summary.json", {
        "env": env, "results": results,
        "commands": {"python": PYTHON, "driver": str(Path(__file__).resolve())},
    })
    print(json.dumps(results, indent=2), flush=True)
    return 0 if all(r["passed"] for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

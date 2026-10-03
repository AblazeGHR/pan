"""v3（独立真机会话）：驱逐边界 gap/fresh-view + 追平重取 + resize 分列 + 独立心跳 +
正常收尾（closed/job_verified）+ 硬死布局（shell+sidecar 消亡）。

- 会话 A：自有 launcher（只读复用旧审查树本审查自产 launcher；本树 repo=3a）装配真机组合：
  stall + 双大文件 → 捕获 applied<first_retained 窗口 → read(applied) 显式 gap（gap[0]==applied、
  携带窗口内数据）→ reset_count==0、PTY 同 PID；追平 → 快照 cursor==total、read 无 gap；
  resize(30,100) 分列 + resize_wait 仅引擎；另一个独立连接在慢 snapshot 期间 5×心跳。
- 会话 B：硬死布局——同 handle 核验终止 runner → shell 与 sidecar 均消亡、退出码非 0。
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path

from v_common import (
    Session,
    process_dead,
    start_hard_watchdog,
    wait_dead,
    wait_until,
    write_evidence,
)
from packages.core.terminal import win_pipe


def session_a(tmp: Path) -> dict:
    out: dict = {}
    session = Session(tmp)
    try:
        client = session.client("cr2-owner")
        assert client.heartbeat()["status"] == "ok"
        session.note_shell()
        session.note_sidecar()
        pid0 = int(session.control.call("describe")["pid"])

        big = b"".join((b"CR2GAP-PAD-%05d " % i) + b"x" * 40 + b"\r\n" for i in range(3200))
        f1, f2 = tmp / "gap1.txt", tmp / "gap2.txt"
        f1.write_bytes(big)
        f2.write_bytes(big)

        assert session.control.call("test_stall", ms=3000) is True
        client.input(f'type "{f1}"\r'.encode("utf-8"))
        client.input(f'type "{f2}"\r'.encode("utf-8"))

        window: dict = {}

        def capture():
            total = int(client.describe()["total_bytes"])
            if total <= 262144 + 65536:
                return None
            applied = int(session.control.call("diagnostics")["applied_cursor"])
            retained = int(client.describe()["first_retained_seq"])
            if applied < retained:
                window.update({"total": total, "applied": applied, "first_retained": retained})
                return dict(window)
            return None

        captured = wait_until(capture, 30.0, interval=0.05)
        out["window"] = dict(window)
        page = client.read(int(window.get("applied", 0)))
        out["gap_read"] = {
            "gap": page["gap"],
            "gap_start_equals_applied": bool(page["gap"]) and int(page["gap"][0]) == int(window.get("applied", -1)),
            "data_nonempty": bool(page["data"]),
        }
        mid = session.control.call("diagnostics")
        out["fresh_view"] = {
            "reset_count": int(mid["reset_count"]),
            "no_auto_reset": int(mid["reset_count"]) == 0,
            "pty_same_pid": int(session.control.call("describe")["pid"]) == pid0,
            "shell_alive": not process_dead(session.shell_pid, session.shell_filetime),
        }

        # 追平重取
        total_stable = wait_until(
            lambda: (
                lambda a, t: t if a >= t else None
            )(int(session.control.call("diagnostics").get("applied_cursor", -1)),
              int(client.describe()["total_bytes"])),
            120.0,
            interval=0.3,
        )
        out["catch_up"] = {"total_stable": total_stable, "caught": bool(total_stable)}
        if total_stable:
            snap = client.snapshot(timeout_ms=8000)
            page2 = client.read(int(snap.get("cursor", -1)))
            out["refetch"] = {
                "cursor": int(snap.get("cursor", -1)),
                "cursor_equals_total": int(snap.get("cursor", -1)) == int(total_stable),
                "read_no_gap": page2["gap"] is None,
            }

        # resize 分列 + resize_wait 仅引擎
        res = client.resize(30, 100)
        detail = res.get("describe") or {}
        confirmed = session.control.call("resize_wait", rows=30, cols=100, timeout=8.0, wait=15.0)
        out["resize"] = {
            "status": res.get("status"),
            "pty_resize": detail.get("pty_resize"),
            "emulator_resize": detail.get("emulator_resize"),
            "engine_confirmed": bool(confirmed),
            "applied_resize": list(session.control.call("diagnostics").get("applied_resize") or []),
        }

        # 停滞 + 独立连接心跳（使用既有 keeper 之外的第二连接）
        other = session.client("cr2-other")
        assert session.control.call("test_stall", ms=1500) is True
        snap_res: dict = {}

        def slow():
            try:
                snap_res.update(client.snapshot(timeout_ms=500))
            except Exception as exc:  # noqa: BLE001
                snap_res["error"] = type(exc).__name__

        t = threading.Thread(target=slow, daemon=True)
        t.start()
        latencies: list[float] = []
        for _ in range(5):
            t0 = time.monotonic()
            assert other.heartbeat(timeout_ms=3000)["status"] == "ok"
            latencies.append(round(time.monotonic() - t0, 3))
            time.sleep(0.12)
        t.join(timeout=10.0)
        time.sleep(1.4)
        other.release_connection()
        out["stall_heartbeat"] = {
            "latencies": latencies,
            "independent_ok": max(latencies) < 0.8,
            "slow_snapshot_degraded": snap_res.get("recovery") != "full",
        }

        # 正常收尾
        stop = client.close()
        final = session.wait_report("finished", timeout=60)
        out["normal_stop"] = {
            "close_status": stop.get("status"),
            "exit_code": final.get("exit_code"),
            "emulator_close": final.get("emulator_close"),
            "shell_dead": wait_dead(session.shell_pid, 10.0, session.shell_filetime)
            if session.shell_pid else None,
            "sidecar_dead": wait_dead(session.sidecar_pid, 12.0, session.sidecar_filetime)
            if session.sidecar_pid else None,
        }
    finally:
        out["cleanup_trace"] = session.cleanup()
    return out


def session_b(tmp: Path) -> dict:
    session = Session(tmp)
    try:
        client = session.client("cr2-owner")
        assert client.heartbeat()["status"] == "ok"
        session.note_shell()
        session.note_sidecar()
        trace = win_pipe.terminate_verified_process(session.runner_pid, session.runner_filetime)
        shell_dead = wait_dead(session.shell_pid, 12.0, session.shell_filetime)
        sidecar_dead = wait_dead(session.sidecar_pid, 14.0, session.sidecar_filetime)
        code = session.proc.wait(timeout=20)
        try:
            client.release_connection()
        except Exception:  # noqa: BLE001
            pass
        return {
            "runner_terminate": trace,
            "shell_dead": shell_dead,
            "sidecar_dead": sidecar_dead,
            "launcher_exit_code_nonzero": code != 0,
            "kernel_guard_note": "已测布局；不泛化",
        }
    finally:
        session.cleanup()


def main() -> int:
    watchdog = start_hard_watchdog("v3", 420)
    tmp = Path(tempfile.mkdtemp(prefix="pan-cr2v3-"))
    a = session_a(tmp)
    b = session_b(tmp)
    out = {"session_a": a, "session_b": b}
    out["verdict"] = {
        "eviction_window_captured": bool(a.get("window")),
        "gap_explicit": bool((a.get("gap_read") or {}).get("gap"))
        and bool((a.get("gap_read") or {}).get("gap_start_equals_applied")),
        "fresh_view_no_auto_reset_pty_alive": all(
            (a.get("fresh_view") or {}).get(k) for k in ("no_auto_reset", "pty_same_pid", "shell_alive")
        ),
        "catch_up_refetch_no_gap": bool((a.get("refetch") or {}).get("cursor_equals_total"))
        and bool((a.get("refetch") or {}).get("read_no_gap")),
        "resize_split_engine_only": (a.get("resize") or {}).get("pty_resize") == "ok"
        and bool((a.get("resize") or {}).get("engine_confirmed")),
        "independent_heartbeat_ok": bool((a.get("stall_heartbeat") or {}).get("independent_ok")),
        "normal_stop_clean": (a.get("normal_stop") or {}).get("close_status") == "exited"
        and bool(((a.get("normal_stop") or {}).get("emulator_close") or {}).get("closed"))
        and bool((a.get("normal_stop") or {}).get("shell_dead"))
        and bool((a.get("normal_stop") or {}).get("sidecar_dead")),
        "hard_kill_layout": bool(b.get("shell_dead")) and bool(b.get("sidecar_dead"))
        and bool(b.get("launcher_exit_code_nonzero")),
        "zero_residue": not any(
            isinstance(v, dict) and v.get("terminated") for v in (a.get("cleanup_trace") or {}).values()
        ),
    }
    write_evidence("v3_live_session", out)
    watchdog.cancel()
    print(json.dumps(out["verdict"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded Ctrl-C delivery probe (R1): three candidates against a real
SetConsoleCtrlHandler child that does NOT read stdin.

Official facts used (learn.microsoft.com):
- GenerateConsoleCtrlEvent(CTRL_C_EVENT, nonzero) returns success but the signal is
  NOT received by the named group; dwProcessGroupId=0 -> all processes sharing the
  CALLING process's console. So an injector must AttachConsole(target) first, and
  must abort if attach fails (never broadcast to its own console).
- SetConsoleCtrlHandler(NULL, TRUE) makes the calling process ignore Ctrl+C
  (self-diagnostic only); NULL/FALSE restores default processing.
- WriteConsoleInput writes records into the console input buffer (needs GENERIC_WRITE
  console input handle; only reachable after AttachConsole).

Candidates:
  A) backend.write(b"\x03")                        (repro of the negative)
  B) helper: FreeConsole -> AttachConsole(child) -> GenerateConsoleCtrlEvent(0, 0)
  C) helper: FreeConsole -> AttachConsole(child) -> WriteConsoleInput(Ctrl+C down/up)

Child: real ctypes SetConsoleCtrlHandler writes EVENT:<n> to a marker file and returns
TRUE (interrupt, not kill); prints console mode + pid; then computes (never reads stdin)
with heartbeats so we can verify it keeps running.

Own processes only; child is spawn via ConPtyBackend (Job-guarded), cleaned via
terminate(True)->wait_dead->close; helper is a self-built python subprocess.
Evidence JSON -> .../backend/evidence/r2/.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

WT = r"D:/project/pan-worktrees/terminal-backend-implement-20261003"
sys.path.insert(0, WT)
PY = sys.executable
TMP = Path(os.environ.get("TEMP", "C:/")) / "pan-rework-ctrl-c"
TMP.mkdir(parents=True, exist_ok=True)
EVID = Path(WT) / "audit" / "terminal" / "implementation" / "backend" / "evidence" / "r2"
EVID.mkdir(parents=True, exist_ok=True)

from packages.core.terminal.backend import ConPtyBackend  # noqa: E402

CHILD_CODE = r"""
import ctypes, os, sys, time
k = ctypes.windll.kernel32
marker = sys.argv[1]
heart = sys.argv[2]
HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
def handler(event):
    with open(marker, 'a') as f:
        f.write('EVENT:%d\n' % event)
    return 1
_h = HANDLER(handler)
k.SetConsoleCtrlHandler(_h, True)
mode = ctypes.c_uint(0)
k.GetConsoleMode(k.GetStdHandle(-10), ctypes.byref(mode))
open(heart, 'w').write('ready pid=%d mode=0x%X\n' % (os.getpid(), mode.value & 0xFFFFFFFF))
print('ready pid=%d mode=0x%X' % (os.getpid(), mode.value & 0xFFFFFFFF), flush=True)
i = 0
while True:
    i += 1
    with open(heart, 'a') as f:
        f.write('beat %d\n' % i)
    time.sleep(0.25)
"""

HELPER_CODE = r"""
import ctypes, sys, time
mode, pid = sys.argv[1], int(sys.argv[2])
k = ctypes.windll.kernel32
out = {}
def err():
    return int(k.GetLastError())
out['freeconsole'] = bool(k.FreeConsole())
attached = bool(k.AttachConsole(pid))
out['attach'] = attached
out['attach_err'] = err()
if not attached:
    print('HELPER-JSON' + __import__('json').dumps(out)); raise SystemExit(2)
k.SetConsoleCtrlHandler(None, True)  # helper ignores Ctrl+C (it shares the console now)
buf = (ctypes.c_uint * 64)()
n = k.GetConsoleProcessList(buf, 64)
out['console_pids'] = [int(buf[i]) for i in range(n)]
out['target_in_console'] = pid in out['console_pids']
if not out['target_in_console']:
    print('HELPER-JSON' + __import__('json').dumps(out)); raise SystemExit(3)
if mode == 'generate':
    ok = bool(k.GenerateConsoleCtrlEvent(0, 0))
    out['generate_ok'] = ok
    out['generate_err'] = err()
elif mode == 'writeinput':
    class KEY_EVENT_RECORD(ctypes.Structure):
        _fields_ = [("bKeyDown", ctypes.c_int), ("wRepeatCount", ctypes.c_ushort),
                    ("wVirtualKeyCode", ctypes.c_ushort), ("wVirtualScanCode", ctypes.c_ushort),
                    ("uChar", ctypes.c_wchar), ("dwControlKeyState", ctypes.c_uint)]
    class INPUT_RECORD(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("pad", ctypes.c_byte * 16)]
        _fields_ = [("EventType", ctypes.c_ushort), ("Event", _U)]
    # conin$：attach 后重新打开可写控制台输入句柄（GetStdHandle 可能仍是 FreeConsole 前的旧值）
    k.CreateFileW.restype = ctypes.c_void_p
    conin = k.CreateFileW('CONIN$', 0x80000000 | 0x40000000, 0x1 | 0x2, None, 3, 0, None)
    out['conin_handle'] = int(conin or 0)
    out['conin_err'] = err()
    h = conin or k.GetStdHandle(-10)
    recs = (INPUT_RECORD * 2)()
    for i, down in enumerate((1, 0)):
        recs[i].EventType = 0x0001
        ke = recs[i].Event.KeyEvent
        ke.bKeyDown = down
        ke.wRepeatCount = 1
        ke.wVirtualKeyCode = 0x43  # 'C'
        ke.wVirtualScanCode = 0x2E
        ke.uChar = '\x03'
        ke.dwControlKeyState = 0x0008  # LEFT_CTRL_PRESSED
    written = ctypes.c_uint(0)
    ctypes.set_last_error(0)
    ok = bool(k.WriteConsoleInputW(h, recs, 2, ctypes.byref(written)))
    out['writeinput_ok'] = ok
    out['writeinput_written'] = int(written.value)
    out['writeinput_err'] = err()
print('HELPER-JSON' + __import__('json').dumps(out))
"""


def read_until(backend, needle: bytes, timeout: float = 10.0):
    buf = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = backend.read(65536, timeout=min(0.2, max(0.0, deadline - time.monotonic())))
        except EOFError:
            return buf, "eof"
        if chunk:
            buf += chunk
            if needle in buf:
                return buf, "found"
    return buf, "timeout"


def run_case(case: str, action) -> dict:
    marker = TMP / f"{case}.marker"
    heart = TMP / f"{case}.heart"
    for p in (marker, heart):
        if p.exists():
            p.unlink()
    child = TMP / "ctrl_child.py"
    child.write_text(CHILD_CODE, encoding="utf-8")
    helper = TMP / "ctrl_helper.py"
    helper.write_text(HELPER_CODE, encoding="utf-8")

    b = ConPtyBackend.spawn(
        [PY, str(child), str(marker), str(heart)], cwd=str(TMP), rows=30, cols=100
    )
    result = {"case": case}
    try:
        buf, why = read_until(b, b"ready pid=")
        result["ready"] = why
        pid = int(buf.decode("utf-8", "replace").split("ready pid=")[1].split()[0])
        result["child_pid"] = pid
        mode_txt = buf.decode("utf-8", "replace").split("mode=")[1].split()[0]
        result["console_mode"] = mode_txt
        result["mode_has_processed_input"] = bool(int(mode_txt, 16) & 0x0001)
        time.sleep(0.3)
        result["action"] = action(pid, helper)
        time.sleep(1.0)
        result["marker_events"] = marker.read_text().count("EVENT:") if marker.exists() else 0
        result["child_alive"] = bool(b.alive())
        beats_before = heart.read_text().count("beat") if heart.exists() else 0
        time.sleep(0.6)
        beats_after = heart.read_text().count("beat") if heart.exists() else 0
        result["heartbeats_continue"] = beats_after > beats_before
        result["delivered"] = result["marker_events"] > 0
    finally:
        try:
            b.terminate(True)
            b.wait_dead(5.0)
            result["cleanup"] = {"close": b.close().get("closed")}
        except Exception as exc:  # noqa: BLE001
            result["cleanup_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return result


def helper_run(mode: str):
    def _action(pid: int, helper: Path):
        proc = subprocess.run(
            [PY, str(helper), mode, str(pid)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        out = {"helper_rc": proc.returncode}
        for line in proc.stdout.splitlines():
            if line.startswith("HELPER-JSON"):
                out.update(json.loads(line[len("HELPER-JSON"):]))
        out["helper_stderr_tail"] = proc.stderr[-200:]
        return out

    return _action


def raw_action(pid: int, helper: Path):
    return {"raw": "backend.write(b'\\x03')"}


READING_CHILD_CODE = r"""
import ctypes, sys
k = ctypes.windll.kernel32
marker = sys.argv[1]
HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
def handler(event):
    open(marker, 'a').write('EVENT:%d\n' % event)
    return 1
_h = HANDLER(handler)
k.SetConsoleCtrlHandler(_h, True)
print('READY-RL', flush=True)
while True:
    line = sys.stdin.readline()
    if line == '':
        print('READ-RELEASED', flush=True)
        continue
    if line.strip() == 'quit':
        print('QUIT', flush=True)
        break
    print('LINE ' + line.strip(), flush=True)
"""


def run_case_c3(helper: Path) -> dict:
    """C3：读取中的子进程 + attach + WriteConsoleInput（排除“记录只在被读取时触发”）。"""
    marker = TMP / "C3.marker"
    if marker.exists():
        marker.unlink()
    child = TMP / "ctrl_child_rl.py"
    child.write_text(READING_CHILD_CODE, encoding="utf-8")
    b = ConPtyBackend.spawn([PY, str(child), str(marker)], cwd=str(TMP), rows=30, cols=100)
    result = {"case": "C3_attach_writeinput_reading_child"}
    try:
        buf, why = read_until(b, b"READY-RL")
        result["ready"] = why
        result["child_pid"] = b.pid
        proc = subprocess.run(
            [PY, str(helper), "writeinput", str(b.pid)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        result["helper_rc"] = proc.returncode
        for line in proc.stdout.splitlines():
            if line.startswith("HELPER-JSON"):
                result["helper"] = json.loads(line[len("HELPER-JSON"):])
        time.sleep(1.0)
        buf2, why2 = read_until(b, b"READ-RELEASED", timeout=3.0)
        result["read_released"] = why2 == "found"
        result["marker_events"] = marker.read_text().count("EVENT:") if marker.exists() else 0
        result["delivered"] = result["marker_events"] > 0
        result["child_alive"] = bool(b.alive())
    finally:
        try:
            b.terminate(True)
            b.wait_dead(5.0)
            result["cleanup"] = {"close": b.close().get("closed")}
        except Exception as exc:  # noqa: BLE001
            result["cleanup_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return result


def main() -> int:
    results = []
    # A) raw 0x03 重新确认（对照）
    marker = TMP / "A.marker"
    heart = TMP / "A.heart"
    for p in (marker, heart):
        if p.exists():
            p.unlink()
    child = TMP / "ctrl_child.py"
    child.write_text(CHILD_CODE, encoding="utf-8")
    b = ConPtyBackend.spawn([PY, str(child), str(marker), str(heart)], cwd=str(TMP), rows=30, cols=100)
    case_a: dict = {"case": "A_raw_0x03"}
    try:
        buf, why = read_until(b, b"ready pid=")
        case_a["ready"] = why
        pid = int(buf.decode("utf-8", "replace").split("ready pid=")[1].split()[0])
        case_a["child_pid"] = pid
        n = b.write(b"\x03")
        case_a["bytes_written"] = n
        time.sleep(1.2)
        case_a["marker_events"] = marker.read_text().count("EVENT:") if marker.exists() else 0
        case_a["child_alive"] = bool(b.alive())
        case_a["delivered"] = case_a["marker_events"] > 0
    finally:
        try:
            b.terminate(True)
            b.wait_dead(5.0)
            case_a["cleanup"] = {"close": b.close().get("closed")}
        except Exception as exc:  # noqa: BLE001
            case_a["cleanup_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    results.append(case_a)

    # B) helper attach + GenerateConsoleCtrlEvent(0, 0)
    results.append(run_case("B_attach_generate", helper_run("generate")))
    # C) helper attach + WriteConsoleInput(Ctrl+C key down/up)（非读取子进程）
    results.append(run_case("C_attach_writeinput", helper_run("writeinput")))
    # C3) 同上，但子进程正在 readline（排除“读取时才触发”）
    results.append(run_case_c3(TMP / "ctrl_helper.py"))

    payload = {"probe": "ctrl_c_candidates", "results": results}
    out = EVID / f"probe-ctrl-c-candidates-{os.getpid()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, default=str))
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

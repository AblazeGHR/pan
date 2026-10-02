"""Codex native-TUI no-interruption feasibility probe (Pan Terminal/PTY exploration).

Question under test
-------------------
Pan runs Codex as ``[node, <npm>/bin/codex.js] app-server --stdio`` and owns the
child's stdin/stdout pipes (packages/core/adapters/codex/app_server_wrapper.py,
``AppServer.start``). Can a *native* Codex TUI attach to that same live backend
without interrupting the backend process, its in-flight turn, or the structured
event stream another client (Pan) is consuming -- and then hand control back?

This probe builds an equivalent-but-isolated backend and measures that.

Isolation guarantees
--------------------
* Every run uses a fresh temporary ``CODEX_HOME``; the user's real ``~/.codex``
  is never read or written (no sessions, no state DBs, no daemon state).
* The model provider is a *local blackhole* TCP listener owned by this probe.
  It accepts and never answers, so the turn stays ``inProgress`` deterministically.
  No external network call, no cost, no real credential, no model output.
* The only credential ever created is a *synthetic placeholder* API key written
  by this probe into its own temp home, used solely to clear the TUI's local
  sign-in gate. No real credential is read, copied, printed or modified.
* The app-server binds a free loopback port and a private CODEX_HOME, so it
  cannot collide with the user's managed app-server daemon
  (``app-server --listen unix:// --managed-daemon``) or any Pan worker.
* The probe only starts/stops processes it created itself. It never enumerates,
  connects to, or signals a process it did not spawn.

Scenarios
---------
``control``   TUI aimed at a port with no listener -> records what "not connected"
              looks like, so the main scenario's connection is provably real.
``main``      Full scenario: Pan-like client starts a turn, second structured
              client attaches, native TUI attaches mid-turn, TUI detaches;
              backend PID, in-flight turn and event stream must survive.
``tap``       Same as ``main`` but routes the TUI through a transparent
              JSON-RPC WebSocket relay so every frame the *TUI* sends/receives
              is recorded. TUI and app-server stay real; only a byte relay is
              inserted for observation.

Usage
-----
    E:/software/miniforge/python.exe audit/terminal/codex/probe_codex_remote_tui.py \
        --scenario tap --out audit/terminal/codex/evidence

Exit code is 0 only when every assertion of the selected scenario holds.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

# --- Reuse the production resolution logic so the probe matches Pan exactly. ---
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from packages.core.adapters.codex.adapter import (  # noqa: E402
    _resolve_codex_js,
    _resolve_codex_node,
)

# pywinpty lives in a throwaway --target dir, never in the global environment.
PYWINPTY_DIR = os.environ.get("PAN_PROBE_PYWINPTY", r"C:\Users\14709\AppData\Local\Temp\codex_probe_env")
if PYWINPTY_DIR not in sys.path:
    sys.path.insert(0, PYWINPTY_DIR)

import websockets  # noqa: E402

PLACEHOLDER_KEY = "probe-synthetic-placeholder-key-not-a-credential"
TUI_COLS, TUI_ROWS = 120, 34


# ----------------------------------------------------------------------------- utils
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def proc_identity(pid: int | None) -> dict:
    """PID + creation time -- the identity check the brief asks for."""
    if pid is None:
        return {"pid": None}
    try:
        import psutil

        p = psutil.Process(pid)
        return {
            "pid": pid,
            "create_time": round(p.create_time(), 3),
            "create_time_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.create_time())),
            "name": p.name(),
        }
    except Exception as exc:  # pragma: no cover
        return {"pid": pid, "error": repr(exc)}


def children_of(pid: int) -> list[int]:
    import psutil

    try:
        return [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except Exception:
        return []


def strip_ansi(text: str) -> str:
    import re

    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b[()][A-Z0-9]", "", text)
    text = re.sub(r"\x1b[=>]", "", text)
    return text.replace("\r", "")


class Blackhole:
    """Loopback listener that accepts connections and never answers.

    Holds the model request open so the turn stays ``inProgress`` for as long as
    the probe needs, with no external traffic and no credentials.
    """

    def __init__(self) -> None:
        self.port = free_port()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", self.port))
        self._srv.listen(16)
        self._conns: list[socket.socket] = []
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        self._srv.settimeout(0.5)
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
                self._conns.append(conn)
            except socket.timeout:
                continue
            except OSError:
                return

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def connections(self) -> int:
        return len(self._conns)

    def close(self) -> None:
        self._stop = True
        for c in self._conns:
            try:
                c.close()
            except Exception:
                pass
        try:
            self._srv.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------- backend
class AppServer:
    """Isolated ``codex app-server --listen ws://127.0.0.1:PORT``."""

    def __init__(self, home: str, port: int, blackhole: Blackhole, log: list[str]) -> None:
        self.home, self.port, self.blackhole, self.log = home, port, blackhole, log
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def command(self) -> list[str]:
        provider = (
            f'model_providers.probe_bh={{name="probe_bh",base_url="{self.blackhole.base_url}",'
            f'wire_api="responses"}}'
        )
        return [
            _resolve_codex_node(), _resolve_codex_js(),
            "-c", 'model_provider="probe_bh"', "-c", provider,
            "app-server", "--listen", self.url,
        ]

    def start(self) -> None:
        env = dict(os.environ)
        env["CODEX_HOME"] = self.home
        cmd = self.command()
        self.proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.log.append(f"[spawn] {' '.join(cmd)} (pid={self.proc.pid})")
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.log.append("[app-server] " + line.decode("utf-8", "replace").rstrip())

    def wait_ready(self, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/readyz", timeout=1) as r:
                    if r.status == 200:
                        return True
            except Exception:
                time.sleep(0.4)
        return False

    def identity(self) -> dict:
        pid = self.proc.pid if self.proc else -1
        return {
            "node": proc_identity(pid),
            "codex_children": [proc_identity(c) for c in children_of(pid)],
        }

    def stop(self) -> dict:
        result = {"was_running": bool(self.proc and self.proc.poll() is None)}
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        result["returncode"] = self.proc.poll() if self.proc else None
        return result


class JsonRpcClient:
    """Minimal app-server JSON-RPC client over the WebSocket transport."""

    def __init__(self, name: str) -> None:
        self.name, self.ws, self.events, self._pending = name, None, [], {}
        self._id = 0

    async def connect(self, url: str, timeout: float = 30.0) -> "JsonRpcClient":
        self.ws = await websockets.connect(url, max_size=128 * 1024 * 1024, open_timeout=timeout)
        asyncio.create_task(self._reader())
        return self

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                self.events.append(msg)
                if "id" in msg and msg["id"] in self._pending:
                    fut = self._pending.pop(msg["id"])
                    if not fut.done():
                        fut.set_result(msg)
        except Exception as exc:
            self.events.append({"__stream_closed__": repr(exc)})

    async def call(self, method: str, params: dict | None = None, timeout: float = 90.0) -> dict:
        self._id += 1
        rid = self._id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self.ws.send(json.dumps({"method": method, "id": rid, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout)

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self.ws.send(json.dumps({"method": method, "params": params or {}}))

    async def handshake(self) -> dict:
        init = await self.call("initialize", {"clientInfo": {"name": self.name, "version": "0.1.0"}})
        await self.notify("initialized")
        return init

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()

    def methods(self) -> list[str]:
        return [e.get("method") for e in self.events if e.get("method")]

    def correlated(self) -> list[str]:
        """turn/item events that carry the thread id -- i.e. real stream traffic."""
        return [
            e.get("method") for e in self.events
            if e.get("method") and e["method"].startswith(("turn/", "item/"))
        ]

    @property
    def stream_closed(self) -> bool:
        return any("__stream_closed__" in e for e in self.events)


# ----------------------------------------------------------------------------- ws tap
class WsTap:
    """Byte-level relay recording JSON-RPC frames in both directions.

    The TUI talks to this relay; the relay talks to the real app-server. Nothing
    is faked -- only observed. This proves *what the TUI actually sent*.

    The relay uses a polled receive loop plus a ``_closing`` flag so that
    ``stop()`` can always terminate it; a blocking ``async for`` would leave the
    handler (and therefore ``wait_closed()``) pending forever.
    """

    POLL_TIMEOUT = 0.5

    def __init__(self, upstream_port: int) -> None:
        self.upstream_port = upstream_port
        self.port = free_port()
        self.frames: list[dict] = []
        self._server = None
        self._closing = False
        self._conns: set = set()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def _handler(self, down_ws) -> None:
        try:
            up_ws = await websockets.connect(f"ws://127.0.0.1:{self.upstream_port}", max_size=128 * 1024 * 1024)
        except Exception as exc:
            self.frames.append({"dir": "tap-error", "error": repr(exc)})
            await down_ws.close()
            return
        self._conns.update({down_ws, up_ws})

        async def pump(src, dst, direction):
            while not self._closing:
                try:
                    raw = await asyncio.wait_for(src.recv(), timeout=self.POLL_TIMEOUT)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    self.frames.append({"dir": direction, "closed": repr(exc)})
                    return
                try:
                    self.frames.append({"dir": direction, "json": json.loads(raw)})
                except Exception:
                    self.frames.append({"dir": direction, "text": raw[:400].decode("utf-8", "replace")})
                try:
                    await dst.send(raw)
                except Exception as exc:
                    self.frames.append({"dir": direction, "send-failed": repr(exc)})
                    return

        await asyncio.gather(
            pump(down_ws, up_ws, "tui->server"),
            pump(up_ws, down_ws, "server->tui"),
            return_exceptions=True,
        )
        self._conns.discard(down_ws)
        self._conns.discard(up_ws)

    async def start(self) -> None:
        self._server = await websockets.serve(self._handler, "127.0.0.1", self.port)

    async def stop(self) -> None:
        self._closing = True
        for conn in list(self._conns):
            try:
                await asyncio.wait_for(conn.close(), timeout=2)
            except Exception:
                try:
                    conn.transport.abort()
                except Exception:
                    pass
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=5)
            except asyncio.TimeoutError:
                pass

    def tui_requests(self) -> list[str]:
        return [
            f["json"].get("method") for f in self.frames
            if f.get("dir") == "tui->server" and isinstance(f.get("json"), dict) and f["json"].get("method")
        ]

    def tui_received_methods(self) -> list[str]:
        return [
            f["json"].get("method") for f in self.frames
            if f.get("dir") == "server->tui" and isinstance(f.get("json"), dict) and f["json"].get("method")
        ]


# ----------------------------------------------------------------------------- pty TUI
class PtyTui:
    """Runs the real ``codex --remote`` native TUI inside a Windows ConPTY."""

    def __init__(self, url: str, home: str, cwd: str, extra_args: list[str] | None = None) -> None:
        self.url, self.home, self.cwd = url, home, cwd
        self.extra_args = list(extra_args or [])
        self.proc = None
        self.raw: list[str] = []
        self._reading = threading.Event()

    def start(self) -> dict:
        from winpty import PtyProcess

        env = dict(os.environ)
        env["CODEX_HOME"] = self.home
        env["TERM"] = "xterm-256color"
        cmd = [
            _resolve_codex_node(), _resolve_codex_js(),
            # The in-app "0.159.2 -> 0.160.0" modal blocks the UI on startup and
            # is unrelated to the attach question; documented config key.
            "-c", "check_for_update_on_startup=false",
            "--remote", self.url, *self.extra_args,
        ]
        self.proc = PtyProcess.spawn(cmd, env=env, dimensions=(TUI_ROWS, TUI_COLS), cwd=self.cwd)
        self._reading.set()
        threading.Thread(target=self._read_loop, daemon=True).start()
        time.sleep(1.0)
        return {"pid": self.proc.pid, "cmd": cmd, "url": self.url}

    def _read_loop(self) -> None:
        while self._reading.is_set():
            try:
                data = self.proc.read(8192)
            except Exception as exc:
                self.raw.append(f"<<read-error {exc!r}>>")
                return
            if not data:
                return
            self.raw.append(data)

    def screen(self) -> str:
        return strip_ansi("".join(self.raw))

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def alive(self) -> bool:
        try:
            return bool(self.proc and self.proc.isalive())
        except Exception:
            return False

    def write(self, data: str) -> None:
        self.proc.write(data)

    def close(self) -> None:
        self._reading.clear()
        for action in (
            lambda: self.proc.write("\x03"),
            lambda: time.sleep(1.0),
            lambda: self.proc.terminate(force=True) if self.proc.isalive() else None,
            lambda: self.proc.close(),
        ):
            try:
                action()
            except Exception:
                pass


# ----------------------------------------------------------------------------- fixtures
def make_home() -> str:
    home = tempfile.mkdtemp(prefix="codex_tui_probe_")
    os.makedirs(os.path.join(home, "probe-cwd"), exist_ok=True)
    return home


def seed_placeholder_credentials(home: str, log: list[str]) -> dict:
    """Write a SYNTHETIC api key into the probe's own temp CODEX_HOME.

    No real credential is involved. This only clears the TUI's local sign-in
    gate so the native UI becomes reachable.
    """
    env = dict(os.environ)
    env["CODEX_HOME"] = home
    base = [_resolve_codex_node(), _resolve_codex_js()]
    login = subprocess.run(
        base + ["login", "--with-api-key"],
        input=(PLACEHOLDER_KEY + "\n").encode(),
        env=env, capture_output=True, timeout=120,
    )
    status = subprocess.run(base + ["login", "status"], env=env, capture_output=True, timeout=120)
    info = {
        "note": "synthetic placeholder key, generated by the probe; not a real credential",
        "login_rc": login.returncode,
        "login_stderr": login.stderr.decode("utf-8", "replace").strip()[:200],
        "status_rc": status.returncode,
        "status_stdout": status.stdout.decode("utf-8", "replace").strip()[:200],
        "auth_json_present": os.path.exists(os.path.join(home, "auth.json")),
    }
    log.append(f"[credentials] {json.dumps(info)}")
    return info


async def turn_status(client: JsonRpcClient, thread_id: str) -> list:
    read = await client.call("thread/read", {"threadId": thread_id, "includeTurns": True})
    turns = ((read.get("result") or {}).get("thread") or {}).get("turns") or []
    return [(t.get("id"), t.get("status")) for t in turns]


# ----------------------------------------------------------------------------- scenarios
async def scenario_control(out_dir: str, log: list[str]) -> dict:
    """TUI aimed at a dead port: establishes what 'not connected' looks like."""
    home = make_home()
    dead_port = free_port()
    tui = PtyTui(f"ws://127.0.0.1:{dead_port}", home, os.path.join(home, "probe-cwd"))
    result: dict = {
        "scenario": "control",
        "purpose": "reference behaviour for an unreachable remote app-server",
        "dead_port": dead_port,
    }
    try:
        result["tui"] = tui.start()
        time.sleep(12)
        result["tui_alive_after_12s"] = tui.alive()
        screen = tui.screen()
        result["screen"] = screen[-2500:]
        result["connect_error_visible"] = "failed to connect" in screen.lower()
    finally:
        tui.close()
        time.sleep(1)
        result["tui_alive_after_close"] = tui.alive()
        shutil.rmtree(home, ignore_errors=True)
        result["home_removed"] = not os.path.exists(home)
        result["log"] = log
    return result


async def scenario_subscriber(out_dir: str, log: list[str]) -> dict:
    """Isolate *why* a resumed thread's live turn gets interrupted.

    No TUI is involved. A plain second JSON-RPC client resumes an active thread
    and then disconnects, while a second thread with an equally live turn is
    left completely untouched as a within-run control.

    If the control thread survives and the resumed one does not, the interrupt
    is caused by the resume/subscribe lifecycle itself rather than by any TUI
    keystroke, Ctrl-C handling or terminal behaviour.
    """
    home = make_home()
    port = free_port()
    blackhole = Blackhole()
    result: dict = {
        "scenario": "subscriber",
        "purpose": "attribute the interrupt of a live turn: TUI keystroke vs resume/disconnect lifecycle",
        "isolation": {"codex_home": home, "listener_port": port, "provider": blackhole.base_url},
    }
    srv = AppServer(home, port, blackhole, log)
    client_a = client_c = None
    checks: list[dict] = []

    def check(name: str, ok: bool, detail=None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        srv.start()
        check("app_server_ready", srv.wait_ready(), srv.url)
        result["backend_pid"] = srv.proc.pid

        client_a = await JsonRpcClient("pan_probe_a").connect(srv.url)
        await client_a.handshake()

        # Two independent live turns: one control, one to be resumed by a peer.
        control_thread = (await client_a.call("thread/start", {
            "cwd": os.path.join(home, "probe-cwd"), "threadSource": "pan_probe",
        })).get("result", {}).get("thread", {}).get("id")
        control_turn = (await client_a.call("turn/start", {
            "threadId": control_thread, "input": [{"type": "text", "text": "CONTROL: never resumed"}],
        })).get("result", {}).get("turn", {}).get("id")

        target_thread = (await client_a.call("thread/start", {
            "cwd": os.path.join(home, "probe-cwd"), "threadSource": "pan_probe",
        })).get("result", {}).get("thread", {}).get("id")
        target_turn = (await client_a.call("turn/start", {
            "threadId": target_thread, "input": [{"type": "text", "text": "TARGET: peer will resume then leave"}],
        })).get("result", {}).get("turn", {}).get("id")

        result["control"] = {"thread": control_thread, "turn": control_turn}
        result["target"] = {"thread": target_thread, "turn": target_turn}
        await asyncio.sleep(4)
        result["before"] = {
            "control": await turn_status(client_a, control_thread),
            "target": await turn_status(client_a, target_thread),
        }
        check(
            "both_turns_live_before_peer",
            any(t[0] == control_turn and t[1] == "inProgress" for t in result["before"]["control"])
            and any(t[0] == target_turn and t[1] == "inProgress" for t in result["before"]["target"]),
            result["before"],
        )

        # Plain peer client resumes ONLY the target thread, then disconnects.
        client_c = await JsonRpcClient("peer_plain_ws").connect(srv.url)
        await client_c.handshake()
        resumed = await client_c.call("thread/resume", {"threadId": target_thread})
        result["peer_resume"] = "ok" if "result" in resumed else resumed.get("error")
        await asyncio.sleep(5)
        result["during_peer"] = {
            "control": await turn_status(client_a, control_thread),
            "target": await turn_status(client_a, target_thread),
        }

        await client_c.close()
        client_c = None
        await asyncio.sleep(6)

        result["after_peer_left"] = {
            "control": await turn_status(client_a, control_thread),
            "target": await turn_status(client_a, target_thread),
        }
        result["peer_requests"] = client_a.methods()[-6:]

        control_alive = any(
            t[0] == control_turn and t[1] == "inProgress" for t in result["after_peer_left"]["control"]
        )
        target_alive = any(
            t[0] == target_turn and t[1] == "inProgress" for t in result["after_peer_left"]["target"]
        )
        check("control_turn_still_in_progress", control_alive, result["after_peer_left"]["control"])
        check(
            "resumed_turn_interrupted_by_peer_disconnect",
            not target_alive,
            result["after_peer_left"]["target"],
        )
        result["conclusion"] = (
            "control thread survived while the peer-resumed thread did not -> the interrupt is "
            "triggered by the resume/disconnect lifecycle, not by the native TUI"
            if control_alive and not target_alive
            else "inconclusive: control_alive=%s target_alive=%s" % (control_alive, target_alive)
        )
        result["pan_client_event_tail"] = [json.dumps(e)[:300] for e in client_a.events[-14:]]

    finally:
        result["checks"] = checks
        if client_c is not None:
            try:
                await asyncio.wait_for(client_c.close(), timeout=5)
            except Exception:
                pass
        if client_a is not None:
            try:
                await asyncio.wait_for(client_a.close(), timeout=5)
            except Exception:
                pass
        result["backend_stop"] = srv.stop()
        blackhole.close()
        time.sleep(1)
        try:
            with socket.socket() as s:
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
            result["port_still_listening"] = True
        except Exception:
            result["port_still_listening"] = False
        check("listener_released", not result["port_still_listening"])
        shutil.rmtree(home, ignore_errors=True)
        result["home_removed"] = not os.path.exists(home)
        check("temp_home_removed", result["home_removed"])
        result["passed"] = all(c["ok"] for c in checks)
        result["failed_checks"] = [c for c in checks if not c["ok"]]
        result["log"] = log

    return result


async def scenario_exit(out_dir: str, log: list[str], exit_mode: str) -> dict:
    """Does *how* the attached TUI leaves decide the fate of the live turn?

    ``ctrl-c`` sends ETX into the PTY first (what a user pressing Ctrl-C does);
    ``force``  only tears the PTY down, with no keystroke at all.

    Comparing the two separates "the user pressed Ctrl-C" from "the TUI process
    went away", which is the difference between a UX hazard and a transport
    lifecycle property.
    """
    home = make_home()
    port = free_port()
    blackhole = Blackhole()
    result: dict = {
        "scenario": f"exit-{exit_mode}",
        "exit_mode": exit_mode,
        "isolation": {"codex_home": home, "listener_port": port, "provider": blackhole.base_url},
    }
    srv = AppServer(home, port, blackhole, log)
    client_a = None
    tui = None
    checks: list[dict] = []

    def check(name: str, ok: bool, detail=None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        result["credentials"] = seed_placeholder_credentials(home, log)
        srv.start()
        check("app_server_ready", srv.wait_ready(), srv.url)
        backend_pid = srv.proc.pid

        client_a = await JsonRpcClient("pan_probe_a").connect(srv.url)
        await client_a.handshake()
        thread_id = (await client_a.call("thread/start", {
            "cwd": os.path.join(home, "probe-cwd"), "threadSource": "pan_probe",
        })).get("result", {}).get("thread", {}).get("id")
        turn_id = (await client_a.call("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": f"EXIT-MODE {exit_mode}"}],
        })).get("result", {}).get("turn", {}).get("id")
        result["thread_id"], result["turn_id"] = thread_id, turn_id
        await asyncio.sleep(3)

        tui = PtyTui(srv.url, home, os.path.join(home, "probe-cwd"), extra_args=["resume", thread_id])
        result["tui"] = tui.start()
        result["tui_identity"] = proc_identity(tui.pid)
        await asyncio.sleep(14)

        result["turn_status_during_attach"] = await turn_status(client_a, thread_id)
        result["tui_screen_tail"] = tui.screen()[-600:]
        check(
            "turn_in_progress_during_attach",
            any(t[0] == turn_id and t[1] == "inProgress" for t in result["turn_status_during_attach"]),
            result["turn_status_during_attach"],
        )
        check(
            "backend_pid_stable_during_attach",
            srv.identity()["node"]["pid"] == backend_pid,
            srv.identity()["node"]["pid"],
        )

        # --- the only difference between the two arms -------------------------
        if exit_mode == "ctrl-c":
            tui.write("\x03")
        tui.close()
        await asyncio.sleep(5)

        result["turn_status_after_exit"] = await turn_status(client_a, thread_id)
        interrupted = any(t[0] == turn_id and t[1] == "interrupted" for t in result["turn_status_after_exit"])
        still_live = any(t[0] == turn_id and t[1] == "inProgress" for t in result["turn_status_after_exit"])
        result["turn_interrupted_on_exit"] = interrupted
        result["turn_survived_exit"] = still_live
        check("recorded_exit_outcome", interrupted or still_live, result["turn_status_after_exit"])
        check(
            "backend_pid_stable_after_exit",
            srv.identity()["node"]["pid"] == backend_pid,
            srv.identity()["node"]["pid"],
        )
        result["pan_client_event_tail"] = [json.dumps(e)[:300] for e in client_a.events[-8:]]

    finally:
        result["checks"] = checks
        if tui is not None:
            tui.close()
        if client_a is not None:
            try:
                await asyncio.wait_for(client_a.close(), timeout=5)
            except Exception:
                pass
        result["backend_stop"] = srv.stop()
        blackhole.close()
        time.sleep(1)
        shutil.rmtree(home, ignore_errors=True)
        result["home_removed"] = not os.path.exists(home)
        check("temp_home_removed", result["home_removed"])
        result["passed"] = all(c["ok"] for c in checks)
        result["failed_checks"] = [c for c in checks if not c["ok"]]
        result["log"] = log

    return result


async def scenario_main(out_dir: str, log: list[str], use_tap: bool) -> dict:
    home = make_home()
    port = free_port()
    blackhole = Blackhole()
    result: dict = {
        "scenario": "tap" if use_tap else "main",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "isolation": {
            "codex_home": home,
            "listener_port": port,
            "model_provider": f"local blackhole {blackhole.base_url} (accepts, never answers)",
            "note": "fresh temp CODEX_HOME; synthetic credential; loopback listener owned by this probe",
        },
    }
    srv = AppServer(home, port, blackhole, log)
    tap = WsTap(port) if use_tap else None
    client_a = client_b = None
    tui = None
    checks: list[dict] = []

    def check(name: str, ok: bool, detail=None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        result["credentials"] = seed_placeholder_credentials(home, log)

        srv.start()
        check("app_server_ready", srv.wait_ready(), srv.url)
        result["backend_command"] = " ".join(srv.command())
        result["backend_identity_initial"] = srv.identity()
        backend_pid = srv.identity()["node"]["pid"]
        check("backend_pid_recorded", backend_pid > 0, backend_pid)

        # ---- Client A: stands in for Pan's app-server_wrapper -----------------
        client_a = await JsonRpcClient("pan_probe_a").connect(srv.url)
        result["initialize_result"] = (await client_a.handshake())["result"]

        started = await client_a.call("thread/start", {
            "cwd": os.path.join(home, "probe-cwd"), "threadSource": "pan_probe",
        })
        thread_id = (started.get("result") or {}).get("thread", {}).get("id")
        result["thread_id"] = thread_id
        check("pan_client_created_thread", bool(thread_id), thread_id)

        turn = await client_a.call("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": "PROBE: hold this turn open"}],
        })
        turn_id = (turn.get("result") or {}).get("turn", {}).get("id")
        result["turn_id"] = turn_id
        result["turn_started_monotonic"] = time.time()
        check("turn_in_flight", bool(turn_id), turn_id)

        await asyncio.sleep(4)
        result["turn_status_before_attach"] = await turn_status(client_a, thread_id)
        check(
            "turn_in_progress_before_attach",
            any(t[0] == turn_id and t[1] == "inProgress" for t in result["turn_status_before_attach"]),
            result["turn_status_before_attach"],
        )
        result["blackhole_connections"] = blackhole.connections
        check("turn_reaches_local_provider_only", blackhole.connections >= 1, blackhole.connections)
        result["events_before_attach"] = len(client_a.events)
        result["backend_identity_before_attach"] = srv.identity()
        check(
            "backend_pid_stable_before_attach",
            srv.identity()["node"]["pid"] == backend_pid,
            srv.identity()["node"]["pid"],
        )

        # ---- Subscriber semantics: does a late client join an ongoing stream? --
        result["thread_loaded_list"] = (await client_a.call("thread/loaded/list")).get("result")
        client_b = await JsonRpcClient("pan_probe_b").connect(srv.url)
        await client_b.handshake()
        resumed = await client_b.call("thread/resume", {"threadId": thread_id})
        result["second_client_resume_error"] = resumed.get("error")
        check("second_client_can_resume_live_thread", "result" in resumed, resumed.get("error"))
        await asyncio.sleep(2)
        result["second_client_events_for_preexisting_turn"] = client_b.correlated()

        # Distinguish "not subscribed" from "nothing happened": steer the live
        # turn AFTER B subscribed and see whether B observes the resulting item.
        steer_id = (await client_a.call("turn/steer", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": "PROBE: steer while second client watches"}],
            "expectedTurnId": turn_id,
        }))
        result["steer_after_late_subscribe"] = steer_id.get("result", steer_id.get("error"))
        await asyncio.sleep(4)
        result["second_client_events_after_steer"] = client_b.correlated()
        check(
            "late_subscriber_sees_activity_on_resumed_thread",
            len(result["second_client_events_after_steer"]) > 0,
            result["second_client_events_after_steer"][:6],
        )

        # Subscribe BEFORE the turn starts -> subsequent turns must stream.
        second_thread = (await client_a.call("thread/start", {
            "cwd": os.path.join(home, "probe-cwd"), "threadSource": "pan_probe",
        })).get("result", {}).get("thread", {}).get("id")
        await client_b.call("thread/resume", {"threadId": second_thread})
        await asyncio.sleep(1)
        before_second = len(client_b.correlated())
        second_turn = (await client_a.call("turn/start", {
            "threadId": second_thread, "input": [{"type": "text", "text": "PROBE: second turn"}],
        })).get("result", {}).get("turn", {}).get("id")
        await asyncio.sleep(4)
        forwarded = client_b.correlated()[before_second:]
        result["second_client_events_after_new_turn"] = forwarded[:10]
        check(
            "subscriber_before_turn_receives_turn_events",
            len(forwarded) > 0,
            {"second_thread": second_thread, "second_turn": second_turn, "events": forwarded[:6]},
        )

        # ---- Native TUI attaches mid-turn ------------------------------------
        tui_url = srv.url
        if tap is not None:
            await tap.start()
            tui_url = tap.url
        result["tui_url"] = tui_url
        result["tui_via_tap"] = bool(tap)
        tui = PtyTui(tui_url, home, os.path.join(home, "probe-cwd"))
        result["tui"] = tui.start()
        result["tui_identity"] = proc_identity(tui.pid)

        await asyncio.sleep(16)
        screen = tui.screen()
        result["tui_alive"] = tui.alive()
        result["tui_screen_tail"] = screen[-2500:]
        result["tui_shows_native_ui"] = "Codex" in screen
        result["tui_stuck_at_login_gate"] = "Sign in with ChatGPT" in screen
        result["tui_connection_error"] = "failed to connect" in screen.lower()
        check("native_tui_rendered", tui.alive() and result["tui_shows_native_ui"], {"alive": tui.alive()})
        check("native_tui_past_login_gate", not result["tui_stuck_at_login_gate"])
        check("native_tui_connected_to_shared_backend", not result["tui_connection_error"])

        if tap is not None:
            reqs = tap.tui_requests()
            recv = tap.tui_received_methods()
            result["tui_jsonrpc_requests"] = reqs[:40]
            result["tui_jsonrpc_request_count"] = len(reqs)
            result["tui_jsonrpc_received_notifications"] = recv[:10]
            result["tui_jsonrpc_received_count"] = len(recv)
            check("tui_initialized_over_ws", "initialize" in reqs, reqs[:8])
            check(
                "tui_receives_structured_notifications",
                len(recv) > 0,
                {"count": len(recv), "sample": recv[:5], "frames": len(tap.frames)},
            )

        result["backend_identity_during_attach"] = srv.identity()
        check(
            "backend_pid_stable_during_tui_attach",
            srv.identity()["node"]["pid"] == backend_pid,
            srv.identity()["node"]["pid"],
        )
        result["turn_status_during_attach"] = await turn_status(client_a, thread_id)
        check(
            "turn_still_in_progress_during_attach",
            any(t[0] == turn_id and t[1] == "inProgress" for t in result["turn_status_during_attach"]),
            result["turn_status_during_attach"],
        )
        result["events_during_attach"] = len(client_a.events)
        check(
            "pan_client_stream_alive_during_attach",
            len(client_a.events) > result["events_before_attach"],
            {"before": result["events_before_attach"], "during": len(client_a.events)},
        )

        # ---- TUI returns control; backend + in-flight turn must survive -------
        tui.write("\x03")
        await asyncio.sleep(4)
        result["tui_alive_after_ctrl_c"] = tui.alive()
        tui.close()
        await asyncio.sleep(3)
        result["tui_alive_after_close"] = tui.alive()
        check("native_tui_can_exit", not tui.alive())

        result["backend_identity_after_detach"] = srv.identity()
        check(
            "backend_pid_stable_after_tui_detach",
            srv.identity()["node"]["pid"] == backend_pid,
            srv.identity()["node"]["pid"],
        )
        result["turn_status_after_detach"] = await turn_status(client_a, thread_id)
        check(
            "original_turn_still_in_progress_after_detach",
            any(t[0] == turn_id and t[1] == "inProgress" for t in result["turn_status_after_detach"]),
            result["turn_status_after_detach"],
        )
        result["events_after_detach"] = len(client_a.events)
        check(
            "pan_client_stream_alive_after_detach",
            len(client_a.events) >= result["events_during_attach"],
            {"during": result["events_during_attach"], "after": len(client_a.events)},
        )
        check("pan_client_connection_survived", not client_a.stream_closed)
        check("second_client_connection_survived", not client_b.stream_closed)

        # ---- TUI #2: explicit attach to a live Pan-owned thread via `resume` --
        # Bare `codex --remote <url>` starts a fresh thread; `resume <thread-id>`
        # is how a native client targets the conversation Pan is driving.
        #
        # A fresh thread+turn is used here because the blackhole provider makes
        # the *harness* drop a still-pending turn after roughly a minute (the
        # turn is interrupted without any client asking -- see
        # ``blackhole_turn_lifetime_seconds``). Using a young turn keeps this
        # measurement inside that window.
        thread_b = (await client_a.call("thread/start", {
            "cwd": os.path.join(home, "probe-cwd"), "threadSource": "pan_probe",
        })).get("result", {}).get("thread", {}).get("id")
        turn_b = (await client_a.call("turn/start", {
            "threadId": thread_b, "input": [{"type": "text", "text": "PROBE: attach target turn"}],
        })).get("result", {}).get("turn", {}).get("id")
        result["attach_thread_id"] = thread_b
        result["attach_turn_id"] = turn_b
        await asyncio.sleep(2)

        frame_offset = len(tap.frames) if tap is not None else 0
        tui2 = PtyTui(tui_url, home, os.path.join(home, "probe-cwd"), extra_args=["resume", thread_b])
        result["tui_attach"] = tui2.start()
        result["tui_attach_identity"] = proc_identity(tui2.pid)
        await asyncio.sleep(16)
        screen2 = tui2.screen()
        result["tui_attach_alive"] = tui2.alive()
        result["tui_attach_screen_tail"] = screen2[-2500:]
        check("attach_tui_rendered", tui2.alive() and "Codex" in screen2, {"alive": tui2.alive()})
        check("attach_tui_connected", "failed to connect" not in screen2.lower())

        if tap is not None:
            attach_frames = tap.frames[frame_offset:]
            attach_reqs = [
                f["json"].get("method") for f in attach_frames
                if f.get("dir") == "tui->server" and isinstance(f.get("json"), dict) and f["json"].get("method")
            ]
            resumed_ids = [
                (f["json"].get("params") or {}).get("threadId")
                for f in attach_frames
                if f.get("dir") == "tui->server" and isinstance(f.get("json"), dict)
                and f["json"].get("method") == "thread/resume"
            ]
            result["attach_tui_requests"] = attach_reqs
            result["attach_tui_resume_thread_ids"] = resumed_ids
            check(
                "attach_tui_produced_traffic",
                len(attach_frames) > 0,
                {"frames": len(attach_frames), "screen_tail": screen2[-400:]},
            )
            check(
                "attach_tui_resumed_pan_thread",
                thread_b in resumed_ids,
                {"wanted": thread_b, "got": resumed_ids, "requests": attach_reqs[:12]},
            )
            attach_recv = [
                f["json"].get("method") for f in attach_frames
                if f.get("dir") == "server->tui" and isinstance(f.get("json"), dict) and f["json"].get("method")
            ]
            result["attach_tui_received"] = attach_recv[:15]
            check("attach_tui_received_structured_events", len(attach_recv) > 0, attach_recv[:6])
            check(
                "attach_tui_sent_no_interrupt",
                attach_reqs.count("turn/interrupt") == 0,
                {"turn_interrupt_requests": attach_reqs.count("turn/interrupt")},
            )

        result["backend_identity_during_attach_2"] = srv.identity()
        check(
            "backend_pid_stable_during_explicit_attach",
            srv.identity()["node"]["pid"] == backend_pid,
            srv.identity()["node"]["pid"],
        )
        result["turn_status_during_attach_2"] = await turn_status(client_a, thread_b)
        check(
            "turn_still_in_progress_during_explicit_attach",
            any(t[0] == turn_b and t[1] == "inProgress" for t in result["turn_status_during_attach_2"]),
            result["turn_status_during_attach_2"],
        )
        result["events_before_attach_2"] = len(client_a.events)

        # ---- TUI #2 hands control back ---------------------------------------
        tui2.write("\x03")
        await asyncio.sleep(3)
        tui2.close()
        await asyncio.sleep(2)
        result["attach_tui_alive_after_close"] = tui2.alive()
        check("attach_tui_can_exit", not tui2.alive())

        result["backend_identity_after_attach_2"] = srv.identity()
        check(
            "backend_pid_stable_after_explicit_attach",
            srv.identity()["node"]["pid"] == backend_pid,
            srv.identity()["node"]["pid"],
        )
        result["turn_status_after_attach_2"] = await turn_status(client_a, thread_b)
        check(
            "original_turn_still_in_progress_after_explicit_attach",
            any(t[0] == turn_b and t[1] == "inProgress" for t in result["turn_status_after_attach_2"]),
            result["turn_status_after_attach_2"],
        )
        result["events_after_attach_2"] = len(client_a.events)
        check(
            "pan_client_stream_alive_after_explicit_attach",
            len(client_a.events) >= result["events_before_attach_2"],
            {"before": result["events_before_attach_2"], "after": len(client_a.events)},
        )
        check("pan_client_still_connected", not client_a.stream_closed)

        # Timing of a harness-limited turn: the report must state the verified
        # in-flight window rather than imply a turn lives forever.
        result["blackhole_turn_lifetime_seconds"] = round(
            time.time() - result["turn_started_monotonic"], 1
        )
        result["turn_status_at_end"] = await turn_status(client_a, thread_id)
        result["pan_client_event_tail"] = [json.dumps(e)[:400] for e in client_a.events[-25:]]
        result["second_client_total_events"] = len(client_b.events)
        result["second_client_received_methods"] = sorted({
            e.get("method") for e in client_b.events if e.get("method")
        })
        result["second_client_event_tail"] = [json.dumps(e)[:300] for e in client_b.events[-10:]]

        # Full tail of the Pan-side stream: needed to explain *why* a turn status
        # changed (e.g. who issued an interrupt) instead of guessing.
        result["pan_client_event_tail"] = [json.dumps(e)[:400] for e in client_a.events[-25:]]

        # control for the "TUI saw the real server" claim
        result["control_reference"] = "see control-*.json: unreachable port yields 'failed to connect' and TUI exit"

    finally:
        result["checks"] = checks
        teardown: list[dict] = []

        async def step(label: str, awaitable, timeout: float = 8.0) -> None:
            """Every teardown step is bounded; a stuck step must never mask cleanup."""
            started = time.time()
            try:
                await asyncio.wait_for(awaitable, timeout=timeout)
                teardown.append({"step": label, "ok": True, "seconds": round(time.time() - started, 2)})
            except Exception as exc:
                teardown.append({
                    "step": label, "ok": False,
                    "seconds": round(time.time() - started, 2), "error": repr(exc),
                })

        if tui is not None:
            started = time.time()
            tui.close()
            teardown.append({"step": "tui.close", "ok": True, "seconds": round(time.time() - started, 2)})
        if client_a is not None:
            await step("client_a.close", client_a.close())
        if client_b is not None:
            await step("client_b.close", client_b.close())
        if tap is not None:
            await step("tap.stop", tap.stop(), timeout=15)
        result["backend_stop"] = srv.stop()
        blackhole.close()
        result["blackhole_connections_total"] = blackhole.connections
        time.sleep(1)
        try:
            with socket.socket() as s:
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
            result["port_still_listening"] = True
        except Exception:
            result["port_still_listening"] = False
        check("listener_released", not result["port_still_listening"])
        shutil.rmtree(home, ignore_errors=True)
        result["home_removed"] = not os.path.exists(home)
        check("temp_home_removed", result["home_removed"])
        result["teardown"] = teardown
        check("teardown_all_steps_ok", all(t["ok"] for t in teardown), [t for t in teardown if not t["ok"]])
        result["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        result["passed"] = all(c["ok"] for c in checks)
        result["failed_checks"] = [c for c in checks if not c["ok"]]
        result["log"] = log

    return result


# ----------------------------------------------------------------------------- main
async def run(scenario: str, out_dir: str) -> int:
    log: list[str] = []
    os.makedirs(out_dir, exist_ok=True)
    if scenario == "control":
        result = await scenario_control(out_dir, log)
    elif scenario == "subscriber":
        result = await scenario_subscriber(out_dir, log)
    elif scenario in ("exit-ctrl-c", "exit-force"):
        result = await scenario_exit(out_dir, log, scenario.split("-", 1)[1])
    else:
        result = await scenario_main(out_dir, log, use_tap=(scenario == "tap"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(out_dir, f"{scenario}-{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    print(f"evidence written: {path}")
    if "passed" in result:
        print(f"PASSED={result['passed']} checks={len(result['checks'])}")
        for c in result["checks"]:
            print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['check']} -> {str(c['detail'])[:130]}")
        return 0 if result["passed"] else 1
    print(json.dumps({k: v for k, v in result.items() if k not in ("screen", "log")}, indent=2)[:1500])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Codex native-TUI no-interruption feasibility probe")
    ap.add_argument("--scenario", choices=["main", "tap", "control", "subscriber", "exit-ctrl-c", "exit-force"], default="main")
    ap.add_argument("--out", default=os.path.join("audit", "terminal", "codex", "evidence"))
    ap.add_argument("--watchdog", type=float, default=300.0,
                    help="dump all thread stacks to stderr after N seconds (hang forensics)")
    args = ap.parse_args()
    # A hang must self-document instead of blocking CI/exploration indefinitely.
    import faulthandler

    faulthandler.dump_traceback_later(args.watchdog, exit=True)
    try:
        return asyncio.run(run(args.scenario, args.out))
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    raise SystemExit(main())

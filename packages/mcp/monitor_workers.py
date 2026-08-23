"""Subscribe to Pan /ws/agent and print worker events (one line each).

Runs under Monitor's `command` mode: each printed line wakes the coordinator.

Two channels:

1. WS events: subscribes to worker.result (normal completion) AND
   worker.zombie (unexpected death / watchdog kill / process exit) so
   unexpected worker loss is visible to the coordinator.

2. Health check (fake-running detection): every PAN_HEALTH_INTERVAL
   (default 30s) it polls HTTP GET /api/sessions/{id} for each watched
   session and inspects the session's cbc transcript file mtime
   (~/.codebuddy/projects/<d-project-<workdir>/*.jsonl). When Pan reports
   the worker as running but BOTH the session updatedAt and the transcript
   have been silent for more than PAN_STALE_AFTER (default 180s), it prints
   a STALE line. Stale sessions are de-duplicated: STALE is emitted only on
   the transition into the stale state; a RECOVERED line is emitted when the
   session resumes activity.

Env:
  PAN_WS_URL           WS endpoint (default ws://127.0.0.1:8768/ws/agent)
  PAN_API_URL          HTTP API base (default derived from PAN_WS_URL:
                       ws://host:port/ws/agent -> http://host:port)
  PAN_SESSION_IDS      comma-separated session ids to restrict subscription
                       and health checks to (omitted = all sessions)
  PAN_HEALTH_INTERVAL  health check interval in seconds (default 30)
  PAN_STALE_AFTER      seconds of silence that counts as stale (default 180)
"""
import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import websockets

_DEFAULT_WS = "ws://127.0.0.1:8768/ws/agent"


# ---------------------------------------------------------------------------
# HTTP helpers (blocking; call via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _api_base() -> str:
    """HTTP API base, derived from PAN_WS_URL unless PAN_API_URL is set."""
    env = os.environ.get("PAN_API_URL")
    if env:
        return env.rstrip("/")
    ws = os.environ.get("PAN_WS_URL", _DEFAULT_WS)
    return (
        ws.replace("ws://", "http://")
        .replace("wss://", "https://")
        .replace("/ws/agent", "")
        .rstrip("/")
    )


def _api_get_json(path: str, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{_api_base()}{path}", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Transcript mtime resolution
# ---------------------------------------------------------------------------

def _sanitize_project_dir_name(cwd: str) -> str:
    """Mirror cbc's path-to-project-dir sanitization.

    Same as packages/core/adapters/cbc/sessions.py:sanitize_project_dir_name:
    strip drive colon, lowercase, replace ``\\`` and ``/`` with ``-``,
    collapse consecutive ``-``.
    """
    p = cwd.replace(":", "").lower()
    p = p.replace("\\", "-").replace("/", "-")
    p = re.sub(r"-+", "-", p).strip("-")
    return p


def _transcript_mtime(workdir: str, cli_session_id: str) -> float | None:
    """Latest transcript mtime for a session, or None when not found.

    Search order:
      1. exact file <cliSessionId>.jsonl in the workdir-derived project dir
      2. any *.jsonl in that project dir (activity proxy, per SKILL.md spec)
      3. cross-directory scan for <cliSessionId>.jsonl under ~/.codebuddy/projects
    """
    proj_base = Path.home() / ".codebuddy" / "projects"
    proj_dir = proj_base / _sanitize_project_dir_name(workdir) if workdir else None
    if proj_dir is not None:
        if cli_session_id:
            cand = proj_dir / f"{cli_session_id}.jsonl"
            try:
                if cand.is_file():
                    return cand.stat().st_mtime
            except OSError:
                pass
        try:
            if proj_dir.is_dir():
                mtimes = [f.stat().st_mtime for f in proj_dir.glob("*.jsonl") if f.is_file()]
                if mtimes:
                    return max(mtimes)
        except OSError:
            pass
    if cli_session_id and proj_base.is_dir():
        try:
            for proj in proj_base.iterdir():
                if proj.is_dir():
                    cand = proj / f"{cli_session_id}.jsonl"
                    if cand.is_file():
                        return cand.stat().st_mtime
        except OSError:
            pass
    return None


def _parse_time(value) -> float | None:
    """Parse session updatedAt (ISO-8601 string) to epoch seconds."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e12 else float(value)
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Health check (fake-running detection)
# ---------------------------------------------------------------------------

def _watched_session_ids() -> list[str]:
    raw = os.environ.get("PAN_SESSION_IDS", "")
    sids = [s.strip() for s in raw.split(",") if s.strip()]
    if sids:
        return sids
    # no explicit filter -> all live workers via GET /api/list
    data = _api_get_json("/api/list")
    if not data:
        return []
    return [w.get("sessionId") for w in data.get("workers", []) if w.get("sessionId")]


def _check_session_stale(sid: str, stale_after: float, tracker: dict[str, bool]) -> None:
    """One staleness check for a session; emits STALE/RECOVERED on transitions."""
    data = _api_get_json(f"/api/sessions/{sid}")
    if not data or "id" not in data:
        return
    worker_status = data.get("workerStatus")
    last_status = (data.get("lastResult") or {}).get("status")
    is_running = worker_status == "running" or last_status == "running"
    if not is_running:
        if tracker.pop(sid, False):
            print(f"RECOVERED session={sid} worker={data.get('workerId')} status={worker_status}", flush=True)
        return

    updated_age = None
    t = _parse_time(data.get("updatedAt"))
    if t is not None:
        updated_age = time.time() - t
    transcript_age = None
    tm = _transcript_mtime(data.get("workdir") or "", data.get("cliSessionId") or "")
    if tm is not None:
        transcript_age = time.time() - tm

    # stale requires BOTH clocks to be silent for > stale_after; if either
    # clock is unavailable we can't confirm a stall -> stay not-stale.
    if updated_age is None or transcript_age is None:
        stale, stale_for = False, None
    else:
        stale = updated_age > stale_after and transcript_age > stale_after
        stale_for = int(max(updated_age, transcript_age))

    if stale:
        if not tracker.get(sid):
            print(
                f"STALE session={sid} worker={data.get('workerId')} "
                f"status={worker_status} stale_for={stale_for}s",
                flush=True,
            )
        tracker[sid] = True
    else:
        if tracker.pop(sid, False):
            print(f"RECOVERED session={sid} worker={data.get('workerId')} status={worker_status}", flush=True)


async def _health_loop() -> None:
    interval = float(os.environ.get("PAN_HEALTH_INTERVAL", "30"))
    stale_after = float(os.environ.get("PAN_STALE_AFTER", "180"))
    tracker: dict[str, bool] = {}
    while True:
        try:
            for sid in await asyncio.to_thread(_watched_session_ids):
                await asyncio.to_thread(_check_session_stale, sid, stale_after, tracker)
        except Exception as e:
            print(f"HEALTH_ERROR: {e}", flush=True)
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# WS subscription loop
# ---------------------------------------------------------------------------

def _subscribe_message() -> dict:
    raw = os.environ.get("PAN_SESSION_IDS", "")
    sids = [s.strip() for s in raw.split(",") if s.strip()]
    msg: dict = {"type": "subscribe", "eventTypes": ["worker.result", "worker.zombie"]}
    if sids:
        msg["sessionIds"] = sids
    return msg


async def _ws_loop() -> None:
    uri = os.environ.get("PAN_WS_URL", _DEFAULT_WS)
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps(_subscribe_message()))
                print("MONITOR_CONNECTED", flush=True)
                async for msg in ws:
                    try:
                        ev = json.loads(msg)
                    except json.JSONDecodeError:
                        print("MONITOR_RAW:", msg[:300], flush=True)
                        continue
                    if ev.get("type") == "worker.result":
                        print(
                            f"DONE session={ev.get('sessionId')} "
                            f"status={ev.get('status')} worker={ev.get('workerId')}",
                            flush=True,
                        )
                    elif ev.get("type") == "worker.zombie":
                        print(
                            f"DIE session={ev.get('sessionId')} "
                            f"worker={ev.get('workerId')} returncode={ev.get('returncode')}",
                            flush=True,
                        )
                    elif ev.get("type") == "subscribed":
                        print("MONITOR_SUBSCRIBED", flush=True)
                    else:
                        print("MONITOR_OTHER:", msg[:200], flush=True)
        except Exception as e:  # reconnect on drop
            print(f"MONITOR_DISCONNECTED: {e}", flush=True)
            await asyncio.sleep(5)


async def main() -> None:
    await asyncio.gather(_ws_loop(), _health_loop())


if __name__ == "__main__":
    asyncio.run(main())

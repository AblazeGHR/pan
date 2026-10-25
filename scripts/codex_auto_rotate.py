#!/usr/bin/env python3
"""Best-effort Codex account rotation orchestrator.

This is an external orchestrator: it uses the existing Pan HTTP API, the
cc-switch bridge, and durable background-job data. It does not modify product
code or start/stop the Pan service.

Default is dry-run (no writes). Pass ``--execute`` to pause queues, send the
stop message, switch provider, schedule delayed restarts, and send targeted
continuations.

The sequence is intentionally:

    freeze live Codex workers
    -> pause their queues
    -> force-stop running turns
    -> CAS switch provider
    -> delayed-restart all live Codex workers
    -> wait for every restart to reach a terminal state
    -> send continuation only to the still-unfinished original tasks
    -> restore queue pause state

``agent_restart_delayed`` is the settle gate: it waits for a valid idle
boundary instead of killing a running turn. The orchestrator therefore does
not need a separate "natural settle" confirmation, but it does need a timeout
and explicit failure branches.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

DEFAULT_PAN_ROOT = Path(os.environ.get("PAN_ROOT", r"D:/project/Pan"))
DEFAULT_API = "http://127.0.0.1:8768"
DEFAULT_TRIGGER = (
    DEFAULT_PAN_ROOT / "data" / "workdirs" / "PAN_SMA" / ".workflow"
    / "codex-quota-trigger.json"
)
DEFAULT_STATE = (
    DEFAULT_PAN_ROOT / "data" / "workdirs" / "PAN_SMA" / ".workflow"
    / "codex-auto-state.json"
)
DEFAULT_BRIDGE = DEFAULT_PAN_ROOT / "data" / "plugins" / "cc-switch-bridge"
DEFAULT_LEDGER_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "codex-auto-rotation"

STOP_MESSAGE = "立刻停止一切操作，等待我说继续再继续"
CONTINUE_MESSAGE = "继续原任务"
TERMINAL_RESTART = {"completed", "failed", "cancelled", "skipped", "timeout"}
LIVE_TERMINAL = {"done", "error", "cancelled"}


class RotationError(RuntimeError):
    """A rotation cannot continue safely."""


class PanApiError(RotationError):
    """An HTTP API call failed."""


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RotationError(f"missing file: {path}") from exc
    except (OSError, ValueError) as exc:
        raise RotationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RotationError(f"expected a JSON object: {path}")
    return value


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value:
        try:
            parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return float("inf")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.timestamp()
    return float("inf")


def _tier(quota: dict[str, Any], name: str) -> dict[str, Any] | None:
    tiers = quota.get("tiers")
    if not isinstance(tiers, list):
        return None
    for row in tiers:
        if isinstance(row, dict) and row.get("name") == name:
            return row
    return None


def _percent(row: dict[str, Any] | None) -> float | None:
    if not isinstance(row, dict):
        return None
    value = row.get("usedPercent")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def select_candidate(
    trigger: dict[str, Any],
    current: str,
    disabled: Iterable[str] = (),
) -> str:
    """Pick the nearest weekly reset with usable five-hour/week windows.

    Order matches the current policy: weekly reset ascending, weekly used
    ascending, five-hour used ascending, id as a deterministic tie-break.
    Unknown/partial quota rows are ignored rather than guessed.
    """
    disabled = {str(value) for value in disabled}
    rows: list[tuple[float, float, float, str]] = []
    for item in trigger.get("quotas") or []:
        if not isinstance(item, dict):
            continue
        provider_id = item.get("id")
        quota = item.get("quota")
        if not isinstance(provider_id, str) or not provider_id or provider_id == current:
            continue
        if provider_id in disabled or not isinstance(quota, dict):
            continue
        if quota.get("status") != "ok" or quota.get("available") is not True:
            continue
        five = _tier(quota, "five_hour")
        week = _tier(quota, "seven_day")
        five_used = _percent(five)
        week_used = _percent(week)
        if five_used is None or week_used is None or five_used >= 100 or week_used >= 100:
            continue
        rows.append((_timestamp(week.get("resetsAt")), week_used, five_used, provider_id))
    if not rows:
        raise RotationError("no eligible candidate account")
    rows.sort()
    return rows[0][3]


@dataclasses.dataclass
class FrozenWorker:
    session_id: str
    worker_id: str
    generation: int
    status: str
    task_id: str | None
    task_seq: int | None
    execution_seq: int | None
    queue_paused: bool = False
    detail_ok: bool = True
    paused_by_us: bool = False
    stop_sent: bool = False
    restart: dict[str, Any] | None = None
    resume: dict[str, Any] | None = None

    @property
    def was_running(self) -> bool:
        return self.status == "running"


class PanApi:
    """Small stdlib-only client for the local Pan HTTP API."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = max(0.5, float(timeout))

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urlrequest.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise PanApiError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except (urlerror.URLError, OSError) as exc:
            raise PanApiError(f"{method} {path} failed: {exc}") from exc
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise PanApiError(f"{method} {path} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise PanApiError(f"{method} {path} returned a non-object")
        return value

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", path, {} if body is None else body)

    def patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", path, body)

    def live_workers(self) -> list[dict[str, Any]]:
        value = self.get("/api/list").get("workers")
        if not isinstance(value, list):
            raise PanApiError("/api/list returned no workers list")
        return [row for row in value if isinstance(row, dict)]

    def summaries(self) -> list[dict[str, Any]]:
        value = self.get("/api/sessions?summary=1").get("sessions")
        if not isinstance(value, list):
            raise PanApiError("/api/sessions?summary=1 returned no sessions list")
        return [row for row in value if isinstance(row, dict)]

    def session(self, session_id: str) -> dict[str, Any]:
        # Metadata view keeps activeTaskId/queuePaused without loading history.
        return self.get(
            "/api/sessions/" + urlparse.quote(session_id, safe="") + "?view=metadata"
        )

    def pause_queue(self, session_id: str, paused: bool) -> dict[str, Any]:
        return self.patch(
            "/api/sessions/" + urlparse.quote(session_id, safe="") + "/queue/paused",
            {"paused": bool(paused)},
        )

    def send(
        self,
        session_id: str,
        text: str,
        *,
        force: bool,
        client_message_id: str,
    ) -> dict[str, Any]:
        return self.post(
            "/api/send",
            {
                "sessionId": session_id,
                "text": text,
                "force": bool(force),
                "source": "automation",
                "clientMessageId": client_message_id,
            },
        )

    def delayed_restart(self, session_id: str) -> dict[str, Any]:
        return self.post(
            "/api/sessions/" + urlparse.quote(session_id, safe="")
            + "/worker/delayed-restart?existing_worker_only=true",
            {},
        )

    def delayed_state(self, session_id: str) -> dict[str, Any] | None:
        value = self.get(
            "/api/sessions/" + urlparse.quote(session_id, safe="")
            + "/worker/delayed-restart"
        ).get("delayedRestart")
        return value if isinstance(value, dict) else None

    def restart_worker(self, worker_id: str) -> dict[str, Any]:
        return self.post(
            "/api/worker/" + urlparse.quote(worker_id, safe="") + "/restart",
            {},
        )


class BridgeSwitcher:
    """Thin adapter over the trusted cc-switch bridge (no UI, no confirmation)."""

    def __init__(self, pan_root: Path, data_dir: Path, bridge: Any | None = None):
        root = str(pan_root.resolve())
        if bridge is not None:
            self.bridge = bridge
            return
        if root not in sys.path:
            sys.path.insert(0, root)
        from packages.cc_switch_bridge.provider import Bridge

        self.bridge = Bridge(str(data_dir))

    def current(self) -> str:
        listing = self.bridge.read_only("list", {"app": "codex"})
        current = listing.get("currentProviderId")
        if not isinstance(current, str) or not current:
            raise RotationError("cc-switch has no current codex provider")
        return current

    def disabled_ids(self) -> set[str]:
        """Profiles whose non-manual switch is disabled; never select them."""
        try:
            gates = self.bridge.settings.gates("codex")
        except Exception:
            return set()
        return {str(provider_id) for provider_id, disabled in gates.items() if disabled}

    def snapshot(self, threshold: float) -> dict[str, Any] | None:
        """Read-only quota snapshot; trigger when current five-hour >= threshold."""
        listing = self.bridge.read_only("list", {"app": "codex"})
        current = listing.get("currentProviderId")
        providers = listing.get("providers")
        if not isinstance(current, str) or not current or not isinstance(providers, list):
            return None
        official = [
            row for row in providers
            if isinstance(row, dict) and row.get("category") == "official" and isinstance(row.get("id"), str)
        ]
        if not any(row["id"] == current for row in official):
            return None
        quotas: list[dict[str, Any]] = []
        for row in official:
            try:
                quotas.append(self.bridge.read_only("quota", {"app": "codex", "id": row["id"]}))
            except Exception:
                continue
        current_row = next((row for row in quotas if row.get("id") == current), None)
        if not isinstance(current_row, dict):
            return None
        quota = current_row.get("quota")
        if not isinstance(quota, dict) or quota.get("status") != "ok" or quota.get("available") is not True:
            return None
        five = _tier(quota, "five_hour")
        used = _percent(five)
        if used is None or used < threshold:
            return None
        return {
            "event": "codex_quota_threshold",
            "currentProviderId": current,
            "thresholdUsedPercent": threshold,
            "remainingPercent": 100.0 - used,
            "observedAt": time.time(),
            "quotas": quotas,
        }

    def switch(self, target: str, expected: str) -> dict[str, Any]:
        result = self.bridge.invoke(
            "auto_switch",
            {"app": "codex", "id": target, "expectedCurrentId": expected},
        )
        current = result.get("currentProviderId") if isinstance(result, dict) else None
        if current != target:
            raise RotationError("cc-switch did not confirm the target provider")
        return result


class Orchestrator:
    def __init__(
        self,
        api: Any,
        switcher: Any,
        *,
        trigger: dict[str, Any],
        disabled: Iterable[str] = (),
        execute: bool = False,
        timeout: float = 300.0,
        poll: float = 1.0,
        stop_message: str = STOP_MESSAGE,
        continue_message: str = CONTINUE_MESSAGE,
        allow_stale_trigger: bool = False,
        ledger_path: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.api = api
        self.switcher = switcher
        self.trigger = trigger
        self.disabled = {str(value) for value in disabled}
        self.execute = bool(execute)
        self.timeout = max(1.0, float(timeout))
        self.poll = max(0.05, float(poll))
        self.stop_message = stop_message
        self.continue_message = continue_message
        self.allow_stale_trigger = bool(allow_stale_trigger)
        self.sleep = sleep
        self.clock = clock
        self.rotation_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)
        self.ledger_path = ledger_path or (
            DEFAULT_LEDGER_DIR / f"rotation-{self.rotation_id}.json"
        )
        self.ledger: dict[str, Any] = {
            "version": 1,
            "rotationId": self.rotation_id,
            "mode": "execute" if self.execute else "dry-run",
            "phase": "initialized",
            "startedAt": _now_iso(),
            "triggerPath": str(self.trigger.get("_path") or ""),
            "trigger": {key: value for key, value in self.trigger.items() if not key.startswith("_")},
            "currentProviderId": None,
            "targetProviderId": None,
            "frozen": [],
            "events": [],
            "errors": [],
        }
        self.frozen: list[FrozenWorker] = []
        self.actual_provider: str | None = None
        self.target_provider: str | None = None
        self.switched = False

    def event(self, phase: str, **fields: Any) -> None:
        row = {"at": _now_iso(), "phase": phase, **fields}
        self.ledger.setdefault("events", []).append(row)
        self.ledger["phase"] = phase
        self.save()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def error(self, phase: str, exc: BaseException | str) -> None:
        message = str(exc)
        self.ledger.setdefault("errors", []).append({"at": _now_iso(), "phase": phase, "error": message})
        self.save()
        print(json.dumps({"phase": phase, "error": message}, ensure_ascii=False), file=sys.stderr, flush=True)

    def save(self) -> None:
        self.ledger["updatedAt"] = _now_iso()
        self.ledger["frozen"] = [dataclasses.asdict(item) for item in self.frozen]
        _atomic_write(self.ledger_path, self.ledger)

    def run(self) -> dict[str, Any]:
        try:
            self.event("start")
            self.preflight()
            self.freeze()
            self.pause()
            self.stop()
            self.switch()
            self.restart()
            self.wait()
            self.resume()
            self.unpause()
            self.ledger["phase"] = "done"
            self.event("done", ok=not self.ledger.get("errors"))
            return self.ledger
        except BaseException as exc:
            self.error("fatal", exc)
            if not self.switched:
                # A failure before the switch can safely restore queue state.
                try:
                    self.unpause()
                except BaseException as cleanup:
                    self.error("fatal-cleanup", cleanup)
            else:
                # After a confirmed switch, keep queues paused: mixed old/new
                # workers must not start new work until a human reconciles.
                self.error("fatal-paused", "queues left paused after confirmed switch; manual recovery required")
            raise

    def preflight(self) -> None:
        """Read-only checks before any pause/stop/switch side effect."""
        actual = self.switcher.current()
        self.actual_provider = actual
        self.ledger["currentProviderId"] = actual
        trigger_current = self.trigger.get("currentProviderId")
        if (
            isinstance(trigger_current, str)
            and trigger_current
            and trigger_current != actual
            and not self.allow_stale_trigger
        ):
            raise RotationError(
                f"trigger current provider {trigger_current} != actual {actual}; "
                "refresh the trigger or pass --allow-stale-trigger"
            )
        disabled = set(self.disabled)
        get_disabled = getattr(self.switcher, "disabled_ids", None)
        if callable(get_disabled):
            disabled |= set(get_disabled())
        target = select_candidate(self.trigger, actual, disabled)
        self.target_provider = target
        self.ledger["targetProviderId"] = target
        self.event("preflight", fromProvider=actual, toProvider=target)

    def freeze(self) -> None:
        summaries = {row.get("id"): row for row in self.api.summaries() if isinstance(row.get("id"), str)}
        workers = self.api.live_workers()
        for worker in sorted(workers, key=lambda row: str(row.get("sessionId") or "")):
            session_id = worker.get("sessionId")
            worker_id = worker.get("workerId")
            if not isinstance(session_id, str) or not isinstance(worker_id, str):
                continue
            summary = summaries.get(session_id)
            if not isinstance(summary, dict) or summary.get("adapter") != "codex":
                continue
            detail: dict[str, Any] = {}
            detail_ok = True
            try:
                detail = self.api.session(session_id)
            except RotationError as exc:
                detail_ok = False
                self.error("freeze-detail", f"{session_id}: {exc}")
            task_id = detail.get("activeTaskId") if isinstance(detail, dict) else None
            if not isinstance(task_id, str) or not task_id:
                value = summary.get("workerTaskId")
                task_id = value if isinstance(value, str) and value else None
            task_seq = summary.get("workerTaskSeq")
            execution_seq = summary.get("workerExecutionSeq")
            frozen = FrozenWorker(
                session_id=session_id,
                worker_id=worker_id,
                generation=int(worker.get("generation") or 0),
                status=str(worker.get("status") or "unknown"),
                task_id=task_id,
                task_seq=task_seq if isinstance(task_seq, int) else None,
                execution_seq=execution_seq if isinstance(execution_seq, int) else None,
                queue_paused=bool(detail.get("queuePaused")) if isinstance(detail, dict) else False,
                detail_ok=detail_ok,
            )
            self.frozen.append(frozen)
        self.event(
            "freeze",
            count=len(self.frozen),
            running=sum(1 for item in self.frozen if item.was_running),
            workers=[
                {
                    "sessionId": item.session_id,
                    "workerId": item.worker_id,
                    "generation": item.generation,
                    "status": item.status,
                    "taskId": item.task_id,
                }
                for item in self.frozen
            ],
        )

    def pause(self) -> None:
        for item in self.frozen:
            if not item.detail_ok:
                raise RotationError(
                    f"cannot read queue state for {item.session_id}; refusing to change it"
                )
            if item.queue_paused:
                item.paused_by_us = False
                self.event("pause", sessionId=item.session_id, skipped="already-paused")
                continue
            if not self.execute:
                item.paused_by_us = True
                self.event("pause", sessionId=item.session_id, planned=True)
                continue
            result = self.api.pause_queue(item.session_id, True)
            if result.get("ok") is False or result.get("error"):
                raise RotationError(f"cannot pause queue for {item.session_id}: {result}")
            item.paused_by_us = True
            self.event("pause", sessionId=item.session_id, paused=True)

    def stop(self) -> None:
        for item in self.frozen:
            if not item.was_running:
                continue
            if not self.execute:
                self.event("stop", sessionId=item.session_id, planned=True)
                continue
            try:
                result = self.api.send(
                    item.session_id,
                    self.stop_message,
                    force=True,
                    client_message_id=f"{self.rotation_id}:stop:{item.session_id}",
                )
                if result.get("error"):
                    raise RotationError(str(result["error"]))
                item.stop_sent = True
                self.event("stop", sessionId=item.session_id, accepted=True)
            except RotationError as exc:
                self.error("stop", f"{item.session_id}: {exc}")

    def switch(self) -> None:
        actual = self.actual_provider or self.switcher.current()
        target = self.target_provider
        if not target:
            raise RotationError("preflight did not select a target provider")
        if not self.execute:
            self.event("switch", fromProvider=actual, toProvider=target, planned=True)
            return
        result = self.switcher.switch(target, actual)
        self.switched = True
        self.event(
            "switch",
            fromProvider=actual,
            toProvider=target,
            verified=result.get("currentProviderId") == target,
        )

    def restart(self) -> None:
        for item in self.frozen:
            if item.status in {"held", "restarting"}:
                item.restart = {
                    "sessionId": item.session_id,
                    "workerId": item.worker_id,
                    "kind": "skipped",
                    "status": "skipped",
                    "reason": f"worker status {item.status}",
                }
                self.event("restart", **item.restart)
                continue
            if not self.execute:
                item.restart = {
                    "sessionId": item.session_id,
                    "workerId": item.worker_id,
                    "kind": "planned",
                    "status": "planned",
                }
                self.event("restart", **item.restart)
                continue
            if item.status in LIVE_TERMINAL:
                result = self.api.restart_worker(item.worker_id)
                error = result.get("error")
                item.restart = {
                    "sessionId": item.session_id,
                    "workerId": item.worker_id,
                    "kind": "immediate",
                    "status": "accepted" if not error else "failed",
                    "error": error,
                }
                self.event("restart", **item.restart)
                continue
            result = self.api.delayed_restart(item.session_id)
            error = result.get("error")
            if not error:
                item.restart = {
                    "sessionId": item.session_id,
                    "workerId": item.worker_id,
                    "kind": "delayed",
                    "status": "accepted",
                    "intent": result.get("delayedRestart"),
                }
            elif "No live Worker" in str(error):
                item.restart = {
                    "sessionId": item.session_id,
                    "workerId": item.worker_id,
                    "kind": "skipped",
                    "status": "skipped",
                    "reason": "offline",
                }
            elif "use Restart" in str(error):
                retry = self.api.restart_worker(item.worker_id)
                item.restart = {
                    "sessionId": item.session_id,
                    "workerId": item.worker_id,
                    "kind": "immediate",
                    "status": "accepted" if not retry.get("error") else "failed",
                    "error": retry.get("error"),
                }
            else:
                item.restart = {
                    "sessionId": item.session_id,
                    "workerId": item.worker_id,
                    "kind": "delayed",
                    "status": "failed",
                    "error": str(error),
                }
            self.event("restart", **item.restart)

    def _live_map(self) -> dict[str, dict[str, Any]]:
        return {
            str(row.get("sessionId")): row
            for row in self.api.live_workers()
            if isinstance(row, dict) and isinstance(row.get("sessionId"), str)
        }

    def _verify_replacement(self, item: FrozenWorker, live: dict[str, dict[str, Any]]) -> tuple[bool, str | None]:
        row = live.get(item.session_id)
        if not isinstance(row, dict):
            return False, "replacement worker missing"
        try:
            generation = int(row.get("generation") or 0)
        except (TypeError, ValueError):
            generation = 0
        if generation <= item.generation:
            return False, "replacement generation not advanced"
        if row.get("status") == "error":
            return False, "replacement worker is error"
        return True, None

    def wait(self) -> None:
        if not self.execute:
            for item in self.frozen:
                if item.restart and item.restart.get("status") == "planned":
                    item.restart["status"] = "planned"
            self.event("wait", skipped="dry-run")
            return
        deadline = self.clock() + self.timeout
        while True:
            pending = [
                item for item in self.frozen
                if item.restart and item.restart.get("status") in {"accepted", "pending"}
            ]
            if not pending:
                break
            if self.clock() >= deadline:
                for item in pending:
                    item.restart["status"] = "timeout"
                    item.restart["error"] = "restart did not reach a terminal state before timeout"
                self.event("wait", timeout=True, pending=[item.session_id for item in pending])
                break
            live = self._live_map()
            for item in pending:
                assert item.restart is not None
                if item.restart.get("kind") == "delayed":
                    state = self.api.delayed_state(item.session_id)
                    if isinstance(state, dict):
                        status = str(state.get("status") or "")
                        if status == "completed":
                            # delayed_state may have just created the replacement;
                            # refresh the live map before checking its generation.
                            ok, error = self._verify_replacement(item, self._live_map())
                            if ok:
                                item.restart.update(
                                    status="completed",
                                    error=None,
                                    replacementWorkerId=state.get("replacementWorkerId"),
                                    replacementGeneration=state.get("replacementGeneration"),
                                )
                            elif error == "replacement worker is error":
                                item.restart.update(status="failed", error=error)
                            # Missing/not-yet-visible replacement stays pending; the
                            # timeout is the final authority.
                        elif status in {"failed", "cancelled"}:
                            item.restart.update(status=status, error=state.get("error"))
                elif item.restart.get("kind") == "immediate":
                    ok, error = self._verify_replacement(item, live)
                    if ok:
                        item.restart.update(status="completed", error=None)
                    elif error == "replacement worker is error":
                        item.restart.update(status="failed", error=error)
            self.event("wait", pending=[item.session_id for item in pending])
            if not any(
                item.restart and item.restart.get("status") in {"accepted", "pending"}
                for item in self.frozen
            ):
                break
            self.sleep(self.poll)

    def _terminal_task(self, detail: dict[str, Any], task_id: str | None) -> bool:
        if not isinstance(task_id, str) or not task_id:
            return False
        rows = detail.get("terminalResults")
        if not isinstance(rows, list):
            return False
        return any(
            isinstance(row, dict) and row.get("taskId") == task_id
            for row in rows
        )

    def resume(self) -> None:
        summaries = {
            row.get("id"): row
            for row in self.api.summaries()
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        for item in self.frozen:
            if not item.was_running:
                item.resume = {"status": "skipped", "reason": "not running at freeze"}
                self.event("resume", sessionId=item.session_id, **item.resume)
                continue
            if not item.restart:
                item.resume = {"status": "skipped", "reason": "no restart record"}
                self.event("resume", sessionId=item.session_id, **item.resume)
                continue
            restart_status = item.restart.get("status")
            if restart_status != "completed" and not (
                not self.execute and restart_status == "planned"
            ):
                item.resume = {
                    "status": "skipped",
                    "reason": f"restart_{restart_status or 'unknown'}",
                }
                self.event("resume", sessionId=item.session_id, **item.resume)
                continue
            if not item.task_id:
                item.resume = {"status": "skipped", "reason": "unknown active task id"}
                self.event("resume", sessionId=item.session_id, **item.resume)
                continue
            summary = summaries.get(item.session_id) or {}
            if self._terminal_task(summary, item.task_id):
                item.resume = {"status": "skipped", "reason": "task already terminal"}
                self.event("resume", sessionId=item.session_id, **item.resume)
                continue
            try:
                detail = self.api.session(item.session_id)
            except RotationError as exc:
                item.resume = {"status": "failed", "error": str(exc)}
                self.event("resume", sessionId=item.session_id, **item.resume)
                continue
            active = detail.get("activeTaskId")
            if active != item.task_id:
                item.resume = {
                    "status": "skipped",
                    "reason": "active task changed or completed",
                    "activeTaskId": active,
                }
                self.event("resume", sessionId=item.session_id, **item.resume)
                continue
            if not self.execute:
                item.resume = {"status": "planned", "activeTaskId": active}
                self.event("resume", sessionId=item.session_id, **item.resume)
                continue
            try:
                result = self.api.send(
                    item.session_id,
                    self.continue_message,
                    force=False,
                    client_message_id=f"{self.rotation_id}:resume:{item.session_id}",
                )
                if result.get("error"):
                    raise RotationError(str(result["error"]))
                item.resume = {"status": "queued", "receipt": result}
                self.event("resume", sessionId=item.session_id, **item.resume)
            except RotationError as exc:
                item.resume = {"status": "failed", "error": str(exc)}
                self.event("resume", sessionId=item.session_id, **item.resume)

    def unpause(self) -> None:
        for item in self.frozen:
            if not item.paused_by_us:
                continue
            if not self.execute:
                self.event("unpause", sessionId=item.session_id, planned=True)
                continue
            try:
                result = self.api.pause_queue(item.session_id, False)
                self.event(
                    "unpause",
                    sessionId=item.session_id,
                    ok=result.get("ok") is not False and not result.get("error"),
                )
            except RotationError as exc:
                self.error("unpause", f"{item.session_id}: {exc}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=DEFAULT_API, help="Pan HTTP API base URL (default: %(default)s)")
    parser.add_argument("--pan-root", type=Path, default=DEFAULT_PAN_ROOT, help="Pan checkout root (default: %(default)s)")
    parser.add_argument("--bridge-data-dir", type=Path, default=DEFAULT_BRIDGE, help="cc-switch bridge data directory")
    parser.add_argument("--trigger", type=Path, default=DEFAULT_TRIGGER, help="quota trigger JSON")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="codex-auto-state.json for disabledAccountIds")
    parser.add_argument("--ledger", type=Path, default=None, help="rotation ledger path (one-shot only)")
    parser.add_argument("--execute", action="store_true", help="perform writes; default is dry-run")
    parser.add_argument("--watch", action="store_true", help="standalone monitor loop; requires --execute")
    parser.add_argument("--retry-on-failure", action="store_true", help="keep watching after a failed rotation")
    parser.add_argument("--interval", type=float, default=60.0, help="watch sampling interval seconds")
    parser.add_argument("--cooldown", type=float, default=60.0, help="watch cooldown after a rotation attempt")
    parser.add_argument("--timeout", type=float, default=300.0, help="restart wait timeout seconds")
    parser.add_argument("--poll", type=float, default=1.0, help="poll interval seconds")
    parser.add_argument("--stop-message", default=STOP_MESSAGE)
    parser.add_argument("--continue-message", default=CONTINUE_MESSAGE)
    parser.add_argument("--allow-stale-trigger", action="store_true", help="do not require trigger.currentProviderId == actual current")
    return parser


def _disabled_accounts(path: Path) -> list[str]:
    try:
        state = _load_json(path)
    except RotationError:
        return []
    value = state.get("disabledAccountIds")
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _run_once(args: argparse.Namespace, trigger: dict[str, Any], *,
              switcher: BridgeSwitcher | None = None,
              ledger_path: Path | None = None) -> int:
    api = PanApi(args.api)
    switcher = switcher or BridgeSwitcher(args.pan_root, args.bridge_data_dir)
    orchestrator = Orchestrator(
        api,
        switcher,
        trigger=trigger,
        disabled=_disabled_accounts(args.state),
        execute=args.execute,
        timeout=args.timeout,
        poll=args.poll,
        stop_message=args.stop_message,
        continue_message=args.continue_message,
        allow_stale_trigger=args.allow_stale_trigger,
        ledger_path=ledger_path if ledger_path is not None else args.ledger,
    )
    try:
        ledger = orchestrator.run()
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "ledger": str(orchestrator.ledger_path),
        }, ensure_ascii=False), file=sys.stderr)
        return 2
    failures = [
        item for item in ledger.get("frozen", [])
        if isinstance(item, dict)
        and isinstance(item.get("restart"), dict)
        and item["restart"].get("status") not in {"completed", "skipped", "planned"}
    ]
    print(json.dumps({
        "ok": not failures and not ledger.get("errors"),
        "mode": ledger.get("mode"),
        "rotationId": ledger.get("rotationId"),
        "ledger": str(orchestrator.ledger_path),
        "frozen": len(ledger.get("frozen", [])),
        "failures": len(failures),
    }, ensure_ascii=False))
    return 0 if not failures and not ledger.get("errors") else 1


def _watch(args: argparse.Namespace) -> int:
    switcher = BridgeSwitcher(args.pan_root, args.bridge_data_dir)
    interval = max(1.0, float(args.interval))
    cooldown = max(1.0, float(args.cooldown))
    while True:
        try:
            state = _load_json(args.state)
            if state.get("mode") != "automatic":
                time.sleep(interval)
                continue
            threshold = float(state.get("thresholdUsedPercent", 95))
            trigger = switcher.snapshot(threshold)
            if not trigger:
                time.sleep(interval)
                continue
            trigger["_path"] = "watch:" + _now_iso()
            ledger_path = DEFAULT_LEDGER_DIR / (
                "rotation-watch-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                + "-" + secrets.token_hex(3) + ".json"
            )
            code = _run_once(args, trigger, switcher=switcher, ledger_path=ledger_path)
            if code != 0 and not args.retry_on_failure:
                return code
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(json.dumps({"ok": False, "watchError": str(exc)}, ensure_ascii=False), file=sys.stderr)
            if not args.retry_on_failure:
                return 2
        time.sleep(cooldown)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.watch and not args.execute:
        parser.error("--watch requires --execute")
    if args.watch:
        return _watch(args)
    trigger = _load_json(args.trigger)
    trigger["_path"] = str(args.trigger)
    return _run_once(args, trigger)


if __name__ == "__main__":
    raise SystemExit(main())

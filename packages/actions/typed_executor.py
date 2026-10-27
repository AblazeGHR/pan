"""Typed effect executors for the canonical Job Attempt boundary.

The legacy Action dispatcher accepts a broad collection of compatibility
payloads.  This module is deliberately narrower: one call represents one
fenced Attempt and one ``effectKey``.  It never creates a Job, Run, or child
registry record.  Process effects use :class:`ProcessOwner`; Session and
channel effects use typed providers supplied by the host.

Receipts are plain JSON objects so a coordinator or an independent host can
persist them without importing this module.  A receipt with outcome
``unknown`` is cached just like a terminal receipt: callers must reconcile it
before an explicit retry can be admitted, and this executor will never replay
it automatically.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Protocol, Sequence

from packages.jobs import final_contracts as contracts
from packages.core.platform.process import ProcessOwner


OUTCOMES = frozenset({"succeeded", "rejected", "unknown", "partial"})
_SUCCESS_STATUSES = frozenset({"queued", "accepted", "sent", "succeeded", "completed", "ok"})
_REJECTED_STATUSES = frozenset({"error", "failed", "rejected", "cancelled", "forbidden", "not_found"})
_UNKNOWN_STATUSES = frozenset({"unknown", "uncertain", "unconfirmed", "timeout", "timed_out"})


class SessionProvider(Protocol):
    """Typed Session delivery provider.

    ``effect_key`` is passed through so a provider can use its own durable
    queue/message idempotency key.  The provider must return a JSON object;
    it must not raise for a normal, explicit rejection.
    """

    async def send_session(
        self,
        session_id: str,
        text: str,
        *,
        effect_key: Mapping[str, Any],
        source: str,
        source_session_id: str | None = None,
    ) -> Mapping[str, Any]: ...


class AssignProvider(Protocol):
    async def assign(
        self,
        session_id: str,
        text: str,
        *,
        task_id: str,
        effect_key: Mapping[str, Any],
        source: str,
        source_session_id: str | None = None,
    ) -> Mapping[str, Any]: ...


class ChannelProvider(Protocol):
    """Typed external-channel provider (for example QQ)."""

    async def send(
        self,
        target_type: str,
        target_id: str,
        text: str,
        *,
        bot_uin: str | None,
        effect_key: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class ReceiptStore(Protocol):
    """Small persistence seam for cross-host receipt lookup."""

    def get(self, effect_key: Mapping[str, Any]) -> Mapping[str, Any] | None: ...

    def put(self, effect_key: Mapping[str, Any], receipt: Mapping[str, Any]) -> None: ...


class InMemoryReceiptStore:
    """Deterministic store useful for a coordinator and contract tests.

    Production hosts can provide a store backed by the canonical effect
    ledger.  The key is the canonical digest, so dictionary ordering cannot
    accidentally create another effect identity.
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def get(self, effect_key: Mapping[str, Any]) -> Mapping[str, Any] | None:
        value = self._items.get(contracts.digest(dict(effect_key)))
        return copy.deepcopy(value) if value is not None else None

    def put(self, effect_key: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
        self._items[contracts.digest(dict(effect_key))] = copy.deepcopy(dict(receipt))


class WorkerSessionProvider:
    """Adapter over Pan's existing typed ``worker.send_session`` API.

    The source metadata is passed through the existing worker boundary.  The
    worker remains the authority for validating source/sourceSessionId; this
    adapter does not infer identity from the payload.  The stable client id is
    derived from the effect key.
    """

    async def send_session(
        self,
        session_id: str,
        text: str,
        *,
        effect_key: Mapping[str, Any],
        source: str = "automation",
        source_session_id: str | None = None,
    ) -> Mapping[str, Any]:
        from packages.core import worker

        return await worker.send_session(
            session_id,
            text,
            source=source,
            source_session_id=source_session_id,
            client_message_id="effect_" + contracts.digest(dict(effect_key))[:40],
        )


class WorkerAssignProvider:
    """Adapter over the existing formal ``worker.assign`` boundary."""

    async def assign(
        self,
        session_id: str,
        text: str,
        *,
        task_id: str,
        effect_key: Mapping[str, Any],
        source: str = "agent",
        source_session_id: str | None = None,
    ) -> Mapping[str, Any]:
        from packages.core import worker

        return await worker.assign(
            session_id,
            text,
            source=source,
            task_id=task_id,
            source_session_id=source_session_id,
        )


@dataclass(frozen=True)
class EffectReceipt:
    """Typed, serialisable result returned by :class:`TypedExecutor`."""

    effect_key: dict[str, Any]
    outcome: str
    receipt: dict[str, Any]
    cleanup_proof: dict[str, Any] | None = None
    target_selection: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "effectKey": copy.deepcopy(self.effect_key),
            "outcome": self.outcome,
            # ``status``/``ok`` are convenience projections for provider
            # callers; ``outcome`` remains the canonical contract field.
            "status": self.outcome,
            "ok": self.outcome == "succeeded",
            "receipt": copy.deepcopy(self.receipt),
        }
        if self.cleanup_proof is not None:
            result["cleanupProof"] = copy.deepcopy(self.cleanup_proof)
        if self.target_selection is not None:
            result["targetSelection"] = copy.deepcopy(self.target_selection)
        return result


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise contracts.ContractError("invalid_schema", f"{name} must be an object", path=name)
    return value


def _non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise contracts.ContractError("invalid_schema", f"{path} must be a non-empty string", path=path)
    return value


def _effect_key(value: Mapping[str, Any]) -> dict[str, Any]:
    key = dict(_mapping(value, "effectKey"))
    required = ("runId", "stepId", "targetId", "operationOrdinal")
    missing = [field for field in required if field not in key]
    if missing:
        raise contracts.ContractError("invalid_schema", "effectKey is missing required fields", path="effectKey")
    for field in required[:3]:
        _non_empty_string(key[field], f"effectKey.{field}")
    ordinal = key["operationOrdinal"]
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise contracts.ContractError("invalid_schema", "operationOrdinal must be a non-negative integer", path="effectKey.operationOrdinal")
    return key


def _callable_result(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call a provider method while supporting sync test doubles."""
    result = fn(*args, **kwargs)
    return result


async def _invoke(fn: Any, *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    result = await asyncio.to_thread(_callable_result, fn, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _status(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "unknown"
    raw = value.get("outcome", value.get("status"))
    if raw is None and value.get("ok") is True:
        return "succeeded"
    if raw is None and value.get("ok") is False:
        return "rejected"
    text = str(raw or "").strip().lower()
    if text in _SUCCESS_STATUSES:
        return "succeeded"
    if text in _REJECTED_STATUSES:
        return "rejected"
    if text in _UNKNOWN_STATUSES:
        return "unknown"
    # A provider response without an explicit status is not proof of queue
    # admission or delivery.  Preserve the uncertainty so callers can query
    # the original effect receipt instead of treating metadata as success.
    return "unknown"


def _provider_error(exc: BaseException) -> tuple[str, dict[str, Any]]:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "unknown", {"code": "timeout", "message": str(exc) or "provider timeout"}
    declared = str(getattr(exc, "outcome", getattr(exc, "status", ""))).lower()
    if declared in _REJECTED_STATUSES:
        return "rejected", {"code": str(getattr(exc, "code", "provider_rejected")), "message": str(exc) or "provider rejected"}
    return "unknown", {"code": "provider_exception", "message": str(exc) or type(exc).__name__}


class TypedExecutor:
    """Execute one typed process, Session, broadcast, or channel effect."""

    def __init__(
        self,
        *,
        process_owner: ProcessOwner | None = None,
        session_provider: SessionProvider | Any | None = None,
        assign_provider: AssignProvider | Any | None = None,
        channel_provider: ChannelProvider | Any | None = None,
        receipt_store: ReceiptStore | MutableMapping[str, Mapping[str, Any]] | None = None,
        independent_executor: Any | None = None,
        lifecycle_executor: Any | None = None,
        process_timeout: float = 30.0,
        terminate_grace: float = 2.0,
    ) -> None:
        self.process_owner = process_owner
        self.independent_executor = independent_executor
        self.lifecycle_executor = lifecycle_executor
        self.session_provider = session_provider
        self.assign_provider = assign_provider
        self.channel_provider = channel_provider
        self.receipt_store = receipt_store if receipt_store is not None else InMemoryReceiptStore()
        self.process_timeout = max(0.0, float(process_timeout))
        self.terminate_grace = max(0.0, float(terminate_grace))

    def _stored(self, key: Mapping[str, Any]) -> Mapping[str, Any] | None:
        store = self.receipt_store
        if isinstance(store, MutableMapping):
            value = store.get(contracts.digest(dict(key)))
            return copy.deepcopy(value) if value is not None else None
        return store.get(key)

    def _store(self, key: Mapping[str, Any], value: Mapping[str, Any]) -> None:
        store = self.receipt_store
        if isinstance(store, MutableMapping):
            store[contracts.digest(dict(key))] = copy.deepcopy(dict(value))
        else:
            store.put(key, value)

    async def execute(
        self,
        *,
        attempt: Mapping[str, Any],
        effect_key: Mapping[str, Any],
        frozen_plan: Mapping[str, Any],
        retry: bool = False,
    ) -> Mapping[str, Any]:
        """Run one effect under the supplied Attempt fence.

        ``retry`` is an explicit admission hint.  Unknown, partial, and
        successful receipts are always returned as-is; none is automatically
        replayed.  A clear rejection may be explicitly retried while keeping
        exactly the same effect key.
        """
        attempt_map = _mapping(attempt, "attempt")
        key = _effect_key(effect_key)
        if attempt_map.get("runId") != key["runId"] or attempt_map.get("stepId") != key["stepId"]:
            raise contracts.ContractError("revision_conflict", "Attempt and effectKey identities do not match")
        for field in ("ownerEpoch", "generation"):
            value = attempt_map.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise contracts.ContractError("invalid_schema", f"attempt.{field} must be an integer >= 1", path=f"attempt.{field}")
        if attempt_map.get("state") not in {None, "running"}:
            raise contracts.ContractError("revision_conflict", "Effect admission requires a running Attempt")

        existing = self._stored(key)
        if existing is not None:
            prior = str(existing.get("outcome", existing.get("status", "unknown")))
            if prior != "rejected" or not retry:
                return copy.deepcopy(dict(existing))

        # Durable hosts own their pending/unknown receipts. Do not cache a
        # pending projection here: reconnection must query the original host.
        plan = _mapping(frozen_plan, "frozenPlan")
        operation = plan.get("operation")
        if operation in {"service.restart", "service.exit"} and self.lifecycle_executor is not None:
            return await self.lifecycle_executor.execute(attempt=attempt, effect_key=key, frozen_plan=frozen_plan, retry=retry)
        if (operation in {"process", "background-process", "command"}
                and frozen_plan.get("effect") != "high_risk" and self.independent_executor is not None):
            return await self.independent_executor.execute(attempt=attempt, effect_key=key, frozen_plan=frozen_plan, retry=retry)

        # Persist before the first provider await. If the effect completes but
        # terminal persistence crashes, the original unknown intent prevents
        # another host from interpreting a missing receipt as permission to send.
        # Cross-host admission still belongs to the canonical Attempt claim.
        self._store(key, EffectReceipt(key, "unknown", {
            "code": "effect_unconfirmed", "attempt": copy.deepcopy(dict(attempt_map))
        }).as_dict())
        try:
            plan = _mapping(frozen_plan, "frozenPlan")
            operation = plan.get("operation") or plan.get("executor") or plan.get("kind")
            if plan.get("effect") == "high_risk" or operation in {"main-lifecycle", "service.restart", "service.exit"}:
                result = EffectReceipt(key, "rejected", {"code": "authorization_denied", "message": "Lifecycle/high-risk effects require the protected supervisor"}).as_dict()
            elif operation in {"shell", "send_session", "send_qq_dispatch"}:
                result = EffectReceipt(key, "rejected", {"code": "unsupported_operation", "message": "Legacy shell/API dispatcher is not a typed effect"}).as_dict()
            elif operation == "assign":
                result = await self._assign(key, plan)
            elif isinstance(plan.get("action"), Mapping) and plan["action"].get("api") == "send_qq":
                operation = "channel"
                result = await self._channel(key, plan)
            elif operation in {"process", "background-process", "command"}:
                result = await self._process(key, plan)
            elif operation in {"session-message", "message"}:
                result = await self._session_message(key, plan)
            elif operation in {"session-broadcast", "broadcast"}:
                result = await self._session_broadcast(key, plan)
            elif operation in {"channel", "qq", "send_qq"}:
                result = await self._channel(key, plan)
            else:
                raise contracts.ContractError("invalid_schema", "Unknown typed executor operation", path="operation")
        except contracts.ContractError:
            raise
        except BaseException as exc:
            outcome, detail = _provider_error(exc)
            result = EffectReceipt(key, outcome, detail).as_dict()

        try:
            self._store(key, result)
        except Exception as exc:  # effect may already have happened: fail closed
            result = EffectReceipt(
                key,
                "unknown",
                {"code": "receipt_persist_failed", "message": str(exc) or type(exc).__name__},
            ).as_dict()
        return result

    async def _process(self, key: Mapping[str, Any], plan: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.process_owner is None:
            # Resolve lazily so importing/constructing the executor remains
            # harmless on an undeclared platform.  The platform factory is the
            # only place that selects Windows Job Object versus POSIX groups.
            try:
                from packages.core.platform.process import default_process_owner
                self.process_owner = default_process_owner()
            except Exception as exc:
                return EffectReceipt(dict(key), "rejected", {"code": "capability_unsupported", "message": str(exc) or "ProcessOwner unavailable"}).as_dict()
        arguments = plan.get("arguments") if isinstance(plan.get("arguments"), Mapping) else plan
        argv = arguments.get("argv")
        if (not isinstance(argv, list) or not argv or not argv[0]
                or any(not isinstance(item, str) or "\x00" in item for item in argv)):
            raise contracts.ContractError("invalid_schema", "argv must be a non-empty array of strings", path="argv")
        cwd = _non_empty_string(arguments.get("cwd"), "cwd")
        if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise contracts.ContractError("invalid_schema", "cwd must be an existing directory", path="cwd")
        hide_window = arguments.get("hideWindow", arguments.get("hide_window", True))
        if not isinstance(hide_window, bool):
            raise contracts.ContractError("invalid_schema", "hideWindow must be boolean", path="hideWindow")
        if arguments.get("shell", False) is not False or "command" in arguments:
            raise contracts.ContractError("invalid_schema", "Process effects require argv without a shell", path="argv")
        handle: Any = None
        identity: Mapping[str, Any] | None = None
        cleanup: dict[str, Any] = {"confirmed": False, "remaining": []}
        result: Mapping[str, Any] | None = None
        try:
            handle = await _invoke(self.process_owner.spawn, list(argv), cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, hide_window=hide_window)
            identity = self.process_owner.recoverable_identity(handle)
            waited = await _invoke(self.process_owner.wait, handle, timeout=self.process_timeout)
            if getattr(waited, "timed_out", False) or not getattr(waited, "exited", True):
                terminated = await _invoke(self.process_owner.terminate, handle, grace=self.terminate_grace)
                tree = await _invoke(self.process_owner.kill_tree, handle, timeout=self.terminate_grace)
                cleanup = {
                    "confirmed": tree.confirmed is True and getattr(terminated, "exited", False) is True,
                    "treeTerminated": tree.confirmed is True,
                    "remaining": list(tree.remaining),
                    "ownedTree": tree.as_dict(),
                    "reason": "timeout",
                }
                outcome = "rejected" if cleanup["confirmed"] else "unknown"
                receipt = {"code": "timeout", "exitCode": getattr(terminated, "exit_code", None), "processIdentity": copy.deepcopy(identity)}
                result = EffectReceipt(dict(key), outcome, receipt, cleanup).as_dict()
            else:
                # The retained owner handle proves the whole tree. A dead root
                # or a fresh PID lookup alone cannot establish cleanup.
                tree = await _invoke(self.process_owner.kill_tree, handle, timeout=self.terminate_grace)
                check = await _invoke(self.process_owner.verify, identity)
                identity_verified = (getattr(check, "ours", None) is True
                                     or (waited.exited is True and tree.confirmed is True))
                cleanup = {
                    "confirmed": waited.exited is True and tree.confirmed is True and identity_verified,
                    "identityVerified": identity_verified,
                    "treeTerminated": tree.confirmed is True,
                    "remaining": list(tree.remaining),
                    "ownedTree": tree.as_dict(),
                }
                code = getattr(waited, "exit_code", None)
                outcome = "succeeded" if code == 0 and cleanup["confirmed"] else "rejected" if code not in (None, 0) and cleanup["confirmed"] else "unknown"
                receipt = {"exitCode": code, "processIdentity": copy.deepcopy(identity)}
                result = EffectReceipt(dict(key), outcome, receipt, cleanup).as_dict()
        except contracts.ContractError:
            raise
        except BaseException as exc:
            result = EffectReceipt(dict(key), "unknown", {"code": "process_exception", "message": str(exc) or type(exc).__name__, "processIdentity": copy.deepcopy(identity) if identity else None}, cleanup).as_dict()
        finally:
            if handle is not None:
                if cleanup.get("confirmed") is not True and "ownedTree" not in cleanup:
                    try:
                        tree = await _invoke(self.process_owner.kill_tree, handle, timeout=self.terminate_grace)
                        cleanup.update(ownedTree=tree.as_dict(), remaining=list(tree.remaining))
                    except Exception as exc:
                        cleanup["treeError"] = str(exc) or type(exc).__name__
                try:
                    await _invoke(handle.close)
                except Exception:
                    # A close failure means ownership/cleanup is not proven.
                    cleanup["confirmed"] = False
                    cleanup["closeError"] = True
                    result = EffectReceipt(dict(key), "unknown", {"code": "cleanup_unconfirmed", "processIdentity": copy.deepcopy(identity) if identity else None}, cleanup).as_dict()
        return result or EffectReceipt(dict(key), "unknown", {"code": "no_receipt"}, cleanup).as_dict()

    async def _session_message(self, key: Mapping[str, Any], plan: Mapping[str, Any]) -> Mapping[str, Any]:
        session_id, text = self._session_args(plan)
        if self.session_provider is None:
            return EffectReceipt(dict(key), "rejected", {"code": "provider_unavailable", "sessionId": session_id}).as_dict()
        method = getattr(self.session_provider, "send_session", None) or getattr(self.session_provider, "send", None)
        if method is None:
            return EffectReceipt(dict(key), "rejected", {"code": "provider_unavailable", "sessionId": session_id}).as_dict()
        try:
            arguments = plan.get("arguments") if isinstance(plan.get("arguments"), Mapping) else plan
            source = arguments.get("source", "automation")
            source_session_id = arguments.get("sourceSessionId", arguments.get("source_session_id"))
            result = await _invoke_provider(method, session_id, text, effect_key=key,
                                             source=source, source_session_id=source_session_id)
            outcome = _status(result)
            return EffectReceipt(dict(key), outcome, dict(result) if isinstance(result, Mapping) else {"value": result}, target_selection={"sessionIds": [session_id]}).as_dict()
        except BaseException as exc:
            outcome, detail = _provider_error(exc)
            detail["sessionId"] = session_id
            return EffectReceipt(dict(key), outcome, detail, target_selection={"sessionIds": [session_id]}).as_dict()

    async def _assign(self, key: Mapping[str, Any], plan: Mapping[str, Any]) -> Mapping[str, Any]:
        arguments = plan.get("arguments") if isinstance(plan.get("arguments"), Mapping) else plan
        session_id = _non_empty_string(arguments.get("sessionId", arguments.get("session_id")), "sessionId")
        text = _non_empty_string(arguments.get("text"), "text")
        if self.assign_provider is None:
            return EffectReceipt(dict(key), "rejected", {"code": "provider_unavailable", "sessionId": session_id}).as_dict()
        method = getattr(self.assign_provider, "assign", None)
        if method is None:
            return EffectReceipt(dict(key), "rejected", {"code": "provider_unavailable", "sessionId": session_id}).as_dict()
        task_id = arguments.get("taskId", arguments.get("task_id"))
        if task_id is None:
            task_id = "effect_" + contracts.digest(dict(key))[:40]
        task_id = _non_empty_string(task_id, "taskId")
        source = arguments.get("source", "agent")
        source_session_id = arguments.get("sourceSessionId", arguments.get("source_session_id"))
        try:
            result = await _invoke_provider(method, session_id, text, task_id=task_id,
                                            effect_key=key, source=source,
                                            source_session_id=source_session_id)
            outcome = _status(result)
            return EffectReceipt(dict(key), outcome,
                                 dict(result) if isinstance(result, Mapping) else {"value": result},
                                 target_selection={"sessionIds": [session_id], "taskId": task_id}).as_dict()
        except BaseException as exc:
            outcome, detail = _provider_error(exc)
            detail.update(sessionId=session_id, taskId=task_id)
            return EffectReceipt(dict(key), outcome, detail,
                                 target_selection={"sessionIds": [session_id], "taskId": task_id}).as_dict()

    @staticmethod
    def _session_args(plan: Mapping[str, Any]) -> tuple[str, str]:
        arguments = plan.get("arguments") if isinstance(plan.get("arguments"), Mapping) else plan
        return _non_empty_string(arguments.get("sessionId", arguments.get("session_id")), "sessionId"), _non_empty_string(arguments.get("text"), "text")

    async def _session_broadcast(self, key: Mapping[str, Any], plan: Mapping[str, Any]) -> Mapping[str, Any]:
        arguments = plan.get("arguments") if isinstance(plan.get("arguments"), Mapping) else plan
        ids = arguments.get("sessionIds", arguments.get("session_ids"))
        if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids):
            raise contracts.ContractError("invalid_schema", "sessionIds must be a non-empty array of strings", path="sessionIds")
        text = _non_empty_string(arguments.get("text"), "text")
        ids = list(dict.fromkeys(ids))
        rows: list[dict[str, Any]] = []
        source_fields = {
            field: arguments[field] for field in ("source", "sourceSessionId", "source_session_id")
            if field in arguments
        }
        for target in ids:
            child_key = dict(key)
            child_key["targetId"] = target
            row = await self._session_message(child_key, {
                "sessionId": target, "text": text, **source_fields
            })
            rows.append(row)
        outcomes = [str(row.get("outcome", "unknown")) for row in rows]
        if all(item == "succeeded" for item in outcomes):
            overall = "succeeded"
        elif all(item == "rejected" for item in outcomes):
            overall = "rejected"
        elif all(item == "unknown" for item in outcomes):
            overall = "unknown"
        else:
            overall = "partial"
        return EffectReceipt(dict(key), overall, {"targets": rows, "matched": len(rows), "succeeded": outcomes.count("succeeded"), "rejected": outcomes.count("rejected"), "unknown": outcomes.count("unknown")}, target_selection={"sessionIds": ids}).as_dict()

    async def _channel(self, key: Mapping[str, Any], plan: Mapping[str, Any]) -> Mapping[str, Any]:
        action = plan.get("action") if isinstance(plan.get("action"), Mapping) else plan
        if action.get("api") != "send_qq":
            raise contracts.ContractError("invalid_schema", "Only typed send_qq channel effects are supported", path="action.api")
        args = _mapping(action.get("args"), "action.args")
        # Reuse the existing strict typed QQ identity validator.  It accepts
        # only user/group + numeric QQ identifiers and an optional numeric bot
        # UIN; no endpoint or provider identity can be supplied by a Job.
        from packages.jobs.actions import normalize_scheduled_qq_action
        _normalized, validation_error = normalize_scheduled_qq_action({"api": "send_qq", "args": dict(args)})
        if validation_error:
            raise contracts.ContractError("invalid_schema", validation_error, path="action.args")
        unknown = set(args) - {"targetType", "targetId", "botUin"}
        if unknown:
            raise contracts.ContractError("invalid_schema", "Unknown send_qq fields", path="action.args")
        target_type = args.get("targetType")
        if target_type not in {"user", "group"}:
            raise contracts.ContractError("invalid_schema", "targetType must be user or group", path="action.args.targetType")
        target_id = _non_empty_string(args.get("targetId"), "action.args.targetId")
        bot_uin = args.get("botUin")
        if bot_uin is not None:
            _non_empty_string(bot_uin, "action.args.botUin")
        text = _non_empty_string(plan.get("text", (plan.get("arguments") or {}).get("text") if isinstance(plan.get("arguments"), Mapping) else None), "text")
        if self.channel_provider is None:
            return EffectReceipt(dict(key), "rejected", {"code": "provider_unavailable", "targetType": target_type, "targetId": target_id}).as_dict()
        method = getattr(self.channel_provider, "send", None) or getattr(self.channel_provider, "send_qq", None)
        if method is None:
            return EffectReceipt(dict(key), "rejected", {"code": "provider_unavailable", "targetId": target_id}).as_dict()
        try:
            result = await _invoke_provider(method, target_type, target_id, text, bot_uin=bot_uin, effect_key=key)
            outcome = _status(result)
            return EffectReceipt(dict(key), outcome, dict(result) if isinstance(result, Mapping) else {"value": result}, target_selection={"targetType": target_type, "targetId": target_id}).as_dict()
        except BaseException as exc:
            outcome, detail = _provider_error(exc)
            detail.update(targetType=target_type, targetId=target_id)
            return EffectReceipt(dict(key), outcome, detail, target_selection={"targetType": target_type, "targetId": target_id}).as_dict()


async def _invoke_provider(method: Any, *args: Any, **kwargs: Any) -> Any:
    """Pass only keywords accepted by a small provider test double."""
    try:
        signature = inspect.signature(method)
        if not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    except (TypeError, ValueError):
        pass
    return await _invoke(method, *args, **kwargs)


# Names used by different WP05 callers; they intentionally refer to one class.
EffectExecutor = TypedExecutor
SystemExecutor = TypedExecutor


__all__ = [
    "ChannelProvider",
    "AssignProvider",
    "EffectExecutor",
    "EffectReceipt",
    "InMemoryReceiptStore",
    "ReceiptStore",
    "SessionProvider",
    "SystemExecutor",
    "TypedExecutor",
    "WorkerSessionProvider",
    "WorkerAssignProvider",
]

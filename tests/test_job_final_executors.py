"""WP05a FE01-FE05 typed effect executor contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.actions.typed_executor import InMemoryReceiptStore, TypedExecutor
from packages.jobs.final_contracts import ContractError


def run(coro):
    return asyncio.run(coro)


class FakeOwner:
    def __init__(self, *, exit_code=0):
        self.calls = []
        self.exit_code = exit_code

    def spawn(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return SimpleNamespace(close=lambda: None)

    def recoverable_identity(self, _handle):
        return {"schema": "pan.process-identity/1", "platform": "test", "pid": 7, "birth": "proof"}

    def wait(self, _handle, *, timeout):
        return SimpleNamespace(exited=True, timed_out=False, exit_code=self.exit_code)

    def verify(self, _identity):
        return SimpleNamespace(status="dead", ours=True)


class SessionProvider:
    def __init__(self, statuses):
        self.statuses = statuses
        self.calls = []

    async def send_session(self, session_id, text, **kwargs):
        self.calls.append((session_id, text, kwargs))
        value = self.statuses.get(session_id, "queued")
        if isinstance(value, BaseException):
            raise value
        return {"status": value, "queueItemId": "q-" + session_id}


class ChannelProvider:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def send(self, target_type, target_id, text, **kwargs):
        self.calls.append((target_type, target_id, text, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def attempt(run_id="run_1", step_id="step_1"):
    return {"runId": run_id, "stepId": step_id, "state": "running", "ownerEpoch": 1, "generation": 1}


def key(run_id="run_1", step_id="step_1", target="target"):
    return {"runId": run_id, "stepId": step_id, "targetId": target, "operationOrdinal": 0}


def test_fe01_owned_process_preserves_argv_and_cleanup_proof(tmp_path: Path):
    owner = FakeOwner()
    result = run(TypedExecutor(process_owner=owner).execute(
        attempt=attempt(), effect_key=key(target="process"),
        frozen_plan={"operation": "process", "argv": ["tool", "two words", "--flag=x"], "cwd": str(tmp_path)},
    ))
    assert result["outcome"] == "succeeded"
    assert owner.calls[0][0] == ["tool", "two words", "--flag=x"]
    assert result["cleanupProof"]["identityVerified"] is True
    assert result["cleanupProof"]["treeTerminated"] is True


def test_fe02_cross_host_lookup_keeps_run_and_effect_identity():
    store = InMemoryReceiptStore()
    first = SessionProvider({"s": "unknown"})
    second = SessionProvider({"s": "queued"})
    effect = key(target="s")
    a = run(TypedExecutor(session_provider=first, receipt_store=store).execute(
        attempt=attempt(), effect_key=effect, frozen_plan={"operation": "session-message", "sessionId": "s", "text": "hello"},
    ))
    b = run(TypedExecutor(session_provider=second, receipt_store=store).execute(
        attempt=attempt(), effect_key=effect, frozen_plan={"operation": "session-message", "sessionId": "s", "text": "hello"}, retry=True,
    ))
    assert a["outcome"] == b["outcome"] == "unknown"
    assert a["effectKey"] == b["effectKey"] == effect
    assert len(first.calls) == 1 and second.calls == []


def test_fe03_session_fanout_returns_per_target_receipts():
    provider = SessionProvider({"ok": "queued", "denied": "error", "late": "unknown"})
    result = run(TypedExecutor(session_provider=provider).execute(
        attempt=attempt(attempt.__name__, "broadcast"), effect_key=key(attempt.__name__, "broadcast", "fanout"),
        frozen_plan={"operation": "session-broadcast", "sessionIds": ["ok", "denied", "late"], "text": "hello"},
    ))
    assert result["outcome"] == "partial"
    assert [row["outcome"] for row in result["receipt"]["targets"]] == ["succeeded", "rejected", "unknown"]


def test_fe04_qq_typed_error_and_timeout_are_distinct():
    action = {"api": "send_qq", "args": {"targetType": "group", "targetId": "12345"}}
    rejected = run(TypedExecutor(channel_provider=ChannelProvider({"status": "error", "code": "rate_limited"})).execute(
        attempt=attempt(), effect_key=key(target="qq-rejected"), frozen_plan={"operation": "channel", "action": action, "text": "hello"},
    ))
    unknown = run(TypedExecutor(channel_provider=ChannelProvider(asyncio.TimeoutError("timeout"))).execute(
        attempt=attempt("run_2"), effect_key=key("run_2", "step_1", "qq-timeout"), frozen_plan={"operation": "channel", "action": action, "text": "hello"},
    ))
    assert rejected["outcome"] == "rejected" and rejected["receipt"]["code"] == "rate_limited"
    assert unknown["outcome"] == "unknown" and unknown["receipt"]["code"] == "timeout"


def test_fe05_lifecycle_and_shell_are_explicitly_refused():
    lifecycle = run(TypedExecutor().execute(
        attempt=attempt(), effect_key=key(target="lifecycle"), frozen_plan={"operation": "main-lifecycle", "effect": "high_risk"},
    ))
    shell = run(TypedExecutor().execute(
        attempt=attempt("run_2"), effect_key=key("run_2", "step_1", "shell"), frozen_plan={"operation": "shell", "command": "echo unsafe"},
    ))
    assert lifecycle["outcome"] == "rejected" and lifecycle["receipt"]["code"] == "authorization_denied"
    assert shell["outcome"] == "rejected" and shell["receipt"]["code"] == "unsupported_operation"


def test_invalid_qq_identity_is_rejected_before_provider_call():
    provider = ChannelProvider({"status": "queued"})
    with pytest.raises(ContractError) as exc:
        run(TypedExecutor(channel_provider=provider).execute(
            attempt=attempt(), effect_key=key(target="qq-invalid"),
            frozen_plan={"operation": "channel", "action": {"api": "send_qq", "args": {"targetType": "user", "targetId": "bad"}}, "text": "x"},
        ))
    assert exc.value.code == "invalid_schema"
    assert provider.calls == []

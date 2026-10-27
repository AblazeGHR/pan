"""WP05a FE01-FE05 typed effect executor contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.actions.typed_executor import InMemoryReceiptStore, TypedExecutor, WorkerSessionProvider
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

    def kill_tree(self, _handle, *, timeout):
        from packages.core.platform.process import TreeTerminationResult
        return TreeTerminationResult((7,), (), True)


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


def test_m03_source_metadata_reaches_original_session_provider():
    provider = SessionProvider({"target": "queued"})
    result = run(TypedExecutor(session_provider=provider).execute(
        attempt=attempt(), effect_key=key(target="target"),
        frozen_plan={"operation": "session-message", "sessionId": "target",
                     "text": "report", "source": "agent",
                     "sourceSessionId": "ses_source"},
    ))
    assert result["outcome"] == "succeeded"
    assert provider.calls[0][2]["source"] == "agent"
    assert provider.calls[0][2]["source_session_id"] == "ses_source"


def test_m03_missing_provider_status_stays_unknown():
    class NoStatus:
        async def send_session(self, session_id, text, **kwargs):
            return {"queueItemId": "q-1"}

    result = run(TypedExecutor(session_provider=NoStatus()).execute(
        attempt=attempt(), effect_key=key(target="target"),
        frozen_plan={"operation": "session-message", "sessionId": "target", "text": "report"},
    ))
    assert result["outcome"] == "unknown"


def test_m04_broadcast_uses_per_target_provider_receipts_without_extra_store_rows():
    provider = SessionProvider({"a": "queued", "b": "error"})
    store = InMemoryReceiptStore()
    effect = key(target="fanout")
    result = run(TypedExecutor(session_provider=provider, receipt_store=store).execute(
        attempt=attempt(), effect_key=effect,
        frozen_plan={"operation": "session-broadcast", "sessionIds": ["a", "b", "a"],
                     "text": "broadcast", "source": "agent", "sourceSessionId": "ses_source"},
    ))
    assert result["outcome"] == "partial"
    assert [row["targetSelection"]["sessionIds"] for row in result["receipt"]["targets"]] == [["a"], ["b"]]
    assert provider.calls[0][2]["source_session_id"] == "ses_source"
    # Only the parent effect is persisted; fan-out rows are receipts in it.
    assert len(store._items) == 1


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


@pytest.mark.parametrize("remaining,confirmed", [((9,), False), ((), False)])
def test_r5_root_exit_without_owned_tree_proof_is_unknown(tmp_path, remaining, confirmed):
    from packages.core.platform.process import TreeTerminationResult
    class Unconfirmed(FakeOwner):
        def kill_tree(self, handle, *, timeout): return TreeTerminationResult((7, 9), remaining, confirmed)
    owner = Unconfirmed()
    executor = TypedExecutor(process_owner=owner)
    args = dict(attempt=attempt(), effect_key=key(), frozen_plan={"operation": "process", "argv": ["tool"], "cwd": str(tmp_path)})
    first = run(executor.execute(**args))
    second = run(executor.execute(**args, retry=True))
    assert first == second and first["outcome"] == "unknown" and len(owner.calls) == 1
    assert first["cleanupProof"]["confirmed"] is False


def test_r5_cleanup_close_failure_cannot_be_success_or_retry(tmp_path):
    class CloseFailure(FakeOwner):
        def spawn(self, argv, **kwargs):
            self.calls.append(argv)
            def close(): raise OSError("close unconfirmed")
            return SimpleNamespace(close=close)
    owner = CloseFailure()
    executor = TypedExecutor(process_owner=owner)
    args = dict(attempt=attempt(), effect_key=key(), frozen_plan={"operation": "process", "argv": ["tool"], "cwd": str(tmp_path)})
    first = run(executor.execute(**args))
    assert first["outcome"] == "unknown" and first["cleanupProof"]["closeError"] is True
    assert run(executor.execute(**args, retry=True)) == first and len(owner.calls) == 1


@pytest.mark.parametrize("changes", [{"shell": True}, {"command": "echo unsafe"}, {"argv": ["tool", "bad\x00arg"]}, {"cwd": "."}])
def test_r5_invalid_process_plan_is_refused_before_spawn(tmp_path, changes):
    owner = FakeOwner()
    with pytest.raises(ContractError):
        run(TypedExecutor(process_owner=owner).execute(attempt=attempt(), effect_key=key(),
            frozen_plan={"operation": "process", "argv": ["tool"], "cwd": str(tmp_path), **changes}))
    assert owner.calls == []


@pytest.mark.parametrize("statuses,outcome", [({"s": "unknown"}, "unknown"), ({"s": "queued", "t": "unknown"}, "partial")])
def test_r5_system_typed_port_reuses_unknown_partial_receipts(statuses, outcome):
    from packages.jobs.system_actions import execute_typed
    provider = SessionProvider(statuses)
    executor = TypedExecutor(session_provider=provider)
    args = dict(attempt=attempt(), effect_key=key(), frozen_plan={"operation": "session-broadcast", "sessionIds": list(statuses), "text": "one"})
    first = run(execute_typed(**args, executor=executor))
    assert first["outcome"] == outcome
    assert run(execute_typed(**args, executor=executor, retry=True)) == first
    assert len(provider.calls) == len(statuses)
    with pytest.raises(ValueError, match="reusable"):
        run(execute_typed(**args, session_provider=provider))



def test_r5_terminal_persistence_crash_retains_unknown_intent_without_resend():
    class Store(InMemoryReceiptStore):
        def put(self, key, receipt):
            if receipt["outcome"] != "unknown": raise OSError("terminal receipt commit crashed")
            super().put(key, receipt)
    store = Store()
    first, later = SessionProvider({"s": "queued"}), SessionProvider({"s": "queued"})
    args = dict(attempt=attempt(), effect_key=key(), frozen_plan={"operation": "session-message", "sessionId": "s", "text": "once"})
    failed = run(TypedExecutor(session_provider=first, receipt_store=store).execute(**args))
    assert failed["outcome"] == "unknown" and failed["receipt"]["code"] == "receipt_persist_failed"
    recovered = run(TypedExecutor(session_provider=later, receipt_store=store).execute(**args, retry=True))
    assert recovered["outcome"] == "unknown" and len(first.calls) == 1 and later.calls == []


def test_r5_failed_intent_persistence_prevents_provider_call():
    class Store(InMemoryReceiptStore):
        def put(self, *args): raise OSError("intent commit failed")
    provider = SessionProvider({"s": "queued"})
    with pytest.raises(OSError, match="intent commit"):
        run(TypedExecutor(session_provider=provider, receipt_store=Store()).execute(attempt=attempt(), effect_key=key(),
            frozen_plan={"operation": "session-message", "sessionId": "s", "text": "once"}))
    assert provider.calls == []


def test_r5_concurrent_same_effect_does_not_send_twice():
    class Blocking(SessionProvider):
        async def send_session(self, session_id, text, **kwargs):
            result = await super().send_session(session_id, text, **kwargs)
            await asyncio.sleep(.01)
            return result
    async def exercise():
        provider = Blocking({"s": "queued"})
        executor = TypedExecutor(session_provider=provider)
        args = dict(attempt=attempt(), effect_key=key(), frozen_plan={"operation": "session-message", "sessionId": "s", "text": "once"})
        receipts = await asyncio.gather(executor.execute(**args), executor.execute(**args))
        assert len(provider.calls) == 1 and {item["outcome"] for item in receipts} == {"unknown", "succeeded"}
    run(exercise())



def test_r5_unrelated_receipt_host_cannot_authorize_ephemeral_message_executor():
    from packages.jobs.system_actions import execute_typed
    provider = SessionProvider({"s": "queued"})
    with pytest.raises(ValueError, match="reusable"):
        run(execute_typed(attempt=attempt(), effect_key=key(),
            frozen_plan={"operation": "session-message", "sessionId": "s", "text": "once"},
            independent_executor=object(), session_provider=provider))
    assert provider.calls == []

"""Job retry policy contracts; all registry/process state is test owned."""
import asyncio
import concurrent.futures
from types import SimpleNamespace
import os
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from packages.core import background_jobs as jobs, background_retry as retry
from tests.test_job_automatic_recovery import env


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs._sessions, "get", lambda sid: Mock(id=sid))
    monkeypatch.setattr(jobs, "_scheduled_task_hooks", {})
    monkeypatch.setattr(jobs, "_owns_process", lambda fact: object() if fact.get("pid") == os.getpid() else None)
    real_spawn = jobs._spawn_background_runner_unlocked
    spawned = []

    def spawn(job, registry_root=None):
        spawned.append((job["jobId"], job["attemptId"]))
        return jobs._update(job["jobId"], dict(status="running", runnerPid=os.getpid(),
                            runnerProcessCreatedAt=1, runnerSpawnStartedAt=time.time()),
                            registry_root=registry_root)

    monkeypatch.setattr(jobs, "_spawn_background_runner_unlocked", spawn)

    def create(**kwargs):
        return jobs.start("ses_target", [sys.executable, "-c", "pass"], str(tmp_path),
                          name="Retry contract", **kwargs)
    create.spawned = spawned
    create.real_spawn = real_spawn
    return create


@pytest.mark.parametrize("reason", sorted(retry.REASONS))
@pytest.mark.parametrize("match", sorted(retry.REASONS))
def test_reason_match_matrix(env, monkeypatch, reason, match):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe, marker
    from tests.test_job_retry_reason_matrix import original_reason
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=[match], maxAttempts=3)))
    original_reason(row, reason)
    observe(env)
    should_retry = match == reason and reason in {'failed', 'timeout', 'orphaned', 'interrupted'}
    assert bool(marker(env, row)) is should_retry
    assert len(env.service.final.get(row.jid)['attempts']) == 1
    asyncio.run(env.service.dispatch_once())
    state = env.service.final.get(row.jid)
    assert len(state['attempts']) == 1 + int(should_retry)
    assert len(row.original.calls) == int(should_retry)
    if should_retry:
        call = row.original.calls[0]
        assert call['effect_key'] == row.key
        assert call['attempt']['attemptId'] != row.attempt['attemptId']
        assert call['attempt']['generation'] == row.attempt['generation'] + 1
    else:
        assert not row.original.calls


def test_default_side_effects_never_retry(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, observe
    row = seed(env, monkeypatch, value=job_definition(env, recovery={'automatic': False}))
    observe(env)
    before = env.service.final.get(row.jid)
    assert before['runs'][row.rid]['state'] == 'failed'
    for _ in range(2):
        asyncio.run(env.service.dispatch_once())
        observe(env)
    assert env.service.final.get(row.jid) == before
    assert len(before['attempts']) == 1 and not row.original.calls


def test_zero_budget_and_spawn_failure_consume_no_extra_attempt(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe, marker
    row = seed(env, monkeypatch, value=job_definition(env, policy(maxAttempts=1)))
    observe(env)
    asyncio.run(env.service.dispatch_once())
    assert len(env.service.final.get(row.jid)['attempts']) == 1 and not row.original.calls
    row = seed(env, monkeypatch, value=job_definition(env, policy(maxAttempts=2)))
    observe(env)
    calls = []
    async def fail(**kwargs):
        calls.append(kwargs)
        raise OSError('owned spawn failure')
    monkeypatch.setattr(row.original, 'execute', fail)
    asyncio.run(env.service.dispatch_once())
    state = env.service.final.get(row.jid)
    assert len(state['attempts']) == 2 and len(calls) == 1
    assert state['runs'][row.rid]['state'] == 'uncertain'
    assert marker(env, row) is None
    for _ in range(2): asyncio.run(env.service.dispatch_once())
    assert len(calls) == 1 and len(env.service.final.get(row.jid)['attempts']) == 2


def test_retry_does_not_swallow_registry_failure(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, observe
    row = seed(env, monkeypatch)
    observe(env)
    before = env.service.final.store.get(row.jid)
    original = env.service._save
    def fail(db, kind, identity, value):
        if kind == 'job' and len(value.get('finalRuntime', {}).get('attempts', {})) == 2:
            raise OSError('owned registry unavailable')
        return original(db, kind, identity, value)
    monkeypatch.setattr(env.service, '_save', fail)
    with pytest.raises(OSError, match='owned registry unavailable'):
        asyncio.run(env.service.dispatch_once())
    assert env.service.final.store.get(row.jid) == before
    assert not row.original.calls


@pytest.mark.parametrize("options", [
    {"retry_if": ["orphaned"]}, {"retry_if": ["unknown"]}, {"retry_if": "failed"},
    {"max_retries": True}, {"max_retries": -1}, {"max_retries": 1.5},
    {"backoff_sec": True}, {"backoff_sec": float("inf")}, {"backoff_sec": float("nan")},
    {"backoff_sec": -1}, {"idempotency_key": ""}, {"deduplication": " "},
])
def test_invalid_policy_has_no_effect(factory, options):
    with pytest.raises(ValueError):
        factory(**options)
    assert not factory.spawned and jobs.list_jobs() == []


def test_restart_reconcile_persists_count_backoff_and_limit(factory, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(jobs.time, "time", lambda: clock[0])
    job = factory(retry_if=["orphaned"], max_retries=2, backoff_sec=10,
                  idempotency_key="watcher-single-writer")
    jobs._update(job["jobId"], dict(runnerPid=999999999, runnerSpawnStartedAt=900))
    assert jobs.reconcile_running() == 1
    ended = jobs.get(job["jobId"])
    assert ended["reason"] == "orphaned" and ended["nextRetryAt"] == 1010
    assert jobs.reconcile_running() == 0
    clock[0] = 1010
    assert jobs.reconcile_running() == 1
    assert jobs.get(job["jobId"])["attempt"] == 2
    assert jobs.reconcile_running() == 0
    # Subsequent recovery reads only JSON, with no in-memory retry counter.
    for attempt in (2, 3):
        jobs._update(job["jobId"], dict(runnerPid=999999999, runnerSpawnStartedAt=900))
        assert jobs.reconcile_running() == 1
        clock[0] += 10
        assert jobs.retry_due() == int(attempt < 3)
    current = jobs.get(job["jobId"])
    assert current["status"] == "failed" and current["reason"] == "orphaned"
    assert current["attempt"] == 3 and current["remaining"] == 0
    assert [a["attempt"] for a in current["retryAttempts"]] == [1, 2, 3]
    assert jobs.reconcile_running() == 0


def test_cancel_during_backoff_prevents_retry(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe, USER
    row = seed(env, monkeypatch, value=job_definition(env, policy(backoffSeconds=['60'])))
    observe(env)
    run = env.service.final.get(row.jid)['runs'][row.rid]
    result = asyncio.run(env.service.control_run_postcommit(row.rid, {'operation': 'cancel',
        'expectedStateRevision': run['stateRevision'], 'requestKey': 'cancel-backoff'}, actor=USER))
    observe(env)
    for _ in range(2): asyncio.run(env.service.dispatch_once())
    state = env.service.final.get(row.jid)
    assert state['runs'][row.rid]['state'] == 'cancelled'
    assert len(state['attempts']) == 1 and not row.original.calls
    assert env.service.operation(result['operationId'], actor=USER)['status'] == 'completed'


def test_accepted_cancel_beats_orphan_recovery(factory):
    job = factory(retry_if=list(retry.REASONS), max_retries=3, deduplication="No-op command")
    jobs._update(job["jobId"], dict(cancelRequestedAt=1, runnerPid=999999999,
                                  runnerSpawnStartedAt=1))
    assert jobs.reconcile_running() == 1
    current = jobs.get(job["jobId"])
    assert current["status"] == "cancelled" and current["lastReason"] == "cancelled"
    assert current["attempt"] == 1 and jobs.retry_due() == 0


def test_concurrent_recovery_and_late_runner_are_fenced(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, observe
    row = seed(env, monkeypatch)
    async def concurrent():
        await asyncio.gather(*(env.service.dispatch_once() for _ in range(4)))
    asyncio.run(concurrent())
    state = env.service.final.get(row.jid)
    assert len(row.original.calls) == 1 and len(state['attempts']) == 2
    current = row.original.calls[0]
    assert current['effect_key'] == row.key
    assert current['attempt']['attemptId'] != row.attempt['attemptId']
    # The retained old Attempt's receipt cannot settle/reexecute the new one.
    observe(env)
    assert any(d['code'] == 'external_receipt_unavailable' and d.get('errorCode') == 'revision_conflict'
               and d.get('runId') == row.rid for d in env.service.diagnostics)
    assert env.service.final.get(row.jid) == state and len(row.original.calls) == 1


def test_orphan_does_not_duplicate_live_task(factory):
    job = factory(retry_if=["orphaned"], max_retries=1, deduplication="No-op command")
    jobs._update(job["jobId"], dict(runnerPid=999999999, runnerSpawnStartedAt=1,
                                  pid=os.getpid(), processCreatedAt=1))
    assert jobs.reconcile_running() == 1
    current = jobs.get(job["jobId"])
    assert current["attempt"] == 1 and current["reason"] == "orphaned"
    assert current["retryBlockedReason"] == "task exit is unverified"


def test_http_policy_and_public_status(factory):
    from packages.web import server
    result = asyncio.run(server.api_background_job_start(dict(
        targetSessionId="ses_target", argv=[sys.executable], cwd=str(jobs.DEFAULT_ROOT.parent),
        name="API retry contract", retryIf=["failed"], maxRetries=1, backoffSec=30,
        idempotencyKey="caller-enforced")))
    assert result["retryIf"] == ["failed"] and result["attempt"] == 1
    jobs.runner_update(result["jobId"], status="failed", reason="timeout")
    public = jobs.job_public_view(jobs.get(result["jobId"]))
    assert public["lastReason"] == "timeout" and public["remaining"] == 1
    assert public["nextRetryAt"] is None


@pytest.mark.parametrize("error, reason", [
    (TimeoutError("runner timeout"), "timeout"),
    (subprocess.TimeoutExpired("owned command", 1), "timeout"),
    (KeyboardInterrupt(), "interrupted"), (OSError("spawn failed"), "failed"),
])
def test_runner_does_not_collapse_terminal_reasons(factory, monkeypatch, error, reason):
    from packages.core import background_runner
    job = factory(retry_if=[reason], max_retries=1, deduplication="No-op command")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(jobs, "runner_spawn", fail)
    if os.name == "nt":
        monkeypatch.setattr(jobs, "claim_hidden_runner", lambda *args, **kwargs: True)
    result = background_runner._run_legacy(SimpleNamespace(job_id=job["jobId"], attempt_id=job["attemptId"]))
    assert result == (130 if reason == "interrupted" else 1)
    current = jobs.get(job["jobId"])
    assert current["reason"] == reason and current["nextRetryAt"] is not None


def test_unavailable_independent_policy_fails_before_remote_start(factory, monkeypatch):
    from packages.jobs import pan_bridge
    remote = Mock()
    monkeypatch.setattr(pan_bridge, "configured_client", lambda: object())
    monkeypatch.setattr(pan_bridge, "request", remote)
    with pytest.raises(ValueError, match="not supported"):
        factory(retry_if=["failed"], max_retries=1, idempotency_key="caller-enforced")
    remote.assert_not_called()
    assert not factory.spawned


def test_recovery_notifies_final_outcome_once(factory, monkeypatch):
    job = factory(retry_if=["failed"], max_retries=1, backoff_sec=60,
                  deduplication="No-op command")
    notices = []
    async def enqueue(*args, **kwargs):
        notices.append(kwargs["event_id"])
        return {"ok": True}
    monkeypatch.setattr(jobs._worker, "enqueue_notice", enqueue)
    jobs.runner_update(job["jobId"], status="failed")
    asyncio.run(jobs.recover_notifications())
    assert notices == []
    jobs._update(job["jobId"], {"nextRetryAt": 0})
    assert jobs.retry_due() == 1
    current = jobs.get(job["jobId"])
    jobs.runner_update(job["jobId"], expected_attempt=current["attemptId"], status="completed", exitCode=0)
    asyncio.run(jobs.recover_notifications())
    asyncio.run(jobs.recover_notifications())
    assert notices == [f"{job['jobId']}:attempt:{current['attemptId']}:terminal"]


@pytest.mark.parametrize("recovery_reason", ["orphaned", "failed"])
def test_separate_recovery_process_after_runner_loss(tmp_path, monkeypatch, recovery_reason):
    """A fresh interpreter recovers the same durable Job; no Pan service needed."""
    from packages.core import session
    monkeypatch.setattr(jobs, "_scheduled_task_hooks", {})
    target = session.Session(id="ses_retry", name="Retry", workdir=str(tmp_path))
    session._cache[target.id] = target
    # Publish a process record as if shutdown interrupted its runner.
    monkeypatch.setattr(jobs, "_spawn_background_runner", lambda job, **kw: job)
    marker = tmp_path / "completed.txt"
    command = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('recovered')"
    if recovery_reason == "failed":
        command = ("from pathlib import Path; import time; p=Path(" + repr(str(marker))
                   + "); first=not p.exists(); p.write_text('recovered'); time.sleep(.1); "
                   "raise SystemExit(7 if first else 0)")
    job = jobs.start(target.id, [sys.executable, "-c", command],
        str(tmp_path), name="Restart contract", hide_window=False,
        retry_if=[recovery_reason], max_retries=1, deduplication="Writes the same marker value")
    if recovery_reason == "failed":
        from packages.core import background_runner
        assert background_runner._run_legacy(SimpleNamespace(
            job_id=job["jobId"], attempt_id=job["attemptId"])) == 7
        assert jobs.get(job["jobId"])["reason"] == "failed"
    root = jobs.DEFAULT_ROOT
    env = {**os.environ, "PYTHONPATH": str(jobs.PROJECT_ROOT), "PAN_BACKGROUND_JOBS_DIR": str(root)}
    script = "from packages.core import background_jobs as j; j.reconcile_running(); print(j.get(" + repr(job["jobId"]) + ")['attempt'])"
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=20)
    try:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "2"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = jobs.get(job["jobId"])
            if current["status"] in retry.TERMINAL:
                break
            time.sleep(.05)
        assert current["status"] == "completed", current
        assert marker.read_text() == "recovered"
        assert current["attempt"] == 2 and current["remaining"] == 0
        assert current["retryAttempts"][0]["reason"] == recovery_reason
        assert current["retryAttempts"][1]["status"] == "completed"
        assert jobs.reconcile_running() == 0
    finally:
        current = jobs.get(job["jobId"])
        for pid, created in ((current.get("pid"), current.get("processCreatedAt")),
                             (current.get("runnerPid"), current.get("runnerProcessCreatedAt"))):
            owned = jobs._owns_process(dict(pid=pid, processCreatedAt=created))
            if owned:
                jobs._kill_tree(owned)

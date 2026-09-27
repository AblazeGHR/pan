"""Durable per-process startup recovery decisions and idempotent effects."""

import asyncio
import json

import pytest

from packages.core import session as sess
from packages.core import worker
from packages.web import server


@pytest.fixture
def recovery_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(server, "_STARTUP_RECOVERY_GENERATION", "test-generation")
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda _session_id: None)
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    sess._cache.clear()
    sess._newline_terminated_jsonl.clear()
    server._STARTUP_RECOVERY_INFLIGHT.clear()
    created = sess.create("startup-candidate")
    created.last_legal_worker_state = "running"
    sess.save(created)
    yield tmp_path, created
    sess._cache.clear()
    sess._newline_terminated_jsonl.clear()
    server._STARTUP_RECOVERY_INFLIGHT.clear()


def claim_owner():
    return asyncio.run(server.api_main_startup_recovery_claim({
        "generation": "test-generation", "tabId": "tab-a",
    }))


def set_startup_preference(monkeypatch, preference):
    monkeypatch.setattr(server, "_session_lifecycle_preferences", lambda config=None: {
        "exitStrategy": "ask", "startupPreference": preference,
    })


def test_startup_generation_snapshot_and_cross_tab_claim_are_durable(recovery_env):
    root, candidate = recovery_env
    record = asyncio.run(server.api_main_startup_recovery())
    assert record["generation"] == "test-generation"
    assert record["state"] == "pending"
    assert [row["id"] for row in record["candidateSnapshot"]] == [candidate.id]

    first = claim_owner()
    second = asyncio.run(server.api_main_startup_recovery_claim({
        "generation": "test-generation", "tabId": "tab-b",
    }))
    assert first["claimed"] is True
    assert second["claimed"] is False
    record_path = root / "data" / "startup_recovery" / "test-generation.json"
    saved = json.loads(record_path.read_text())
    assert saved["claim"]["tabId"] == "tab-a"
    saved["claim"]["leaseUntil"] = 0
    record_path.write_text(json.dumps(saved))
    takeover = asyncio.run(server.api_main_startup_recovery_claim({
        "generation": "test-generation", "tabId": "tab-b",
    }))
    assert takeover["claimed"] is True
    assert json.loads(record_path.read_text())["claim"]["tabId"] == "tab-b"


def test_each_process_generation_uses_its_own_decision_record(recovery_env, monkeypatch):
    root, _candidate = recovery_env
    first = asyncio.run(server.api_main_startup_recovery())
    monkeypatch.setattr(server, "_STARTUP_RECOVERY_GENERATION", "next-generation")
    second = asyncio.run(server.api_main_startup_recovery())

    assert first["generation"] == "test-generation"
    assert second["generation"] == "next-generation"
    assert (root / "data" / "startup_recovery" / "test-generation.json").exists()
    assert (root / "data" / "startup_recovery" / "next-generation.json").exists()


def test_preserve_running_choice_does_not_mutate_session_metadata(recovery_env, monkeypatch):
    root, candidate = recovery_env
    metadata_path = root / "sessions" / f"{candidate.id}.json"
    before = metadata_path.read_bytes()
    asyncio.run(server.api_main_startup_recovery())
    claim_owner()

    result = asyncio.run(server.api_main_startup_recovery_decision({
        "generation": "test-generation", "tabId": "tab-a", "choice": "preserve-running",
    }))

    assert result["state"] == "completed"
    assert result["decision"] == "preserve-running"
    assert metadata_path.read_bytes() == before


def test_restart_decision_persists_first_and_repeated_post_does_not_broadcast_twice(
    recovery_env, monkeypatch,
):
    _, candidate = recovery_env
    asyncio.run(server.api_main_startup_recovery())
    claim_owner()
    calls = []

    async def broadcast(payload):
        calls.append(payload)
        return {
            "ok": True,
            "status": "queued",
            "results": [{"sessionId": payload["sessionIds"][0], "status": "queued"}],
        }

    monkeypatch.setattr(server, "api_sessions_broadcast", broadcast)
    body = {"generation": "test-generation", "tabId": "tab-a", "choice": "restart"}
    first = asyncio.run(server.api_main_startup_recovery_decision(body))
    second = asyncio.run(server.api_main_startup_recovery_decision(body))

    assert first["state"] == "completed"
    assert second["decisionId"] == first["decisionId"]
    assert len(calls) == 1
    assert calls[0]["text"] == "继续"
    assert calls[0]["source"] == "user"
    assert calls[0]["clientMessageId"] == "startup-recovery:test-generation"


def test_concurrent_identical_decisions_apply_broadcast_once(recovery_env, monkeypatch):
    _, candidate = recovery_env
    asyncio.run(server.api_main_startup_recovery())
    claim_owner()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def broadcast(payload):
        calls.append(payload)
        started.set()
        await release.wait()
        return {
            "ok": True, "status": "queued",
            "results": [{"sessionId": candidate.id, "status": "queued"}],
        }

    monkeypatch.setattr(server, "api_sessions_broadcast", broadcast)
    body = {"generation": "test-generation", "tabId": "tab-a", "choice": "restart"}

    async def run_pair():
        first = asyncio.create_task(server.api_main_startup_recovery_decision(body))
        await started.wait()
        second = asyncio.create_task(server.api_main_startup_recovery_decision(body))
        await asyncio.sleep(0)
        release.set()
        return await asyncio.gather(first, second)

    results = asyncio.run(run_pair())
    assert len(calls) == 1
    assert [result["state"] for result in results] == ["completed", "completed"]


def test_failed_startup_decision_only_allows_same_choice_retry(recovery_env, monkeypatch):
    _, candidate = recovery_env
    asyncio.run(server.api_main_startup_recovery())
    claim_owner()
    calls = []

    async def sync_actual(session_id, *, source):
        calls.append((session_id, source))
        if len(calls) == 1:
            return {"sessionId": session_id, "status": "error", "error": "disk"}
        return {"sessionId": session_id, "status": "updated", "legalWorkerState": "offline"}

    monkeypatch.setattr(worker, "sync_legal_worker_state_to_runtime", sync_actual)
    body = {"generation": "test-generation", "tabId": "tab-a", "choice": "sync-actual"}
    failed = asyncio.run(server.api_main_startup_recovery_decision(body))
    assert failed["state"] == "failed"
    assert failed["decision"] == "sync-actual"
    assert calls == [(candidate.id, "session-recovery/startup-sync-actual")]

    with pytest.raises(server.HTTPException) as caught:
        asyncio.run(server.api_main_startup_recovery_decision({
            **body, "choice": "restart",
        }))
    assert caught.value.status_code == 409

    completed = asyncio.run(server.api_main_startup_recovery_decision(body))
    assert completed["state"] == "completed"
    assert completed["attempts"] == 2
    assert len(calls) == 2


def test_live_broadcast_path_receives_client_message_id(recovery_env, monkeypatch):
    _, candidate = recovery_env
    sent = []

    async def send_session(session_id, text, **kwargs):
        sent.append((session_id, text, kwargs))
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_session", send_session)
    result = asyncio.run(server.api_sessions_broadcast({
        "sessionIds": [candidate.id],
        "text": "继续",
        "source": "user",
        "clientMessageId": "startup-recovery:test-generation",
    }))
    assert result["ok"] is True
    assert sent == [(
        candidate.id,
        "继续",
        {"source": "user", "force": False,
         "client_message_id": "startup-recovery:test-generation",
         "source_session_id": None},
    )]


def test_decision_api_rejects_other_generation_and_non_owner(recovery_env):
    asyncio.run(server.api_main_startup_recovery())
    with pytest.raises(server.HTTPException) as caught:
        asyncio.run(server.api_main_startup_recovery_decision({
            "generation": "old-generation", "tabId": "tab-a", "choice": "restart",
        }))
    assert caught.value.status_code == 409
    with pytest.raises(server.HTTPException) as caught:
        asyncio.run(server.api_main_startup_recovery_decision({
            "generation": "test-generation", "tabId": "tab-a", "choice": "restart",
        }))
    assert caught.value.status_code == 409


def test_automatic_ask_keeps_prompt_pending_and_applies_no_side_effect(recovery_env, monkeypatch):
    _root, candidate = recovery_env
    set_startup_preference(monkeypatch, "ask")

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("ask mode must wait for a dashboard choice")

    monkeypatch.setattr(server, "api_sessions_broadcast", unexpected)
    monkeypatch.setattr(worker, "sync_legal_worker_state_to_runtime", unexpected)
    result = asyncio.run(server._initialize_startup_recovery([candidate]))

    assert result["state"] == "pending"
    assert result["decision"] is None
    assert result["attempts"] == 0


def test_automatic_wake_uses_the_durable_normal_broadcast_identity(recovery_env, monkeypatch):
    _root, candidate = recovery_env
    set_startup_preference(monkeypatch, "wake-running")
    calls = []

    async def broadcast(payload):
        calls.append(payload)
        return {
            "ok": True,
            "results": [{"sessionId": candidate.id, "status": "queued"}],
        }

    monkeypatch.setattr(server, "api_sessions_broadcast", broadcast)
    result = asyncio.run(server._initialize_startup_recovery([candidate]))

    assert result["state"] == "completed"
    assert result["decision"] == "restart"
    assert result["autoPreference"] == "wake-running"
    assert len(calls) == 1
    assert calls[0] == {
        "sessionIds": [candidate.id],
        "text": "继续",
        "source": "user",
        "clientMessageId": "startup-recovery:test-generation",
    }


def test_automatic_sync_uses_runtime_helper(recovery_env, monkeypatch):
    _root, candidate = recovery_env
    set_startup_preference(monkeypatch, "sync-actual")
    calls = []

    async def sync_actual(session_id, *, source):
        calls.append((session_id, source))
        return {
            "sessionId": session_id,
            "status": "updated",
            "legalWorkerState": "offline",
            "runtimeWorkerStatus": "offline",
        }

    monkeypatch.setattr(worker, "sync_legal_worker_state_to_runtime", sync_actual)
    result = asyncio.run(server._initialize_startup_recovery([candidate]))

    assert result["state"] == "completed"
    assert result["decision"] == "sync-actual"
    assert result["autoPreference"] == "sync-actual"
    assert calls == [(candidate.id, "session-recovery/startup-sync-actual")]
    assert result["results"][0]["legalWorkerState"] == "offline"


def test_automatic_preserve_leaves_candidate_legal_state_unchanged(recovery_env, monkeypatch):
    _root, candidate = recovery_env
    set_startup_preference(monkeypatch, "preserve-running")

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("preserve mode must not wake or synchronize")

    monkeypatch.setattr(server, "api_sessions_broadcast", unexpected)
    monkeypatch.setattr(worker, "sync_legal_worker_state_to_runtime", unexpected)
    result = asyncio.run(server._initialize_startup_recovery([candidate]))

    assert result["state"] == "completed"
    assert result["decision"] == "preserve-running"
    assert result["results"] == [{
        "sessionId": candidate.id,
        "status": "preserved",
        "legalWorkerState": "running",
    }]
    assert sess.get(candidate.id, load_history=False).last_legal_worker_state == "running"


def test_automatic_startup_with_no_candidates_is_a_quiet_noop(recovery_env, monkeypatch):
    set_startup_preference(monkeypatch, "wake-running")

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("empty candidate snapshots must have no effects")

    monkeypatch.setattr(server, "api_sessions_broadcast", unexpected)
    monkeypatch.setattr(worker, "sync_legal_worker_state_to_runtime", unexpected)
    result = asyncio.run(server._initialize_startup_recovery([]))

    assert result["state"] == "no_candidates"
    assert result["candidateSnapshot"] == []
    assert result["attempts"] == 0


def test_automatic_failure_can_be_diagnosed_and_retried_only_as_saved_choice(
    recovery_env, monkeypatch,
):
    _root, candidate = recovery_env
    set_startup_preference(monkeypatch, "wake-running")
    calls = []

    async def broadcast(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {
                "ok": False,
                "results": [{"sessionId": candidate.id, "status": "error", "error": "disk"}],
            }
        return {
            "ok": True,
            "results": [{"sessionId": candidate.id, "status": "queued"}],
        }

    monkeypatch.setattr(server, "api_sessions_broadcast", broadcast)
    failed = asyncio.run(server._initialize_startup_recovery([candidate]))
    assert failed["state"] == "failed"
    assert failed["decision"] == "restart"
    assert failed["attempts"] == 1
    assert failed["results"][0]["error"] == "disk"

    assert claim_owner()["claimed"] is True
    completed = asyncio.run(server.api_main_startup_recovery_decision({
        "generation": "test-generation", "tabId": "tab-a", "choice": "restart",
    }))

    assert completed["state"] == "completed"
    assert completed["attempts"] == 2
    assert completed["decisionId"] == failed["decisionId"]
    assert len(calls) == 2
    assert {call["clientMessageId"] for call in calls} == {
        "startup-recovery:test-generation",
    }


def test_automatic_broadcast_exception_has_session_rows_for_prompt_diagnostics(
    recovery_env, monkeypatch,
):
    _root, candidate = recovery_env
    set_startup_preference(monkeypatch, "wake-running")

    async def broadcast(_payload):
        raise RuntimeError("queue store unavailable")

    monkeypatch.setattr(server, "api_sessions_broadcast", broadcast)
    result = asyncio.run(server._initialize_startup_recovery([candidate]))

    assert result["state"] == "failed"
    assert result["error"] == "Startup recovery failed: queue store unavailable"
    assert result["results"] == [{
        "sessionId": candidate.id,
        "status": "error",
        "error": "Startup recovery failed: queue store unavailable",
    }]

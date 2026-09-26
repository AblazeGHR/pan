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

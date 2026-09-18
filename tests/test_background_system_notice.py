"""T-046 structured identity and Pan-system Job notice coverage."""

import asyncio

from unittest.mock import AsyncMock

import pytest

from packages.core import session as _sess
from packages.core import worker
from packages.web import server as web_server


@pytest.fixture(autouse=True)
def clean_sessions():
    _sess._cache.clear()
    worker.set_broadcaster(None)
    yield
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _session(sid, name=None):
    value = _sess.Session(id=sid, name=name or sid)
    _sess._cache[sid] = value
    return value


def test_system_notice_has_structured_job_identity_and_system_prefix(monkeypatch):
    target = _session("ses_target", "Agent B")
    _session("ses_creator", "Agent A")
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    monkeypatch.setattr(worker, "_wake_worker", lambda *args, **kwargs: asyncio.sleep(0))

    result = asyncio.run(worker.enqueue_notice(
        target.id,
        '{"jobId":"job_t046","status":"failed"}',
        source="automation",
        event_id="job_t046:terminal",
        notice_kind="background_job_terminal",
        job_id="job_t046",
        notice_status="failed",
        creator_session_id="ses_creator",
        target_session_ids=[target.id],
    ))

    assert result["ok"] is True
    item = target.queue_pending[0]
    assert item["status"] == "failed"
    assert item["jobId"] == "job_t046"
    assert item["noticeKind"] == "background_job_terminal"
    assert item["creatorSessionId"] == "ses_creator"
    assert item["targetSessionId"] == "ses_target"
    assert item["targetSessionIds"] == ["ses_target"]
    assert item["eventId"] == "job_t046:terminal"
    rendered = worker._format_report_batch([item])
    assert "////by pan system" in rendered
    assert "jobId: job_t046" in rendered
    assert "creatorSessionId: ses_creator" in rendered
    assert "targetSessionId: ses_target" in rendered
    assert "@@@@by agent" not in rendered
    serialized = web_server._serialize_queue_item(item, target)
    assert serialized["meta"]["status"] == "failed"
    assert serialized["meta"]["jobId"] == "job_t046"
    assert serialized["meta"]["targetSessionIds"] == ["ses_target"]


def test_system_notice_without_creator_stays_system_notice():
    rendered = worker._format_report_batch([{
        "type": "notice", "status": "cancelled", "result": "cancelled",
        "source": "automation", "noticeKind": "background_job_terminal",
        "jobId": "job_no_creator", "targetSessionId": "ses_target",
        "targetSessionIds": ["ses_target"],
    }])
    assert rendered.startswith("////by pan system\n")
    assert "creatorSessionId" not in rendered
    assert "unknown" not in rendered


def test_agent_notice_without_notice_kind_keeps_agent_prefix():
    rendered = worker._format_report_batch([{
        "type": "notice", "status": "notice", "result": "hello",
        "source": "agent", "sourceSessionId": "ses_creator",
        "sessionId": "ses_target", "taskId": None, "workerId": None,
    }])
    assert "@@@@by agent : ses_creator" in rendered
    assert "////by pan system" not in rendered

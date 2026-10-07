"""Read-only compact navigation projection retains canonical identity and provenance."""
import asyncio
import json
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from packages.web import server

@pytest.mark.parametrize("content,expected", [
    ("////by system: inline text", "inline text"),
    ("  ////by pan system\nbody  with\tspace", "body with space"),
    ("@@@@by agent: very long sender metadata\nreport body", "report body"),
    ("////by agent: sender\nfollowup", "followup"),
    ("@@@@by qq: sender\nqq body", "qq body"),
    ("plain content", "plain content"),
    ("x" * 2_000_000, "x" * 119 + "…"),
], ids=["inline-system","system-newline","report","ma","qq","plain","large-body"])
def test_preview(content, expected):
    assert server._navigation_preview(content) == expected


def test_compact_rows_keep_identity_and_source_without_body_or_parts():
    history = [{"role": "user", "source": "agent", "taskIdSource": "active", "messageId": "existing-id", "content": "////by agent: sender\n" + "a" * 2_000_000, "parts": [{"data": "secret-attachment"}]}, {"role": "assistant", "content": "ordinary"}]
    page = {"history": history, "start": 123, "total": 125, "historyEpoch": "epoch", "historyRevision": 9, "hasMore": True}
    with patch.object(server, "_history_page_lookup", return_value=page) as lookup:
        compact = server._navigation_page_lookup("sid", 125, 99999)
    lookup.assert_called_once_with("sid", 125, 500, True)
    assert compact["historyEpoch"] == "epoch"
    assert compact["history"][0]["messageId"] == "existing-id"
    assert compact["history"][0]["source"] == "agent"
    assert compact["history"][0]["taskIdSource"] == "active"
    assert compact["history"][1]["messageId"] == "legacy:sid:epoch:124"
    assert all(len(row["content"]) <= 64 for row in compact["history"])
    assert "parts" not in compact["history"][0]
    assert len(json.dumps(compact)) < 2000
    assert len(history[0]["content"]) > 2_000_000  # no mutation


def test_missing_navigation_session_is_observable_404():
    with patch.object(server, "_navigation_page_lookup", return_value=server._NOT_FOUND):
        with pytest.raises(HTTPException) as error:
            asyncio.run(server.api_session_navigation("missing"))
    assert error.value.status_code == 404

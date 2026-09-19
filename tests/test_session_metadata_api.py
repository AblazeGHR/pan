"""Lean Session metadata view keeps UI reads small without changing full API semantics."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as session_module
import packages.web.server as web_server


def test_metadata_view_omits_history_raw_usage_and_last_result(monkeypatch):
    target = session_module.Session(
        id="ses-metadata",
        name="Metadata",
        system_prompt="Keep this prompt in the detail view",
        history=[{"role": "user", "content": f"message-{i}"} for i in range(100)],
        raw_usage={"model": {"rawUsage": {"input_tokens": 10}}},
        total_usage={"prompt_tokens": 10},
        last_result={"status": "done", "result": "large result"},
        managed=["ses-child"],
        updated_at="2026-09-19T01:02:03+00:00",
    )
    monkeypatch.setattr(web_server.sess, "get", lambda sid: target if sid == target.id else None)

    metadata = asyncio.run(web_server.api_get_session(target.id, view="metadata"))
    assert metadata["id"] == target.id
    assert metadata["systemPrompt"] == target.system_prompt
    assert metadata["managed"] == ["ses-child"]
    assert metadata["updatedAt"] == target.updated_at
    assert "history" not in metadata
    assert "rawUsage" not in metadata
    assert "lastResult" not in metadata


def test_full_session_view_remains_backward_compatible(monkeypatch):
    target = session_module.Session(
        id="ses-full",
        name="Full",
        history=[{"role": "user", "content": "kept"}],
        raw_usage={"model": {"rawUsage": {"input_tokens": 10}}},
        last_result={"status": "done", "result": "kept"},
    )
    monkeypatch.setattr(web_server.sess, "get", lambda sid: target)

    full = asyncio.run(web_server.api_get_session(target.id))
    assert full["history"]
    assert full["rawUsage"]
    assert full["lastResult"]["status"] == "done"


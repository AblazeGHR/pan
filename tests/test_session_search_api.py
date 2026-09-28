"""Read-only full-history search API coverage."""

import asyncio
import json

from packages.core import session as sess
from packages.web import server


def test_session_search_scans_jsonl_without_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    sess._cache.clear()
    session_id = "ses-search"
    (tmp_path / f"{session_id}.json").write_text(
        json.dumps({"id": session_id, "name": "Search"}), encoding="utf-8"
    )
    history = tmp_path / f"{session_id}.history.jsonl"
    history.write_text(
        "\n".join([
            json.dumps({"role": "user", "content": "old needle"}),
            json.dumps({"role": "assistant", "content": "nothing"}),
            json.dumps({"role": "user", "content": "NEEDLE twice: needle"}),
        ]) + "\n",
        encoding="utf-8",
    )

    result = asyncio.run(server.api_session_search(session_id, q="needle"))

    assert result["total"] == 3
    assert result["totalMatches"] == 3
    assert [item["index"] for item in result["matches"]] == [0, 2]
    assert result["matches"][1]["fromEnd"] == 0
    assert history.read_text(encoding="utf-8").casefold().count("needle") == 3

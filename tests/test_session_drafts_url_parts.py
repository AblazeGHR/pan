"""Draft URL-node schema: persisted parts must be safe and exact.

The composer persists inline URL nodes alongside text and attachment parts.
These tests pin the server half of that contract: an http(s) address and its
local occurrence id round-trip verbatim (query/hash included), while a
dangerous or malformed address is rejected instead of being silently stored.
"""

import pytest

from packages.core.session_drafts import normalize_draft


def draft_with_parts(parts):
    text = "".join(part["value"] for part in parts if part["type"] == "text")
    return {"text": text, "parts": parts, "attachments": []}


def test_url_part_round_trips_query_and_hash_verbatim():
    url = "https://example.test/a%20b?q=1&next=%2Fx#L42-L48"
    normalized = normalize_draft(
        draft_with_parts([{"type": "url", "id": "url_1", "url": url, "label": "Docs (draft)"}])
    )
    assert normalized["parts"] == [
        {"type": "url", "id": "url_1", "url": url, "label": "Docs (draft)"}
    ]
    assert normalized["text"] == ""


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "file:///C:/secret.txt",
        "ftp://example.test/a",
        "https://exa mple.test/a",
        "https://example.test/a\nnewline",
    ],
)
def test_rejects_unsafe_url_parts(url):
    with pytest.raises(ValueError):
        normalize_draft(draft_with_parts([{"type": "url", "id": "url_1", "url": url}]))


def test_url_part_requires_identity():
    with pytest.raises(ValueError):
        normalize_draft(
            draft_with_parts([{"type": "url", "url": "https://example.test/a"}])
        )


def test_text_projection_still_matches_only_text_parts():
    normalized = normalize_draft(
        draft_with_parts(
            [
                {"type": "text", "value": "see "},
                {"type": "url", "id": "url_1", "url": "https://example.test/a"},
                {"type": "text", "value": " now"},
            ]
        )
    )
    assert normalized["text"] == "see  now"
    with pytest.raises(ValueError):
        normalize_draft(
            {
                "text": "wrong",
                "parts": [{"type": "url", "id": "url_1", "url": "https://example.test/a"}],
                "attachments": [],
            }
        )

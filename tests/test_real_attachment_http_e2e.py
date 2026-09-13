"""Real-process HTTP coverage for the attachment reference contract.

The fixture reuses the repository's isolated Pan launcher and binds only to
8767.  It never uses the production data directory or the protected 8768
service.
"""

from __future__ import annotations

import httpx
import pytest

from tests.test_real_queue_http_e2e import _PanRuntime, _wait_for


@pytest.fixture
def attachment_runtime(tmp_path):
    runtime = _PanRuntime(tmp_path / "attachment-http-data")
    runtime.start()
    try:
        yield runtime
    finally:
        runtime.stop()


def _upload(runtime: _PanRuntime, session_id: str, body: bytes, filename: str) -> dict:
    from urllib.parse import quote

    response = httpx.post(
        f"{runtime.base_url}/api/sessions/{session_id}/attachments",
        content=body,
        headers={"X-Filename": quote(filename)},
        timeout=15,
        trust_env=False,
        verify=False,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_real_http_upload_reference_send_and_restart(attachment_runtime):
    runtime = attachment_runtime
    first = runtime.request("POST", "/api/sessions", json={
        "name": "attachment-http-a", "adapter": "cbc",
    })
    second = runtime.request("POST", "/api/sessions", json={
        "name": "attachment-http-b", "adapter": "cbc",
    })
    first_id, second_id = first["id"], second["id"]

    uploaded = _upload(runtime, first_id, "附件 body".encode("utf-8"), "需求说明 [v1](最终).txt")
    assert uploaded["ok"] is True
    assert uploaded["attachmentId"] == uploaded["storageFilename"]
    assert uploaded["displayName"] == "需求说明 [v1](最终).txt"
    assert uploaded["filename"] == uploaded["displayName"]
    assert uploaded["path"].endswith(uploaded["storageFilename"])
    assert uploaded["href"].endswith(
        f"?session_id={first_id}"
    )

    with httpx.Client(base_url=runtime.base_url, timeout=15, trust_env=False, verify=False) as client:
        download = client.get(uploaded["href"])
    assert download.status_code == 200
    assert download.content == "附件 body".encode("utf-8")

    message = f"前置 [{uploaded['displayName']}]({uploaded['href']}) 后置"
    queued = runtime.request(
        "POST", f"/api/sessions/{first_id}/queue",
        json={"text": message, "clientMessageId": "attachment-http-1"},
    )
    assert queued["ok"] is True
    assert queued["item"]["text"] == message

    _wait_for(
        lambda: next(
            (
                entry for entry in runtime.request(
                    "GET", f"/api/sessions/{first_id}/history?limit=100"
                )["history"]
                if entry.get("role") == "user" and entry.get("content") == message
            ),
            None,
        ),
        label="uploaded attachment message in real session history",
    )

    cross_session = runtime.request(
        "POST", f"/api/sessions/{second_id}/queue",
        json={
            "text": f"[cross-session]({uploaded['href']})",
            "clientMessageId": "attachment-http-cross-session",
        },
    )
    assert cross_session == {"ok": False, "error": {
        "code": "attachment_session_mismatch",
        "message": "Attachment belongs to another session",
    }}

    stale = runtime.request(
        "POST", f"/api/sessions/{first_id}/queue",
        json={
            "text": (
                "[stale](/api/attachments/"
                "upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.txt"
                f"?session_id={first_id})"
            ),
            "clientMessageId": "attachment-http-stale",
        },
    )
    assert stale == {"ok": False, "error": {
        "code": "attachment_not_found",
        "message": "Attachment is no longer available",
    }}

    runtime.stop()
    runtime.start()
    restored_history = runtime.request(
        "GET", f"/api/sessions/{first_id}/history?limit=100"
    )["history"]
    assert any(entry.get("content") == message for entry in restored_history)
    with httpx.Client(base_url=runtime.base_url, timeout=15, trust_env=False, verify=False) as client:
        restored_download = client.get(uploaded["href"])
    assert restored_download.status_code == 200
    assert restored_download.content == "附件 body".encode("utf-8")

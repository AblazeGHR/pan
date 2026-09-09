"""Regression tests for web-server console logging."""

import io

import packages.web.server as server


def test_log_escapes_text_not_supported_by_cp1252_stdout(monkeypatch):
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(server.sys, "stdout", stream)

    server._log("GET  /api/health  → 200")
    stream.flush()

    assert "\\u2192" in output.getvalue().decode("cp1252")

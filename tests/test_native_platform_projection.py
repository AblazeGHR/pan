"""Native adapter support and platform support are independent gates."""
import asyncio
from unittest.mock import Mock

import pytest

from packages.core import platform, session
from packages.web import server


@pytest.mark.parametrize("adapter", ["codex", "cbc", "kimi"])
def test_native_projection_obeys_platform(monkeypatch, adapter):
    s = session.Session(id="ses-native-projection", name="native", adapter=adapter)
    monkeypatch.setattr(platform, "supports", lambda name: False)
    assert server._session_to_api(s)["nativeControlCapable"] is False
    assert server._session_summary(s)["nativeControlCapable"] is False
    monkeypatch.setattr(platform, "supports", lambda name: True)
    assert server._session_to_api(s)["nativeControlCapable"] is True
    assert server._session_summary(s)["nativeControlCapable"] is True


def test_creation_rejects_native_before_session_write(monkeypatch):
    monkeypatch.setattr(platform, "supports", lambda name: False)
    create = Mock(side_effect=AssertionError("must not create"))
    monkeypatch.setattr(session, "create", create)
    response = asyncio.run(server.api_create_session({
        "name": "native-denied", "adapter": "codex", "nativeControl": True,
        "mcpServers": [],
    }))
    assert "unsupported_platform" in response["error"]
    create.assert_not_called()


def test_creation_rejects_native_before_resolving_workdir(monkeypatch):
    monkeypatch.setattr(platform, "supports", lambda name: False)
    resolve = Mock(side_effect=AssertionError("must not resolve/create directory"))
    monkeypatch.setattr(server, "_resolve_workdir", resolve)
    with pytest.raises(ValueError, match="unsupported_platform"):
        server._build_session_params({"name": "denied", "adapter": "codex", "nativeControl": True})
    resolve.assert_not_called()


def test_explicit_stream_creation_remains_supported(monkeypatch):
    monkeypatch.setattr(platform, "supports", lambda name: False)
    params = server._build_session_params({
        "name": "stream-allowed", "adapter": "codex", "nativeControl": False,
        "mcpServers": [],
    })
    assert params["adapter_config"].get("native_control") is not True

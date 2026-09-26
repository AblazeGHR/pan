"""New Session defaults API validation and config.json persistence."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.core.config as config
import packages.web.server as srv


def _use_temp_config(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    return path


def _payload(**overrides):
    result = {
        "adapter": "cbc",
        "outputMode": "",
        "sessionTemplate": "",
        "workdir": "D:/work",
    }
    result.update(overrides)
    return result


def test_put_new_session_defaults_persists_and_preserves_other_config(tmp_path, monkeypatch):
    path = _use_temp_config(tmp_path, monkeypatch)
    unrelated = {"port": 9123, "ui": {"showQQ": False}, "custom": {"keep": 1}}
    path.write_text(json.dumps(unrelated), encoding="utf-8")

    response = asyncio.run(srv.api_put_new_session_defaults(_payload()))

    expected = _payload()
    assert response == {"defaults": expected}
    assert json.loads(path.read_text(encoding="utf-8")) == {
        **unrelated,
        "new_session_defaults": expected,
    }
    assert asyncio.run(srv.api_get_new_session_defaults()) == {"defaults": expected}


@pytest.mark.parametrize(
    "payload",
    [
        _payload(adapter="not-registered"),
        _payload(outputMode="not-a-mode"),
        _payload(workdir=None),
        _payload(extra="not-allowed"),
        {"adapter": "cbc", "outputMode": "", "sessionTemplate": ""},
    ],
)
def test_put_new_session_defaults_rejects_invalid_fields(tmp_path, monkeypatch, payload):
    path = _use_temp_config(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(srv.api_put_new_session_defaults(payload))
    assert exc.value.status_code == 400
    assert not path.exists()


def test_put_new_session_defaults_rejects_unknown_template(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)

    class EmptyTemplateManager:
        def manifest_changed(self):
            return False

        def get_session_template(self, name):
            return None

    monkeypatch.setattr(srv, "_character_manager", EmptyTemplateManager())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(srv.api_put_new_session_defaults(
            _payload(sessionTemplate="missing-template"),
        ))
    assert exc.value.status_code == 400

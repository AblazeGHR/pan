"""Regression coverage for the React-only web entry points."""

import asyncio

from fastapi.testclient import TestClient

from packages.core import config
from packages.web import server


def test_config_has_no_frontend_setting():
    assert "frontend" not in config.DEFAULT_CONFIG


def test_root_redirects_to_react_when_dist_exists(monkeypatch):
    monkeypatch.setattr(server, "REACT_DIST_EXISTS", True)
    with TestClient(server.app) as client:
        response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/react/"


def test_missing_react_dist_is_actionable_and_never_falls_back(monkeypatch):
    monkeypatch.setattr(server, "REACT_DIST_EXISTS", False)
    with TestClient(server.app) as client:
        root = client.get("/")
        react = client.get("/react/")
        vanilla = client.get("/vanilla")
    assert root.status_code == 503
    assert "packages/web/dist" in root.text
    assert react.status_code == 503
    assert "pnpm" in react.text
    assert vanilla.status_code == 404


def test_config_reload_does_not_report_removed_frontend_setting(monkeypatch):
    result = asyncio.run(server.api_config_reload({"scope": "worker"}))
    assert "frontend" not in result["requiresRestart"]

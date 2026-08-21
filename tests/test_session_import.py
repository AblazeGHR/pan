"""Tests for session_import MCP tool + import endpoint template/panAccess support.

Covers (design docs/design-import-session-mcp.md §8.1 / §8.2):
- Backend: import endpoints (cbc + kimi) accept sessionTemplate / panAccess on
  new-session branch (reusing _build_session_params template resolution);
  reimport responses carry `reimported: True`; _session_summary exposes
  cliSessionId for the MCP reimport precheck.
- MCP: session_import four-action dispatch (list_projects / list_workspaces /
  list_sessions / import), body construction, history→historyCount trimming,
  imported/reimportedExisting flags, _auto_claim, and the §8.2 reimport
  precheck for restricted callers.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.mcp import server as mcp_server
from packages.web import server


def _cleanup():
    _sess._cache.clear()
    _sess._all_loaded = False
    worker.workers.clear()
    worker.set_broadcaster(None)


def _fresh_session_dir() -> Path:
    """Point _sess at a temp dir so real data/sessions/ is never touched."""
    tmp = Path(tempfile.mkdtemp()) / "sessions"
    tmp.mkdir(parents=True, exist_ok=True)
    _sess.SESSION_DIR = tmp
    return tmp


def _write_cbc_jsonl(sid: str, lines: list[dict]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    jsonl = tmp / f"{sid}.jsonl"
    jsonl.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in lines),
        encoding="utf-8",
    )
    return jsonl


# ══════════════════════════════════════════════════════════════════════════ #
#  Backend: import endpoints support sessionTemplate / panAccess            #
# ══════════════════════════════════════════════════════════════════════════ #


def test_session_summary_includes_cli_session_id():
    _cleanup()
    s = _sess.Session(id="ses_a", name="a", adapter="cbc")
    s.cli_session_id = "cli-xyz-123"
    d = server._session_summary(s)
    assert d["cliSessionId"] == "cli-xyz-123"
    assert d["id"] == "ses_a"
    _cleanup()


def test_import_cbc_template_and_pan_access(monkeypatch):
    """New import applies sessionTemplate + panAccess via _build_session_params."""
    _cleanup()
    _fresh_session_dir()
    sid = "tpl-import-0001"
    jsonl = _write_cbc_jsonl(sid, [
        {"type": "message", "role": "user", "sessionId": sid,
         "content": [{"type": "text", "text": "hi"}], "timestamp": 1786800000000},
    ])

    from packages.core.character import CharacterManager
    cm = CharacterManager(str(Path(tempfile.mkdtemp())))
    cm.load_manifest(["packages/mcp/manifest.json"])
    monkeypatch.setattr(server, "_character_manager", cm)

    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=jsonl):
        resp = asyncio.run(server.api_cbc_sessions_import({
            "session_id": sid,
            "cwd": "D:/tmp/proj",
            "name": "my-imported",
            "sessionTemplate": "meta-agent",
            "panAccess": {"restrictToManaged": True, "autoClaimCreated": True},
        }))

    assert "error" not in resp, resp
    s = next(x for x in _sess.list_all() if x.cli_session_id == sid)
    assert s.name == "my-imported"
    assert s.session_template == "meta-agent"
    assert s.model == "hy3"  # from template
    assert s.restrict_to_managed is True  # explicit panAccess override
    assert s.auto_claim_created is True   # explicit panAccess override
    assert s.can_claim_unmanaged is True  # template value (meta-agent default)
    # workdir stays the external project path, NOT data/workdirs/<name>
    assert s.workdir == "D:/tmp/proj"
    # template mcp_servers resolved into adapter_config
    assert s.adapter_config.get("mcp_servers")
    assert resp["workdir"] == "D:/tmp/proj"
    assert resp["sessionTemplate"] == "meta-agent"
    assert resp["panAccess"]["restrictToManaged"] is True
    _cleanup()


def test_import_without_template_keeps_existing_behavior():
    """No sessionTemplate/panAccess → identical to the old import path."""
    _cleanup()
    _fresh_session_dir()
    sid = "plain-import-0001"
    jsonl = _write_cbc_jsonl(sid, [
        {"type": "message", "role": "user", "sessionId": sid,
         "content": [{"type": "text", "text": "hello"}], "timestamp": 1786800000000},
    ])

    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=jsonl):
        resp = asyncio.run(server.api_cbc_sessions_import(
            {"session_id": sid, "cwd": "D:/tmp/plain"}))

    assert "error" not in resp, resp
    s = next(x for x in _sess.list_all() if x.cli_session_id == sid)
    assert s.workdir == "D:/tmp/plain"
    assert s.session_template is None
    assert s.restrict_to_managed is False
    assert s.auto_claim_created is False
    assert resp.get("reimported") is None
    _cleanup()


def test_import_kimi_template_and_pan_access(monkeypatch):
    """kimi import applies template/panAccess on new-session branch."""
    _cleanup()
    _fresh_session_dir()
    sid = "kimi-import-0001"

    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.kimi.sessions.parse_kimi_history",
               return_value=[{"role": "user", "content": "hi"}]):
        resp = asyncio.run(server.api_kimi_sessions_import({
            "session_id": sid,
            "cwd": "D:/ws",
            "name": "kimi-imported",
            "panAccess": {"canClaimUnmanaged": True},
        }))

    assert "error" not in resp, resp
    s = next(x for x in _sess.list_all() if x.cli_session_id == sid)
    assert s.adapter == "kimi"
    assert s.name == "kimi-imported"
    assert s.workdir == "D:/ws"
    assert s.can_claim_unmanaged is True
    assert s.restrict_to_managed is False
    _cleanup()


def test_import_reimport_marks_reimported():
    """Overwriting an existing Pan session returns reimported: True."""
    _cleanup()
    _fresh_session_dir()
    sid = "reimport-0001"
    jsonl = _write_cbc_jsonl(sid, [
        {"type": "message", "role": "user", "sessionId": sid,
         "content": [{"type": "text", "text": "new"}], "timestamp": 1786800000000},
    ])

    existing = _sess.Session(id="ses_existing", name="old", model="test-model",
                             adapter="cbc")
    existing.cli_session_id = sid
    existing.history = [{"role": "user", "content": "old"}]
    _sess._cache["ses_existing"] = existing

    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=jsonl):
        resp = asyncio.run(server.api_cbc_sessions_import(
            {"session_id": sid, "cwd": "D:/tmp/reimport"}))

    assert "error" not in resp, resp
    assert resp["reimported"] is True
    assert resp["id"] == "ses_existing"  # in-place overwrite
    assert resp["history"] == [{"role": "user", "content": "new"}]
    _cleanup()


def test_import_bad_template_degrades_to_no_mcp(monkeypatch):
    """Unknown template MCP server degrades to no MCP instead of 500ing."""
    _cleanup()
    _fresh_session_dir()
    sid = "badtpl-0001"
    jsonl = _write_cbc_jsonl(sid, [
        {"type": "message", "role": "user", "sessionId": sid,
         "content": [{"type": "text", "text": "hi"}], "timestamp": 1},
    ])

    import json as _json
    from packages.core.character import CharacterManager
    cm = CharacterManager(str(Path(tempfile.mkdtemp())))
    manifest = {
        "session_templates": [
            {"name": "broken", "adapter": "cbc", "model": "hy3",
             "mcp_mode": "always", "mcp_servers": ["does-not-exist"]}
        ],
        "mcp_servers": [],
        "command_routes": [],
    }
    mp = Path(tempfile.mkdtemp()) / "manifest.json"
    mp.write_text(_json.dumps(manifest), encoding="utf-8")
    cm.load_manifest([str(mp)])
    monkeypatch.setattr(server, "_character_manager", cm)

    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=jsonl):
        resp = asyncio.run(server.api_cbc_sessions_import(
            {"session_id": sid, "cwd": "D:/tmp/x", "sessionTemplate": "broken"}))

    assert "error" not in resp, resp
    s = next(x for x in _sess.list_all() if x.cli_session_id == sid)
    assert s.session_template == "broken"
    assert (s.adapter_config.get("mcp_servers") or []) == []  # 降级为无 MCP
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  MCP: session_import action dispatch                                       #
# ══════════════════════════════════════════════════════════════════════════ #


class _FakeAPI:
    """Routing fake for packages.mcp.server._api."""

    def __init__(self, identity=None, summary=None, import_resp=None):
        self.identity = identity
        self.summary = summary or []
        self.import_resp = import_resp
        self.calls = []

    def __call__(self, method, path, body=None, timeout=30.0):
        self.calls.append((method, path, body, timeout))
        if method == "GET" and path.startswith("/api/sessions/") and "?" not in path:
            return dict(self.identity) if self.identity else {"error": "not found"}
        if method == "GET" and path == "/api/sessions?summary=1":
            return {"sessions": [dict(s) for s in self.summary]}
        if method == "GET" and path.startswith("/api/cbc/projects"):
            return {"projects": [
                {"project_dir": "d-project-Pan", "session_count": 5,
                 "resumable_count": 3, "drive": "D:"}]}
        if method == "GET" and path.startswith("/api/kimi/workspaces"):
            return {"workspaces": [
                {"root": "D:/ws", "name": "ws", "workspace_id": 1,
                 "session_count": 2}]}
        if method == "GET" and path.startswith("/api/cbc/sessions"):
            return {"sessions": [
                {"session_id": "sid-1", "title": "Explore parser",
                 "message_count": 6, "last_timestamp": "2026-01-01T00:00:00",
                 "model": "hy3"}], "total": 1, "shown": 1}
        if method == "GET" and path.startswith("/api/kimi/sessions"):
            return {"sessions": [{"session_id": "ksid-1", "title": "Kimi topic"}]}
        if method == "POST" and path.endswith("/sessions/import"):
            return dict(self.import_resp) if self.import_resp else {"error": "no import_resp"}
        if method == "POST" and path == "/api/claim":
            return {"ok": True}
        return {"ok": True}


def _import_session_dict(**over):
    d = {
        "id": "ses_new", "name": "n", "adapter": "cbc", "cliSessionId": "sid-1",
        "model": "hy3", "permissionMode": None,
        "panAccess": {"restrictToManaged": False, "canClaimUnmanaged": False,
                      "autoClaimCreated": False},
        "sessionTemplate": None, "workdir": "D:/PROJECT/CLIConductor",
        "workerStatus": None,
        "history": [{"role": "user", "content": "a"},
                    {"role": "assistant", "content": "b"},
                    {"role": "user", "content": "c"}],
        "rawUsage": {"total": 1}, "totalUsage": {"total": 1},
    }
    d.update(over)
    return d


def test_mcp_import_invalid_action(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="browse")
    assert r["ok"] is False and r["error"]["code"] == "invalid_action"
    _cleanup()


def test_mcp_import_list_projects(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="list_projects")
    assert r["projects"][0]["project_dir"] == "d-project-Pan"
    assert fake.calls[0][0] == "GET"
    assert fake.calls[0][1] == "/api/cbc/projects"
    _cleanup()


def test_mcp_import_list_workspaces(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="list_workspaces")
    assert r["workspaces"][0]["root"] == "D:/ws"
    assert fake.calls[0][1] == "/api/kimi/workspaces"
    _cleanup()


def test_mcp_import_list_sessions_cbc_path(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="list_sessions", project_dir="d-project-Pan")
    assert r["sessions"][0]["session_id"] == "sid-1"
    path = fake.calls[0][1]
    assert path.startswith("/api/cbc/sessions")
    from urllib.parse import parse_qs, urlsplit
    qs = parse_qs(urlsplit(path).query)
    assert qs["project_dir"] == ["d-project-Pan"]
    _cleanup()


def test_mcp_import_list_sessions_cbc_cwd_query(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    mcp_server.session_import(action="list_sessions", cwd="D:/PROJECT/CLIConductor",
                              query="parser", limit=10)
    from urllib.parse import parse_qs, urlsplit
    qs = parse_qs(urlsplit(fake.calls[0][1]).query)
    assert qs["cwd"] == ["D:/PROJECT/CLIConductor"]
    assert qs["q"] == ["parser"]
    _cleanup()


def test_mcp_import_list_sessions_kimi_cwd(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="list_sessions", adapter="kimi",
                                  cwd="D:/ws")
    assert r["sessions"][0]["session_id"] == "ksid-1"
    assert "cwd" in fake.calls[0][1]
    _cleanup()


def test_mcp_import_list_sessions_missing_project(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="list_sessions")
    assert r["ok"] is False and r["error"]["code"] == "missing_params"
    _cleanup()


def test_mcp_import_list_sessions_kimi_missing_cwd(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="list_sessions", adapter="kimi")
    assert r["ok"] is False and r["error"]["code"] == "missing_params"
    _cleanup()


def test_mcp_import_missing_session_id(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="import")
    assert r["ok"] is False and r["error"]["code"] == "missing_params"
    _cleanup()


def test_mcp_import_kimi_missing_cwd(monkeypatch):
    _cleanup()
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="import", adapter="kimi", session_id="x")
    assert r["ok"] is False and r["error"]["code"] == "missing_params"
    _cleanup()


def test_mcp_import_cbc_builds_body_and_autoclaims(monkeypatch):
    """import POSTs sessionTemplate/panAccess and auto-claims the result."""
    _cleanup()
    identity = {"id": "ses_ma", "managed": [],
                "panAccess": {"restrictToManaged": False,
                              "canClaimUnmanaged": False,
                              "autoClaimCreated": True}}
    fake = _FakeAPI(identity=identity, import_resp=_import_session_dict())
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")

    r = mcp_server.session_import(
        action="import", session_id="sid-1", project_dir="d-project-Pan",
        name="my-imported", session_template="meta-agent",
        pan_access={"autoClaimCreated": True, "restrictToManaged": True})

    # import call built correctly
    import_call = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/sessions/import")]
    assert import_call, fake.calls
    _method, _path, body, timeout = import_call[0]
    assert _path == "/api/cbc/sessions/import"
    assert body["session_id"] == "sid-1"
    assert body["project_dir"] == "d-project-Pan"
    assert body["name"] == "my-imported"
    assert body["sessionTemplate"] == "meta-agent"
    assert body["panAccess"] == {"autoClaimCreated": True, "restrictToManaged": True}
    assert timeout == 120.0

    # result trimmed: history → historyCount, usage stripped, imported flag
    assert r["id"] == "ses_new"
    assert "history" not in r
    assert r["historyCount"] == 3
    assert "rawUsage" not in r and "totalUsage" not in r
    assert r["imported"] is True
    assert r["reimportedExisting"] is False
    # auto-claim fired
    assert any(c[0] == "POST" and c[1] == "/api/claim" for c in fake.calls)
    _cleanup()


def test_mcp_import_kimi_body(monkeypatch):
    _cleanup()
    fake = _FakeAPI(import_resp=_import_session_dict(adapter="kimi"))
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="import", adapter="kimi",
                                  session_id="ksid-1", cwd="D:/ws")
    import_call = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/sessions/import")]
    assert import_call[0][1] == "/api/kimi/sessions/import"
    assert import_call[0][2]["cwd"] == "D:/ws"
    assert r["imported"] is True
    _cleanup()


def test_mcp_import_reimported_existing_flag(monkeypatch):
    """Backend reimport marker maps to reimportedExisting=True."""
    _cleanup()
    fake = _FakeAPI(import_resp=_import_session_dict(reimported=True))
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="import", session_id="sid-1",
                                  project_dir="d-project-Pan")
    assert r["reimportedExisting"] is True
    assert r["imported"] is True
    _cleanup()


def test_mcp_import_reimport_precheck_denies_unmanaged(monkeypatch):
    """Restricted caller reimporting an unmanaged session is refused."""
    _cleanup()
    identity = {"id": "ses_ma", "managed": [],
                "panAccess": {"restrictToManaged": True,
                              "canClaimUnmanaged": False,
                              "autoClaimCreated": False}}
    summary = [{"id": "ses_other", "cliSessionId": "sid-1", "managedBy": None}]
    fake = _FakeAPI(identity=identity, summary=summary)
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")

    r = mcp_server.session_import(action="import", session_id="sid-1",
                                  project_dir="d-project-Pan")
    assert r["ok"] is False
    assert r["error"]["code"] == "permission_denied"
    assert "would overwrite session ses_other" in r["error"]["message"]
    # import must not be attempted
    assert not any(c[0] == "POST" and c[1].endswith("/sessions/import") for c in fake.calls)
    _cleanup()


def test_mcp_import_reimport_precheck_allows_managed(monkeypatch):
    """Restricted caller may reimport a session it manages."""
    _cleanup()
    identity = {"id": "ses_ma", "managed": ["ses_mine"],
                "panAccess": {"restrictToManaged": True,
                              "canClaimUnmanaged": False,
                              "autoClaimCreated": False}}
    summary = [{"id": "ses_mine", "cliSessionId": "sid-1", "managedBy": "ses_ma"}]
    fake = _FakeAPI(identity=identity, summary=summary,
                    import_resp=_import_session_dict(id="ses_mine", reimported=True))
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")

    r = mcp_server.session_import(action="import", session_id="sid-1",
                                  project_dir="d-project-Pan")
    assert r["reimportedExisting"] is True
    assert any(c[0] == "POST" and c[1].endswith("/sessions/import") for c in fake.calls)
    _cleanup()


def test_mcp_import_reimport_precheck_allows_new(monkeypatch):
    """No matching cliSessionId → pure new import, no precheck denial."""
    _cleanup()
    identity = {"id": "ses_ma", "managed": [],
                "panAccess": {"restrictToManaged": True,
                              "canClaimUnmanaged": False,
                              "autoClaimCreated": False}}
    fake = _FakeAPI(identity=identity, summary=[], import_resp=_import_session_dict())
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")

    r = mcp_server.session_import(action="import", session_id="sid-1",
                                  project_dir="d-project-Pan")
    assert r["imported"] is True
    assert r["reimportedExisting"] is False
    _cleanup()


def test_mcp_import_no_precheck_when_unrestricted(monkeypatch):
    """Unrestricted / identity-less callers skip the summary precheck."""
    _cleanup()
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    fake = _FakeAPI(import_resp=_import_session_dict())
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_import(action="import", session_id="sid-1",
                                  project_dir="d-project-Pan")
    assert r["imported"] is True
    # no ?summary=1 lookup for reimport precheck
    assert not any("summary=1" in c[1] for c in fake.calls)
    _cleanup()

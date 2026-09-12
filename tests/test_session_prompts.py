"""Canonical prompt persistence and effective-prompt consumers (no live workers)."""

import asyncio
import json
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from packages.core import session as sess
from packages.core import worker
from packages.core.adapters import get_adapter


@pytest.mark.parametrize("original", [None, "", "  \n", " 原始规则\nkeep whitespace \n"])
def test_new_session_and_round_trip(original):
    s = sess.create("new", system_prompt=original)
    assert s.original_prompt == original
    assert s.handoff_prompt is None
    assert s.system_prompt == original
    assert "system_prompt" not in asdict(s)
    raw = json.loads(sess._path(s.id).read_text(encoding="utf-8"))
    assert raw["original_prompt"] == original
    assert raw["handoff_prompt"] is None
    assert "system_prompt" not in raw
    sess._cache.clear()
    loaded = sess.get(s.id)
    assert loaded.system_prompt == original
    exported = loaded.to_dict()
    assert exported["system_prompt"] == original
    assert sess.Session._from_data(exported).to_dict() == loaded.to_dict()


@pytest.mark.parametrize("legacy", [None, "", "  \n", "规则",
    "【交接上下文（由被交接 session A 的 agent 编写）】\n旧简报\n\n"
    "【原 session 的 system prompt】\n无法安全拆分的原文\n"])
@pytest.mark.parametrize("has_jsonl", [False, True])
def test_legacy_load_is_lossless_readonly_and_next_save_migrates(legacy, has_jsonl):
    sess.SESSION_DIR.mkdir(parents=True)
    path = sess._path("ses_old")
    payload = {"id": "ses_old", "name": "old", "system_prompt": legacy, "history": []}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if has_jsonl:
        sess._history_path("ses_old").write_text("", encoding="utf-8")
    before = path.read_bytes()
    s = sess.get("ses_old")
    assert path.read_bytes() == before
    assert s.original_prompt == legacy
    assert s.handoff_prompt is None
    assert s.system_prompt == legacy
    sess.save(s)  # no unrelated metadata update needed for migration
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["original_prompt"] == legacy
    assert raw["handoff_prompt"] is None
    assert "system_prompt" not in raw
    sess._cache.clear()
    assert sess.get(s.id).original_prompt == legacy


@pytest.mark.parametrize("original", [None, "", "rules"])
def test_canonical_original_wins_over_stale_legacy_alias(original):
    s = sess.Session._from_data({
        "id": "ses_mixed", "name": "mixed", "original_prompt": original,
        "handoff_prompt": "latest", "system_prompt": "stale compounded prompt",
    })
    assert s.original_prompt == original
    assert s.handoff_prompt == "latest"
    assert "stale" not in s.system_prompt
    assert sess.Session._from_data(s.to_dict()).system_prompt == s.system_prompt


@pytest.mark.parametrize("copy_settings", [True, False])
@pytest.mark.parametrize("original", [None, "", " \n", " 稳定规则\n"])
def test_repeated_handoffs_only_keep_latest_brief_after_cold_load(original, copy_settings):
    s = sess.create("chain", original_prompt=original)
    for generation in range(4):
        brief = f"generation-{generation}"
        _, s = sess.handoff_session(s.id, brief, copy_settings=copy_settings)
        sess._cache.clear()
        sess._all_loaded = False
        s = sess.get(s.id)
        assert s.original_prompt == original
        assert s.handoff_prompt == brief
        assert s.system_prompt.count(brief) == 1
        assert all(f"generation-{old}" not in s.system_prompt for old in range(generation))
        if original and original.strip():
            assert s.system_prompt.endswith(original)
            assert s.system_prompt.count("【交接上下文") == 1
        else:
            assert s.system_prompt == brief


def test_legacy_compounded_baseline_does_not_grow_recursively():
    legacy = "【交接上下文】\nold brief\n【原 session 的 system prompt】\nrules"
    s = sess.create("legacy", system_prompt=legacy)
    for brief in ("first", "second", "third"):
        _, s = sess.handoff_session(s.id, brief)
        assert s.original_prompt == legacy
        assert s.system_prompt.count(legacy) == 1
        assert s.handoff_prompt == brief
    assert "first" not in s.system_prompt and "second" not in s.system_prompt


@pytest.mark.parametrize("brief", [None, "", " \n"])
def test_empty_brief_preserves_original_exactly(brief):
    s = sess.create("empty-brief", original_prompt="  rules \n", handoff_prompt=brief)
    assert s.system_prompt == "  rules \n"
    sess._cache.clear()
    assert sess.get(s.id).handoff_prompt == brief


def test_legacy_assignment_replaces_prompt_and_clears_brief():
    s = sess.create("replace", original_prompt="old", handoff_prompt="brief")
    s.system_prompt = "replacement"
    assert s.original_prompt == "replacement"
    assert s.handoff_prompt is None
    assert s.system_prompt == "replacement"


@pytest.mark.parametrize("mode", ["full", "async"])
def test_prompt_changes_survive_all_save_paths(mode):
    s = sess.create("save", original_prompt="rules", handoff_prompt="first")
    s.handoff_prompt = "second"
    if mode == "full":
        sess.save_full(s)
    else:
        asyncio.run(sess.save_async(s))
    sess._cache.clear()
    loaded = sess.get(s.id)
    assert loaded.original_prompt == "rules"
    assert loaded.handoff_prompt == "second"
    assert "first" not in loaded.system_prompt


def test_worker_and_oneshot_adapters_consume_effective_prompt(tmp_path):
    s = sess.create("worker", original_prompt="rules", handoff_prompt="latest", workdir=str(tmp_path))
    for name in ("cbc", "claude", "kimi", "codex"):
        args = worker._spawn_system_prompt_args(get_adapter(name), s, mcp_on=True)
        assert args == ["--system-prompt", s.system_prompt]
    for name in ("cbc", "claude"):
        args = get_adapter(name).oneshot_args(s, "task")
        assert args[args.index("--system-prompt") + 1] == s.system_prompt
    paths = []
    args = worker._spawn_system_prompt_args(get_adapter("codex"), s, True, paths)
    try:
        from pathlib import Path
        assert Path(args[1]).read_text(encoding="utf-8") == s.system_prompt
    finally:
        for path in paths:
            worker._cleanup_system_prompt_file(path)
    s.cli_session_id = "resume-id"
    assert worker._spawn_system_prompt_args(get_adapter("cbc"), s, True) is None


def test_api_creation_empty_override_and_canonical_precedence(monkeypatch):
    from packages.web import server
    from packages.core.manifest_loader import SessionTemplate

    class Templates:
        def get_session_template(self, name):
            return SessionTemplate(name=name, system_prompt="template rules", mcp_mode="never")

    monkeypatch.setattr(server, "_character_manager", Templates())
    monkeypatch.setattr(server, "_ensure_manifest_fresh", lambda: None)
    for fields, expected in (({}, "template rules"), ({"systemPrompt": ""}, ""),
        ({"systemPrompt": None}, None), ({"originalPrompt": "", "systemPrompt": "legacy"}, "")):
        params = server._build_session_params({"name": "api", "sessionTemplate": "t", **fields}, resolve_workdir=False)
        s = sess.create(**params)
        assert s.original_prompt == expected
        assert s.handoff_prompt is None


def test_api_settings_and_export_preserve_prompt_components(monkeypatch):
    from packages.web import server
    monkeypatch.setattr(server, "broadcast", AsyncMock())
    s = sess.create("settings", original_prompt="rules", handoff_prompt="brief")
    result = asyncio.run(server.api_update_session(s.id, {"originalPrompt": "updated"}))
    assert result["originalPrompt"] == "updated"
    assert result["handoffPrompt"] == "brief"
    assert result["systemPrompt"] == s.system_prompt
    sess._cache.clear()
    s = sess.get(s.id)
    assert s.original_prompt == "updated" and s.handoff_prompt == "brief"
    server._apply_session_updates(s, {"gameId": "unrelated"})
    assert s.original_prompt == "updated" and s.handoff_prompt == "brief"
    with pytest.raises(ValueError, match="read-only"):
        server._apply_session_updates(s, {"systemPrompt": s.system_prompt})
    with pytest.raises(ValueError, match="string or null"):
        server._apply_session_updates(s, {"originalPrompt": 42})
    with pytest.raises(ValueError, match="panAccess"):
        server._apply_session_updates(s, {"originalPrompt": "must not apply", "panAccess": []})
    assert s.original_prompt == "updated"
    server._apply_session_updates(s, {"originalPrompt": "", "handoffPrompt": None})
    assert s.system_prompt == ""


def test_summary_omits_prompt_but_full_detail_exposes_persisted_prompt():
    from packages.web import server

    s = sess.create(
        "summary-detail",
        original_prompt="Persisted original rules",
        handoff_prompt="Latest handoff brief",
    )

    summary = server._session_summary(s)
    full = server._session_to_api(s)

    assert "systemPrompt" not in summary
    assert full["systemPrompt"] == s.system_prompt


def test_http_branch_preserves_components(monkeypatch):
    from packages.web import server
    from unittest.mock import Mock
    s = sess.create("parent", original_prompt="rules", handoff_prompt="brief", cli_session_id="parent-cli")
    provider = Mock()
    provider.fork_session.return_value = "child-cli"
    provider.parse_history.return_value = []
    provider.get_raw_usage.return_value = []
    monkeypatch.setattr(server, "_sessions_provider", lambda _: provider)
    monkeypatch.setattr(server, "broadcast", AsyncMock())
    result = asyncio.run(server.api_branch_session(s.id, {"name": "child"}))
    child = sess.get(result["id"])
    assert child.original_prompt == "rules"
    assert child.handoff_prompt == "brief"
    _, successor = sess.handoff_session(child.id, "next")
    assert "brief" not in successor.system_prompt


def test_mcp_create_transmits_explicit_empty_prompt(monkeypatch):
    from packages.mcp import server
    from unittest.mock import Mock
    request = Mock(return_value={"id": "ses_created"})
    monkeypatch.setattr(server, "_api", request)
    monkeypatch.setattr(server, "_auto_claim", lambda *args, **kwargs: None)
    server.session_create(name="empty", system_prompt="")
    assert request.call_args.args[2]["systemPrompt"] == ""

"""Session 配置 × adapter 能力校验回归测试。

覆盖（session-adapter-validation 立项）：
- unknown adapter：create / spawn / handoff 返回结构化错误（不再 KeyError→500）
- 显式 invalid model：create / PATCH / handoff 拒绝，session 不被改写
- unsupported permission / effort / thinking / maxThinkingTokens：按
  supported_settings 与值域拒绝（不再静默忽略 / 伪成功持久化）
- codex 按 model 收窄的 effort 值域
- 已有 Session spawn 传不同 adapter：明确拒绝并提示 handoff（不再静默忽略）
- template：未知模板名拒绝；跨 adapter 模板 model 宽容守卫；合法模板不回归
- MCP：重复 server 名 / 非字符串名 / 非法 transport 拒绝
- 正向行为不回归：合法 model / effort 创建、copySettings=false handoff
"""

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters.cbc import CbcAdapter
from packages.core.adapters.kimi import KimiAdapter
from packages.core.adapters.codex import CodexAdapter
from packages.web import server as srv


def _cleanup():
    _sess._cache.clear()
    _sess._all_loaded = False
    worker.workers.clear()
    worker.set_broadcaster(None)


def _make(sid: str, name: str, **kw) -> _sess.Session:
    s = _sess.Session(id=sid, name=name, adapter=kw.pop("adapter", "cbc"), **kw)
    _sess._cache[sid] = s
    return s


def _patch_caps(monkeypatch,
                cbc_models=("deepseek-v4-flash", "hy3", "glm-5.2"),
                kimi_models=("moonshot-cn/kimi-k2.6",),
                codex_models=("gpt-5.1-codex",)):
    """钉住三個 adapter 的模型清单，测试不依赖本机 config.json / CLI。"""
    monkeypatch.setattr(CbcAdapter, "supported_models",
                        property(lambda self: list(cbc_models)))
    monkeypatch.setattr(KimiAdapter, "supported_models",
                        property(lambda self: list(kimi_models)))
    monkeypatch.setattr(CodexAdapter, "supported_models",
                        property(lambda self: list(codex_models)))


def _manifest_manager():
    from packages.core.character import CharacterManager
    cm = CharacterManager(str(Path(tempfile.mkdtemp())))
    cm.load_manifest(["packages/mcp/manifest.json"])
    return cm


# ══════════════════════════════════════════════════════════════════════════ #
#  create（POST /api/sessions）                                              #
# ══════════════════════════════════════════════════════════════════════════ #

def test_create_unknown_adapter_structured_error(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_create_session({"name": "ua1", "adapter": "nosuch"}))
    assert "error" in r, r
    assert "Unknown adapter 'nosuch'" in r["error"]
    assert "cbc" in r["error"]  # 可用列表
    assert _sess.get("ua1") is None
    _cleanup()


def test_create_invalid_model_rejected(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_create_session(
            {"name": "bad-model", "model": "glm-5.3-flash"}))
    assert "error" in r, r
    assert "adapter 'cbc' does not support model 'glm-5.3-flash'" in r["error"]
    assert "hy3" in r["error"]  # Available models 列表
    assert not any(s.name == "bad-model" for s in _sess.list_all())
    _cleanup()


def test_create_valid_model_succeeds(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    # 默认模板会解析 pan MCP server → 需要 manifest catalog
    monkeypatch.setattr(srv, "_character_manager", _manifest_manager())
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_create_session({"name": "ok-model", "model": "hy3"}))
    assert "error" not in r, r
    s = _sess.get(r["id"])
    assert s.model == "hy3"
    _cleanup()


def test_create_max_thinking_tokens_rejected(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_create_session(
            {"name": "mtt", "maxThinkingTokens": 1024}))
    assert "error" in r, r
    assert "'maxThinkingTokens'" in r["error"] and "cbc" in r["error"]
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  PATCH /api/sessions/{id}（含 worker settings 共用的 _apply_session_updates）#
# ══════════════════════════════════════════════════════════════════════════ #

def test_patch_invalid_model_rejected_and_session_unchanged(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    s = _make("ses_p1", "p1", model="hy3")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_update_session("ses_p1", {"model": "not-a-model"}))
    assert "error" in r and "does not support model" in r["error"], r
    assert s.model == "hy3"  # validate-first：session 未被改写
    _cleanup()


def test_patch_unsupported_permission_for_kimi(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_p2", "p2", adapter="kimi")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_update_session(
            "ses_p2", {"permissionMode": "bypassPermissions"}))
    assert "error" in r, r
    assert "adapter 'kimi' does not support permission mode 'bypassPermissions'" in r["error"]
    _cleanup()


def test_patch_unsupported_effort_for_kimi(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    s = _make("ses_p3", "p3", adapter="kimi")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_update_session("ses_p3", {"effort": "high"}))
    assert "error" in r, r
    assert "'effort' setting" in r["error"], r
    assert s.adapter_config.get("effort", "") != "high"
    _cleanup()


def test_patch_invalid_effort_value_for_cbc(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_p4", "p4", adapter="cbc")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_update_session("ses_p4", {"effort": "bogus"}))
    assert "error" in r, r
    assert "effort 'bogus' is not supported by adapter 'cbc'" in r["error"]
    _cleanup()


def test_patch_max_thinking_tokens_rejected(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    s = _make("ses_p5", "p5", adapter="cbc")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_update_session("ses_p5", {"maxThinkingTokens": 4096}))
    assert "error" in r and "'maxThinkingTokens'" in r["error"], r
    assert "max_thinking_tokens" not in s.adapter_config or \
        s.adapter_config["max_thinking_tokens"] is None
    _cleanup()


def test_patch_thinking_unsupported_for_kimi(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_p6", "p6", adapter="kimi")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_update_session(
            "ses_p6", {"alwaysThinkingEnabled": True}))
    assert "error" in r and "'thinking' setting" in r["error"], r
    _cleanup()


def test_patch_codex_per_model_effort_enforced(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    monkeypatch.setattr(
        CodexAdapter, "model_efforts",
        property(lambda self: {"gpt-5.1-codex": ["low", "medium", "high"]}))
    _make("ses_p7", "p7", adapter="codex", model="gpt-5.1-codex")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_update_session("ses_p7", {"effort": "ultra"}))
    assert "error" in r, r
    assert "for model 'gpt-5.1-codex'" in r["error"], r
    _cleanup()


def test_apply_session_updates_raises_without_mutation(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    s = _make("ses_p8", "p8", model="hy3", permission_mode="plan")
    with pytest.raises(ValueError, match="does not support model"):
        srv._apply_session_updates(s, {"model": "nope", "effort": "high"})
    assert s.model == "hy3" and s.permission_mode == "plan"
    assert s.adapter_config.get("effort", "") != "high"
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  spawn（POST /api/spawn）                                                  #
# ══════════════════════════════════════════════════════════════════════════ #

def test_spawn_existing_session_cross_adapter_rejected(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_s1", "s1", adapter="cbc")
    r = asyncio.run(srv.api_spawn({"sessionId": "ses_s1", "adapter": "kimi"}))
    assert "error" in r, r
    assert "uses adapter 'cbc'" in r["error"]
    assert "handoff" in r["error"]
    assert "ses_s1" not in worker.workers
    _cleanup()


def test_spawn_existing_session_same_adapter_ok(monkeypatch):
    """同 adapter 显式传入不拒绝（如 MCP agent_spawn 透传当前值）。"""
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_s2", "s2", adapter="cbc")
    with patch.object(worker, "create_worker", new=AsyncMock(return_value="blocked-by-test")):
        r = asyncio.run(srv.api_spawn({"sessionId": "ses_s2", "adapter": "cbc"}))
    # create_worker 被打桩后返回其返回值；关键是未触发 adapter 错误
    assert "cannot be spawned" not in str(r), r
    _cleanup()


def test_spawn_new_session_unknown_adapter(monkeypatch):
    _cleanup()


def test_create_worker_legacy_unknown_adapter_returns_actionable_error(monkeypatch):
    """存量脏 Session 也不能让 create_worker 裸 KeyError 崩溃。"""
    _cleanup()
    _make("ses-w-unknown", "w-unknown", adapter="removed-adapter")
    result = asyncio.run(worker.create_worker("ses-w-unknown"))
    assert isinstance(result, str)
    assert "Unknown adapter 'removed-adapter'" in result
    assert "Available adapters" in result
    _cleanup()
    _patch_caps(monkeypatch)
    r = asyncio.run(srv.api_spawn({"name": "s-new", "adapter": "wat"}))
    assert "error" in r and "Unknown adapter 'wat'" in r["error"], r
    assert not any(s.name == "s-new" for s in _sess.list_all())
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  handoff                                                                  #
# ══════════════════════════════════════════════════════════════════════════ #

def test_handoff_unknown_adapter(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_h1", "h1", adapter="cbc")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_session_handoff(
            "ses_h1", {"handoffPrompt": "交接", "copySettings": False,
                       "adapter": "nosuch"}))
    assert "error" in r and "Unknown adapter 'nosuch'" in r["error"], r
    _cleanup()


def test_handoff_invalid_explicit_model(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_h2", "h2", adapter="cbc")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_session_handoff(
            "ses_h2", {"handoffPrompt": "交接", "copySettings": False,
                       "adapter": "kimi", "model": "hy3"}))
    assert "error" in r, r
    assert "adapter 'kimi' does not support model 'hy3'" in r["error"], r
    _cleanup()


def test_handoff_cross_adapter_inherited_model_rejected(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_h3", "h3", adapter="cbc", model="hy3")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_session_handoff(
            "ses_h3", {"handoffPrompt": "交接", "copySettings": True,
                       "adapter": "kimi"}))
    assert "error" in r, r
    assert "would inherit model 'hy3'" in r["error"], r
    _cleanup()


def test_handoff_cross_adapter_no_model_uses_new_default(monkeypatch):
    """copySettings=false + 显式 adapter：正向路径不回归，B 用新 adapter 默认。"""
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_h4", "h4", adapter="cbc", model="hy3")
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_session_handoff(
            "ses_h4", {"handoffPrompt": "交接", "copySettings": False,
                       "adapter": "kimi"}))
    assert r.get("ok") is True, r
    assert r["session"]["adapter"] == "kimi"
    assert r["session"]["model"] == "moonshot-cn/kimi-k2.6"
    _cleanup()


def test_handoff_cross_adapter_sanitizes_copied_config(monkeypatch):
    """copySettings=true 换 adapter：不支持的能力降级为默认（effort→""）。"""
    _cleanup()
    _patch_caps(monkeypatch)
    _make("ses_h5", "h5", adapter="cbc", model=None,
          adapter_config={"effort": "high", "max_thinking_tokens": 512,
                          "always_thinking_enabled": True})
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_session_handoff(
            "ses_h5", {"handoffPrompt": "交接", "copySettings": True,
                       "adapter": "kimi"}))
    assert r.get("ok") is True, r
    b = _sess.get(r["session"]["id"])
    assert b.adapter == "kimi"
    assert b.adapter_config.get("effort", "") == ""
    assert "max_thinking_tokens" not in b.adapter_config
    assert not b.adapter_config.get("always_thinking_enabled")
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  template                                                                 #
# ══════════════════════════════════════════════════════════════════════════ #

def test_create_unknown_template_rejected(monkeypatch):
    _cleanup()
    _patch_caps(monkeypatch)
    monkeypatch.setattr(srv, "_character_manager", _manifest_manager())
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_create_session(
            {"name": "t-bad", "sessionTemplate": "no-such-template"}))
    assert "error" in r, r
    assert "Unknown session template 'no-such-template'" in r["error"]
    assert "meta-agent" in r["error"]  # Available templates
    _cleanup()


def test_create_template_model_adopted_when_valid(monkeypatch):
    """meta-agent（cbc/hy3）+ cbc 请求：模板 model 合法 → 照常采用（不回归）。"""
    _cleanup()
    _patch_caps(monkeypatch)
    monkeypatch.setattr(srv, "_character_manager", _manifest_manager())
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_create_session(
            {"name": "t-ok", "sessionTemplate": "meta-agent"}))
    assert "error" not in r, r
    assert _sess.get(r["id"]).model == "hy3"
    _cleanup()


def test_create_template_model_mismatch_rejected(monkeypatch):
    """模板明确指定的 model 不属于请求的 adapter：直接拒绝。"""
    _cleanup()
    _patch_caps(monkeypatch)
    monkeypatch.setattr(srv, "_character_manager", _manifest_manager())
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_create_session(
            {"name": "t-mix", "adapter": "kimi", "sessionTemplate": "meta-agent"}))
    assert "error" in r, r
    assert "adapter 'kimi' does not support model 'hy3'" in r["error"], r
    assert not any(s.name == "t-mix" for s in _sess.list_all())
    _cleanup()


def test_create_default_template_trusts_adapter_default_model(monkeypatch):
    """内置 default 模板（无显式 sessionTemplate）不硬校验 adapter 默认模型。

    模拟 claude 场景：config 默认模型不在其 best-effort builtin 列表内，
    但来自 config/默认回退的模型应保留既有宽容语义，而不是误杀创建。
    """
    _cleanup()
    from packages.core.adapters.claude import ClaudeAdapter
    _patch_caps(monkeypatch)
    monkeypatch.setattr(ClaudeAdapter, "supported_models",
                        property(lambda self: ["claude-opus-4-8"]))
    monkeypatch.setattr(ClaudeAdapter, "default_model",
                        property(lambda self: "claude-custom-legacy"))
    monkeypatch.setattr(srv, "_character_manager", _manifest_manager())
    with patch.object(srv, "broadcast", new=AsyncMock()):
        r = asyncio.run(srv.api_create_session({"name": "cl-default", "adapter": "claude"}))
    assert "error" not in r, r
    assert _sess.get(r["id"]).model == "claude-custom-legacy"
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  MCP server 解析                                                          #
# ══════════════════════════════════════════════════════════════════════════ #

def test_mcp_duplicate_server_name_rejected(monkeypatch):
    _cleanup()
    monkeypatch.setattr(srv, "_character_manager", _manifest_manager())
    s = _make("ses_m1", "m1")
    with pytest.raises(ValueError, match=r"Duplicate MCP server: 'pan'"):
        srv._resolve_mcp_server_configs(["pan", "pan"])
    _cleanup()


def test_mcp_non_string_name_rejected(monkeypatch):
    _cleanup()
    monkeypatch.setattr(srv, "_character_manager", _manifest_manager())
    with pytest.raises(ValueError, match="Invalid MCP server name"):
        srv._resolve_mcp_server_configs([123])
    _cleanup()


def test_mcp_invalid_transport_rejected(monkeypatch, tmp_path):
    _cleanup()
    import json as _json
    from packages.core.character import CharacterManager
    manifest = tmp_path / "manifest.json"
    manifest.write_text(_json.dumps({
        "mcp_servers": [{
            "name": "bad-transport",
            "url": "http://localhost:9999/mcp",
            "transport": "smoke-signals",
        }],
    }), encoding="utf-8")
    cm = CharacterManager(str(tmp_path))
    cm.load_manifest([str(manifest)])
    monkeypatch.setattr(srv, "_character_manager", cm)
    with pytest.raises(ValueError, match="invalid transport"):
        srv._resolve_mcp_server_configs(["bad-transport"])
    _cleanup()


def test_mcp_valid_transport_still_accepted(monkeypatch):
    """正向：白名单内的 transport/type 不被误杀。"""
    _cleanup()
    import json as _json
    from packages.core.character import CharacterManager
    tmp = Path(tempfile.mkdtemp())
    manifest = tmp / "manifest.json"
    manifest.write_text(_json.dumps({
        "mcp_servers": [{
            "name": "http-ok",
            "url": "http://localhost:9999/mcp",
            "transport": "http",
        }],
    }), encoding="utf-8")
    cm = CharacterManager(str(tmp))
    cm.load_manifest([str(manifest)])
    monkeypatch.setattr(srv, "_character_manager", cm)
    configs = srv._resolve_mcp_server_configs(["http-ok"])
    assert configs[0]["transport"] == "http"
    _cleanup()


if __name__ == "__main__":
    print("run via pytest: python -m pytest tests/test_session_adapter_validation.py -q")

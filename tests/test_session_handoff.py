"""Tests for 替身交接（session_handoff v1）：孪生 session B 接替 A。

覆盖：
- 关系网接替：B.managed=A.managed（子会话 managed_by 改 B）、
  report_subscriptions / QQ postbox（qq_subscriptions）转移、原父 manager 接替
- B 自动 manage A：B.managed 追加 A，A.managed_by=B，B 订阅 A 的报告
- 设置复制开关：copy_settings=true 1:1 复制（不含 system_prompt、cli_session_id
  清空）；false 用默认设置（需显式 adapter）
- B.system_prompt = handoff_prompt 与 A 原 system_prompt 拼接
- 重命名：A → "(archive) <原名>"，B → "<原名>"；持久化
- MCP tool 参数校验与调用链
"""

import asyncio
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
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


def _make(sid: str, name: str, **kw) -> _sess.Session:
    s = _sess.Session(id=sid, name=name, adapter=kw.pop("adapter", "cbc"), **kw)
    _sess._cache[sid] = s
    return s


# ══════════════════════════════════════════════════════════════════════════ #
#  core: handoff_session 关系网接替 + 重命名 + B manage A                  #
# ══════════════════════════════════════════════════════════════════════════ #

def test_handoff_transfers_relationships_and_renames():
    _cleanup()
    _fresh_session_dir()
    a = _make("ses_a", "dev", adapter="cbc", system_prompt="原系统提示",
              qq_subscriptions={"user:12345", "group:678"})
    c = _make("ses_c", "child", adapter="cbc")
    a.managed = ["ses_c"]
    a.report_subscriptions = {"ses_c"}
    c.managed_by = "ses_a"

    a2, b = _sess.handoff_session("ses_a", "这是交接简报", copy_settings=True)

    assert a2.id == "ses_a" and b.id != "ses_a"
    # B 接替 A 的名字，A 归档
    assert b.name == "dev"
    assert a2.name == "(archive) dev"
    # 关系网：B.managed = A.managed + [A]
    assert b.managed == ["ses_c", "ses_a"], b.managed
    # 子会话改由 B 管理
    c2 = _sess.get("ses_c")
    assert c2.managed_by == b.id
    # B 订阅 A 的 report_subscriptions + A 的报告
    assert b.report_subscriptions == {"ses_c", "ses_a"}, b.report_subscriptions
    # QQ postbox 绑定转移给 B
    assert b.qq_subscriptions == {"user:12345", "group:678"}
    # A 解除原关系网（managed_by 保留 = B）
    assert a2.managed == []
    assert a2.managed_by == b.id
    assert a2.report_subscriptions == set()
    assert a2.qq_subscriptions == set()
    # B 是全新会话：无 CLI 上下文
    assert b.cli_session_id is None
    _cleanup()


def test_handoff_inherits_parent_manager():
    """A 曾被 M 管理 → B 接替 A 在 M 下的位置，A 转由 B 管理。"""
    _cleanup()
    _fresh_session_dir()
    m = _make("ses_m", "meta", adapter="cbc")
    a = _make("ses_a", "worker", adapter="cbc")
    m.managed = ["ses_a"]
    m.report_subscriptions = {"ses_a"}
    a.managed_by = "ses_m"

    a2, b = _sess.handoff_session("ses_a", "交接", copy_settings=True)

    assert b.managed_by == "ses_m"
    m2 = _sess.get("ses_m")
    assert m2.managed == [b.id], m2.managed
    assert m2.report_subscriptions == {b.id}, m2.report_subscriptions
    assert a2.managed_by == b.id
    _cleanup()


def test_handoff_copy_settings_and_system_prompt_join():
    """copy_settings=true：复制设置（不含 system_prompt，cli_session_id 清空），
    B.system_prompt = handoff_prompt 与 A 原 system_prompt 拼接。"""
    _cleanup()
    _fresh_session_dir()
    a = _make("ses_a", "cfg", adapter="kimi", model="hy3",
              permission_mode="bypassPermissions",
              character_id="char_1", session_template="meta-agent",
              game_id="g1", system_prompt="原系统提示",
              pan_access={"restrict_to_managed": True,
                          "can_claim_unmanaged": True,
                          "auto_claim_created": False},
              adapter_config={"effort": "high", "cli_session_id": "cli-old",
                              "mcp_servers": [{"name": "pan"}],
                              "always_thinking_enabled": True})

    _, b = _sess.handoff_session("ses_a", "交接简报", copy_settings=True)

    assert b.adapter == "kimi"
    assert b.model == "hy3"
    assert b.permission_mode == "bypassPermissions"
    assert b.character_id == "char_1"
    assert b.session_template == "meta-agent"
    assert b.game_id == "g1"
    assert b.pan_access == a.pan_access
    assert b.adapter_config["effort"] == "high"
    assert b.adapter_config["mcp_servers"] == [{"name": "pan"}]
    assert b.adapter_config["always_thinking_enabled"] is True
    # cli_session_id 清空（全新会话）
    assert "cli_session_id" not in b.adapter_config
    assert b.cli_session_id is None
    # B 无 history（精简上下文：不继承 A 的对话历史）
    assert b.history == []
    # system_prompt 拼接：handoff_prompt + 原 system_prompt，且不含原值被复制语义
    assert "交接简报" in b.system_prompt
    assert "原系统提示" in b.system_prompt
    assert b.system_prompt.index("交接简报") < b.system_prompt.index("原系统提示")
    _cleanup()


def test_handoff_no_copy_uses_defaults():
    """copy_settings=false：默认设置，显式 adapter/model 生效。"""
    _cleanup()
    _fresh_session_dir()
    a = _make("ses_a", "orig", adapter="kimi", model="hy3",
              pan_access={"restrict_to_managed": True},
              adapter_config={"effort": "high", "mcp_servers": [{"name": "pan"}]})

    _, b = _sess.handoff_session("ses_a", "交接", copy_settings=False,
                                 adapter="cbc", model="deepseek-v4-flash")

    assert b.adapter == "cbc"
    assert b.model == "deepseek-v4-flash"
    assert b.permission_mode is None
    assert b.character_id is None
    assert b.session_template is None
    assert b.adapter_config == {}
    assert b.pan_access == {"restrict_to_managed": False,
                            "can_claim_unmanaged": False,
                            "auto_claim_created": False}  # Session 默认补齐三项 False
    assert b.cli_session_id is None
    # workdir 跟随 A（孪生续作，同项目上下文）
    _cleanup()


def test_handoff_requires_prompt():
    _cleanup()
    _fresh_session_dir()
    _make("ses_a", "dev", adapter="cbc")

    err = _sess.handoff_session("ses_a", "   ", copy_settings=True)
    assert isinstance(err, str) and "handoff_prompt" in err

    err = _sess.handoff_session("ses_missing", "交接", copy_settings=True)
    assert isinstance(err, str) and "not found" in err
    _cleanup()


def test_handoff_persists_to_disk():
    """交接结果落盘：A 归档重命名、B 可冷加载恢复。"""
    _cleanup()
    session_dir = _fresh_session_dir()
    _make("ses_a", "persist-me", adapter="cbc", qq_subscriptions={"user:9"})

    a2, b = _sess.handoff_session("ses_a", "交接", copy_settings=True)

    # 两个 session 都落盘
    assert (session_dir / f"{a2.id}.json").exists()
    assert (session_dir / f"{b.id}.json").exists()
    # 冷加载：清缓存后从磁盘恢复
    _sess._cache.clear()
    _sess._all_loaded = False
    a_r = _sess.get("ses_a")
    b_r = _sess.get(b.id)
    assert a_r is not None and a_r.name == "(archive) persist-me"
    assert a_r.managed == [] and a_r.managed_by == b.id
    assert b_r is not None and b_r.name == "persist-me"
    assert b_r.managed == ["ses_a"]
    assert b_r.qq_subscriptions == {"user:9"}
    _cleanup()


def test_handoff_uses_suffix_when_original_name_is_still_occupied():
    """A stale same-named archive/session must not make B duplicate its name."""
    _cleanup()
    _fresh_session_dir()
    _make("ses_old_archive", "dev")
    _make("ses_a", "dev", adapter="cbc")

    archived, b = _sess.handoff_session("ses_a", "交接", copy_settings=True)

    assert b.name == "dev-1"
    assert archived.name == "(archive) dev"
    assert b.managed == ["ses_a"]
    assert b.report_subscriptions == {"ses_a"}
    _cleanup()


def test_handoff_skips_all_occupied_name_suffixes():
    _cleanup()
    _fresh_session_dir()
    _make("ses_old", "dev")
    _make("ses_old_1", "dev-1")
    _make("ses_old_2", "dev-2")
    _make("ses_a", "dev")

    _, b = _sess.handoff_session("ses_a", "交接", copy_settings=True)

    assert b.name == "dev-3"
    _cleanup()


def test_handoff_suffixes_archive_name_and_persists_relationships():
    """An existing archive name is suffixed without changing B's name."""
    _cleanup()
    session_dir = _fresh_session_dir()
    _make("ses_old_archive", "(archive) dev")
    a = _make("ses_a", "dev")
    child = _make("ses_child", "child")
    a.managed = [child.id]
    a.report_subscriptions = {child.id}
    child.managed_by = a.id

    archived, b = _sess.handoff_session("ses_a", "交接", copy_settings=True)

    assert archived.name == "(archive) dev-1"
    assert b.name == "dev"
    assert b.managed == [child.id, a.id]
    assert b.report_subscriptions == {child.id, a.id}
    assert _sess.get(child.id).managed_by == b.id
    assert (session_dir / f"{archived.id}.json").exists()
    assert (session_dir / f"{b.id}.json").exists()

    _sess._cache.clear()
    _sess._all_loaded = False
    archived_r = _sess.get(archived.id)
    b_r = _sess.get(b.id)
    assert archived_r.name == "(archive) dev-1"
    assert b_r.name == "dev"
    assert b_r.managed == [child.id, a.id]
    _cleanup()


def test_concurrent_handoffs_allocate_distinct_archive_names():
    _cleanup()
    _fresh_session_dir()
    _make("ses_a1", "dev")
    _make("ses_a2", "dev")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda sid: _sess.handoff_session(sid, "交接", copy_settings=True),
            ("ses_a1", "ses_a2")))

    archived_names = {archived.name for archived, _ in results}
    replacement_names = {replacement.name for _, replacement in results}
    assert archived_names == {"(archive) dev", "(archive) dev-1"}
    assert replacement_names == {"dev-1", "dev-2"}
    assert len(archived_names | replacement_names) == 4
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  HTTP 端点                                                               #
# ══════════════════════════════════════════════════════════════════════════ #

def test_api_handoff_endpoint():
    _cleanup()
    _fresh_session_dir()
    _make("ses_a", "dev", adapter="cbc")

    with patch.object(server, "broadcast", new=AsyncMock()):
        resp = asyncio.run(server.api_session_handoff(
            "ses_a", {"handoffPrompt": "交接", "copySettings": True}))

    assert resp["ok"] is True, resp
    assert resp["archivedSession"]["name"] == "(archive) dev"
    assert resp["session"]["name"] == "dev"
    # B 管理 A：A 的 managedBy 指向 B；B 的 managed 含 A
    assert resp["archivedSession"]["managedBy"] == resp["session"]["id"]
    assert resp["session"]["managed"] == ["ses_a"]
    assert resp["session"]["managedBy"] is None
    _cleanup()


def test_api_handoff_missing_prompt():
    _cleanup()
    _fresh_session_dir()
    _make("ses_a", "dev", adapter="cbc")

    resp = asyncio.run(server.api_session_handoff("ses_a", {}))
    assert "error" in resp and "handoffPrompt" in resp["error"]
    _cleanup()


def test_api_handoff_no_copy_requires_adapter():
    _cleanup()
    _fresh_session_dir()
    _make("ses_a", "dev", adapter="kimi")

    resp = asyncio.run(server.api_session_handoff(
        "ses_a", {"handoffPrompt": "交接", "copySettings": False}))
    assert "error" in resp and "adapter" in resp["error"]
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  MCP tool：参数校验 + 调用链                                              #
# ══════════════════════════════════════════════════════════════════════════ #

def _patch_mcp_http(monkeypatch):
    """放开访问检查 + 捕获 _api 调用。返回 (captured_path, captured_body) 容器。"""
    calls = {}

    def fake_check_access(*args, **kwargs):
        return None

    def fake_api(method, path, body=None, timeout=30.0):
        calls["method"] = method
        calls["path"] = path
        calls["body"] = body
        return {"ok": True, "session": {}, "archivedSession": {}}

    monkeypatch.setattr(mcp_server, "_check_access", fake_check_access)
    monkeypatch.setattr(mcp_server, "_api", fake_api)
    return calls


def test_mcp_handoff_valid_call(monkeypatch):
    _cleanup()
    calls = _patch_mcp_http(monkeypatch)

    result = mcp_server.session_handoff(
        session_id="ses_a", handoff_prompt="交接", copy_settings=True,
        adapter="kimi", model="hy3")

    assert result["ok"] is True
    assert calls["method"] == "POST"
    assert calls["path"] == "/api/sessions/ses_a/handoff"
    assert calls["body"] == {"handoffPrompt": "交接", "copySettings": True,
                             "adapter": "kimi", "model": "hy3"}
    _cleanup()


def test_mcp_handoff_no_copy_requires_adapter(monkeypatch):
    _cleanup()
    calls = _patch_mcp_http(monkeypatch)

    result = mcp_server.session_handoff(
        session_id="ses_a", handoff_prompt="交接", copy_settings=False)
    assert result["ok"] is False
    assert result["error"]["code"] == "missing_params"
    assert "adapter" in result["error"]["message"]
    assert "_api" not in calls  # 未发出请求
    _cleanup()


def test_mcp_handoff_docstring_covers_purpose():
    doc = mcp_server.session_handoff.__doc__ or ""
    # LLM 需要理解这是替身交接，用于精简上下文 / 切换 adapter
    assert "替身交接" in doc
    assert "精简上下文" in doc
    assert "切换 adapter" in doc
    assert "handoff_prompt" in doc
    _cleanup()

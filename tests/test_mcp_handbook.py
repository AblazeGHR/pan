"""Tests for the pan_handbook MCP tool and tool-description call-chain guidance.

Covers:
    - pan_handbook reads docs/skills/pan/SKILL.md (single source of truth)
    - PAN_SKILL_PATH override / missing-file error path
    - every MCP tool's docstring ends with the /pan skill pointer
    - the four main-chain tools (session_create / worker_assign / session_get /
      session_delete) carry step-numbered call-chain guidance
"""

import ast
import sys
from pathlib import Path

import pytest

# Importing FastMCP requires the real optional environment.  A missing
# python-dotenv is an environment skip, never a product failure; do not shim
# the dependency because this file is meant to test the real MCP module.
pytest.importorskip("dotenv", reason="python-dotenv is required for FastMCP handbook tests")
pytest.importorskip("mcp", reason="FastMCP package is required for MCP handbook tests")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.mcp.server as mcp_server

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = PROJECT_ROOT / "docs" / "skills" / "pan" / "SKILL.md"
MCP_SERVER_PATH = PROJECT_ROOT / "packages" / "mcp" / "server.py"

def _mcp_tool_names_from_ast() -> list[str]:
    """Return every top-level function currently decorated with @mcp.tool."""
    tree = ast.parse(MCP_SERVER_PATH.read_text(encoding="utf-8"), str(MCP_SERVER_PATH))
    names = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "mcp"
                and target.attr == "tool"
            ):
                names.append(node.name)
                break
    return names


# Keep this inventory derived from the source registration decorators.  It
# automatically covers newly added tools instead of silently omitting them
# from handbook/docstring checks, and explicitly includes codex_quota.
TOOL_NAMES = _mcp_tool_names_from_ast()
TOOLS = tuple(getattr(mcp_server, name) for name in TOOL_NAMES)

SKILL_POINTER = "完整编排流程见 /pan skill。"


class TestPanHandbook:
    def test_reads_skill_file_single_source(self):
        """Content must match the real SKILL.md on disk (no duplication)."""
        result = mcp_server.pan_handbook()
        assert result["ok"] is True
        assert result["name"] == "pan"
        assert result["path"] == str(SKILL_PATH)
        assert result["content"] == SKILL_PATH.read_text(encoding="utf-8")

    def test_returns_meaningful_content(self):
        result = mcp_server.pan_handbook()
        assert "# Pan" in result["content"]
        assert "session_handoff" in result["content"]
        assert "watchdog" in result["content"]

    def test_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "SKILL.md"
        custom.write_text("# custom handbook\n", encoding="utf-8")
        monkeypatch.setenv("PAN_SKILL_PATH", str(custom))
        result = mcp_server.pan_handbook()
        assert result["ok"] is True
        assert result["path"] == str(custom)
        assert result["content"] == "# custom handbook\n"

    def test_missing_file_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "_pan_skill_path",
                            lambda: str(tmp_path / "does-not-exist" / "SKILL.md"))
        result = mcp_server.pan_handbook()
        assert result["ok"] is False
        assert result["error"]["code"] == "skill_not_found"


class TestDescriptionCallChain:
    def test_ast_inventory_covers_all_registered_tools(self):
        assert len(TOOL_NAMES) == len(set(TOOL_NAMES))
        assert "codex_quota" in TOOL_NAMES
        assert {tool.__name__ for tool in TOOLS} == set(TOOL_NAMES)

    def test_every_tool_ends_with_pan_skill_pointer(self):
        for tool in TOOLS:
            doc = tool.__doc__ or ""
            assert doc.rstrip().endswith(SKILL_POINTER), tool.__name__

    def test_session_create_workdir_default(self):
        doc = mcp_server.session_create.__doc__
        assert "workdir 默认 data/workdirs/<name>" in doc

    def test_worker_assign_completion_signal(self):
        doc = mcp_server.worker_assign.__doc__
        assert "worker.result" in doc
        assert "/ws/agent" in doc
        assert "session_get" in doc

    def test_worker_send_agent_prefix(self):
        doc = mcp_server.worker_send.__doc__
        assert "////by agent" in doc

    def test_session_create_chain_points_to_assign(self):
        doc = mcp_server.session_create.__doc__
        assert "worker_assign" in doc
        assert "session_id" in doc

    def test_worker_assign_chain_queued_and_next(self):
        doc = mcp_server.worker_assign.__doc__
        assert "queued" in doc
        assert "session_delete" in doc

    def test_session_get_chain_result_read(self):
        doc = mcp_server.session_get.__doc__
        assert "lastResult.status" in doc
        assert "session_delete" in doc

    def test_session_delete_chain_cleanup(self):
        doc = mcp_server.session_delete.__doc__
        assert "batch-delete" in doc

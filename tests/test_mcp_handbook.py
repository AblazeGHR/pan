"""Tests for the pan_handbook MCP tool and tool-description call-chain guidance.

Covers:
    - pan_handbook reads docs/skills/pan/SKILL.md (single source of truth)
    - PAN_SKILL_PATH override / missing-file error path
    - every MCP tool's docstring ends with the /pan skill pointer
    - the four main-chain tools (session_create / worker_assign / session_get /
      session_delete) carry step-numbered call-chain guidance
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.mcp.server as mcp_server

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = PROJECT_ROOT / "docs" / "skills" / "pan" / "SKILL.md"

# All tools exposed by the Pan MCP server.
TOOLS = [
    mcp_server.session_create,
    mcp_server.session_list,
    mcp_server.session_get,
    mcp_server.session_delete,
    mcp_server.session_update,
    mcp_server.session_history,
    mcp_server.report_subscribe,
    mcp_server.report_unsubscribe,
    mcp_server.worker_spawn,
    mcp_server.worker_task,
    mcp_server.worker_kill,
    mcp_server.worker_list,
    mcp_server.worker_handoff,
    mcp_server.worker_assign,
    mcp_server.worker_send,
    mcp_server.model_list,
    mcp_server.pan_handbook,
]

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
        assert "worker_handoff" in result["content"]
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

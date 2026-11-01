"""Revision12 MCP compatibility inventory and fail-closed dispositions."""
from packages.mcp import server


def test_removed_mcp_job_tools_have_structured_unsupported_dispositions():
    expected = set(server.RETIRED_MCP_TOOLS)
    assert expected == set(server.LEGACY_MCP_COMPATIBILITY)
    for tool in sorted(expected):
        result = server.legacy_mcp_compatibility(tool, {})
        assert result["ok"] is False
        assert result["error"]["code"] == "capability_unsupported"
        assert result["error"]["tool"] == tool
        assert result["error"]["canonical"].startswith("Use registered job_")


def test_compatibility_disposition_is_pure_and_unknown_shape_fails_closed(monkeypatch):
    monkeypatch.setattr(server, "_api", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("legacy compatibility must not call HTTP")))
    assert server.legacy_mcp_compatibility("scheduler_run_now", {"task_id": "old"})["error"]["code"] == "capability_unsupported"
    assert server.legacy_mcp_compatibility("scheduler_run_now", []) ["error"]["code"] == "invalid_schema"
    assert server.legacy_mcp_compatibility("not-a-tool", {})["error"]["code"] == "invalid_tool"


def test_canonical_tools_and_retained_read_cancel_tools_remain_discoverable():
    registered = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    assert "job_create" in registered and "job_run" in registered and "job_operation_find" in registered
    for tool in server.RETAINED_LEGACY_MCP_TOOLS:
        assert tool in registered
    for tool in server.RETIRED_MCP_TOOLS:
        assert tool not in registered

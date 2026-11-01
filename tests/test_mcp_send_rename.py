"""Atomic public send vocabulary across the registry, stdio and prompts."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from packages.mcp.server import mcp


ROOT = Path(__file__).resolve().parents[1]
SEND_NAMES = {"agent_send", "agent_send_to_queue"}


def _assert_send_tools(tools):
    names = [tool.name for tool in tools]
    assert len(names) == len(set(names))
    assert {name for name in names if name.startswith("agent_send")
            and name != "agent_send_many"} == SEND_NAMES
    by_name = {tool.name: tool for tool in tools}
    current, deferred = (by_name[name] for name in ("agent_send", "agent_send_to_queue"))
    assert "Default for sending updates to the agent's current task." in current.description
    assert "不绕过暂停、锁" in current.description
    assert "client_message_id" in current.inputSchema["properties"]
    assert "Deferred follow-up" in deferred.description
    assert set(deferred.inputSchema["properties"]) == {"session_id", "text"}
    return by_name


def test_real_stdio_tools_list_has_only_the_two_send_names(tmp_path):
    async def run():
        # Inventory only; no backend call or real application data is needed.
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("PAN_")}
        env.update(PAN_API_URL="http://127.0.0.1:9", PAN_DATA_DIR=str(tmp_path),
                   PYTHONPATH=str(ROOT))
        params = StdioServerParameters(command=sys.executable,
                                      args=["-m", "packages.mcp.server"],
                                      cwd=str(ROOT), env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                remote = _assert_send_tools((await client.list_tools()).tools)
                retired = await client.call_tool("agent_send" + "_force", {
                    "session_id": "nonexistent", "text": "must not route"})
                assert retired.isError is True
        local = _assert_send_tools(await mcp.list_tools())
        assert set(local) == set(remote)
        for name in SEND_NAMES:
            assert remote[name].inputSchema == local[name].inputSchema
            assert remote[name].description == local[name].description
    asyncio.run(run())


def test_manifests_and_handbook_share_the_public_send_vocabulary():
    for path in [ROOT / "manifest.json", ROOT / "packages/mcp/manifest.json",
                 *sorted((ROOT / "docs/skills/pan").rglob("*.md"))]:
        content = path.read_text(encoding="utf-8")
        names = set(re.findall(r"\bagent_send\w*", content))
        assert names <= SEND_NAMES | {"agent_send_many"}, path
    core = json.loads((ROOT / "packages/mcp/manifest.json").read_text(encoding="utf-8"))
    prompt = json.dumps(core, ensure_ascii=False)
    assert "agent_send 默认更新当前任务" in prompt
    assert "agent_send_to_queue 仅用于当前任务结束后处理的独立延迟事项" in prompt
    handbook = (ROOT / "docs/skills/pan/SKILL.md").read_text(encoding="utf-8")
    assert "更新当前任务：补充 / 澄清 / 纠正 → agent_send(" in handbook
    assert "明确留待当前任务结束的独立后续 → agent_send_to_queue(" in handbook

"""Check that the Pan skill lists every registered Pan MCP tool.

The import-only FastMCP catalog is authoritative after dynamic registration
and retirement. No lifespan, listener, HTTP call or application write is used.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = ROOT / "packages" / "mcp" / "server.py"
SKILL = ROOT / "docs" / "skills" / "pan" / "SKILL.md"


def source_tools() -> list[str]:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"), filename=str(SERVER))
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "tool"
            for dec in node.decorator_list
        ):
            names.append(node.name)
    return names


def listed_tools() -> list[str]:
    text = SKILL.read_text(encoding="utf-8")
    section = text.split("## 5. 可用 MCP 工具", 1)[1].split("## 6. 状态判断", 1)[0]
    return re.findall(r"^\| `([a-z][a-z0-9_]*)` \|", section, re.MULTILINE)


def main() -> int:
    # Inspect the actual post-registration catalog: decorators also occur on
    # historical tombstones, while canonical tools are dynamically registered.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from packages.mcp import server
    actual = [tool.name for tool in server.mcp._tool_manager.list_tools()]
    retired = set(server.RETIRED_MCP_TOOLS)
    listed = listed_tools()
    actual_set, listed_set = set(actual), set(listed)
    declared = re.search(r"当前共 (\d+) 个实际暴露工具", SKILL.read_text(encoding="utf-8"))
    declared_count = int(declared.group(1)) if declared else None
    expected = actual_set | retired
    missing, extra = sorted(expected - listed_set), sorted(listed_set - expected)
    print(f"registered catalog: {len(actual)}; retired tombstones: {len(retired)}")
    print(f"skill rows: {len(listed)}; declared exposed count: {declared_count}")
    if not retired or retired & actual_set or len(actual_set) != len(actual):
        print("FAIL: retired tools registered, empty retirement coverage or duplicate registration")
        return 1
    if declared_count != len(actual) or listed_set != expected or len(listed) != len(expected):
        print(f"FAIL: missing={missing}, extra={extra}")
        return 1
    rows = SKILL.read_text(encoding="utf-8").splitlines()
    for name in retired:
        row = next((line for line in rows if line.startswith("| `" + name + "` |")), "")
        if "Retired / undiscoverable" not in row:
            print(f"FAIL: missing explicit retired disposition: {name}")
            return 1
    print("PASS: exact registered catalog and retired dispositions match the handbook")
    return 0


if __name__ == "__main__":
    sys.exit(main())

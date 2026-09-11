"""Check that the Pan skill lists every registered Pan MCP tool.

This is intentionally stdlib-only and derives the authoritative set from
packages/mcp/server.py rather than maintaining a second hand-written list.
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
    actual = source_tools()
    listed = listed_tools()
    actual_set, listed_set = set(actual), set(listed)
    aliases = {name for name in actual if name.startswith("worker_")}
    first_class = actual_set - aliases
    declared = re.search(
        r"当前共 (\d+) 个实际暴露工具", SKILL.read_text(encoding="utf-8")
    )
    declared_count = int(declared.group(1)) if declared else None
    missing = sorted(actual_set - listed_set)
    extra = sorted(listed_set - actual_set)
    print(f"source @mcp.tool(): {len(actual)}")
    print(f"skill tool rows: {len(listed)}")
    print(f"first-class tools: {len(first_class)}; worker aliases: {len(aliases)}")
    print(f"declared count: {declared_count}")
    if len(actual) != 49 or len(actual_set) != len(actual):
        print("FAIL: source tool set is not the expected 49 unique tools")
        return 1
    if declared_count != len(actual) or listed_set != actual_set or len(listed) != len(actual):
        print(f"FAIL: missing={missing}, extra={extra}")
        return 1
    print("PASS: source count, declared count, and every tool row agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())

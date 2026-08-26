"""Minimal standalone MCP server for kimi MCP probing.

Exposes a single tool `pan_probe` that returns a sentinel string and, when the
env var PROBE_MARKER is set, writes a marker file (so tests can detect tool
invocation without parsing model output).

Run via stdio:  python probe_server.py
"""
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kimi-mcp-probe")


@mcp.tool()
def pan_probe() -> str:
    """Probe tool. Returns a fixed sentinel so tests can detect MCP usage."""
    marker = os.environ.get("PROBE_MARKER")
    if marker:
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write("PAN_MCP_OK")
        except OSError:
            pass
    return "PAN_MCP_OK"


if __name__ == "__main__":
    mcp.run(transport="stdio")

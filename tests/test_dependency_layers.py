"""Regression checks for dependency layering and Memory-off imports."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML = {"sentence_transformers", "torch", "transformers", "llama_cpp", "openai", "watchdog", "jieba", "numpy", "tiktoken"}

def req(path: str) -> list[str]:
    return [line.strip().split("#", 1)[0].strip() for line in (ROOT / path).read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]

def test_requirement_layers_are_explicit():
    minimal = "\n".join(req("minimal-requirements.txt")).lower()
    assert "httpx>=0.28.0" in minimal
    assert "pytest" not in minimal
    assert not any(package in minimal for package in ML)
    assert "-r minimal-requirements.txt" in req("dev-requirements.txt")
    assert "pytest" in req("dev-requirements.txt")
    assert "pytest-timeout" in req("dev-requirements.txt")
    memory = "\n".join(req("memory-requirements.txt")).lower()
    assert "sentence-transformers" in memory and "llama-cpp-python" in memory
    assert "nonebot" in (ROOT / "packages/qq/requirements.txt").read_text(encoding="utf-8")
    assert "nonebot" not in minimal

def test_runtime_probe_scripts_check_httpx_and_fastmcp():
    for script in (ROOT / "scripts/setup.bat", ROOT / "scripts/start_pan.bat"):
        text = script.read_text(encoding="utf-8")
        assert "import fastapi, uvicorn, websockets, psutil, httpx" in text
        assert "mcp.server.fastmcp" in text

def test_core_and_mcp_import_with_memory_providers_blocked():
    code = """
import builtins
blocked = {"sentence_transformers", "torch", "transformers", "llama_cpp", "openai", "watchdog", "jieba", "numpy", "tiktoken"}
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split(".", 1)[0] in blocked:
        raise AssertionError("optional Memory provider imported: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import packages.web.server
from mcp.server.fastmcp import FastMCP
from packages.core import worker
assert worker._MEMORY_ENABLED is False
assert FastMCP is not None
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout

def test_memory_disabled_guard_precedes_lazy_import():
    source = (ROOT / "packages/core/worker.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_maybe_inject_memory")
    imports = [node.lineno for node in ast.walk(function) if isinstance(node, ast.ImportFrom) and node.module == "memory_context"]
    assert imports and min(imports) > function.lineno
    assert "if not _MEMORY_ENABLED:\n        return text" in source

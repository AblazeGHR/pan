"""Regression tests for Pan's config/env/default interpreter resolver."""

import asyncio
import json
import inspect
import sys
from pathlib import Path

import pytest

from packages.core import config
from packages.core import launcher
from packages.core.adapters.mcp import build_mcp_servers
from packages.core.adapters.mcp import write_mcp_json
from packages.core.adapters.kimi.adapter import KimiAdapter
from packages.core.adapters.opencode.adapter import OpencodeAdapter
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.manifest_loader import load_manifests
from packages.core.session import Session


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    monkeypatch.delenv("PAN_PYTHON", raising=False)
    return path


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _fake_executable(tmp_path, name="configured python.exe") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("placeholder", encoding="utf-8")
    return path


def test_config_python_has_priority_over_pan_python(isolated_config, tmp_path, monkeypatch):
    configured = _fake_executable(tmp_path)
    _write(isolated_config, {"python": str(configured)})
    monkeypatch.setenv("PAN_PYTHON", sys.executable)

    assert config.resolve_pan_python_argv() == [str(configured)]
    assert config.resolve_pan_python_info() == {"source": "config.json", "argvLength": 1}


def test_pan_python_is_used_when_config_is_missing(isolated_config, monkeypatch):
    monkeypatch.setenv("PAN_PYTHON", sys.executable)

    assert config.resolve_pan_python_argv() == [sys.executable]
    assert config.resolve_pan_python_info()["source"] == "PAN_PYTHON"


def test_default_python_is_used_when_config_and_env_are_empty(isolated_config):
    _write(isolated_config, {"python": ""})

    assert config.resolve_pan_python_argv() == [sys.executable]
    assert config.resolve_pan_python_info()["source"] == "sys.executable"


def test_invalid_config_falls_back_to_valid_environment(isolated_config, monkeypatch):
    _write(isolated_config, {"python": str(isolated_config.parent / "missing.exe")})
    monkeypatch.setenv("PAN_PYTHON", sys.executable)

    assert config.resolve_pan_python_argv() == [sys.executable]


def test_invalid_config_and_environment_fall_back_without_bad_command(isolated_config, monkeypatch):
    _write(isolated_config, {"python": str(isolated_config.parent / "missing.exe")})
    monkeypatch.setenv("PAN_PYTHON", str(isolated_config.parent / "also-missing.exe"))

    assert config.resolve_pan_python_argv() == [sys.executable]


def test_existing_non_executable_path_is_rejected(isolated_config, tmp_path):
    not_executable = tmp_path / "not-an-interpreter.txt"
    not_executable.write_text("not python", encoding="utf-8")
    _write(isolated_config, {"python": str(not_executable)})

    assert config.resolve_pan_python_argv() == [sys.executable]


def test_structured_launcher_preserves_argument_boundaries(isolated_config, tmp_path, monkeypatch):
    launcher = _fake_executable(tmp_path, "py launcher.exe")
    # A path with a space and an explicit launcher argument must remain two
    # argv entries; the resolver never tokenizes a command string.
    _write(isolated_config, {"python": {"command": str(launcher), "args": ["-3"]}})
    monkeypatch.setenv("PAN_PYTHON", sys.executable)

    assert config.resolve_pan_python_argv() == [str(launcher), "-3"]


def test_manifest_pan_placeholder_expands_command_and_launcher_args(
    isolated_config, tmp_path, monkeypatch
):
    launcher = _fake_executable(tmp_path, "py launcher.exe")
    _write(isolated_config, {"python": {"command": str(launcher), "args": ["-3"]}})
    monkeypatch.setenv("PAN_PYTHON", sys.executable)

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "mcp_servers": [{
            "name": "pan",
            "command": "${PAN_PYTHON}",
            "args": ["-m", "packages.mcp.server"],
            "cwd": "${PLUGIN_DIR}",
        }],
    }), encoding="utf-8")
    server = load_manifests([str(manifest)]).mcp_servers[0]

    assert server.command == str(launcher)
    assert server.args == ["-3", "-m", "packages.mcp.server"]
    assert "PAN_PYTHON" not in server.env


def test_generated_mcp_config_uses_configured_command_with_spaces(
    isolated_config, tmp_path, monkeypatch
):
    configured = _fake_executable(tmp_path, "Python Install\\python.exe")
    _write(isolated_config, {"python": str(configured)})
    monkeypatch.setenv("PAN_PYTHON", sys.executable)
    session = Session(
        id="ses_mcp_config",
        name="mcp-config",
        adapter="cbc",
        adapter_config={"mcp_servers": [{
            "name": "pan",
            "command": sys.executable,
            "args": ["-m", "packages.mcp.server"],
            "cwd": str(tmp_path),
        }]},
    )

    output = tmp_path / "data" / "mcp-configs" / "session.json"
    generated = write_mcp_json(output, session)

    assert generated["pan"]["command"] == str(configured)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["pan"]["command"] == str(configured)
    assert saved["mcpServers"]["pan"]["args"] == ["-m", "packages.mcp.server"]


def test_existing_pan_session_descriptor_refreshes_on_next_mcp_generation(
    isolated_config, tmp_path, monkeypatch
):
    old = _fake_executable(tmp_path, "old python.exe")
    new = _fake_executable(tmp_path, "new python.exe")
    _write(isolated_config, {"python": str(old)})
    session = Session(
        id="ses_pan_python",
        name="pan-python",
        adapter="cbc",
        adapter_config={"mcp_servers": [{
            "name": "pan",
            "command": str(old),
            "args": ["-m", "packages.mcp.server"],
            "cwd": str(tmp_path),
        }]},
    )

    _write(isolated_config, {"python": str(new)})
    monkeypatch.setenv("PAN_PYTHON", sys.executable)
    entry = build_mcp_servers(session)["pan"]

    assert entry["command"] == str(new)
    assert entry["args"] == ["-m", "packages.mcp.server"]


@pytest.mark.parametrize("adapter", [KimiAdapter(), OpencodeAdapter(), CodexAdapter()])
def test_python_wrappers_use_resolver_prefix(isolated_config, tmp_path, adapter):
    configured = _fake_executable(tmp_path, f"{adapter.name} configured python.exe")
    _write(isolated_config, {"python": str(configured)})
    session = Session(id="ses_wrapper", name="wrapper", adapter=adapter.name)

    assert adapter.base_args()[:1] == [str(configured)]


def test_config_reload_reports_non_sensitive_python_choice(isolated_config, tmp_path, monkeypatch):
    configured = _fake_executable(tmp_path, "reload configured python.exe")
    _write(isolated_config, {"python": str(configured)})
    monkeypatch.setenv("PAN_PYTHON", "should-not-win")

    import packages.web.server as server

    result = asyncio.run(server.api_config_reload({"scope": "python"}))
    assert result["reloaded"] is True
    assert result["python"] == {"source": "config.json", "argvLength": 1}
    assert "should-not-win" not in json.dumps(result)


def test_startup_script_is_a_thin_launcher_wrapper():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "start_pan.bat").read_text(encoding="utf-8")
    assert "packages.core.launcher" in script
    assert "start_main.ps1" not in script
    assert "resolve_pan_python.ps1" not in script
    assert not (root / "scripts" / "resolve_pan_python.ps1").exists()

    resolver = inspect.getsource(launcher.resolve_python_argv)
    assert resolver.index("config.json python") < resolver.index("PAN_PYTHON")
    assert resolver.index("PAN_PYTHON") < resolver.index("checkout .venv")

"""Port propagation into Pan-owned MCP descriptors, without service access."""

import json
import sys
from pathlib import Path

import pytest

from packages.core import config, launcher, session as session_module
from packages.core.adapters.cbc import adapter as cbc_module
from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.core.adapters.claude.adapter import ClaudeAdapter
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.adapters.kimi import adapter as kimi_module
from packages.core.adapters.kimi.adapter import KimiAdapter
from packages.core.adapters.mcp import build_mcp_servers
from packages.core.adapters.opencode.adapter import OpencodeAdapter
from packages.core.session import Session


ADAPTER_NAMES = ("cbc", "claude", "codex", "kimi", "opencode")
PAN_SERVER_NAMES = ("pan", "pan-qq", "pan-wechat")
CONFIG_PORT = 19181
ENV_PORT = 19182
API_URL = "http://pan-instance.test:19183"


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    monkeypatch.delenv("PAN_API_URL", raising=False)
    monkeypatch.delenv("PAN_PORT", raising=False)
    return path


def _write_config(path: Path, port: int) -> None:
    path.write_text(json.dumps({"port": port}), encoding="utf-8")


def _session(adapter_name: str, server_name: str, tmp_path: Path) -> Session:
    module = {
        "pan": "packages.mcp.server",
        "pan-qq": "packages.qq.mcp",
        "pan-wechat": "packages.wechat.mcp",
    }[server_name]
    return Session(
        id=f"ses_{adapter_name}_{server_name}",
        name=f"{adapter_name}-{server_name}",
        adapter=adapter_name,
        workdir=str(tmp_path / "workdir"),
        adapter_config={"mcp_servers": [{
            "name": server_name,
            "command": sys.executable,
            "args": ["-m", module],
            "cwd": str(Path(__file__).resolve().parents[1]),
        }]},
    )


def _generated_env(adapter_name: str, session: Session, tmp_path: Path, monkeypatch) -> dict:
    """Run an adapter's descriptor generation path and return its child env."""
    if adapter_name == "cbc":
        output_dir = tmp_path / "cbc"
        monkeypatch.setattr(cbc_module, "MCP_CONFIG_DIR", output_dir)
        CbcAdapter().mcp_args(session)
        data = json.loads((output_dir / f"{session.id}.mcp.json").read_text(encoding="utf-8"))
        return data["mcpServers"][session.adapter_config["mcp_servers"][0]["name"]]["env"]

    if adapter_name == "claude":
        session_dir = tmp_path / "claude" / "sessions"
        monkeypatch.setattr(session_module, "SESSION_DIR", session_dir)
        ClaudeAdapter().mcp_args(session)
        path = session_dir.parent / "mcp-configs" / f"{session.id}.mcp.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["mcpServers"][session.adapter_config["mcp_servers"][0]["name"]]["env"]

    if adapter_name == "kimi":
        home_root = tmp_path / "kimi-homes"
        monkeypatch.setattr(kimi_module, "KIMI_HOME_ROOT", home_root)
        fake_user_home = tmp_path / "fake-user-home"
        fake_user_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user_home))
        args = KimiAdapter().mcp_args(session)
        data = json.loads((Path(args[1]) / "mcp.json").read_text(encoding="utf-8"))
        return data["mcpServers"][session.adapter_config["mcp_servers"][0]["name"]]["env"]

    if adapter_name == "opencode":
        Path(session.workdir).mkdir(parents=True)
        OpencodeAdapter().mcp_args(session)
        path = Path(session.adapter_config["opencode_mcp_config_path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        name = session.adapter_config["mcp_servers"][0]["name"]
        return data["mcp"][name]["environment"]

    if adapter_name == "codex":
        name = session.adapter_config["mcp_servers"][0]["name"]
        args = CodexAdapter().mcp_args(session)
        prefix = f"mcp_servers.{name}.env."
        env = {}
        for override in args:
            if override.startswith(prefix):
                key, value = override[len(prefix):].split("=", 1)
                env[key] = json.loads(value)
        return env

    raise AssertionError(f"Unhandled adapter: {adapter_name}")


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
@pytest.mark.parametrize("server_name", PAN_SERVER_NAMES)
@pytest.mark.parametrize(
    ("runtime_env", "expected_url"),
    [
        ({}, f"http://127.0.0.1:{CONFIG_PORT}"),
        ({"PAN_PORT": str(ENV_PORT)}, f"http://127.0.0.1:{ENV_PORT}"),
        ({"PAN_PORT": str(ENV_PORT), "PAN_API_URL": API_URL}, API_URL),
    ],
    ids=("config-port", "pan-port-wins", "api-url-wins"),
)
def test_all_adapters_generate_first_party_mcp_with_effective_pan_url(
    adapter_name, server_name, runtime_env, expected_url,
    isolated_config, tmp_path, monkeypatch,
):
    _write_config(isolated_config, CONFIG_PORT)
    for key, value in runtime_env.items():
        monkeypatch.setenv(key, value)

    session = _session(adapter_name, server_name, tmp_path)
    env = _generated_env(adapter_name, session, tmp_path, monkeypatch)

    assert env["PAN_API_URL"] == expected_url
    if server_name in ("pan", "pan-qq"):
        assert env["PAN_AGENT_SESSION_ID"] == session.id
        assert env["PAN_AGENT_SESSION_TITLE"] == session.name
    else:
        assert "PAN_AGENT_SESSION_ID" not in env


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
@pytest.mark.parametrize("custom_name", ("external-tool", "pan"))
def test_custom_mcp_environment_is_not_given_pan_runtime_values(
    adapter_name, custom_name, isolated_config, tmp_path, monkeypatch,
):
    _write_config(isolated_config, CONFIG_PORT)
    monkeypatch.setenv("PAN_PORT", str(ENV_PORT))
    monkeypatch.setenv("PAN_API_URL", API_URL)
    session = Session(
        id=f"ses_custom_{adapter_name}",
        name="custom-server",
        adapter=adapter_name,
        workdir=str(tmp_path / "custom-workdir"),
        adapter_config={"mcp_servers": [{
            "name": custom_name,
            "command": sys.executable,
            "args": ["-c", "pass"],
            "env": {"EXTERNAL_SETTING": "preserved"},
        }]},
    )

    if adapter_name == "codex":
        from packages.core.adapters.codex.adapter import _c_override

        args = CodexAdapter().mcp_args(session)
        assert _c_override(f"mcp_servers.{custom_name}.env.EXTERNAL_SETTING", "preserved") in args
        assert not any(f"mcp_servers.{custom_name}.env.PAN_API_URL=" in arg for arg in args)
        if custom_name == "pan":
            assert _c_override(
                "mcp_servers.pan.env.PAN_AGENT_SESSION_ID", session.id
            ) in args
            assert _c_override(
                "mcp_servers.pan.env.PAN_AGENT_SESSION_TITLE", session.name
            ) in args
    else:
        env = _generated_env(adapter_name, session, tmp_path, monkeypatch)
        expected_env = {"EXTERNAL_SETTING": "preserved"}
        if custom_name == "pan":
            expected_env.update({
                "PAN_AGENT_SESSION_ID": session.id,
                "PAN_AGENT_SESSION_TITLE": session.name,
            })
        assert env == expected_env


def test_launcher_pins_its_effective_port_into_pan_child_environment(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "main.py").write_text("# fake entry point for mocked Popen\n", encoding="utf-8")
    monkeypatch.setenv("PAN_PORT", "19999")
    captured = {}

    class FakeProcess:
        pid = 54321

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher, "process_create_time", lambda _pid: 123.0)
    monkeypatch.setattr(launcher, "process_identity", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(launcher, "_record", lambda pid, *_args, **_kwargs: {"pid": pid})

    record = launcher._start_main(
        root, 19184, [sys.executable], hidden=False, log_path=tmp_path / "launcher.log"
    )

    assert record == {"pid": 54321}
    assert captured["env"]["PAN_PORT"] == "19184"
    assert captured["argv"][-1] == "--pan-port-marker=19184"


def test_build_mcp_servers_resolves_port_and_preserves_non_pan_descriptors(
    isolated_config, monkeypatch,
):
    _write_config(isolated_config, CONFIG_PORT)
    monkeypatch.setenv("PAN_PORT", str(ENV_PORT))
    monkeypatch.setenv("PAN_API_URL", API_URL)
    session = Session(
        id="ses_builtin_and_custom",
        name="builtins-and-custom",
        adapter="cbc",
        adapter_config={"mcp_servers": [
            {
                "name": "pan",
                "command": sys.executable,
                "args": ["-m", "packages.mcp.server"],
            },
            {
                "name": "pan-qq",
                "command": sys.executable,
                "args": ["-m", "packages.qq.mcp"],
            },
            {
                "name": "pan-wechat",
                "command": sys.executable,
                "args": ["-m", "packages.wechat.mcp"],
            },
            {
                "name": "external-tool",
                "command": "external-tool",
                "env": {"EXTERNAL_SETTING": "preserved"},
            },
        ]},
    )

    servers = build_mcp_servers(session)

    assert servers["pan"]["env"]["PAN_API_URL"] == API_URL
    assert servers["pan-qq"]["env"]["PAN_API_URL"] == API_URL
    assert servers["pan-wechat"]["env"]["PAN_API_URL"] == API_URL
    assert servers["external-tool"]["env"] == {"EXTERNAL_SETTING": "preserved"}
    assert "PAN_API_URL" not in servers["external-tool"]["env"]

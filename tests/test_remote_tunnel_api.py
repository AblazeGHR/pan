"""Remote tunnel (cloudflared) protocol config + restart API tests.

覆盖：
- config.py remote.protocol 默认值（http2）
- start_cf.ps1 协议注入 dry-run：隔离目录 + 隔离 TEMP + 置空 PATH 里的
  cloudflared，脚本在 Start-Process 处失败前已生成临时 yml，据此断言：
  * protocol 有值 → yml 含 `protocol: <value>`（根级）
  * protocol 缺省 → 不注入（与旧行为一致）
  * 源 yml 已有 protocol 行 → 被替换而非重复追加
  * 端口替换照常生效
- 进程匹配隔离：只有命令行含 pan_cf_config_ 临时 yml 的 cloudflared 会被
  识别为 Pan tunnel；cloudflared-ssh 服务进程（裸 config.yml）不匹配
- GET /api/remote/status：available/enabled/running 各分支
- POST /api/remote/restart：disabled 时拒绝且不碰进程；happy path 只杀
  匹配进程并重跑 start_cf.ps1；脚本失败时返回 error

全部 mock 进程层，绝不杀真实进程 / 不启动真实 cloudflared / 不触碰真实
config.json。
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.core.config as config  # noqa: E402
import packages.web.server as srv  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_PORT = 8795


def _use_temp_config(tmp_path, monkeypatch):
    """Point the config module at a temp config.json; return the path."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_path)
    return cfg_path


# ── config default ──


def test_default_protocol_is_http2(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    cfg = config.load_config()
    assert cfg["remote"]["protocol"] == "http2"


# ── start_cf.ps1 protocol injection (dry-run) ──

_SOURCE_YML = (
    "tunnel: test-tunnel\n"
    "ingress:\n"
    "  - hostname: t.example.com\n"
    "    service: http://localhost:9999\n"
    "  - service: http_status:404\n"
)


def _run_start_cf_dry(base: Path, protocol, source_yml: str = _SOURCE_YML):
    """Run start_cf.ps1 in an isolated dir with cloudflared unresolvable.

    The temp yml is written *before* Start-Process, so the script failing to
    find cloudflared.exe is expected — the generated yml is the artifact we
    assert on. TEMP is redirected into the isolated dir; PATH is stripped to
    System32 so no real cloudflared can ever be launched.
    """
    scripts = base / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copy(REPO_ROOT / "scripts" / "start_cf.ps1", scripts / "start_cf.ps1")

    src_yml = base / "cf.yml"
    src_yml.write_text(source_yml, encoding="utf-8")

    remote = {"config_path": str(src_yml)}
    if protocol is not None:
        remote["protocol"] = protocol
    (base / "config.json").write_text(
        json.dumps({"port": TEST_PORT, "remote": remote}), encoding="utf-8"
    )

    temp_dir = base / "temp"
    temp_dir.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "PATH": r"C:\Windows\System32",
    }
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(scripts / "start_cf.ps1")],
        capture_output=True, env=env, timeout=60,
        # PowerShell stderr may be GBK-encoded on zh-CN Windows.
        encoding="utf-8", errors="replace",
    )
    out_yml = temp_dir / f"pan_cf_config_{TEST_PORT}.yml"
    return r, out_yml


def _read_yml(p: Path) -> str:
    # PS 5.1 `Set-Content -Encoding utf8` writes a BOM.
    return p.read_text(encoding="utf-8-sig")


def test_ps1_injects_http2_protocol(tmp_path):
    r, out = _run_start_cf_dry(tmp_path, "http2")
    assert out.exists(), f"temp yml not generated: {r.stderr}"
    text = _read_yml(out)
    assert "protocol: http2" in text
    assert "http://localhost:%d" % TEST_PORT in text
    assert "http://localhost:9999" not in text  # port replacement still works


def test_ps1_without_protocol_unchanged(tmp_path):
    r, out = _run_start_cf_dry(tmp_path, None)
    assert out.exists(), f"temp yml not generated: {r.stderr}"
    text = _read_yml(out)
    assert "protocol:" not in text  # no injection → old behaviour


def test_ps1_injects_quic_protocol(tmp_path):
    r, out = _run_start_cf_dry(tmp_path, "quic")
    assert out.exists(), f"temp yml not generated: {r.stderr}"
    assert "protocol: quic" in _read_yml(out)


def test_ps1_replaces_existing_protocol_line(tmp_path):
    r, out = _run_start_cf_dry(
        tmp_path, "http2",
        source_yml="protocol: quic\n" + _SOURCE_YML,
    )
    assert out.exists(), f"temp yml not generated: {r.stderr}"
    text = _read_yml(out)
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("protocol:")]
    assert lines == ["protocol: http2"]


# ── process matcher isolation ──


def test_matcher_matches_only_pan_tunnel():
    pan_cmd = (
        r"cloudflared tunnel --config "
        r"C:\Users\x\AppData\Local\Temp\pan_cf_config_8768.yml run"
    )
    assert srv._matches_pan_tunnel("cloudflared.exe", pan_cmd)
    # cloudflared-ssh / named service process: no temp-yml marker → never matched
    assert not srv._matches_pan_tunnel(
        "cloudflared.exe",
        r'cloudflared tunnel --config C:\cloudflared\config.yml run',
    )
    assert not srv._matches_pan_tunnel("cloudflared.exe", "")
    assert not srv._matches_pan_tunnel("cloudflared.exe", None)
    # non-cloudflared binary never matches, even with the marker in its args
    assert not srv._matches_pan_tunnel("python.exe", "x.py pan_cf_config_8768.yml")


class _FakeProc:
    def __init__(self, pid, name, cmdline):
        self.info = {"pid": pid, "name": name, "cmdline": cmdline}


def test_find_pan_tunnel_processes_filters_by_matcher(monkeypatch):
    fake = types.ModuleType("psutil")
    fake.process_iter = lambda attrs: [
        _FakeProc(111, "cloudflared.exe", [
            "cloudflared", "tunnel", "--config",
            r"C:\Temp\pan_cf_config_8768.yml", "run",
        ]),
        _FakeProc(222, "cloudflared.exe", [
            "cloudflared", "tunnel", "--config", r"C:\cf\config.yml", "run",
        ]),
    ]
    monkeypatch.setitem(sys.modules, "psutil", fake)
    found = srv._find_pan_tunnel_processes()
    assert [p["pid"] for p in found] == [111]


# ── GET /api/remote/status ──


def _fake_finder(pids):
    return lambda: [
        {"pid": pid, "name": "cloudflared.exe", "cmdline": "x pan_cf_config_.yml"}
        for pid in pids
    ]


def test_status_remote_disabled(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps({"remote": {"enabled": False}}), encoding="utf-8"
    )
    monkeypatch.setattr(srv, "_find_pan_tunnel_processes", _fake_finder([]))
    r = asyncio.run(srv.api_remote_status())
    assert r["available"] is True
    assert r["enabled"] is False
    assert r["running"] is False


def test_status_no_remote_section(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(json.dumps({"port": 1234}), encoding="utf-8")
    monkeypatch.setattr(srv, "_find_pan_tunnel_processes", _fake_finder([]))
    r = asyncio.run(srv.api_remote_status())
    assert r["available"] is False
    assert r["enabled"] is False  # default merged config
    assert r["running"] is False


def test_status_enabled_running(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps({"remote": {"enabled": True, "protocol": "http2"},
                    "port": TEST_PORT}),
        encoding="utf-8",
    )
    monkeypatch.setattr(srv, "_find_pan_tunnel_processes", _fake_finder([4321]))
    r = asyncio.run(srv.api_remote_status())
    assert r["available"] is True
    assert r["enabled"] is True
    assert r["running"] is True
    assert r["protocol"] == "http2"
    assert r["port"] == TEST_PORT


# ── POST /api/remote/restart ──


def test_restart_refuses_when_disabled(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps({"remote": {"enabled": False}}), encoding="utf-8"
    )

    def _boom():
        raise AssertionError("must not touch processes when remote disabled")

    monkeypatch.setattr(srv, "_find_pan_tunnel_processes", _boom)
    r = asyncio.run(srv.api_remote_restart())
    assert r["ok"] is False
    assert "not enabled" in r["error"]


def _patch_restart_env(monkeypatch, find_results, run_rc=0, run_stderr=""):
    """Patch process scan / kill / script-run / sleep for restart tests.

    find_results: list of per-call return values for _find_pan_tunnel_processes
    (call 1 = pre-kill scan, call 2 = post-restart confirmation).
    """
    calls = []

    def fake_find():
        idx = min(len(calls), len(find_results) - 1)
        calls.append("find")
        return find_results[idx]

    killed_log = []

    def fake_kill(procs):
        killed_log.append([p["pid"] for p in procs])
        return [p["pid"] for p in procs]

    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if cmd[0] == "powershell" and "start_cf.ps1" in " ".join(cmd):
            return subprocess.CompletedProcess(cmd, run_rc, "", run_stderr)
        return real_run(cmd, **kw)

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(srv, "_find_pan_tunnel_processes", fake_find)
    monkeypatch.setattr(srv, "_kill_pan_tunnel_processes", fake_kill)
    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    monkeypatch.setattr(srv.asyncio, "sleep", fake_sleep)
    return calls, killed_log


def test_restart_happy_path(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps({"remote": {"enabled": True}}), encoding="utf-8"
    )
    calls, killed_log = _patch_restart_env(
        monkeypatch,
        find_results=[
            [{"pid": 111, "name": "cloudflared.exe", "cmdline": "pan_cf_config_.yml"}],
            [{"pid": 222, "name": "cloudflared.exe", "cmdline": "pan_cf_config_.yml"}],
        ],
    )
    r = asyncio.run(srv.api_remote_restart())
    assert r["ok"] is True
    assert r["killed"] == [111]
    assert killed_log == [[111]]  # only the matched Pan process was killed
    assert r["restarted"] is True
    assert calls.count("find") == 2  # pre-kill scan + post-restart confirm


def test_restart_script_failure(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps({"remote": {"enabled": True}}), encoding="utf-8"
    )
    _patch_restart_env(
        monkeypatch,
        find_results=[
            [{"pid": 111, "name": "cloudflared.exe", "cmdline": "pan_cf_config_.yml"}],
            [],
        ],
        run_rc=1,
        run_stderr="boom",
    )
    r = asyncio.run(srv.api_remote_restart())
    assert r["ok"] is False
    assert "start_cf.ps1 failed" in r["error"]
    assert "boom" in r["error"]
    assert r["killed"] == [111]

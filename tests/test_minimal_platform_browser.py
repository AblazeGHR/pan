"""Fresh production bundle, real owned HTTP and Chromium; no account/provider."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from packages.jobs.window import command_window_options
from tests import test_real_queue_http_e2e as base


def test_minimal_production_capability_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(base, 'SERVER_LAUNCHER', Path(__file__).parent / 'support/minimal_platform_browser_server.py')
    rt = base._PanRuntime(tmp_path / 'production-profile')
    rt.data_root.mkdir()
    (rt.data_root / 'config.json').write_text(json.dumps({'plugin_manifests': [], 'memory': {'enabled': False}}))
    out = Path('evidence/cp-x3-minimal-20261029').resolve()
    out.mkdir(exist_ok=True, parents=True)
    index = Path('packages/web/dist/index.html')
    assert index.is_file(), 'pnpm build is a prerequisite'
    facts = {'productionIndexSHA256': hashlib.sha256(index.read_bytes()).hexdigest(), 'port': base.PORT}
    try:
        rt.start()
        key = (rt.data_root / 'automation/user.key').read_text(encoding='utf-8')
        result = subprocess.run(['node', 'packages/web/e2e/minimal-platform-production.mjs', rt.base_url, str(out)],
            env={**os.environ, 'PAN_TEST_MINIMAL_KEY': key}, capture_output=True,
            encoding='utf-8', timeout=90, **command_window_options())
        (out / 'browser.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        facts['browserExit'] = result.returncode
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        rt.stop()
        facts['portReleased'] = base._port_is_free(base.PORT)
        (out / 'browser-runtime.json').write_text(json.dumps(facts, indent=2))
    assert facts['portReleased']

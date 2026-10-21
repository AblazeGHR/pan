"""Real owned Windows consoles, including a negative control and host show/hide."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest



@pytest.mark.parametrize('mode,launcher', [('legacy', 'exe'), ('hidden', 'exe'),
    ('hidden', 'cmd'), ('hidden', 'powershell'), ('shown', 'exe')])
def test_owned_api_provider_console_lifecycle(tmp_path, mode, launcher):
    if os.name != 'nt':
        pytest.skip('Windows native console evidence')
    script = Path(__file__).parent / 'fixtures/provider_console_host.py'
    from packages.jobs.desktop import HiddenDesktop
    desktop = HiddenDesktop()
    startup = subprocess.STARTUPINFO()
    startup.lpDesktop = desktop.path
    try:
        result = subprocess.run([sys.executable, str(script), mode, launcher],
            capture_output=True, text=True, timeout=40,
            startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        desktop.close()
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence['cleaned']
    (tmp_path / 'console-evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
    print(json.dumps(evidence))

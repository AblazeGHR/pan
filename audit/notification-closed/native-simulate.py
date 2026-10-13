"""Execute the managed click callback with no notification or browser launch.

This is a synthetic .NET event, never an OS Shell/user click.
"""
import base64
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core.notifications import _windows_notifyicon_command

results = []
for fail in (False, True):
    link = 'http://127.0.0.1:49123/react/?session=ses_simulation'
    script = base64.b64decode(_windows_notifyicon_command('Pan: fixture', 'synthetic', link)).decode('utf-16le')
    script = script.replace('$notify.Visible = $true', '$notify.Visible = $false')
    script = script.replace('$notify.ShowBalloonTip(5000)', '# No Shell notification in simulation')
    script = script.replace('$timer.Interval = 60000', '$timer.Interval = 50')
    script = script.replace('$timer.add_Tick({ $context.ExitThread() })', '''$timer.add_Tick({
        $timer.Stop()
        $method = $notify.GetType().GetMethod('OnBalloonTipClicked', [Reflection.BindingFlags]'Instance,NonPublic')
        $method.Invoke($notify, @())
        $method.Invoke($notify, @())
    })''')
    launch = "throw 'simulated launch failure'" if fail else "[Console]::WriteLine('SIMULATED_LAUNCH:' + $FilePath)"
    script = f"function Start-Process {{ param([string]$FilePath, [string]$ErrorAction) {launch} }}\n" + script
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-EncodedCommand', encoded], capture_output=True, text=True, timeout=20)
    passed = proc.returncode == (1 if fail else 0) and (('Pan notification URL launch failed' in proc.stderr) if fail else proc.stdout.count('SIMULATED_LAUNCH:' + link) == 1)
    results.append({'simulation': 'launch-failure' if fail else 'duplicate-managed-click', 'ok': passed, 'exitCode': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
evidence = {'nativeActualClick': False, 'osNotificationShown': False, 'results': results}
(Path(__file__).with_name('native-simulate.json')).write_text(json.dumps(evidence, indent=2), encoding='utf-8')
print(json.dumps(evidence, indent=2))
sys.exit(0 if all(r['ok'] for r in results) else 1)

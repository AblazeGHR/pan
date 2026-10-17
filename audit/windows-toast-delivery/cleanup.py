"""Remove only the diagnostic app and exact production diagnostic tags."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import windows_toast

result = {'probeApp': windows_toast.clear_app_notifications('Pan.Owned.Toast20261017')}
script = (
    "$ErrorActionPreference='Stop'\n"
    "[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]\n"
    "[Windows.UI.Notifications.ToastNotificationManager]::History.Remove('owned-production-20261017','Pan-owned-diag','Pan.Desktop.Notifications')\n"
    "[Windows.UI.Notifications.ToastNotificationManager]::History.Remove('completion-ses_owned_toast_20261017','Pan-completions','Pan.Desktop.Notifications')\n"
    "[Console]::Out.WriteLine('{\"ok\":true}')\n"
)
result['exactProductionTags'] = windows_toast._run_powershell(script)
(Path(__file__).parent / 'cleanup.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
assert all(item.get('ok') for item in result.values())

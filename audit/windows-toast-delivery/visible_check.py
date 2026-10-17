"""Human-observed visibility check. Send one owned item or remove that exact item.

This helper never claims visibility or clicking from API success. Run via the
isolated test launcher; observation is performed by the human/desktop tool.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import windows_toast

TITLE = 'Pan: OWNED VISIBLE CHECK 20261017'
TAG = 'owned-visible-20261017'
GROUP = 'Pan-owned-visible'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--send', action='store_true')
    actions.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    if args.send:
        result = windows_toast.show_toast(TITLE,
            'Local diagnostic: record banner and notification-center visibility.',
            tag=TAG, group=GROUP)
    else:
        script = (
            "$ErrorActionPreference='Stop'\n"
            "[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]\n"
            f"[Windows.UI.Notifications.ToastNotificationManager]::History.Remove('{TAG}','{GROUP}','{windows_toast.TOAST_APP_ID}')\n"
            "[Console]::Out.WriteLine('{\"ok\":true}')\n"
        )
        result = windows_toast._run_powershell(script)
    evidence = {'at': datetime.now(timezone.utc).isoformat(),
                'action': 'send' if args.send else 'cleanup', 'title': TITLE,
                'appId': windows_toast.TOAST_APP_ID, 'tag': TAG, 'group': GROUP,
                'helper': result, 'banner': 'unverified', 'panel': 'unverified',
                'nativeClick': 'not_tested_no_target'}
    target = Path(__file__).parent / ('visible-send.json' if args.send else 'visible-cleanup.json')
    target.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())

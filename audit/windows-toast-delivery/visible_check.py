"""Human-observed visibility check. Send one owned item or remove that exact item.

This helper never claims visibility or clicking from API success. Run via the
isolated test launcher; observation is performed by the human/desktop tool.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import notifications, windows_toast
from urllib.parse import urlsplit

TITLE = 'Pan: OWNED VISIBLE CHECK 20261017'
TAG = 'owned-visible-20261017'
GROUP = 'Pan-owned-visible'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--send', action='store_true')
    actions.add_argument('--cleanup', action='store_true')
    parser.add_argument('--long', action='store_true', help='Request a longer banner for human observation')
    parser.add_argument('--origin', help='MA-owned isolated runtime origin; excludes protected ports')
    parser.add_argument('--session', help='Real owned Session in that isolated runtime')
    args = parser.parse_args()
    launch_url = None
    if args.origin or args.session:
        if not args.origin or not args.session:
            parser.error('--origin and --session must be provided together')
        os.environ['PAN_API_URL'] = args.origin
        launch_url = notifications.completion_deep_link(args.session)
        if not launch_url or urlsplit(launch_url).port in (None, 8767, 8768):
            parser.error('Expected a validated loopback deep link on an explicit non-protected port')
    if args.send:
        result = windows_toast.show_toast(TITLE,
            'Local diagnostic: record banner and notification-center visibility.',
            tag=TAG, group=GROUP, launch_url=launch_url,
            duration='long' if args.long else None)
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
                'launchUrl': launch_url, 'duration': 'long' if args.long else 'default',
                'helper': result, 'banner': 'unverified', 'panel': 'unverified',
                'nativeClick': 'unverified' if launch_url else 'not_tested_no_target'}
    target = Path(__file__).parent / ('visible-send.json' if args.send else 'visible-cleanup.json')
    target.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())

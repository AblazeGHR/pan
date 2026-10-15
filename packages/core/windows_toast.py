"""Real Windows toast delivery for Pan (notification-center compatible).

Replaces the legacy NotifyIcon balloon, which never reaches the Windows
notification center. Verified on Windows 11 26100 (audit/notification-center/):

- An unpackaged app can post a WinRT toast with an AUMID and the platform
  records it in the notification center database (handler type "app:desktop",
  WNS id "NonImmersivePackage") - no Start Menu shortcut, no AppUserModelId
  registration and no custom protocol are required, and the stored payload is
  the exact toast XML we sent.
- The click target is an action button whose ``arguments`` is the full
  loopback deep link (``http://127.0.0.1:<port>/react/?session=<id>``) with
  ``activationType="protocol"``: http(s) activation is dispatched by the
  system to the installed default browser and does not run through this
  process, so a click keeps working after the sender process exited (verified
  by a real click; see the audit report). Cold-starting a fully closed browser
  goes through the same system http-handler path, but that variant is NOT yet
  verified on this machine - treat it as expected behavior, not as proven.
  The URL is always built by
  ``notifications.completion_deep_link`` from validated parts (loopback only,
  opaque session id, no userinfo/query/fragment).
- ``Tag``/``Group`` give replace-by-target semantics: one notification per
  completion session (a re-completion replaces the previous entry) and one per
  reminder content hash, so repeats do not stack up in the notification center.
  Tags stay deterministic: values over 64 characters append a digest of the
  FULL input, which removes the deterministic collision two long session ids
  would otherwise get from a shared truncated prefix; the digest itself
  retains only a theoretical collision probability.
- The sender is an inbox PowerShell process (no third-party dependencies) that
  shows the toast and exits immediately; there is no long-lived helper and no
  message loop per notification.
- ``ToastNotifier.Setting`` is reported for failure feedback: when the user or
  policy disabled notifications for this app, the sender reports
  ``windows_notifications_disabled`` instead of pretending success. The show
  call itself is otherwise fire-and-forget; delivery beyond the API accept is
  not provable from this process.

Payload isolation: title/body/tag/group/XML travel only inside base64 literals
of the encoded command; the command never contains raw user text.
"""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
import subprocess

# One identity for every Pan instance on the machine; the notification center
# groups entries under it. Changing it starts a new notification-history entry.
TOAST_APP_ID = "Pan.Desktop.Notifications"

# Button label for the click target (built here, carried as base64, so no
# PowerShell script-encoding pitfalls with non-ASCII literals).
BUTTON_OPEN_SESSION = "打开会话"

COMPLETION_GROUP = "Pan-completions"
REMINDER_GROUP = "Pan-reminders"

_TIMEOUT_SECONDS = 20

# XML 1.0 disallows most C0 control characters; they would make LoadXml throw.
_INVALID_XML_CHARS = re.compile(
    "[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)

_APP_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _sanitize_text(value: object) -> str:
    return _INVALID_XML_CHARS.sub("", str(value or ""))


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def bounded_tag(value: str | None) -> str | None:
    """Deterministically bound a tag to at most 64 characters.

    Windows' per-app tag limit is not documented for unpackaged apps, but a
    plain prefix cut is not safe: two session ids sharing their first 53
    characters would truncate to one identical completion tag, and the second
    completion would silently replace the first notification (wrong target
    binding). Over-long values therefore append a SHA-1 digest of the FULL
    input: identical inputs always collapse (replace-per-session still works)
    while distinct inputs stay distinct up to the digest width.
    """
    if value is None:
        return None
    if len(value) <= 64:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{value[:40]}-{digest}"


def build_toast_xml(title: object, body: object, *,
                    launch_url: str | None = None,
                    button_text: str = BUTTON_OPEN_SESSION) -> str:
    """Toast XML with escaped text. ``launch_url`` is only ever the validated
    loopback deep link produced by ``notifications.completion_deep_link``."""
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
    ]
    launch_attr = ""
    if launch_url:
        launch_attr = f' launch="{_xml_escape(launch_url)}"'
    parts.append(f"<toast{launch_attr}>")
    parts.append('<visual><binding template="ToastGeneric">')
    parts.append(f"<text>{_xml_escape(_sanitize_text(title))}</text>")
    parts.append(f"<text>{_xml_escape(_sanitize_text(body))}</text>")
    parts.append("</binding></visual>")
    if launch_url:
        parts.append("<actions>")
        parts.append(
            f'<action activationType="protocol" arguments="{_xml_escape(launch_url)}" '
            f'content="{_xml_escape(button_text)}"/>'
        )
        parts.append("</actions>")
    parts.append("</toast>")
    return "".join(parts)


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def build_show_script(app_id: str, xml_text: str, tag: str | None,
                      group: str | None) -> str:
    """Build the PowerShell script that shows exactly one toast and exits."""
    if not _APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError("Invalid toast app id")
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
        "$result = @{ ok = $false; method = 'windows.toast' }\n"
        "try {\n"
        "  [void][Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType=WindowsRuntime]\n"
        "  [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
        "ContentType=WindowsRuntime]\n"
        f"  $xmlText = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_b64(xml_text)}'))\n"
        "  $xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "  $xml.LoadXml($xmlText)\n"
        "  $toast = New-Object Windows.UI.Notifications.ToastNotification $xml\n"
        f"  $tag = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_b64(tag or '')}'))\n"
        f"  $group = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_b64(group or '')}'))\n"
        "  if ($tag) { $toast.Tag = $tag }\n"
        "  if ($group) { $toast.Group = $group }\n"
        f"  $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}')\n"
        "  $result.setting = [string]$notifier.Setting\n"
        "  $disabled = @('DisabledForApplication','DisabledForUser',"
        "'DisabledByGroupPolicy','DisabledByManifest')\n"
        "  if ($disabled -contains $result.setting) {\n"
        "    $result.code = 'windows_notifications_disabled'\n"
        "    $result.message = 'Notification setting: ' + $result.setting\n"
        "  } else {\n"
        "    $notifier.Show($toast)\n"
        "    $result.ok = $true\n"
        "  }\n"
        "} catch {\n"
        "  $result.ok = $false\n"
        "  $result.code = 'windows_toast_failed'\n"
        "  $result.message = $_.Exception.Message\n"
        "}\n"
        "[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress))\n"
        "if (-not $result.ok) { exit 1 }\n"
    )


def encode_command(script: str) -> str:
    """Encode a PowerShell script as UTF-16LE base64 (-EncodedCommand)."""
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _powershell_executable() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def _run_powershell(script: str) -> dict:
    executable = _powershell_executable()
    if not executable:
        return {
            "ok": False,
            "code": "windows_powershell_unavailable",
            "message": "PowerShell executable was not found",
        }
    # No -ExecutionPolicy flag: execution policy does not apply to encoded
    # commands, and "bypass"-style flags are malware-heuristic bait.
    argv = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
            "-EncodedCommand", encode_command(script)]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "code": "windows_system_notification_timeout",
            "message": "PowerShell toast sender timed out",
        }
    except OSError as exc:
        return {
            "ok": False,
            "code": "windows_system_notification_launch_failed",
            "message": str(exc),
        }
    result = _parse_result(completed.stdout)
    if completed.returncode != 0 or not (result or {}).get("ok"):
        detail = (result or {}).get("message") or (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "code": (result or {}).get("code") or "windows_system_notification_failed",
            "message": f"PowerShell toast exited with code {completed.returncode}: {detail}"
            if completed.returncode != 0 else str(detail),
        }
    return result


def _parse_result(stdout: str) -> dict | None:
    """The script prints one JSON line; ignore any other console noise."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            import json
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def show_toast(title: object, body: object, *,
               launch_url: str | None = None,
               tag: str | None = None,
               group: str | None = None,
               app_id: str = TOAST_APP_ID) -> dict:
    """Show one Windows toast; safe to call from a worker thread.

    Returns ``{"ok": True, "method": "windows.toast", ...}`` after the OS
    accepted the toast, or a diagnostic error dict. ``delivery`` is reported as
    ``"unknown"`` for clickable toasts: process completion is not proof of
    display, and the notification center entry is what makes a later click
    work.
    """
    xml_text = build_toast_xml(title, body, launch_url=launch_url)
    script = build_show_script(app_id, xml_text, bounded_tag(tag), bounded_tag(group))
    result = _run_powershell(script)
    if result.get("ok"):
        response = {"ok": True, "method": "windows.toast"}
        if result.get("setting"):
            response["setting"] = result["setting"]
        if launch_url:
            response["delivery"] = "unknown"
        return response
    return result


def build_clear_script(app_id: str) -> str:
    if not _APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError("Invalid toast app id")
    return (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
        "[void][Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType=WindowsRuntime]\n"
        f"[Windows.UI.Notifications.ToastNotificationManager]::History.Clear('{app_id}')\n"
        "[Console]::Out.WriteLine('{\"ok\":true}')\n"
    )


def clear_app_notifications(app_id: str = TOAST_APP_ID) -> dict:
    """Remove this app's entries from the notification center (cleanup path)."""
    return _run_powershell(build_clear_script(app_id))

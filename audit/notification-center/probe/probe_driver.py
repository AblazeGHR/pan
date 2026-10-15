"""Probe driver: real Windows toast -> Action Center -> protocol activation.

Steps:
  1. start a logging HTTP server on an owned dynamic port
  2. ensure toast identity (Start Menu shortcut with AUMID + HKCU pan:// protocol)
  3. show one WinRT toast with protocol activation (sender process exits)
  4. screenshot the Action Center (proof the toast is recorded there)
  5. invoke the protocol the way Windows does on a toast click
  6. verify: activation log line + server access log for the deep link

Writes a JSON summary next to this file.
"""
from __future__ import annotations

import base64
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
PS = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
      "-ExecutionPolicy", "Bypass"]
AUMID = "Pan.NotificationsProbe"
PROTOCOL = "pan-probe"
SHORTCUT = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "PanProbe" / "PanProbe.lnk"


summary: dict = {"aumid": AUMID, "protocol": PROTOCOL, "shortcut": str(SHORTCUT)}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_ps(script: str, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(PS + ["-File", str(HERE / script), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False)


def xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def main() -> int:
    port = free_port()
    assert port not in (8767, 8768), "must not touch protected ports"
    server_log = HERE / "server-access.jsonl"
    server = subprocess.Popen(
        [sys.executable, str(HERE / "logserver.py"), str(port), str(server_log)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.7)

        # -- identity -------------------------------------------------------
        activate_ps1 = (HERE / "activate.ps1").resolve()
        protocol_command = (
            f'"powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass '
            f'-File "{activate_ps1}" "%1"'
        )
        r = run_ps("identity.ps1",
                   "-Aumid", AUMID,
                   "-ShortcutPath", str(SHORTCUT),
                   "-ShortcutTarget", "powershell.exe",
                   "-ShortcutArguments",
                   f'-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{activate_ps1}" pan://dashboard',
                   "-ProtocolName", PROTOCOL,
                   "-ProtocolCommand", protocol_command)
        summary["identity"] = {"returncode": r.returncode, "stdout": r.stdout.strip(),
                               "stderr": r.stderr.strip()[-2000:]}

        # -- toast ----------------------------------------------------------
        session_id = "ses_probe01"
        target = f"http://127.0.0.1:{port}/react/?session={session_id}"
        launch_uri = f"{PROTOCOL}://open?target={quote(target, safe='')}"
        title = "Pan: Probe session completed"
        body = 'probe body — special chars <&> "quotes" \\ backslash 中文'
        toast_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            # The toast element has no activationType (schema-verified); the
            # launch string is carried for evidence, the clickable target is
            # the protocol action button.
            f'<toast launch="{xml_escape(launch_uri)}">\n'
            '  <visual>\n'
            '    <binding template="ToastGeneric">\n'
            f'      <text>{xml_escape(title)}</text>\n'
            f'      <text>{xml_escape(body)}</text>\n'
            '    </binding>\n'
            '  </visual>\n'
            '  <actions>\n'
            f'    <action activationType="protocol" arguments="{xml_escape(launch_uri)}" content="打开会话"/>\n'
            '  </actions>\n'
            '</toast>\n'
        )
        summary["toast_xml"] = toast_xml
        r = run_ps("toast.ps1", "-Aumid", AUMID,
                   "-XmlBase64", base64.b64encode(toast_xml.encode("utf-8")).decode("ascii"),
                   "-Tag", f"completion-{session_id}", "-Group", "Pan-completions")
        summary["toast"] = {"returncode": r.returncode, "stdout": r.stdout.strip(),
                            "stderr": r.stderr.strip()[-2000:]}

        # -- action center screenshot (toast must still be present) ---------
        shot = HERE / "action-center.png"
        r = run_ps("actioncenter-shot.ps1", "-OutPath", str(shot))
        summary["screenshot"] = {"returncode": r.returncode, "stdout": r.stdout.strip(),
                                 "stderr": r.stderr.strip()[-2000:],
                                 "file": str(shot), "exists": shot.exists()}

        # -- protocol invocation (what Windows does on a toast click) -------
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                        f'Start-Process "{launch_uri}"'],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30, check=False)
        time.sleep(3.0)

        activations = HERE / "activations.jsonl"
        summary["activations"] = (activations.read_text(encoding="utf-8").strip().splitlines()
                                  if activations.exists() else [])
        summary["server_access"] = (server_log.read_text(encoding="utf-8").strip().splitlines()
                                    if server_log.exists() else [])
        summary["port"] = port
        summary["launch_uri"] = launch_uri
    finally:
        server.terminate()
    (HERE / "probe-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ok = (summary.get("identity", {}).get("returncode") == 0
          and summary.get("toast", {}).get("returncode") == 0
          and summary.get("screenshot", {}).get("exists")
          and any('"raw"' in line and PROTOCOL in line for line in summary.get("activations", []))
          and any("/react/?session=" in line for line in summary.get("server_access", [])))
    print("PROBE_OK" if ok else "PROBE_INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Windows toast sender unit tests; no real OS notification is launched.

These lock the contract that replaced the NotifyIcon balloon:
- payload isolation (title/body/tag/group/XML only inside base64 literals),
- XML escaping and control-character sanitization,
- one fire-and-forget PowerShell process (no message loop, no Bypass flag),
- replace-by-target Tag/Group, Setting-based failure feedback,
- diagnostic error codes preserved from the previous sender contract.
"""
import base64
import re
from types import SimpleNamespace

import pytest

from packages.core import windows_toast


def _script_literals(script: str) -> list[str]:
    return re.findall(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)", script)


def _decode(encoded_command: str) -> str:
    return base64.b64decode(encoded_command).decode("utf-16le")


def test_toast_xml_escapes_user_text_and_keeps_layout():
    xml = windows_toast.build_toast_xml(
        "Pan: <b>&\"quoted\"'</b>", "line1\nline2\t\x00\x1f控制",
        launch_url="http://127.0.0.1:49123/react/?session=ses_a",
    )
    assert "<b>" not in xml and "&lt;b&gt;" in xml
    assert "&amp;" in xml and "&quot;" in xml and "&apos;" in xml
    assert "\x00" not in xml and "\x1f" not in xml
    assert "line1\nline2\t控制" in xml
    assert 'launch="http://127.0.0.1:49123/react/?session=ses_a"' in xml
    assert ('<action activationType="protocol" '
            'arguments="http://127.0.0.1:49123/react/?session=ses_a" content="打开会话"/>') in xml


def test_toast_xml_without_launch_has_no_click_target():
    xml = windows_toast.build_toast_xml("Pan: title", "body")
    assert "launch=" not in xml
    assert "<action" not in xml
    assert "<actions>" not in xml


def test_bounded_tag_keeps_short_values_and_bounds_long_ones():
    assert windows_toast.bounded_tag(None) is None
    assert windows_toast.bounded_tag("completion-ses_ab") == "completion-ses_ab"
    exact = "completion-" + "x" * 53  # exactly 64 chars: unchanged
    assert len(exact) == 64
    assert windows_toast.bounded_tag(exact) == exact
    long = "completion-" + "x" * 80
    bounded = windows_toast.bounded_tag(long)
    assert len(bounded) <= 64
    assert bounded.startswith("completion-" + "x" * 29)  # 40-char prefix kept
    assert windows_toast.bounded_tag(long) == bounded  # deterministic


def test_bounded_tag_long_session_ids_cannot_collide_on_shared_prefix():
    # The reviewed case: session ids sharing their first 53 characters. A plain
    # prefix cut would give both completions ONE identical tag, and the second
    # completion would silently replace the first notification (wrong target).
    sid_a = "x" * 53 + "A"
    sid_b = "x" * 53 + "B"
    tag_a_raw = f"completion-{sid_a}"
    tag_b_raw = f"completion-{sid_b}"
    assert tag_a_raw[:64] == tag_b_raw[:64]  # documents the old collision
    tag_a = windows_toast.bounded_tag(tag_a_raw)
    tag_b = windows_toast.bounded_tag(tag_b_raw)
    assert tag_a != tag_b
    assert len(tag_a) <= 64 and len(tag_b) <= 64
    assert windows_toast.bounded_tag(tag_a_raw) == tag_a  # stable across calls
    # Maximum-length allowlist ids (64 chars) stay distinct the same way.
    max_a = windows_toast.bounded_tag("completion-" + "y" * 63 + "A")
    max_b = windows_toast.bounded_tag("completion-" + "y" * 63 + "B")
    assert max_a != max_b and len(max_a) <= 64


def test_show_script_carries_payload_only_as_base64_and_has_no_message_loop():
    script = windows_toast.build_show_script(
        "Pan.Desktop.Notifications",
        "<toast><visual>secret-marker</visual></toast>",
        "completion-ses_a", "Pan-completions",
    )
    assert not script.startswith("param()")
    assert "secret-marker" not in script
    assert "completion-ses_a" not in script
    literals = _script_literals(script)
    assert len(literals) == 3
    assert base64.b64decode(literals[0]).decode("utf-8").startswith("<toast>")
    assert base64.b64decode(literals[1]).decode("utf-8") == "completion-ses_a"
    assert base64.b64decode(literals[2]).decode("utf-8") == "Pan-completions"
    assert "ToastNotificationManager" in script
    assert "CreateToastNotifier('Pan.Desktop.Notifications')" in script
    assert "$notifier.Show($toast)" in script
    assert "$notifier.Setting" in script
    assert "NotifyIcon" not in script and "ShowBalloonTip" not in script
    assert "Start-Sleep" not in script and "Application]::Run" not in script
    assert "$toast.Tag = $tag" in script and "$toast.Group = $group" in script


def test_show_script_rejects_invalid_app_id():
    with pytest.raises(ValueError):
        windows_toast.build_show_script("bad id'; rm", "<toast/>", None, None)
    with pytest.raises(ValueError):
        windows_toast.build_clear_script("bad id'; rm")


def test_encode_command_roundtrip_utf16le():
    encoded = windows_toast.encode_command("Write-Output '中文'")
    assert base64.b64decode(encoded).decode("utf-16le") == "Write-Output '中文'"


def test_parse_result_ignores_console_noise():
    stdout = "warning line\n{\"ok\":true,\"method\":\"windows.toast\"}\n"
    assert windows_toast._parse_result(stdout) == {"ok": True, "method": "windows.toast"}
    assert windows_toast._parse_result("no json here") is None
    assert windows_toast._parse_result("") is None


def test_show_toast_success_shape_and_delivery_marker(monkeypatch):
    calls = []

    def fake_run(script):
        calls.append(script)
        return {"ok": True, "method": "windows.toast", "setting": "Enabled"}

    monkeypatch.setattr(windows_toast, "_run_powershell", fake_run)
    result = windows_toast.show_toast("Pan: t", "b", launch_url="http://127.0.0.1:1/react/?session=s")
    assert result == {"ok": True, "method": "windows.toast", "setting": "Enabled",
                      "delivery": "unknown"}
    plain = windows_toast.show_toast("Pan: t", "b")
    assert plain == {"ok": True, "method": "windows.toast", "setting": "Enabled",
                     "delivery": "unknown"}
    assert len(calls) == 2
    # The launch URL reaches the XML only; the script keeps it base64-wrapped.
    xml_b64 = _script_literals(calls[0])[0]
    assert "http://127.0.0.1:1/react/?session=s" in base64.b64decode(xml_b64).decode("utf-8")
    assert "http://127.0.0.1:1/react/?session=s" not in calls[0]


def test_run_powershell_maps_failure_modes(monkeypatch):
    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: None)
    assert windows_toast._run_powershell("x")["code"] == "windows_powershell_unavailable"

    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: "powershell.exe")

    def timeout(argv, **kwargs):
        raise windows_toast.subprocess.TimeoutExpired(argv, 20)

    monkeypatch.setattr(windows_toast.subprocess, "run", timeout)
    assert windows_toast._run_powershell("x")["code"] == "windows_system_notification_timeout"

    def oserror(argv, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(windows_toast.subprocess, "run", oserror)
    assert windows_toast._run_powershell("x")["code"] == "windows_system_notification_launch_failed"

    failed = SimpleNamespace(returncode=7, stdout="", stderr="toast exploded")
    monkeypatch.setattr(windows_toast.subprocess, "run", lambda argv, **kw: failed)
    result = windows_toast._run_powershell("x")
    assert result["code"] == "windows_system_notification_failed"
    assert "toast exploded" in result["message"]

    disabled = SimpleNamespace(
        returncode=1,
        stdout='{"ok":false,"code":"windows_notifications_disabled","message":"Notification setting: DisabledForUser"}',
        stderr="",
    )
    monkeypatch.setattr(windows_toast.subprocess, "run", lambda argv, **kw: disabled)
    result = windows_toast._run_powershell("x")
    assert result["code"] == "windows_notifications_disabled"
    assert "DisabledForUser" in result["message"]


def test_run_powershell_uses_encoded_command_without_bypass_flag(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(windows_toast.subprocess, "run", fake_run)
    result = windows_toast._run_powershell("Write-Output 1")
    assert result["ok"] is True
    argv = seen["argv"]
    assert argv[-2:] == ["-EncodedCommand", windows_toast.encode_command("Write-Output 1")]
    assert "-ExecutionPolicy" not in argv
    assert seen["kwargs"]["timeout"] == 20
    assert seen["kwargs"]["creationflags"] == getattr(windows_toast.subprocess, "CREATE_NO_WINDOW", 0)


def test_clear_app_notifications_builds_history_clear(monkeypatch):
    calls = []
    monkeypatch.setattr(windows_toast, "_run_powershell", lambda script: calls.append(script) or {"ok": True})
    assert windows_toast.clear_app_notifications("Pan.Desktop.Notifications") == {"ok": True}
    assert "History.Clear('Pan.Desktop.Notifications')" in calls[0]


def test_completion_tags_replace_per_session_and_reminder_tags_per_content():
    from packages.core import notifications
    assert notifications.reminder_dedup_tag("Pan: a", "b") == notifications.reminder_dedup_tag("Pan: a", "b")
    assert notifications.reminder_dedup_tag("Pan: a", "b") != notifications.reminder_dedup_tag("Pan: a", "c")
    assert notifications.reminder_dedup_tag("Pan: a", "b").startswith("reminder-")


@pytest.mark.parametrize('text', ['X' * 20000, '<&\"\'😀中文' * 5000], ids=['ascii', 'escaped-unicode'])
def test_large_result_fits_real_transport_and_xml_byte_limits(text):
    import xml.etree.ElementTree as ET
    url = 'http://127.0.0.1:49152/react/?session=ses_owned'
    xml = windows_toast.build_toast_xml(text, text, launch_url=url)
    assert len(xml.encode('utf-8')) <= 4096
    root = ET.fromstring(xml)
    assert root.attrib['launch'] == url
    assert root.find('actions/action').attrib['arguments'] == url
    assert root.find('visual/binding/text').text.endswith('…')
    assert root.findall('visual/binding/text')[1].text.endswith('…')
    script = windows_toast.build_show_script(windows_toast.TOAST_APP_ID, xml,
                                             'completion-ses_owned', 'Pan-completions')
    # Windows CreateProcess's 32767 UTF-16 code-unit limit includes argv.
    assert len(windows_toast.encode_command(script)) + 256 < 32767


def test_oversized_activation_metadata_fails_without_launch_or_truncating_target(monkeypatch):
    def unexpected(script):
        pytest.fail('Invalid activation metadata must not launch a helper')
    monkeypatch.setattr(windows_toast, '_run_powershell', unexpected)
    result = windows_toast.show_toast('title', 'body', launch_url='http://127.0.0.1/' + 'x' * 5000)
    assert result['code'] == 'windows_toast_payload_invalid'


def test_helper_preserves_winrt_hresult_and_setting(monkeypatch):
    monkeypatch.setattr(windows_toast, '_powershell_executable', lambda: 'powershell.exe')
    monkeypatch.setattr(windows_toast.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout='{"ok":false,"code":"windows_toast_failed",'
        '"hresult":"0x803E0115","setting":"Enabled","message":"too large"}', stderr=''))
    result = windows_toast._run_powershell('x')
    assert result['hresult'] == '0x803E0115'
    assert result['setting'] == 'Enabled'
    assert result['code'] == 'windows_toast_failed'

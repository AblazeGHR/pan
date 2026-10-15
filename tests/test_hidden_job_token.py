"""Real same-user IPC and fail-closed launch checks for the hidden Job token."""
import ctypes, json, mmap, os, sys, time, uuid
from pathlib import Path
import pytest
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows token API")

def test_hidden_token_preserves_peer_file_and_ipc_access(tmp_path):
    import win32api, win32event, win32file, win32con
    from packages.jobs.desktop import DesktopCommand
    from packages.core.terminal.identity import read_creation_filetime
    name = "PanOwnedIPC_" + uuid.uuid4().hex
    logpath = tmp_path / "stdio.log"
    helper = Path(__file__).parent / "helpers" / "hidden_token_ipc_probe.py"
    with logpath.open("ab", buffering=0) as log, DesktopCommand(
        [sys.executable, str(helper.resolve()), str(tmp_path), name], str(tmp_path), log) as process:
        deadline = time.monotonic() + 8
        while not (tmp_path / "ready.json").exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert read_creation_filetime(process.handle)
        query = win32api.OpenProcess(0x400, False, process.pid)
        query.Close()
        with pytest.raises(Exception) as failure:
            win32api.OpenProcess(0x200, False, process.pid)
        assert failure.value.winerror == 5
        (tmp_path / "owned.txt").write_text("parent-file")
        event = win32event.OpenEvent(0x1F0003, False, name + "event")
        mutex = win32event.OpenMutex(0x1F0001, False, name + "mutex")
        section = mmap.mmap(-1, 4096, tagname=name + "section")
        pipe = win32file.CreateFile(r"\\.\pipe" + chr(92) + name, win32con.GENERIC_WRITE, 0, None,
                                   win32con.OPEN_EXISTING, 0, None)
        try:
            assert win32event.WaitForSingleObject(mutex, 1000) == 0
            win32event.ReleaseMutex(mutex)
            section[:14] = b"parent-section"
            win32file.WriteFile(pipe, b"parent-pipe")
            win32event.SetEvent(event)
            assert process.wait(timeout=10) == 0
        finally:
            section.close()
            for handle in (pipe, mutex, event): handle.Close()
    text = logpath.read_text()
    assert "owned IPC stdout" in text and "owned IPC stderr" in text

@pytest.mark.parametrize("stage", ["token", "create"])
def test_hidden_token_failure_does_not_fallback_to_plain_launch(tmp_path, monkeypatch, stage):
    from packages.jobs import independent_runner as runner
    from packages.jobs.restricted_token import HiddenJobToken
    called = []
    def fail(*args, **kwargs):
        called.append(stage)
        raise ctypes.WinError(1314)
    monkeypatch.setattr(HiddenJobToken, "_build" if stage == "token" else "create_process", fail)
    marker = tmp_path / "must-not-execute"
    result = runner._run_windows_task(tmp_path, "fault", "claim",
        {"argv": [sys.executable, "-c", "from pathlib import Path;Path(" + repr(str(marker)) + ").touch()"],
         "cwd": str(tmp_path)}, dict(os.environ), tmp_path / "fault.log")
    assert result[:2] == ("failed", -1314)
    assert called == [stage] and not marker.exists()
    assert "winerror=1314" in (tmp_path / "fault.log").read_text(encoding="utf-8")

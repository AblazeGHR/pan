import asyncio
import json
import subprocess
import threading

import pytest

from packages.web import native_file_picker as native


class Process:
    def __init__(self, result=None, error=None):
        self.returncode = None
        self.result = result or {'status': 'selected', 'paths': ['C:\\文件.txt', 'C:\\two.txt']}
        self.error = error
        self.killed = False
        self.inputs = []

    def communicate(self, data=None, timeout=None):
        self.inputs.append(data)
        if self.error and not self.killed:
            raise self.error
        self.returncode = 0
        return json.dumps(self.result, ensure_ascii=False).encode('utf-8'), b''

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.setattr(native, 'interactive_desktop', lambda: True)
    process = Process()
    calls = []

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(native.subprocess, 'Popen', spawn)
    return process, calls


def test_selected_paths_only_fixed_helper_hidden_no_cwd_or_shell(helper, tmp_path):
    process, calls = helper
    original = native.os.getcwd()
    result = asyncio.run(native.NativeFilePicker().pick(str(tmp_path)))
    assert result == process.result
    argv, options = calls[0]
    assert argv[-1].endswith('native_file_dialog.ps1')
    assert '-STA' in argv
    assert 'shell' not in options and 'cwd' not in options
    assert options['creationflags'] == subprocess.CREATE_NO_WINDOW
    assert json.loads(process.inputs[0]) == {'directory': str(tmp_path.resolve())}
    assert native.os.getcwd() == original
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('status', ['cancelled', 'failed'])
def test_cancel_or_failure(helper, status):
    helper[0].result = {'status': status, 'paths': []}
    assert asyncio.run(native.NativeFilePicker().pick()) == {'status': status, 'paths': []}


def test_timeout_kills_only_owned_helper_and_releases_gate(helper):
    process, calls = helper
    process.error = subprocess.TimeoutExpired('helper', .01)
    picker = native.NativeFilePicker(timeout=.01)
    assert asyncio.run(picker.pick())['status'] == 'timeout'
    assert process.killed and not picker._gate.locked()
    assert len(calls) == 1


def test_unavailable_does_not_spawn(helper, monkeypatch):
    monkeypatch.setattr(native, 'interactive_desktop', lambda: False)
    assert asyncio.run(native.NativeFilePicker().pick())['status'] == 'unavailable'
    assert not helper[1]


def test_spawn_failure_releases_gate(helper, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('not available')
    monkeypatch.setattr(native.subprocess, 'Popen', fail)
    picker = native.NativeFilePicker()
    assert asyncio.run(picker.pick())['status'] == 'failed'
    assert not picker._gate.locked()


def test_cleanup_uncertain_keeps_owned_helper_and_refuses_more(helper):
    process, calls = helper
    process.error = subprocess.TimeoutExpired('helper', .01)
    def denied():
        raise OSError('cannot terminate')
    process.kill = denied
    picker = native.NativeFilePicker(timeout=.01)
    assert asyncio.run(picker.pick())['status'] == 'timeout'
    assert picker._process is process
    assert asyncio.run(picker.pick())['status'] == 'busy'
    picker.close()
    assert len(calls) == 1


def test_concurrent_request_nonblocking_cancel_and_shutdown_cleanup(helper):
    process, calls = helper
    entered, finish = threading.Event(), threading.Event()
    original = process.communicate

    def wait(data=None, timeout=None):
        if data:
            entered.set()
            assert finish.wait(2)
        return original(data, timeout)

    process.communicate = wait
    picker = native.NativeFilePicker()

    async def run():
        first = asyncio.create_task(picker.pick())
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await picker.pick())['status'] == 'busy'
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        picker.close()
        assert process.killed
        finish.set()
    asyncio.run(run())
    assert len(calls) == 1 and not picker._gate.locked()
    assert asyncio.run(picker.pick())['status'] == 'unavailable'


def test_api_selection_does_not_register_until_existing_confirmation(helper, monkeypatch, tmp_path):
    import packages.web.server as server
    from packages.core.session import Session
    session = Session(id='ses_native_test', name='native test', workdir=str(tmp_path))
    monkeypatch.setattr(server.sess, 'get', lambda sid: session if sid == session.id else None)
    monkeypatch.setattr(server, 'ATTACHMENTS_DIR', tmp_path / 'attachments')
    monkeypatch.setattr(native, 'native_file_picker', native.NativeFilePicker())
    target = tmp_path / 'original.txt'
    target.write_text('unchanged')
    helper[0].result = {'status': 'selected', 'paths': [str(target)]}
    result = asyncio.run(server.pick_native_server_files(session.id, {'directory': str(tmp_path)}))
    assert result['paths'] == [str(target)]
    assert not (tmp_path / 'attachments').exists()
    registered = asyncio.run(server.register_server_file_attachment(session.id, {'path': result['paths'][0]}))
    record = server._attachment_record(session.id, registered['attachmentId'])
    assert record['source'] == 'server_file'
    assert record['path'] == str(target.resolve())
    assert target.read_text() == 'unchanged'
    assert not any(p.name == 'original.txt' for p in (tmp_path / 'attachments').rglob('*'))


def test_api_invalid_session_and_directory(monkeypatch):
    import packages.web.server as server
    from fastapi import HTTPException
    monkeypatch.setattr(server.sess, 'get', lambda sid: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(server.pick_native_server_files('missing', {}))
    assert error.value.status_code == 404
    monkeypatch.setattr(server.sess, 'get', lambda sid: object())
    with pytest.raises(HTTPException) as error:
        asyncio.run(server.pick_native_server_files('present', {'directory': ['invalid']}))
    assert error.value.status_code == 400

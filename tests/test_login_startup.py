"""Native login registration contracts; only owned temp files/registry keys."""
import contextlib
import ctypes
import importlib.machinery
import importlib.util
import os
from pathlib import Path, PurePosixPath
import plistlib
import sys
import threading
import uuid

import pytest

from packages.core import login_startup as login


class MemoryStore:
    def __init__(self):
        self.value = None
        self.writes = 0
        self.fail = False
        self.denied_read = False
        self.disabled = False
        self.other = None
        self.mutex = threading.Lock()
        self.run_key, self.name, self.path = 'owned-test', 'Pan.Test', Path('/owned/test.desktop')

    def read(self):
        if self.denied_read:
            raise PermissionError('read denied')
        return self.value

    def write(self, value):
        if self.fail:
            raise PermissionError('write denied')
        self.value = value
        self.writes += 1

    def external(self):
        return self.other

    def blocked(self):
        return self.disabled

    @contextlib.contextmanager
    def locked(self):
        with self.mutex:
            yield


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'GNOME')
    monkeypatch.setenv('DISPLAY', ':99')
    return login.LoginStartup(tmp_path / '中文 Pan', platform='linux', store=MemoryStore(),
                              python=Path('/usr/bin/python3'), target_allowed=True)


def test_default_reads_never_register_or_save_config(manager):
    for _ in range(3):
        state = manager.status()
        assert not state['enabled'] and not state['registered']
    assert manager.store.writes == 0
    assert not manager.root.exists()


def test_idempotent_toggle_and_native_truth(manager):
    state = manager.status()
    on = manager.update(True, state['revision'])
    assert on['enabled']
    assert manager.update(True, on['revision']) == on
    assert manager.store.writes == 1
    off = manager.update(False, on['revision'])
    assert not off['registered']
    manager.update(False, off['revision'])
    assert manager.store.writes == 2
    # An OS-side removal is immediately visible; no saved boolean.
    manager.update(True, off['revision'])
    manager.store.value = None
    assert not manager.status()['enabled']


def test_failed_write_keeps_previous_state(manager):
    before = manager.status()
    manager.store.fail = True
    with pytest.raises(PermissionError):
        manager.update(True, before['revision'])
    assert manager.status() == before


@pytest.mark.parametrize('foreign', ['external command', '[Desktop Entry]\nHidden=true\n', '<malformed plist>'])
def test_foreign_bytes_never_modified(manager, foreign):
    manager.store.value = foreign
    state = manager.status()
    assert state['conflict'] and not state['available']
    for enabled in (True, False):
        with pytest.raises(login.RegistrationError, match='foreign'):
            manager.update(enabled, state['revision'])
    assert manager.store.value == foreign and manager.store.writes == 0


def test_different_checkout_conflict_explains_both_targets(manager):
    manager.store.value = manager.expected()
    other = login.LoginStartup(manager.root.parent / 'other', platform='linux', store=manager.store,
                               python=manager.python, target_allowed=True)
    state = other.status()
    assert state['conflict'] and str(manager.root / 'scripts/login_start.pyw') in decode_desktop_exec(next(line[5:] for line in state['registration'].splitlines() if line.startswith('Exec=')))
    assert str(other.root) == state['targetRoot']


def test_system_entry_is_conflict(manager):
    manager.store.other = '/etc/xdg/autostart/Pan.Login.desktop'
    state = manager.status()
    assert state['conflict']
    with pytest.raises(login.RegistrationError):
        manager.update(False, state['revision'])
    assert not manager.store.writes


def test_revision_rejects_stale_browser_and_concurrent_toggle(manager):
    revision = manager.status()['revision']
    outcomes = []
    def enable():
        try:
            outcomes.append(manager.update(True, revision)['enabled'])
        except login.RegistrationError:
            outcomes.append('stale')
    threads = [threading.Thread(target=enable) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(outcomes, key=str) == [True, 'stale']
    assert manager.store.writes == 1
    with pytest.raises(login.RegistrationError, match='changed elsewhere'):
        manager.update(False, revision)


def test_task_target_and_headless_are_explicit(manager, monkeypatch):
    manager.allowed = False
    state = manager.status()
    assert 'Task/development' in state['reason']
    with pytest.raises(login.RegistrationError): manager.update(True, state['revision'])
    manager.allowed = True
    monkeypatch.delenv('XDG_CURRENT_DESKTOP')
    state = manager.status()
    assert not state['available'] and 'desktop session' in state['reason']
    assert not manager.store.writes


def test_windows_os_disable_is_not_reported_enabled(manager):
    manager.store.value = manager.expected()
    manager.store.disabled = True
    state = manager.status()
    assert state['registered'] and not state['enabled'] and state['blocked']
    with pytest.raises(login.RegistrationError): manager.update(True, state['revision'])
    assert not manager.update(False, state['revision'])['registered']


def test_launchagent_plist_parses_argv_cwd_and_one_shot(tmp_path):
    root = tmp_path / '中文 path $ ` % "'
    argv = ['/opt/python path/bin/python3', str(root / 'scripts/login_start.pyw'), '--python', '/opt/python path/bin/python3']
    value = plistlib.loads(login.render('darwin', argv, root).encode())
    assert value['ProgramArguments'] == argv and value['WorkingDirectory'] == str(root)
    assert value['RunAtLoad'] is True and value['KeepAlive'] is False


def decode_desktop_exec(value):
    # Independent spec decoder: first unescape desktop string, then quoted Exec.
    value = value.replace('\\\\', '\\')
    tokens, current, quoted, escaped = [], '', False, False
    for char in value:
        if escaped:
            current += char; escaped = False
        elif char == '\\': escaped = True
        elif char == '"': quoted = not quoted
        elif char == ' ' and not quoted:
            tokens.append(current.replace('%%', '%')); current = ''
        else: current += char
    tokens.append(current.replace('%%', '%'))
    return tokens


def test_desktop_exec_round_trip_reserved_spaces_chinese():
    argv = ['/opt/中文 python/bin/python', '/中文 path/$`"\\%file.pyw', '--python', '/opt/python']
    rendered = login.render('linux', argv, PurePosixPath('/opt/中文 path'))
    line = next(line[5:] for line in rendered.splitlines() if line.startswith('Exec='))
    assert decode_desktop_exec(line) == argv
    assert 'Terminal=false' in rendered and 'Hidden=false' in rendered
    assert 'Path=/opt/中文 path\n' in rendered


def test_windows_format_limit():
    with pytest.raises(login.RegistrationError, match='260'):
        login.render('win32', ['C:/' + 'x' * 260], Path('C:/Pan'))


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native registry/command line')
def test_windows_real_owned_registration_round_trip_and_cleanup(tmp_path):
    import winreg
    name = 'Pan.Test.' + uuid.uuid4().hex
    key_path = 'Software\\PanAutostartTests\\' + name
    store = login.NativeStore('win32', name=name, run_key=key_path)
    manager = login.LoginStartup(Path('D:/中文 Pan'), platform='win32', store=store,
                                python=Path(sys.executable), target_allowed=True)
    try:
        assert not manager.status()['registered']
        on = manager.update(True, manager.status()['revision'])
        assert on['enabled'] and store.read() == manager.expected()
        # Use Windows' own command parser to prove spaces/Chinese survive.
        argc = ctypes.c_int()
        parser = ctypes.windll.shell32.CommandLineToArgvW
        parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argv = parser(store.read(), ctypes.byref(argc))
        try:
            assert [argv[i] for i in range(argc.value)] == [str(Path(sys.executable).with_name('pythonw.exe')),
                str(manager.root / 'scripts/login_start.pyw'), '--python', str(Path(sys.executable))]
        finally: ctypes.windll.kernel32.LocalFree(argv)
        assert manager.update(True, on['revision'])['enabled']
        assert not manager.update(False, on['revision'])['registered']
        assert store.read() is None
        store.write('external sentinel')
        state = manager.status()
        with pytest.raises(login.RegistrationError): manager.update(False, state['revision'])
        assert store.read() == 'external sentinel'
    finally:
        # This UUID key belongs exclusively to this test; never touch product Run.
        try: winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError: pass
    with pytest.raises(FileNotFoundError): winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)


@pytest.mark.parametrize('platform', ['darwin', 'linux'])
def test_native_file_readback_and_external_protection(tmp_path, monkeypatch, platform):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setenv('XDG_CONFIG_DIRS', str(tmp_path / 'system-xdg'))
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'KDE')
    monkeypatch.setenv('WAYLAND_DISPLAY', 'test')
    store = login.NativeStore(platform, home=tmp_path)
    if platform == 'darwin': monkeypatch.setattr(store, 'blocked', lambda: False)
    manager = login.LoginStartup(tmp_path / 'Pan', platform=platform, store=store, target_allowed=True)
    assert not store.path.exists()
    state = manager.update(True, manager.status()['revision'])
    assert state['enabled']
    assert not manager.update(False, state['revision'])['enabled']
    store.path.write_text('foreign bytes', encoding='utf-8')
    with pytest.raises(login.RegistrationError): manager.update(False, manager.status()['revision'])
    assert store.path.read_text() == 'foreign bytes'


def test_api_validation_and_error_truth(manager, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from packages.web import server
    monkeypatch.setattr(login, 'LoginStartup', lambda: manager)
    app = FastAPI()
    app.get('/api/settings/login-startup')(server.api_get_login_startup)
    app.put('/api/settings/login-startup')(server.api_put_login_startup)
    client = TestClient(app)
    state = client.get('/api/settings/login-startup').json()
    for body in ({'enabled': 'true', 'revision': state['revision']}, {'enabled': True},
                 {'enabled': True, 'revision': state['revision'], 'target': 'foreign'}):
        assert client.put('/api/settings/login-startup', json=body).status_code == 422
    manager.store.fail = True
    result = client.put('/api/settings/login-startup', json={'enabled': True, 'revision': state['revision']})
    assert result.status_code == 503
    assert client.get('/api/settings/login-startup').json()['enabled'] is False
    manager.store.denied_read = True
    assert client.get('/api/settings/login-startup').status_code == 503


def test_login_entry_pins_paths_and_uses_existing_duplicate_guard(tmp_path, monkeypatch):
    from packages.core import launcher
    path = Path(__file__).resolve().parents[1] / 'scripts/login_start.pyw'
    loader = importlib.machinery.SourceFileLoader('pan_login_test', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    # Root derives from an absolute installed entry, not cwd or an environment root.
    fake_entry = tmp_path / '中文 Pan/scripts/login_start.pyw'
    fake_entry.parent.mkdir(parents=True)
    module.__file__ = str(fake_entry)
    monkeypatch.chdir(tmp_path)
    calls = []
    def duplicate(root, **kwargs):
        calls.append((root, kwargs))
        raise launcher.DuplicateStart('already running')
    monkeypatch.setattr(launcher, 'start_service', duplicate)
    monkeypatch.setattr(sys, 'argv', [str(fake_entry), '--python', sys.executable])
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try: assert module.main() == 0
    finally: sys.stdout, sys.stderr = old_stdout, old_stderr
    assert calls == [(tmp_path / '中文 Pan', {'hidden': True, 'python_argv': [sys.executable]})]
    assert Path.cwd() == tmp_path / '中文 Pan'
    assert (tmp_path / '中文 Pan/data/logs/login-startup.log').is_file()

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows windowless entry probe')
def test_pythonw_entry_has_no_console_and_calls_hidden_launcher(tmp_path):
    import json
    import shutil
    import subprocess
    root = tmp_path / '中文 Pan'
    (root / 'scripts').mkdir(parents=True)
    (root / 'packages/core').mkdir(parents=True)
    for name in ('packages/__init__.py', 'packages/core/__init__.py'):
        (root / name).write_text('', encoding='utf-8')
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/login_start.pyw', root / 'scripts/login_start.pyw')
    # The own stub records the actual pythonw process window facts, without
    # ever starting Pan or a Worker. Actual launcher child flags are tested below.
    (root / 'packages/core/launcher.py').write_text('''
import ctypes, json, os
class DuplicateStart(RuntimeError): pass

def start_service(root, **kwargs):
    window = ctypes.windll.kernel32.GetConsoleWindow()
    visible = bool(window and ctypes.windll.user32.IsWindowVisible(window))
    (root / "probe.json").write_text(json.dumps({"window": window, "visible": visible,
        "cwd": os.getcwd(), "root": str(root), "kwargs": kwargs}), encoding="utf-8")
''', encoding='utf-8')
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PAN_') and key != 'PYTHONPATH'}
    # No CREATE_NO_WINDOW here: prove the registered pythonw entry itself is
    # windowless, even before the launcher applies its child window policy.
    result = subprocess.run([str(pythonw), str(root / 'scripts/login_start.pyw'), '--python', sys.executable],
                            cwd=tmp_path, env=env, timeout=15)
    assert result.returncode == 0
    proof = json.loads((root / 'probe.json').read_text(encoding='utf-8'))
    assert proof['window'] == 0 and proof['visible'] is False
    assert proof['cwd'] == proof['root'] == str(root)
    assert proof['kwargs'] == {'hidden': True, 'python_argv': [sys.executable]}


def test_launcher_hidden_override_pins_absolute_interpreter_without_path_lookup(tmp_path, monkeypatch):
    from packages.core import launcher
    monkeypatch.setattr(launcher, 'listener_owner', lambda _: None)
    monkeypatch.setattr(launcher, 'refresh_qq_state', lambda *args: None)
    monkeypatch.setattr(launcher, 'resolve_python_argv', lambda *args, **kwargs: pytest.fail('PATH resolution used'))
    monkeypatch.setattr(launcher, '_console_hidden', lambda *args: pytest.fail('manual console setting overrides login'))
    captured = []
    def start(root, port, python, hidden, log):
        captured.append((root, python, hidden))
        return {'pid': 1234, 'createdAt': 1.0}
    monkeypatch.setattr(launcher, '_start_main', start)
    monkeypatch.setattr(launcher, 'save_state', lambda *args: None)
    monkeypatch.setattr(launcher, 'wait_ready', lambda **kwargs: {'ok': True})
    monkeypatch.setattr(launcher, 'start_cloudflared', lambda *args, **kwargs: None)
    launcher.start_service(tmp_path, hidden=True, python_argv=[sys.executable])
    assert captured == [(tmp_path, [sys.executable], True)]

@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_corrupt_or_symlinked_entry_cannot_be_claimed(tmp_path, monkeypatch, platform):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setenv('XDG_CONFIG_DIRS', str(tmp_path / 'system-xdg'))
    store = login.NativeStore(platform, home=tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(b'\xff\xfeinvalid')
    with pytest.raises(UnicodeError): store.read()
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == store.path or original(path))
    with pytest.raises(login.RegistrationError, match='symbolic link'): store.read()
    assert store.path.read_bytes() == b'\xff\xfeinvalid'


def test_production_target_rejects_task_branch_and_fails_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    (tmp_path / '.git').write_text('gitdir: task', encoding='utf-8')
    monkeypatch.setattr(login.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='feature/task\n'))
    assert not login.production_target(tmp_path)
    monkeypatch.setattr(login.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='practical\n'))
    assert login.production_target(tmp_path)
    monkeypatch.setattr(login.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=128, stdout=''))
    assert not login.production_target(tmp_path)


def test_unconfirmed_native_write_is_failure(manager, monkeypatch):
    monkeypatch.setattr(manager.store, 'write', lambda value: None)
    with pytest.raises(login.RegistrationError, match='read-back'):
        manager.update(True, manager.status()['revision'])
    assert not manager.status()['enabled']

@pytest.mark.parametrize('native,blocked', [('true', True), ('disabled', True), ('false', False), ('enabled', False)])
def test_macos_override_parser_is_native_truth(tmp_path, monkeypatch, native, blocked):
    from types import SimpleNamespace
    store = login.NativeStore('darwin', home=tmp_path)
    calls = []
    monkeypatch.setattr(login.os, 'getuid', lambda: 501, raising=False)
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='disabled services = {\n "Pan.Login" => ' + native + '\n}', stderr='')
    monkeypatch.setattr(login.subprocess, 'run', run)
    assert store.blocked() is blocked
    assert calls == [['/bin/launchctl', 'print-disabled', 'gui/501']]


def test_macos_override_query_failure_is_not_false_success(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(login.os, 'getuid', lambda: 501, raising=False)
    monkeypatch.setattr(login.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr='no GUI domain'))
    with pytest.raises(login.RegistrationError, match='GUI launchd domain'):
        login.NativeStore('darwin', home=tmp_path).blocked()

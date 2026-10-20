"""Opt-in, current-user login registration. Native entries are the only setting store.

Reads never create an entry. Mutations use a cross-process lock plus revision CAS;
unknown/foreign bytes are never replaced or deleted.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import plistlib
import re
import subprocess
import sys
import tempfile

from packages.jobs.window import command_window_options
from pathlib import Path

NAME = 'Pan.Login'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


class RegistrationError(RuntimeError):
    pass


def desktop_quote(value: str) -> str:
    # Desktop Entry string escaping happens after Exec argument escaping.
    value = value.replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return ('"' + value + '"').replace('\\', '\\\\')


def render(platform: str, argv: list[str], root: Path) -> str:
    if platform == 'win32':
        command = subprocess.list2cmdline(argv)
        if len(command) > 260:
            raise RegistrationError('Windows Run command exceeds the native 260 character limit; use a shorter installation path.')
        return command
    if platform == 'darwin':
        return plistlib.dumps({'Label': NAME, 'ProgramArguments': argv,
                              'WorkingDirectory': str(root), 'RunAtLoad': True,
                              'KeepAlive': False}).decode()
    return ('[Desktop Entry]\nType=Application\nName=Pan\n'
            'Exec=' + ' '.join(desktop_quote(arg) for arg in argv) + '\n'
            'Path=' + str(root).replace('\\', '\\\\') + '\nTerminal=false\nHidden=false\n')


class NativeStore:
    def __init__(self, platform: str, *, home: Path | None = None,
                 name: str = NAME, run_key: str = RUN_KEY):
        self.platform, self.name, self.run_key = platform, name, run_key
        home = home or Path.home()
        self.path = (home / 'Library/LaunchAgents' / (name + '.plist') if platform == 'darwin'
                     else Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config'))) / 'autostart' / (name + '.desktop'))
        self.lock_path = Path(tempfile.gettempdir()) / ('pan-login-' + hashlib.sha256(
            (str(home) + platform + name + run_key).encode()).hexdigest() + '.lock')

    @contextlib.contextmanager
    def locked(self):
        with self.lock_path.open('a+b') as handle:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self) -> str | None:
        if self.platform == 'win32':
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.run_key) as key:
                    value, kind = winreg.QueryValueEx(key, self.name)
                    return value if kind == winreg.REG_SZ else repr((value, kind))
            except FileNotFoundError:
                return None
        try:
            if self.path.is_symlink():
                raise RegistrationError('Autostart path is a symbolic link; refusing to manage it.')
            return self.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return None

    def external(self) -> str | None:
        if self.platform == 'linux':
            for directory in os.environ.get('XDG_CONFIG_DIRS', '/etc/xdg').split(':'):
                candidate = Path(directory) / 'autostart' / (self.name + '.desktop')
                if candidate != self.path and candidate.exists():
                    return str(candidate)
        if self.platform == 'win32':
            import winreg
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self.run_key, 0, winreg.KEY_READ | view) as key:
                        winreg.QueryValueEx(key, self.name)
                        return 'HKLM\\' + self.run_key + '\\' + self.name
                except FileNotFoundError:
                    pass
        return None

    def blocked(self) -> bool:
        if self.platform == 'darwin':
            try:
                result = subprocess.run(['/bin/launchctl', 'print-disabled', f'gui/{os.getuid()}'],
                                        capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RegistrationError('Cannot query launchd login overrides: ' + str(exc)) from exc
            if result.returncode:
                raise RegistrationError('Cannot query the current user GUI launchd domain: ' + result.stderr.strip())
            match = re.search(r'"' + re.escape(self.name) + r'"\s*=>\s*([^\s;]+)', result.stdout)
            if match:
                value = match.group(1)
                if value not in ('true', 'false', 'disabled', 'enabled'):
                    raise RegistrationError('Unrecognized launchd disabled override; native state is unknown.')
                return value in ('true', 'disabled')
            return False
        if self.platform != 'win32':
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run') as key:
                value, _ = winreg.QueryValueEx(key, self.name)
                return bool(value and value[0] not in (2, 6))
        except FileNotFoundError:
            return False

    def write(self, value: str | None):
        if self.platform == 'win32':
            import winreg
            if value is None:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.run_key, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, self.name)
            else:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.run_key, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, self.name, 0, winreg.REG_SZ, value)
        elif value is None:
            self.path.unlink()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.pan-login-')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
                    handle.write(value); handle.flush(); os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)


def production_target(root: Path) -> bool:
    """A task worktree must never become an unattended production target."""
    if 'pan-worktrees' in (part.lower() for part in root.parts):
        return False
    try:
        result = subprocess.run(['git', '-C', str(root), 'branch', '--show-current'],
                                capture_output=True, text=True, timeout=5,
                                **command_window_options())
        return (not (root / '.git').exists()) if result.returncode != 0 else result.stdout.strip() in ('main', 'practical')
    except (OSError, subprocess.TimeoutExpired):
        return not (root / '.git').exists()


class LoginStartup:
    def __init__(self, root: Path | None = None, *, platform: str | None = None,
                 store: NativeStore | None = None, python: Path | None = None,
                 target_allowed: bool | None = None):
        self.root = (root or Path(__file__).resolve().parents[2]).resolve()
        self.platform = platform or sys.platform
        self.store = store or NativeStore(self.platform)
        self.python = (python or Path(sys.executable)).absolute()
        self.allowed = production_target(self.root) if target_allowed is None else target_allowed

    def expected(self) -> str:
        python = self.python
        if any(char in str(self.root) + str(python) for char in ('\n', '\r', '\x00')):
            raise RegistrationError('Installation paths cannot contain control characters.')
        if self.platform == 'win32':
            python = python.with_name('pythonw.exe')
            if not python.is_file():
                raise RegistrationError('A sibling pythonw.exe is required for windowless login startup.')
        return render(self.platform, [str(python), str(self.root / 'scripts/login_start.pyw'),
                                     '--python', str(self.python)], self.root)

    def status(self) -> dict:
        raw = self.store.read()
        reason = ''
        supported = self.platform in ('win32', 'darwin', 'linux')
        if self.platform == 'linux' and not (os.environ.get('XDG_CURRENT_DESKTOP') and
                (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))):
            supported = False
            reason = 'XDG login startup requires a desktop session; headless/system service startup is not supported.'
        expected = None
        try:
            expected = self.expected()
        except RegistrationError as exc:
            reason = str(exc)
        external = self.store.external()
        conflict = (raw is not None and raw != expected) or external is not None
        blocked = self.store.blocked()
        if conflict:
            reason = 'A foreign or different-checkout login entry exists. Remove it from its owning installation before changing this setting.'
        elif blocked:
            reason = ('Windows has disabled this entry in Startup Apps. Enable it there or remove the owned entry here.'
                      if self.platform == 'win32' else 'launchd has disabled this login agent. Re-enable it in the OS or remove the owned entry here.')
        elif not self.allowed:
            reason = 'Task/development branches cannot register login startup. Use the accepted main or practical installation.'
        elif not supported and not reason:
            reason = 'Login startup is not supported on this platform.'
        return {'enabled': raw is not None and raw == expected and not blocked,
                'registered': raw is not None, 'conflict': conflict, 'blocked': blocked,
                'available': supported and expected is not None and self.allowed and not conflict,
                'reason': reason, 'targetRoot': str(self.root), 'dataDirectory': str(self.root / 'data'),
                'entry': self.store.run_key + '\\' + self.store.name if self.platform == 'win32' else str(self.store.path),
                'registration': raw if raw is not None else external,
                'revision': hashlib.sha256(repr((raw, blocked, external, str(self.root), expected, self.allowed)).encode()).hexdigest()}

    def update(self, enabled: bool, revision: str) -> dict:
        with self.store.locked():
            status = self.status()
            if status['revision'] != revision:
                raise RegistrationError('Login startup changed elsewhere. Refresh and try again.')
            if status['conflict']:
                raise RegistrationError(status['reason'])
            if enabled and (not status['available'] or status['blocked']):
                raise RegistrationError(status['reason'])
            if self.status()['revision'] != revision:
                raise RegistrationError('Login startup changed elsewhere. Refresh and try again.')
            before = self.store.read()
            if before is not None and before != self.expected():
                raise RegistrationError('A foreign login entry appeared; refusing to change it.')
            wanted = self.expected() if enabled else None
            if before != wanted:
                self.store.write(wanted)
            after = self.status()
            if after['registered'] != enabled or (enabled and not after['enabled']):
                raise RegistrationError('Native registration read-back did not confirm the requested state. Refresh to inspect the actual state.')
            return after

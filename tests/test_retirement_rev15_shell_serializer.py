"""Exact child-environment shell identity and real Windows owned execution."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='native Windows command-line/owned creation contract')


@pytest.mark.parametrize('case', ['inherited', 'child', 'case-normalized', 'custom',
    'missing-child', 'missing-parent', 'empty', 'relative', 'drive-relative',
    'relative-interpreter', 'nonmatching-child', 'basename-only', 'ambiguous',
    'other-flags', 'extra-argument'])
def test_exact_envelope_and_effective_child_comspec(monkeypatch, case):
    from packages.core.platform.process_creation_win import _owned_command_line
    shell = os.environ['COMSPEC']
    raw = '  "C:\\owned dir\\tool.exe" "quote\\\" and 中文" > "result file"  '
    args = [shell, '/d', '/s', '/c', raw]
    env = None
    expected_raw = case in {'inherited', 'child', 'case-normalized', 'custom'}
    if case == 'child':
        monkeypatch.setenv('COMSPEC', 'Z:\\parent-only\\cmd.exe')
        env = {'cOmSpEc': shell}
    if case == 'case-normalized': env = {'COMSPEC': shell.upper()}
    if case == 'custom':
        args[0] = 'D:\\owned custom\\interpreter.exe'
        env = {'COMSPEC': args[0]}
    if case == 'missing-child': env = {}
    if case == 'missing-parent': monkeypatch.delenv('COMSPEC')
    if case == 'empty': env = {'COMSPEC': ''}
    if case == 'relative': env = {'COMSPEC': 'cmd.exe'}
    if case == 'drive-relative': env = {'COMSPEC': 'C:cmd.exe'}
    if case == 'relative-interpreter': args[0] = 'cmd.exe'
    if case == 'nonmatching-child': env = {'COMSPEC': 'Z:\\child-only\\cmd.exe'}
    if case == 'basename-only': args[0] = 'Z:\\different\\cmd.exe'
    if case == 'ambiguous': env = {'COMSPEC': shell, 'comspec': 'Z:\\other.exe'}
    if case == 'other-flags': args[2] = '/S'
    if case == 'extra-argument': args.append('extra')
    expected = subprocess.list2cmdline(args[:4]) + ' "' + raw + '"' if expected_raw else subprocess.list2cmdline(args)
    assert _owned_command_line(args, env) == expected
    assert args[-1] == raw or case == 'extra-argument'


@pytest.mark.parametrize('hidden', [True, False])
def test_real_custom_child_comspec_preserves_raw_tail_and_owned_cleanup(tmp_path, monkeypatch, hidden):
    from packages.core.platform.process import WindowsProcessOwner
    from packages.core.platform import process_creation_win as native
    interpreter = tmp_path / 'custom shell space' / 'owned-interpreter.exe'
    interpreter.parent.mkdir()
    shutil.copyfile(os.environ['COMSPEC'], interpreter)
    child_env = dict(os.environ)
    child_env['COMSPEC'] = str(interpreter)
    # Parent and child deliberately disagree. Identity must follow child env.
    monkeypatch.setenv('COMSPEC', str(tmp_path / 'nonexistent-parent.exe'))
    command = f'"{sys.executable}" -c "from pathlib import Path; Path(\'raw result.txt\').write_text(\'owned\')"'
    argv = [str(interpreter), '/d', '/s', '/c', command]
    captured = []
    original = native.create_owned_process
    def record(line, *args, **kwargs):
        captured.append((line, kwargs['hidden']))
        return original(line, *args, **kwargs)
    monkeypatch.setattr(native, 'create_owned_process', record)
    owner = WindowsProcessOwner()
    child = owner.spawn(argv, cwd=tmp_path, env=child_env, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, hide_window=hidden)
    try:
        output, _ = child.popen.communicate(timeout=15)
        assert child.popen.returncode == 0, output
        assert (tmp_path / 'raw result.txt').read_text() == 'owned'
        assert captured == [(subprocess.list2cmdline(argv[:4]) + ' "' + command + '"', hidden)]
        assert child.identity.kind == 'filetime'
        assert (child.popen.desktop is not None) is hidden
    finally:
        child.close()
    assert child.closed and child.guard is None


@pytest.mark.parametrize('hidden', [True, False])
def test_real_ordinary_argv_quotes_slashes_spaces_unicode_empty_unchanged(tmp_path, hidden):
    from packages.core.platform.process import WindowsProcessOwner
    from packages.core.platform.process_creation_win import _owned_command_line
    arguments = ['two words', 'embedded"quote', 'trailing\\', 'slashes\\\\"quote', '', '中文', '/d', '/s', '/c']
    argv = [sys.executable, '-c',
        "import json,sys; from pathlib import Path; Path('argv.json').write_text(json.dumps(sys.argv[1:]),encoding='utf-8')", *arguments]
    assert _owned_command_line(argv, None) == subprocess.list2cmdline(argv)
    child = WindowsProcessOwner().spawn(argv, cwd=tmp_path, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, hide_window=hidden)
    try:
        output, _ = child.popen.communicate(timeout=15)
        assert child.popen.returncode == 0, output
        assert json.loads((tmp_path / 'argv.json').read_text(encoding='utf-8')) == arguments
    finally:
        child.close()
    assert child.closed and child.guard is None


@pytest.mark.parametrize('identity', ['missing', 'relative', 'nonmatching'])
def test_real_unproven_child_comspec_keeps_original_crt_path(tmp_path, monkeypatch, identity):
    from packages.core.platform.process import WindowsProcessOwner
    from packages.core.platform import process_creation_win as native
    shell = os.environ['COMSPEC']
    env = {key: val for key, val in os.environ.items() if key.upper() != 'COMSPEC'}
    if identity != 'missing':
        env['COMSPEC'] = 'cmd.exe' if identity == 'relative' else str(tmp_path / 'other' / 'cmd.exe')
    command = f'"{sys.executable}" -c "from pathlib import Path; Path(\'must-not-exist\').write_text(\'owned\')"'
    argv = [shell, '/d', '/s', '/c', command]
    lines = []
    original = native.create_owned_process
    def record(line, *args, **kwargs):
        lines.append(line)
        return original(line, *args, **kwargs)
    monkeypatch.setattr(native, 'create_owned_process', record)
    child = WindowsProcessOwner().spawn(argv, cwd=tmp_path, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, hide_window=True)
    try:
        child.popen.communicate(timeout=15)
        assert child.popen.returncode == 1
        assert not (tmp_path / 'must-not-exist').exists()
        assert lines == [subprocess.list2cmdline(argv)]
    finally:
        child.close()
    assert child.closed and child.guard is None

"""Owned Windows reproduction; observations do not replace the retained red.

The creation-edge wrapper records and forwards every argument unchanged. It
does not mock a process, transport, receipt or command result.
"""
import hashlib
import json
import subprocess
import sys

import pytest

from packages.jobs.final_service import normalize_legacy_form_fields


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows native command-line/owned creation diagnostic')
def test_same_raw_command_legacy_shell_vs_current_crt_serialization(tmp_path, monkeypatch):
    assert sys.platform == 'win32', 'This exact diagnostic requires real Windows; no simulated pass'
    from packages.core.platform.desktop_win import DesktopCommand
    from packages.core.platform.process import WindowsProcessOwner
    from packages.core.platform import process_creation_win as native

    command = f'"{sys.executable}" -c "from pathlib import Path; Path(\'shell-result.txt\').write_text(\'owned\')"'
    original = native.create_owned_process
    observed = []

    def capture(line, *args, **kwargs):
        observed.append({'commandLine': line, 'utf16leHex': line.encode('utf-16le').hex(),
                         'sha256Utf16le': hashlib.sha256(line.encode('utf-16le')).hexdigest(),
                         'hidden': kwargs['hidden']})
        return original(line, *args, **kwargs)

    monkeypatch.setattr(native, 'create_owned_process', capture)
    outcomes = []
    for mode in ('legacy-shell', 'canonical-shell', 'canonical-without-s', 'ordinary-argv'):
        cwd = tmp_path / mode
        cwd.mkdir()
        log_path = cwd / 'process.log'
        with log_path.open('wb', buffering=0) as log:
            if mode == 'legacy-shell':
                with DesktopCommand(command, str(cwd), log, shell=True) as child:
                    code = child.wait(timeout=15)
                closed = child.handle == child.thread_handle == 0
            else:
                mapped = normalize_legacy_form_fields({'command': command, 'cwd': str(cwd), 'hideWindow': True})
                assert mapped['argv'][-1] == command
                if mode == 'ordinary-argv':
                    argv = [sys.executable, '-c', "from pathlib import Path; Path('shell-result.txt').write_text('owned')"]
                else:
                    argv = mapped['argv']
                    if mode == 'canonical-without-s':
                        argv = [arg for arg in argv[:-1] if arg != '/s'] + [command]
                    assert argv[-1] == command
                child = WindowsProcessOwner().spawn(argv, cwd=cwd, stdout=log,
                                                    stderr=subprocess.STDOUT, hide_window=True)
                try:
                    code = child.popen.wait(timeout=15)
                finally:
                    child.close()
                closed = child.closed
                expected_line = (subprocess.list2cmdline(argv[:4]) + ' "' + command + '"'
                                 if mode == 'canonical-shell' else subprocess.list2cmdline(argv))
                assert observed[-1]['commandLine'] == expected_line
            assert closed
        marker = cwd / 'shell-result.txt'
        outcomes.append({'mode': mode, 'exit': code, 'marker': marker.read_text() if marker.exists() else None,
                         'closed': closed, 'logHex': log_path.read_bytes().hex(), **observed[-1]})

    assert [item['exit'] for item in outcomes] == [0, 0, 1, 0], outcomes
    assert [item['marker'] for item in outcomes] == ['owned', 'owned', None, 'owned']
    legacy, current, without_s, argv = outcomes
    assert legacy['commandLine'].endswith('/c "' + command + '"')
    assert current['commandLine'].endswith('/d /s /c "' + command + '"')
    assert '\\"' in without_s['commandLine']
    assert current['hidden'] and legacy['hidden'] and argv['hidden']
    print('SHELL_DIAGNOSTIC=' + json.dumps({'rawCommand': command,
        'rawCommandUtf16leHex': command.encode('utf-16le').hex(), 'results': outcomes}))

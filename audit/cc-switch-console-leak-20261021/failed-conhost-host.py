"""Isolated host exercising actual API provider spawn after host FreeConsole."""
import asyncio
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from ctypes import wintypes

from packages.core.host_console import HostConsole
from packages.plugins import providers


async def main():
    mode, launcher = sys.argv[1:3]
    host = HostConsole()
    host.managed = True
    host.initialize()  # actual initial FreeConsole path
    assert host.snapshot()['state'] == 'absent'
    if mode == 'shown':
        assert host.change('show')['state'] == 'visible'
    if mode in ('legacy', 'close'):
        providers.command_window_options = lambda: {}
    probe = str(Path(__file__).with_name('provider_console_probe.py'))
    command, args = sys.executable, [probe]
    if mode == 'close':
        command, args = str(Path(os.environ['SystemRoot']) / 'System32/conhost.exe'), [command, *args]
    if launcher == 'cmd':
        command, args = os.environ['COMSPEC'], ['/d', '/c', subprocess.list2cmdline([command, *args])]
    elif launcher == 'powershell':
        command, args = 'powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
            '& ' + ' '.join("'" + x.replace("'", "''") + "'" for x in [command, *args])]
    document = {'provider': {'transport': 'stdio_jsonrpc', 'command_ref': 'owned'},
                'capabilities': [{'id': 'probe', 'description': 'owned', 'input_schema': {},
                                  'execution': {'operation': 'probe'}}]}
    catalog = SimpleNamespace(servers={'owned': {'name': 'owned', 'command': command, 'args': args}})
    provider = providers.ApiProvider(document, catalog, SimpleNamespace(id='owned', name='owned'))
    payload = {'name': 'probe', 'arguments': {}}
    evidence = {'mode': mode, 'launcher': launcher, 'host_pid': os.getpid()}
    try:
        first = (await provider.request('call', payload))['structuredContent']
        evidence['child'] = first
        assert first['visible'] == (mode in ('legacy', 'close')), evidence
        assert (await provider.request('call', payload))['structuredContent'] == first
        if mode == 'close':
            assert first['window_class'] == 'ConsoleWindowClass', first
            # HWND comes directly from this owned child, never from enumeration.
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            assert user.PostMessageW(first['hwnd'], 0x112, 0xF060, 0)
            try:
                await asyncio.wait_for(provider.process.wait(), 10)
            except TimeoutError:
                print(json.dumps({'close_timeout': first, 'returncode': provider.process.returncode}), flush=True)
                raise
            try:
                await provider.request('call', payload)
                raise AssertionError('closed provider succeeded')
            except (OSError, RuntimeError):
                pass
            old_process = provider.process
            try:
                await provider.request('call', payload)
                raise AssertionError('failed connection replayed')
            except RuntimeError as exc:
                assert 'explicit reconnect required' in str(exc)
            assert provider.process is old_process
            evidence['close_causes_failure_without_replay'] = True
    finally:
        await provider.close()
        assert provider.process.returncode is not None
        evidence['exitcode'] = provider.process.returncode
        if mode == 'shown':
            assert host.snapshot()['state'] == 'visible'
            assert host.change('hide')['state'] == 'hidden'
            assert host.change('show')['state'] == 'visible'
            host.change('hide')
        evidence['cleaned'] = True
    print(json.dumps(evidence), flush=True)


asyncio.run(main())

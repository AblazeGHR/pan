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
    if mode == 'legacy':
        providers.command_window_options = lambda: {}
    probe = str(Path(__file__).with_name('provider_console_probe.py'))
    command, args = sys.executable, [probe]
    if launcher == 'cmd':
        command, args = os.environ['COMSPEC'], ['/d', '/c', subprocess.list2cmdline([command, *args])]
    elif launcher == 'powershell':
        command, args = 'powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
            '& ' + ' '.join("'" + x.replace("'", "''") + "'" for x in [command, *args])]
    document = {'provider': {'transport': 'stdio_jsonrpc', 'command_ref': 'owned'},
                'capabilities': [{'id': 'probe', 'description': 'owned', 'input_schema': {},
                                  'execution': {'operation': 'probe'}}]}
    catalog = SimpleNamespace(servers={'owned': {'name': 'owned', 'command': command, 'args': args,
        'env': {'PAN_TEST_PROVIDER_HOST_PID': str(os.getpid())}}})
    provider = providers.ApiProvider(document, catalog, SimpleNamespace(id='owned', name='owned'))
    payload = {'name': 'probe', 'arguments': {}}
    evidence = {'mode': mode, 'launcher': launcher, 'host_pid': os.getpid()}
    child_handle = None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        first = (await provider.request('call', payload))['structuredContent']
        evidence['child'] = first
        child_handle = kernel.OpenProcess(0x100000, False, first['pid'])
        assert child_handle, ctypes.get_last_error()
        assert bool(first['hwnd']) == (mode == 'legacy'), evidence
        # PseudoConsoleWindow visibility is not evidence of a visible terminal.
        assert not first['desktop_windows'], evidence
        assert (await provider.request('call', payload))['structuredContent'] == first
    finally:
        await provider.close()
        assert provider.process.returncode is not None
        if child_handle:
            try:
                assert kernel.WaitForSingleObject(child_handle, 5000) == 0, 'owned provider descendant survived'
            finally:
                assert kernel.CloseHandle(child_handle)
        evidence['exitcode'] = provider.process.returncode
        if mode == 'shown':
            assert host.snapshot()['state'] == 'visible'
            assert host.change('hide')['state'] == 'hidden'
            assert host.change('show')['state'] == 'visible'
            host.change('hide')
        evidence['cleaned'] = True
    print(json.dumps(evidence), flush=True)


asyncio.run(main())

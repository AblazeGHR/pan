"""Owned plugin endpoints: actual transport on the host running these tests."""
import asyncio
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.plugins import ipc


def test_long_caller_path_concurrent_bridges_roundtrip_and_cleanup(tmp_path):
    async def run():
        root = tmp_path / ('caller-' + 'x' * 80) / ('profile-' + 'y' * 80)
        root.mkdir(parents=True)
        sentinel = root / 'keep'
        sentinel.write_text('owned caller data')
        bridges = []
        addresses = []
        try:
            for index in range(2):
                runtime = SimpleNamespace(handle=AsyncMock(return_value={'index': index}), close=AsyncMock())
                bridge = ipc.Bridge(runtime, root)
                bridges.append(bridge)
                addresses.append(bridge.address)
                assert await asyncio.to_thread(ipc.request, {'op': 'probe'}, bridge.address) == {'index': index}
                if os.name != 'nt':
                    assert len(os.fsencode(bridge.address)) < 104
                    assert stat.S_IMODE(Path(bridge.address).parent.stat().st_mode) == 0o700
            assert len(set(addresses)) == 2
        finally:
            for bridge in bridges:
                await bridge.close()
                assert not bridge.thread.is_alive()
                if os.name != 'nt':
                    assert not Path(bridge.address).exists()
                    assert not Path(bridge.address).parent.exists()
            assert sentinel.read_text() == 'owned caller data'
    asyncio.run(run())


def test_long_temp_root_uses_short_private_endpoint(monkeypatch, tmp_path):
    from packages.core.platform import plugin_ipc
    host_temp = plugin_ipc.tempfile.gettempdir()
    long_temp = tmp_path / ('t' * 110)
    long_temp.mkdir()
    monkeypatch.setattr(plugin_ipc.tempfile, 'gettempdir', lambda: str(long_temp))
    if os.name == 'nt':
        # Pure policy test on Windows: redirect only the POSIX /tmp allocation
        # to an owned short root, never claim a real Unix bind succeeded.
        original = plugin_ipc.tempfile.mkdtemp
        monkeypatch.setattr(plugin_ipc.tempfile, 'mkdtemp',
            lambda **kwargs: original(**{**kwargs, 'dir': host_temp}) if kwargs['dir'] == '/tmp' else original(**kwargs))
    endpoint = plugin_ipc.UnixEndpoint()
    try:
        assert len(os.fsencode(endpoint.address)) < 104
        assert Path(endpoint.address).parent.parent != long_temp
    finally:
        endpoint.close()


def test_listener_failure_cleans_only_its_endpoint(monkeypatch, tmp_path):
    from packages.core.platform import plugin_ipc
    endpoints = []
    original = plugin_ipc.UnixEndpoint
    def allocate():
        value = original()
        endpoints.append(value)
        return value
    monkeypatch.setattr(plugin_ipc, 'UnixEndpoint', allocate)
    # Exercise constructor unwind without claiming Windows has AF_UNIX transport.
    monkeypatch.setattr(ipc, 'os', SimpleNamespace(name='posix', getpid=os.getpid))
    monkeypatch.setattr(ipc, 'Listener', lambda *a, **k: (_ for _ in ()).throw(OSError('bind failed')))
    async def run():
        with pytest.raises(OSError, match='bind failed'):
            ipc.Bridge(SimpleNamespace(), tmp_path)
    asyncio.run(run())
    assert len(endpoints) == 1
    assert not Path(endpoints[0].address).parent.exists()


def test_private_endpoint_refuses_recursive_cleanup_of_unknown_entries():
    from packages.core.platform.plugin_ipc import UnixEndpoint
    endpoint = UnixEndpoint()
    extra = Path(endpoint.address).parent / 'unknown'
    extra.write_text('keep')
    try:
        with pytest.raises(OSError):
            endpoint.close()
        assert extra.read_text() == 'keep'
    finally:
        extra.unlink()
        endpoint.close()


def test_start_exception_restores_environment_and_retires_bridge(monkeypatch, tmp_path):
    runtime = SimpleNamespace(start=lambda: (_ for _ in ()).throw(RuntimeError('start failed')), close=AsyncMock())
    monkeypatch.setattr(ipc, 'Runtime', lambda *a, **k: runtime)
    monkeypatch.setenv(ipc.ENV, 'previous-owned-endpoint')
    async def run():
        with pytest.raises(RuntimeError, match='start failed'):
            async with ipc.plugin_lifespan(None, None, tmp_path):
                pytest.fail('must not yield')
    asyncio.run(run())
    assert os.environ[ipc.ENV] == 'previous-owned-endpoint'
    runtime.close.assert_awaited_once()

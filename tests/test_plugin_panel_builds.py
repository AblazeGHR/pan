"""Local web-app panel contract, build lifecycle and asset serving.

Builds run a real child process in a temporary plugin directory; no installed
plugin, real Pan data directory or network is involved.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from packages.core.manifest_loader import load_manifests
from packages.plugins.catalog import Catalog
from packages.plugins.panel_builds import PanelNotReady, _BuildProcess
from packages.plugins.panels import PanelHost

BUILD_SCRIPT = """\
import pathlib, sys, time
root = pathlib.Path(__file__).resolve().parent
if (root / 'fail.flag').exists():
    print('intentional build failure')
    sys.exit(3)
version = (root / 'version.txt').read_text(encoding='utf-8').strip()
time.sleep(float((root / 'delay.txt').read_text(encoding='utf-8')) if (root / 'delay.txt').exists() else 0)
out = root / 'dist'
(out / 'assets').mkdir(parents=True, exist_ok=True)
(out / 'index.html').write_text('<!doctype html><title>v' + version + '</title>'
                                '<script type="module" src="./assets/app.js"></script>', encoding='utf-8')
(out / 'assets' / 'app.js').write_text('console.log("v' + version + '")', encoding='utf-8')
print('built version', version)
"""


def write_plugin(root: Path, *, entry='index.html', output='dist', version='1') -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'build.py').write_text(BUILD_SCRIPT, encoding='utf-8')
    (root / 'version.txt').write_text(version, encoding='utf-8')
    manifest = {
        'plugins': [{
            'protocol': 'pan-plugin/v1', 'id': 'local-console', 'display_name': 'Local Console',
            'version': '1.0.0', 'source': {'kind': 'panel'},
            'panels': [{
                'id': 'console', 'title': 'Local Console', 'renderer': 'web-app/v1',
                'entry': entry,
                'build': {'command': [sys.executable, 'build.py'], 'cwd': '.',
                          'output': output, 'timeout_seconds': 60},
            }],
        }],
    }
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return root / 'manifest.json'


def host_for(manifest_path: Path, data_root: Path) -> PanelHost:
    manifest = load_manifests([str(manifest_path)])
    return PanelHost(lambda: manifest, data_root)


def test_web_app_manifest_contract_and_path_fencing(tmp_path):
    manifest_path = write_plugin(tmp_path / 'plugin')
    manifest = load_manifests([str(manifest_path)])
    panel = Catalog(manifest).get('local-console').panels[0]
    assert panel['renderer'] == 'web-app/v1'
    assert Path(panel['build']['output']).is_absolute()
    assert panel['build']['cwd'] == str((tmp_path / 'plugin').resolve())

    for broken in ({'entry': '../outside.html'}, {'entry': '/absolute.html'}, {'entry': 'C:/x.html'}):
        candidate = json.loads(manifest_path.read_text(encoding='utf-8'))
        candidate['plugins'][0]['panels'][0].update(broken)
        manifest_path.write_text(json.dumps(candidate), encoding='utf-8')
        with pytest.raises(ValueError):
            Catalog(load_manifests([str(manifest_path)]))

    candidate = json.loads(manifest_path.read_text(encoding='utf-8'))
    candidate['plugins'][0]['panels'][0]['build']['output'] = '../outside'
    (tmp_path / 'plugin' / 'manifest.json').write_text(json.dumps(candidate), encoding='utf-8')
    with pytest.raises(ValueError, match='escapes the plugin directory'):
        load_manifests([str(manifest_path)])

    candidate['plugins'][0]['panels'][0] = {
        'id': 'console', 'title': 'Local Console', 'renderer': 'account-switcher/v1',
        'actions': {'list': {'capability': 'local-console.list', 'effect': 'read'}},
    }
    (tmp_path / 'plugin' / 'manifest.json').write_text(json.dumps(candidate), encoding='utf-8')
    with pytest.raises(ValueError, match='account-switcher/v1'):
        Catalog(load_manifests([str(manifest_path)]))


def test_build_timeout_is_reported_without_publishing_an_artifact(tmp_path):
    manifest_path = write_plugin(tmp_path / 'plugin')
    candidate = json.loads(manifest_path.read_text(encoding='utf-8'))
    candidate['plugins'][0]['panels'][0]['build'] = {
        'command': [sys.executable, '-c', 'import time; time.sleep(5)'],
        'cwd': '.', 'output': 'dist', 'timeout_seconds': 1,
    }
    manifest_path.write_text(json.dumps(candidate), encoding='utf-8')
    data = tmp_path / 'data'

    async def run():
        host = host_for(manifest_path, data)
        try:
            await host.start_build('local-console', 'console')
            state = await host.builds.wait('local-console', 'console')
            assert state['status'] == 'failed'
            assert 'timed out' in state['lastError']
            assert not state['available']
        finally:
            await host.close()

    asyncio.run(run())


def test_build_enable_serve_duplicate_failure_and_unbuild(tmp_path):
    manifest_path = write_plugin(tmp_path / 'plugin')
    data = tmp_path / 'data'

    async def run():
        host = host_for(manifest_path, data)
        try:
            assert host.panels() == []
            assert host.web_panels()[0]['status'] == 'unbuilt'
            with pytest.raises(ValueError, match='successful build'):
                host.set_panel_enabled('local-console', 'console', True)

            started = await host.start_build('local-console', 'console')
            assert started['status'] == 'building'
            finished = await host.builds.wait('local-console', 'console')
            assert finished['status'] == 'built' and finished['available']
            assert 'built version 1' in finished['log']

            with pytest.raises(PanelNotReady):
                host.panel_asset('local-console', 'console', 'missing-build', '')
            host.set_panel_enabled('local-console', 'console', True)
            build_a = finished['activeBuild']
            listing = host.panels()
            assert listing[0]['renderer'] == 'web-app/v1'
            assert listing[0]['assetPath'] == f'/panel-assets/local-console/console/{build_a}/'
            assert listing[0]['path'] == '/plugins/local-console/console'

            entry = host.panel_asset('local-console', 'console', build_a, '')
            assert entry.media_type.startswith('text/html')
            assert b'v1' in entry.path.read_bytes()
            script = host.panel_asset('local-console', 'console', build_a, 'assets/app.js')
            assert script.media_type.startswith('text/javascript')
            spa = host.panel_asset('local-console', 'console', build_a, 'deep/route')
            assert spa.path == entry.path
            assert host.panel_asset('local-console', 'console', build_a, 'missing.js') is None
            assert host.panel_asset('local-console', 'console', build_a, '.pan-panel-build.json') is None
            for bad in ('../state.json', 'assets/../../state.json', 'C:/Windows/win.ini', '%2e%2e/x'):
                with pytest.raises(ValueError):
                    host.panel_asset('local-console', 'console', build_a, bad)

            # A slow build makes a duplicate request attach to the running one.
            (tmp_path / 'plugin' / 'delay.txt').write_text('0.6', encoding='utf-8')
            first = await host.start_build('local-console', 'console')
            second = await host.start_build('local-console', 'console')
            assert first['status'] == second['status'] == 'building'
            with pytest.raises(ValueError, match='while a build is running'):
                host.unbuild_panel('local-console', 'console')
            done = await host.builds.wait('local-console', 'console')
            assert done['status'] == 'built'
            assert len(list((data / 'plugin-panels/local-console/console/builds').iterdir())) == 2

            # A failing rebuild keeps the previous artifact usable.
            (tmp_path / 'plugin' / 'version.txt').write_text('2', encoding='utf-8')
            (tmp_path / 'plugin' / 'fail.flag').write_text('', encoding='utf-8')
            await host.start_build('local-console', 'console')
            failed = await host.builds.wait('local-console', 'console')
            assert failed['status'] == 'failed' and failed['available']
            assert 'code 3' in failed['lastError']
            assert 'intentional build failure' in failed['log']
            assert b'v1' in host.panel_asset('local-console', 'console', build_a, 'assets/app.js').path.read_bytes()
            assert host.panels()[0]['id'] == 'console'

            # Unbuild removes only the host copy; source files and settings stay.
            (tmp_path / 'plugin' / 'fail.flag').unlink()
            host.unbuild_panel('local-console', 'console')
            state = host.builds.status('local-console', 'console')
            assert state == {**state, 'enabled': False, 'available': False, 'status': 'unbuilt',
                             'activeBuild': None, 'log': ''}
            assert host.panels() == []
            assert (tmp_path / 'plugin' / 'build.py').is_file()
            assert (tmp_path / 'plugin' / 'version.txt').read_text(encoding='utf-8') == '2'
            assert not (data / 'plugin-panels/local-console/console/builds').exists()
        finally:
            await host.close()

        # Cold load keeps the terminal state and recovers an interrupted build.
        resumed = host_for(manifest_path, data)
        try:
            assert resumed.builds.status('local-console', 'console')['status'] == 'unbuilt'
            state_file = data / 'plugin-panels/local-console/console/state.json'
            saved = json.loads(state_file.read_text(encoding='utf-8'))
            saved['status'] = 'building'
            state_file.write_text(json.dumps(saved), encoding='utf-8')
        finally:
            await resumed.close()
        recovered = host_for(manifest_path, data)
        try:
            status = recovered.builds.status('local-console', 'console')
            assert status['status'] == 'failed'
            assert 'interrupted' in status['lastError']
        finally:
            await recovered.close()

    asyncio.run(run())


def test_existing_account_switcher_panel_and_mcp_catalog_are_unchanged(tmp_path):
    manifest = load_manifests(['manifest.json', 'packages/mcp/manifest.json'])
    host = PanelHost(lambda: manifest, tmp_path)
    panels = host.panels()
    assert panels[0]['renderer'] == 'account-switcher/v1'
    assert panels[0]['path'] == '/plugins/cc-switch-bridge/accounts'
    assert 'assetPath' not in panels[0]
    assert host.web_panels() == []
    assert 'cc-switch-bridge' in Catalog(manifest).plugins


def test_panel_build_http_routes_ignore_page_supplied_commands(tmp_path):
    import httpx
    from packages.web import server

    manifest_path = write_plugin(tmp_path / 'plugin')
    data = tmp_path / 'data'

    async def run():
        host = host_for(manifest_path, data)
        server.app.state.plugin_panels = host
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url='http://127.0.0.1') as client:
                listed = (await client.get('/api/plugin-panels')).json()['panels']
                assert listed[0]['status'] == 'unbuilt'
                assert (await client.post('/api/plugin-panels/local-console/console/enable')).status_code == 400
                response = await client.post('/api/plugin-panels/local-console/console/build',
                                             json={'command': ['definitely-not-run']})
                assert response.status_code == 200 and response.json()['status'] == 'building'
                for _ in range(100):
                    state = (await client.get('/api/plugin-panels/local-console/console')).json()
                    if state['status'] != 'building':
                        break
                    await asyncio.sleep(0.05)
                assert state['status'] == 'built', state
                assert 'built version 1' in state['log']
                assert (await client.post('/api/plugin-panels/local-console/console/enable')).status_code == 200
                build_id = state['activeBuild']
                base = f'/panel-assets/local-console/console/{build_id}/'
                navigation = (await client.get('/api/plugins/panels')).json()['panels']
                assert navigation == [{'id': 'console', 'title': 'Local Console',
                                       'renderer': 'web-app/v1', 'entry': 'index.html',
                                       'pluginId': 'local-console', 'path': '/plugins/local-console/console',
                                       'assetPath': base}]
                page = await client.get(base)
                assert page.status_code == 200 and page.headers['content-type'].startswith('text/html')
                script = await client.get(base + 'assets/app.js')
                assert script.status_code == 200 and script.headers['content-type'].startswith('text/javascript')
                traversal = await client.get(base + '%2e%2e/%2e%2e/state.json')
                assert traversal.status_code != 200 or b'panelId' not in traversal.content
                assert (await client.post('/api/plugin-panels/local-console/console/disable')).status_code == 200
                assert (await client.get(base)).status_code == 404
                assert (await client.post('/api/plugin-panels/local-console/console/unbuild')).status_code == 200
                assert (await client.get('/api/plugin-panels/local-console/console')).json()['status'] == 'unbuilt'
        finally:
            server.app.state.plugin_panels = None
            await host.close()

    asyncio.run(run())


TREE_SCRIPT = """\
import pathlib, subprocess, sys, time
root = pathlib.Path(__file__).resolve().parent
mode = (root / 'mode.txt').read_text(encoding='utf-8').strip()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
(root / 'child.pid').write_text(str(child.pid), encoding='utf-8')
out = root / 'dist'
(out / 'assets').mkdir(parents=True, exist_ok=True)
(out / 'index.html').write_text('<!doctype html><title>tree</title>', encoding='utf-8')
(out / 'assets' / 'app.js').write_text('console.log("tree")', encoding='utf-8')
if mode == 'exit':
    print('root exited with a live child', flush=True)
    sys.exit(0)
time.sleep(60)
"""


def tree_plugin(tmp_path: Path, mode: str, timeout: int = 30) -> Path:
    manifest_path = write_plugin(tmp_path / 'plugin')
    plugin = tmp_path / 'plugin'
    (plugin / 'build.py').write_text(TREE_SCRIPT, encoding='utf-8')
    (plugin / 'mode.txt').write_text(mode, encoding='utf-8')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['plugins'][0]['panels'][0]['build']['timeout_seconds'] = timeout
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    return manifest_path


async def async_child_pid(plugin: Path) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        path = plugin / 'child.pid'
        if path.exists():
            return int(path.read_text(encoding='utf-8'))
        await asyncio.sleep(0.02)
    raise AssertionError('build child pid was not recorded')


def test_root_exit_with_child_holding_stdout_is_cleaned_and_bounded(tmp_path):
    plugin = tmp_path / 'plugin'
    manifest_path = tree_plugin(tmp_path, 'exit')

    async def run():
        host = host_for(manifest_path, tmp_path / 'data')
        try:
            started = time.monotonic()
            await host.start_build('local-console', 'console')
            state = await host.builds.wait('local-console', 'console')
            elapsed = time.monotonic() - started
            assert state['status'] == 'built', state
            assert 'Terminated ' in state['log'] and 'remaining build process' in state['log']
            assert elapsed < 10, elapsed
            pid = int((plugin / 'child.pid').read_text(encoding='utf-8'))
            assert not psutil.pid_exists(pid), f'build child {pid} survived'
        finally:
            await host.close()

    asyncio.run(run())


def test_build_timeout_kills_the_whole_tree_and_reports_failure(tmp_path):
    plugin = tmp_path / 'plugin'
    manifest_path = tree_plugin(tmp_path, 'sleep', timeout=1)

    async def run():
        host = host_for(manifest_path, tmp_path / 'data')
        try:
            await host.start_build('local-console', 'console')
            state = await host.builds.wait('local-console', 'console')
            assert state['status'] == 'failed'
            assert 'timed out' in state['lastError']
            assert 'not confirmed' not in state['lastError']
            pid = int((plugin / 'child.pid').read_text(encoding='utf-8'))
            assert not psutil.pid_exists(pid), f'timed-out build child {pid} survived'
        finally:
            await host.close()

    asyncio.run(run())


def test_cancelled_build_and_host_close_clean_the_owned_tree(tmp_path):
    async def run():
        for mode in ('cancel', 'close'):
            manifest_path = tree_plugin(tmp_path / mode, 'sleep', timeout=60)
            data = tmp_path / mode / 'data'
            host = host_for(manifest_path, data)
            key = ('local-console', 'console')
            await host.start_build('local-console', 'console')
            pid = await async_child_pid(tmp_path / mode / 'plugin')
            if mode == 'cancel':
                task = host.builds._tasks[key]
                task.cancel()
                state = await host.builds.wait('local-console', 'console')
                assert state['status'] == 'failed'
                assert 'cancelled' in state['lastError']
                await host.close()
            else:
                await host.close()
                saved = json.loads((data / 'plugin-panels/local-console/console/state.json')
                                   .read_text(encoding='utf-8'))
                assert saved['status'] == 'failed'
                assert 'cancelled' in saved['lastError']
            assert not psutil.pid_exists(pid), f'owned build child {pid} survived {mode}'

    asyncio.run(run())


def test_rebuild_keeps_old_immutable_assets_for_open_pages_and_cold_load(tmp_path):
    plugin = tmp_path / 'plugin'
    manifest_path = write_plugin(plugin, version='1')
    data = tmp_path / 'data'

    async def run():
        host = host_for(manifest_path, data)
        try:
            await host.start_build('local-console', 'console')
            first = await host.builds.wait('local-console', 'console')
            host.set_panel_enabled('local-console', 'console', True)
            first_id = first['activeBuild']
            first_base = host.builds.asset_path('local-console', 'console', first_id)
            assert host.panels()[0]['assetPath'] == first_base
            assert b'v1' in host.panel_asset('local-console', 'console', first_id, 'assets/app.js').path.read_bytes()

            (plugin / 'version.txt').write_text('2', encoding='utf-8')
            await host.start_build('local-console', 'console')
            second = await host.builds.wait('local-console', 'console')
            assert second['status'] == 'built' and second['activeBuild'] != first_id
            second_base = host.builds.asset_path('local-console', 'console', second['activeBuild'])
            assert host.panels()[0]['assetPath'] == second_base
            # The already-open page keeps requesting its old immutable URL.
            assert b'v1' in host.panel_asset('local-console', 'console', first_id, 'assets/app.js').path.read_bytes()
            assert b'v2' in host.panel_asset('local-console', 'console', second['activeBuild'], 'assets/app.js').path.read_bytes()
        finally:
            await host.close()

        resumed = host_for(manifest_path, data)
        try:
            state = resumed.builds.status('local-console', 'console')
            assert state['activeBuild'] == second['activeBuild']
            assert resumed.panels()[0]['assetPath'] == second_base
            assert b'v1' in resumed.panel_asset('local-console', 'console', first_id, 'assets/app.js').path.read_bytes()
            assert b'v2' in resumed.panel_asset('local-console', 'console', second['activeBuild'], 'assets/app.js').path.read_bytes()
        finally:
            await resumed.close()

    asyncio.run(run())


def test_publish_state_failure_keeps_previous_build_and_pointer(tmp_path, monkeypatch):
    plugin = tmp_path / 'plugin'
    manifest_path = write_plugin(plugin, version='1')
    data = tmp_path / 'data'

    async def run():
        host = host_for(manifest_path, data)
        try:
            await host.start_build('local-console', 'console')
            first = await host.builds.wait('local-console', 'console')
            host.set_panel_enabled('local-console', 'console', True)
            first_id = first['activeBuild']
            real_save = host.builds._save

            def failing_save(state):
                if state['status'] == 'built' and state['activeBuild'] != first_id:
                    raise OSError('simulated disk failure')
                real_save(state)

            monkeypatch.setattr(host.builds, '_save', failing_save)
            (plugin / 'version.txt').write_text('2', encoding='utf-8')
            await host.start_build('local-console', 'console')
            failed = await host.builds.wait('local-console', 'console')
            assert failed['status'] == 'failed'
            assert 'publish' in failed['lastError']
            assert failed['activeBuild'] == first_id
            assert b'v1' in host.panel_asset('local-console', 'console', first_id, 'assets/app.js').path.read_bytes()
            builds = sorted(item.name for item in
                            (data / 'plugin-panels/local-console/console/builds').iterdir())
            assert builds == [first_id]
            monkeypatch.setattr(host.builds, '_save', real_save)
            assert host.builds.asset('local-console', 'console', first_id, '') is not None
        finally:
            await host.close()

    asyncio.run(run())


def _unconfirmed(_self):
    raise RuntimeError('simulated unconfirmed tree')


async def _pid_gone(pid: int, label: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert not psutil.pid_exists(pid), f'{label}: owned child {pid} survived'


async def _heartbeat():
    stats = {'ticks': 0, 'max_gap': 0.0}
    last = time.monotonic()

    async def beat():
        nonlocal last
        while True:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            stats['max_gap'] = max(stats['max_gap'], now - last)
            stats['ticks'] += 1
            last = now

    task = asyncio.create_task(beat())
    return task, stats


def test_abort_with_unconfirmed_tree_is_bounded_and_does_not_close_live_reader(tmp_path):
    plugin = tmp_path / 'plugin'
    tree_plugin(tmp_path, 'sleep', timeout=60)

    async def run():
        process = _BuildProcess([sys.executable, str(plugin / 'build.py')], plugin, lambda _text: None)
        process.spawn()
        pid = await async_child_pid(plugin)
        process._terminate_and_confirm = lambda: _unconfirmed(process)
        task, stats = await _heartbeat()
        try:
            result = await asyncio.wait_for(process.abort(), 15)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert 'not confirmed' in result
        assert process.reader_done.is_set(), 'blocked reader must be interrupted without closing it'
        assert stats['ticks'] > 20 and stats['max_gap'] < 0.5, stats
        process.dispose()
        await _pid_gone(pid, 'abort')
        assert not psutil.pid_exists(pid), 'owned child survived abort cleanup'

    asyncio.run(run())


def test_drain_reader_unconfirmed_cleanup_keeps_heartbeat(tmp_path):
    plugin = tmp_path / 'plugin'
    tree_plugin(tmp_path, 'exit')  # root exits; child keeps stdout open

    async def run():
        process = _BuildProcess([sys.executable, str(plugin / 'build.py')], plugin, lambda _text: None)
        process.spawn()
        pid = await async_child_pid(plugin)
        process._terminate_and_confirm = lambda: _unconfirmed(process)
        task, stats = await _heartbeat()
        try:
            result = await asyncio.wait_for(process._drain_reader(), 10)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert result and 'could not confirm' in result
        assert process.reader_done.is_set()
        assert stats['ticks'] > 20 and stats['max_gap'] < 0.5, stats
        process.dispose()
        await _pid_gone(pid, 'drain')
        assert not psutil.pid_exists(pid)

    asyncio.run(run())


def test_host_close_with_unconfirmed_tree_is_bounded_and_visible(tmp_path, monkeypatch):
    plugin = tmp_path / 'plugin'
    manifest_path = tree_plugin(tmp_path, 'sleep', timeout=60)
    data = tmp_path / 'data'
    monkeypatch.setattr(_BuildProcess, '_terminate_and_confirm', _unconfirmed)

    async def run():
        host = host_for(manifest_path, data)
        await host.start_build('local-console', 'console')
        pid = await async_child_pid(plugin)
        task, stats = await _heartbeat()
        try:
            await asyncio.wait_for(host.close(), 20)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        saved = json.loads((data / 'plugin-panels/local-console/console/state.json')
                           .read_text(encoding='utf-8'))
        assert saved['status'] == 'failed'
        assert saved['activeBuild'] is None
        assert 'cancelled' in saved['lastError'] and 'not confirmed' in saved['lastError']
        assert stats['ticks'] > 20 and stats['max_gap'] < 0.5, stats
        await _pid_gone(pid, 'host-close')
        assert not psutil.pid_exists(pid), 'owned child survived host close'

    asyncio.run(run())


def test_posix_group_cleanup_uses_saved_pgid_after_root_exit(monkeypatch):
    process = _BuildProcess(['unused'], Path('.'), lambda _text: None)
    process.proc = SimpleNamespace(pid=12345)
    process.pgid = 4242
    calls = []

    def killpg(pgid, sig):
        calls.append((pgid, sig))
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(os, 'killpg', killpg, raising=False)
    monkeypatch.setattr(os, 'getpgid', lambda _pid: (_ for _ in ()).throw(AssertionError('getpgid is not allowed')), raising=False)
    assert process._terminate_tree() == []
    assert calls and calls[0][0] == 4242, calls


def test_posix_group_cleanup_permission_and_unknown_are_not_success(monkeypatch):
    process = _BuildProcess(['unused'], Path('.'), lambda _text: None)
    process.proc = SimpleNamespace(pid=12345)
    process.pgid = 4243

    monkeypatch.setattr(os, 'killpg', lambda _pgid, _sig: (_ for _ in ()).throw(PermissionError('denied')), raising=False)
    with pytest.raises(RuntimeError, match='permission denied'):
        process._terminate_tree()
    with pytest.raises(RuntimeError, match='permission denied'):
        process._terminate_and_confirm()

    def unknown(_pgid, _sig):
        raise OSError('unknown')

    monkeypatch.setattr(os, 'killpg', unknown, raising=False)
    with pytest.raises(RuntimeError, match='could not terminate'):
        process._terminate_tree()


def test_close_during_spawn_reaps_late_owned_process(tmp_path, monkeypatch):
    plugin = tmp_path / 'plugin'
    manifest_path = tree_plugin(tmp_path, 'sleep', timeout=60)
    data = tmp_path / 'data'
    entered = threading.Event()
    gate = threading.Event()
    spawned = threading.Event()
    holder = {}
    original_spawn = _BuildProcess.spawn

    def gated_spawn(self):
        holder['process'] = self
        entered.set()
        assert gate.wait(10), 'test did not release the spawn gate'
        original_spawn(self)
        spawned.set()

    monkeypatch.setattr(_BuildProcess, 'spawn', gated_spawn)

    async def run():
        host = host_for(manifest_path, data)
        await host.start_build('local-console', 'console')
        assert await asyncio.to_thread(entered.wait, 5), 'spawn was not entered'
        started = time.monotonic()
        await asyncio.wait_for(host.close(), 10)
        close_elapsed = time.monotonic() - started
        assert close_elapsed < 8, f'close blocked on spawn: {close_elapsed:.1f}s'
        assert not spawned.is_set(), 'close returned only after spawn finished'

        gate.set()
        assert await asyncio.to_thread(spawned.wait, 10), 'spawn did not finish after release'
        process = holder['process']
        pid = process.proc.pid

        async def receipt():
            for _ in range(200):
                items = host.builds.spawn_receipts()
                if items:
                    return items[-1]
                await asyncio.sleep(0.05)
            return None

        item = await receipt()
        assert item and item['pid'] == pid and item['outcome'] == 'cleaned', item
        assert process.proc.poll() is not None, 'late process was not reaped'
        await _pid_gone(pid, 'late-spawn')
        saved = json.loads((data / 'plugin-panels/local-console/console/state.json')
                           .read_text(encoding='utf-8'))
        assert saved['status'] == 'failed' and saved['activeBuild'] is None
        assert 'cancelled' in saved['lastError']
        with pytest.raises(ValueError, match='closed'):
            await host.start_build('local-console', 'console')

    asyncio.run(run())

"""Local web-app panel contract, build lifecycle and asset serving.

Builds run a real child process in a temporary plugin directory; no installed
plugin, real Pan data directory or network is involved.
"""
import asyncio
import json
from pathlib import Path
import sys

import pytest

from packages.core.manifest_loader import load_manifests
from packages.plugins.catalog import Catalog
from packages.plugins.panel_builds import PanelNotReady
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
                host.panel_asset('local-console', 'console', '')
            host.set_panel_enabled('local-console', 'console', True)
            listing = host.panels()
            assert listing[0]['renderer'] == 'web-app/v1'
            assert listing[0]['assetPath'] == '/panel-assets/local-console/console/'
            assert listing[0]['path'] == '/plugins/local-console/console'

            entry = host.panel_asset('local-console', 'console', '')
            assert entry.media_type.startswith('text/html')
            assert b'v1' in entry.path.read_bytes()
            script = host.panel_asset('local-console', 'console', 'assets/app.js')
            assert script.media_type.startswith('text/javascript')
            spa = host.panel_asset('local-console', 'console', 'deep/route')
            assert spa.path == entry.path
            assert host.panel_asset('local-console', 'console', 'missing.js') is None
            assert host.panel_asset('local-console', 'console', '.pan-panel-build.json') is None
            for bad in ('../state.json', 'assets/../../state.json', 'C:/Windows/win.ini', '%2e%2e/x'):
                with pytest.raises(ValueError):
                    host.panel_asset('local-console', 'console', bad)

            # A slow build makes a duplicate request attach to the running one.
            (tmp_path / 'plugin' / 'delay.txt').write_text('0.6', encoding='utf-8')
            first = await host.start_build('local-console', 'console')
            second = await host.start_build('local-console', 'console')
            assert first['status'] == second['status'] == 'building'
            with pytest.raises(ValueError, match='while a build is running'):
                host.unbuild_panel('local-console', 'console')
            done = await host.builds.wait('local-console', 'console')
            assert done['status'] == 'built'
            assert len(list((data / 'plugin-panels/local-console/console/builds').iterdir())) == 1

            # A failing rebuild keeps the previous artifact usable.
            (tmp_path / 'plugin' / 'version.txt').write_text('2', encoding='utf-8')
            (tmp_path / 'plugin' / 'fail.flag').write_text('', encoding='utf-8')
            await host.start_build('local-console', 'console')
            failed = await host.builds.wait('local-console', 'console')
            assert failed['status'] == 'failed' and failed['available']
            assert 'code 3' in failed['lastError']
            assert 'intentional build failure' in failed['log']
            assert b'v1' in host.panel_asset('local-console', 'console', 'assets/app.js').path.read_bytes()
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
                navigation = (await client.get('/api/plugins/panels')).json()['panels']
                assert navigation == [{'id': 'console', 'title': 'Local Console',
                                       'renderer': 'web-app/v1', 'entry': 'index.html',
                                       'pluginId': 'local-console', 'path': '/plugins/local-console/console',
                                       'assetPath': '/panel-assets/local-console/console/'}]
                page = await client.get('/panel-assets/local-console/console/')
                assert page.status_code == 200 and page.headers['content-type'].startswith('text/html')
                script = await client.get('/panel-assets/local-console/console/assets/app.js')
                assert script.status_code == 200 and script.headers['content-type'].startswith('text/javascript')
                traversal = await client.get('/panel-assets/local-console/console/%2e%2e/%2e%2e/state.json')
                assert traversal.status_code != 200 or b'panelId' not in traversal.content
                assert (await client.post('/api/plugin-panels/local-console/console/disable')).status_code == 200
                assert (await client.get('/panel-assets/local-console/console/')).status_code == 404
                assert (await client.post('/api/plugin-panels/local-console/console/unbuild')).status_code == 200
                assert (await client.get('/api/plugin-panels/local-console/console')).json()['status'] == 'unbuilt'
        finally:
            server.app.state.plugin_panels = None
            await host.close()

    asyncio.run(run())

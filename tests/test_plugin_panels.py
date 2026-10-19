"""No installed cc-switch command or real account/database is used by these tests."""
import asyncio
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.cc_switch_bridge.provider import Bridge, normalize_quota
from packages.core.manifest_loader import load_manifests, REPO_ROOT
from packages.plugins.catalog import Catalog
from packages.plugins.data import plugin_data_dir
from packages.plugins.panels import PanelHost
from packages.plugins.runtime import Runtime


@pytest.fixture
def manifest():
    value = load_manifests(['manifest.json', 'packages/mcp/manifest.json'])
    helper = next(s for s in value.mcp_servers if s.name == 'cc-switch-bridge-helper')
    helper.command = sys.executable
    helper.args = [str(REPO_ROOT / 'tests/fixtures/cc_switch_bridge_provider.py')]
    helper.env = {'PYTHONPATH': str(REPO_ROOT)}
    helper.cwd = str(REPO_ROOT)
    return value


def test_bridge_is_distinct_plugin_and_helper_is_hidden(manifest):
    catalog = Catalog(manifest)
    assert 'cc-switch-bridge' in catalog.plugins
    assert 'cc-switch-bridge-helper' not in catalog.plugins
    plugin = catalog.get('cc-switch-bridge')
    assert plugin.agent_capabilities == ('cc-switch-bridge.switch', 'cc-switch-bridge.quota')
    assert plugin.panels[0]['renderer'] == 'account-switcher/v1'
    assert len(plugin.panels[0]['actions']) == 4


def test_owned_data_paths_reject_escape_and_owner_symlink(tmp_path):
    for name in ('../other', 'a/b', '.', 'UPPER'):
        with pytest.raises(ValueError):
            plugin_data_dir(tmp_path, name)
    assert plugin_data_dir(tmp_path, 'cc-switch-bridge').parent == tmp_path / 'plugins'
    with pytest.raises(ValueError, match='version/owner mismatch'):
        plugin_data_dir(tmp_path, 'cc-switch-bridge', 2)


def test_invalid_panel_declarations_fail_before_execution(manifest):
    for change in ('namespace', 'duplicate', 'export', 'effect'):
        candidate = copy.deepcopy(manifest)
        plugin = next(p for p in candidate.plugins if p['id'] == 'cc-switch-bridge')
        if change == 'namespace':
            plugin['panels'][0]['actions']['list']['capability'] = 'qq.send_message'
        elif change == 'duplicate':
            plugin['panels'].append(copy.deepcopy(plugin['panels'][0]))
        elif change == 'export':
            plugin['agent_capabilities'] = ['cc-switch-bridge.does-not-exist']
        else:
            plugin['panels'][0]['actions']['switch']['effect'] = 'read'
        with pytest.raises(ValueError):
            Catalog(candidate)


@pytest.mark.parametrize('mode,view', [('standalone', 'standalone'), ('pan_native', 'native'), ('gateway', 'gateway')])
def test_real_stdio_bridge_exports_two_agent_tools_and_keeps_panel_internal(manifest, tmp_path, mode, view):
    async def run():
        session = SimpleNamespace(id='ses_fixture', name='fixture', adapter_config={'plugin_bindings': [
            {'plugin_id': 'cc-switch-bridge', 'enabled': True, 'mode': mode}]})
        runtime = Runtime(lambda: manifest, lambda _: session, data_root=tmp_path)
        try:
            names = {d['name'] for d in await runtime.directory(session.id, view)}
            assert names == {'cc-switch-bridge.switch', 'cc-switch-bridge.quota'}
            missing = await runtime.invoke(session.id, view, 'cc-switch-bridge.list', {}, plugin_id='cc-switch-bridge')
            assert missing['isError']
            name = 'pan_cc_switch_bridge_quota' if view == 'native' else 'cc-switch-bridge.quota'
            listed = await runtime.invoke(session.id, view, name, {'app': 'codex'}, plugin_id='cc-switch-bridge')
            assert listed['structuredContent']['accounts'][0]['login'] == 'alice@example.test'
        finally:
            await runtime.close()
    asyncio.run(run())


def test_panel_stdio_list_quota_fake_switch_and_durable_duplicate_suppression(manifest, tmp_path):
    async def run():
        host = PanelHost(lambda: manifest, tmp_path)
        args = {'app': 'codex', 'id': 'p-b', 'expectedCurrentId': 'p-a'}
        request_id = 'fixture-request-0001'
        try:
            assert host.panels()[0]['path'] == '/plugins/cc-switch-bridge/accounts'
            listed = await host.invoke('cc-switch-bridge', 'accounts', 'list', {'app': 'codex'})
            assert 'never-expose' not in json.dumps(listed)
            assert listed['structuredContent']['providers'][0]['origin'] == 'https://api.example.test'
            quota = await host.invoke('cc-switch-bridge', 'accounts', 'quota', {'app': 'codex', 'id': 'p-a'})
            assert not quota.get('isError'), quota
            assert quota['structuredContent']['quota']['tiers'][0]['usedPercent'] == 70
            one, two = await asyncio.gather(*[host.invoke('cc-switch-bridge', 'accounts', 'switch', args, request_id) for _ in range(2)])
            assert one == two
            assert one['structuredContent']['currentProviderId'] == 'p-b'
            state = tmp_path / 'plugins/cc-switch-bridge/fixture-state.json'
            assert json.loads(state.read_text())['switches'] == 1
            with pytest.raises(ValueError, match='already used'):
                await host.invoke('cc-switch-bridge', 'accounts', 'switch', dict(args, id='p-a'), request_id)
            stale = await host.invoke('cc-switch-bridge', 'accounts', 'switch', args, 'fixture-request-0002')
            assert stale['isError']
            # A business rejection leaves the helper usable for a fresh read.
            assert not (await host.invoke('cc-switch-bridge', 'accounts', 'list', {'app': 'codex'})).get('isError')
            account = await host.invoke('cc-switch-bridge', 'accounts', 'default_account',
                {'app': 'codex', 'id': 'acc-b', 'expectedCurrentId': 'acc-a'}, 'fixture-request-0003')
            assert account['structuredContent']['accounts'][1]['isDefault']
        finally:
            await host.close()
        resumed = PanelHost(lambda: manifest, tmp_path)
        try:
            await resumed.invoke('cc-switch-bridge', 'accounts', 'switch', args, request_id)
            assert not resumed.runtime.providers
            journal = tmp_path / 'plugins/cc-switch-bridge/panel-requests.json'
            record = json.loads(journal.read_text(encoding='utf-8'))
            record['fixture-request-unknown'] = {'signature': next(iter(record.values()))['signature']}
            journal.write_text(json.dumps(record))
            result = await resumed.invoke('cc-switch-bridge', 'accounts', 'switch', args, 'fixture-request-unknown')
            assert result['isError'] and not resumed.runtime.providers
        finally:
            await resumed.close()
    asyncio.run(run())


def test_cancelled_panel_caller_does_not_replay_admitted_mutation(manifest, tmp_path):
    async def run():
        host = PanelHost(lambda: manifest, tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def execute(message):
            calls.append(message)
            entered.set()
            await release.wait()
            return {'structuredContent': {'ok': True}, 'content': []}
        host.runtime.handle = execute
        args = {'app': 'codex', 'id': 'p-b', 'expectedCurrentId': 'p-a'}
        task = asyncio.create_task(host.invoke('cc-switch-bridge', 'accounts', 'switch', args, 'fixture-cancel-0001'))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await host.close()
        assert len(calls) == 1
        record = json.loads((tmp_path / 'plugins/cc-switch-bridge/panel-requests.json').read_text())
        assert record['fixture-cancel-0001']['result']['structuredContent']['ok']
    asyncio.run(run())


def test_cli_availability_codes_are_stable_and_do_not_hide_success(tmp_path, monkeypatch):
    from packages.cc_switch_bridge import provider
    monkeypatch.delenv('PAN_CC_SWITCH_CLI', raising=False)
    monkeypatch.setattr(provider.shutil, 'which', lambda _: None)
    with pytest.raises(provider.CliError) as missing:
        Bridge(tmp_path)._run(['--version'])
    assert missing.value.code == 'cli_not_found'
    assert json.loads(provider.error_text(missing.value))['error']['code'] == 'cli_not_found'

    monkeypatch.setenv('PAN_CC_SWITCH_CLI', 'retired-fixture.exe')
    monkeypatch.setattr(provider.subprocess, 'run', Mock(return_value=SimpleNamespace(
        returncode=0, stdout='cc-switch 5.10.5', stderr='')))
    with pytest.raises(provider.CliError) as incompatible:
        Bridge(tmp_path)._run(['--version'])
    assert incompatible.value.code == 'cli_incompatible'

    monkeypatch.setattr(provider.subprocess, 'run', Mock(side_effect=OSError('not runnable')))
    with pytest.raises(provider.CliError) as unavailable:
        Bridge(tmp_path)._run(['--version'])
    assert unavailable.value.code == 'cli_unavailable'

    monkeypatch.setattr(provider.subprocess, 'run', Mock(return_value=SimpleNamespace(
        returncode=1, stdout='secret-token', stderr='secret-token')))
    with pytest.raises(provider.CliError) as legacy:
        Bridge(tmp_path)._run(['--version'])
    assert legacy.value.code == 'cli_incompatible'
    with pytest.raises(provider.CliError) as failed:
        Bridge(tmp_path)._call_cli('fixture-only.exe', ['--app', 'codex', 'provider', 'list', '--json'])
    assert failed.value.code == 'cli_failed'
    assert 'secret' not in provider.error_text(failed.value)

    monkeypatch.setattr(provider.subprocess, 'run', Mock(side_effect=[
        SimpleNamespace(returncode=0, stdout=json.dumps({'contract': 'pan-cc-switch-gui/v1'}), stderr=''),
        SimpleNamespace(returncode=0, stdout=json.dumps({'providers': []}), stderr='')]))
    assert Bridge(tmp_path)._run(['--app', 'codex', 'provider', 'list', '--json']) == {'providers': []}


def test_readmes_index_the_external_compatible_cli_fork():
    for name in ('README.md', 'README.en.md'):
        text = (REPO_ROOT / name).read_text(encoding='utf-8')
        assert 'https://github.com/AblazeGHR/cc-switch-pan' in text
        assert 'cc-switch-bridge' in text
        assert ('不捆绑' in text) or ('does not bundle' in text)


def test_cli_errors_and_quota_normalization_do_not_expose_credentials(tmp_path, monkeypatch):
    from packages.cc_switch_bridge import provider
    runner = Mock(side_effect=[
        SimpleNamespace(returncode=0, stdout=json.dumps({'contract': 'pan-cc-switch-gui/v1'}), stderr=''),
        SimpleNamespace(returncode=1, stdout='secret-token', stderr='secret-token')])
    monkeypatch.setenv('PAN_CC_SWITCH_CLI', 'fixture-only.exe')
    monkeypatch.setattr(provider.subprocess, 'run', runner)
    with pytest.raises(ValueError, match='退出码') as failure:
        Bridge(tmp_path)._run(['--app', 'codex', 'provider', 'list', '--json'])
    assert 'secret' not in str(failure.value)
    assert runner.call_args.args[0] == ['fixture-only.exe', '--app', 'codex', 'provider', 'list', '--json']
    assert runner.call_args_list[0].args[0] == ['fixture-only.exe', '--version']
    result = normalize_quota({'status': 'query_failed', 'error': 'secret-token', 'result': None})
    assert 'secret' not in json.dumps(result)
    assert result['tiers'] == [] and not result['available']
    import time
    bridge = Bridge(tmp_path)
    bridge._deadline = time.monotonic() - 1
    runner.reset_mock()
    with pytest.raises(ValueError, match='预算'):
        bridge._run(['auth', 'status', '--json'])
    runner.assert_not_called()


@pytest.mark.parametrize('version', ['cc-switch 5.10.5', '{"contract":"legacy"}'])
def test_legacy_cli_never_reaches_account_or_provider_operations(tmp_path, monkeypatch, version):
    from packages.cc_switch_bridge import provider
    monkeypatch.setenv('PAN_CC_SWITCH_CLI', 'retired-fixture.exe')
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=version, stderr=''))
    monkeypatch.setattr(provider.subprocess, 'run', runner)
    with pytest.raises(ValueError, match='GUI Fork'):
        Bridge(tmp_path)._run(['auth', 'default', 'fixture-account'], False)
    runner.assert_called_once()
    assert runner.call_args.args[0] == ['retired-fixture.exe', '--version']


def test_account_reauthentication_status_reaches_the_panel_without_credentials(tmp_path):
    def runner(args, json_output=True):
        if args[:2] == ['auth', 'status']:
            return {'accounts': [{'id': 'fixture-a', 'login': 'alice@example.test',
                                  'reauth_required': True, 'refresh_token': 'never-expose'}]}
        return {'providers': []}
    result = Bridge(tmp_path, runner=runner).listing('codex')
    assert result['accounts'][0]['reauthRequired'] is True
    assert 'never-expose' not in json.dumps(result)


def test_bridge_helpers_share_a_lock_without_invoking_the_cli(tmp_path):
    import time
    first, second = Bridge(tmp_path), Bridge(tmp_path)
    first._deadline = time.monotonic() + 1
    with first._lock():
        second._deadline = time.monotonic() + .02
        with pytest.raises(ValueError, match='其他 bridge'):
            with second._lock():
                raise AssertionError('Conflicting helper was admitted')
    second._deadline = time.monotonic() + 1
    with second._lock():
        pass


def test_panel_http_routes_use_declared_actions_without_starting_a_worker(manifest, tmp_path, monkeypatch):
    import httpx
    from packages.web import server
    async def run():
        host = PanelHost(lambda: manifest, tmp_path)
        monkeypatch.setattr(server.app.state, 'plugin_panels', host, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1') as client:
            response = await client.get('/api/plugins/panels')
            assert response.status_code == 200
            assert response.json()['panels'][0]['pluginId'] == 'cc-switch-bridge'
            assert not host.runtime.providers
            route = '/api/plugins/cc-switch-bridge/panels/accounts/actions/'
            response = await client.post(route + 'list', json={'arguments': {'app': 'codex'}})
            assert response.status_code == 200 and response.json()['structuredContent']['currentProviderId'] == 'p-a'
            assert (await client.post(route + 'delete', json={'arguments': {}})).status_code == 400
            assert (await client.post(route + 'switch', json={'arguments': {}})).status_code == 400
            assert (await client.post(route + 'list', json={'arguments': {}, 'command': 'not-allowed'})).status_code == 400
            manifest.plugins[:] = [p for p in manifest.plugins if p['id'] != 'cc-switch-bridge']
            assert (await client.get('/api/plugins/panels')).json()['panels'] == []
            assert (await client.post(route + 'list', json={'arguments': {'app': 'codex'}})).status_code == 400
        await host.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['standalone', 'pan_native'])
def test_panels_work_with_plugins_restricted_to_one_agent_mode(manifest, tmp_path, mode):
    plugin = next(p for p in manifest.plugins if p['id'] == 'cc-switch-bridge')
    plugin['exposure'] = {'supported_modes': [mode], 'default_mode': mode}
    async def run():
        host = PanelHost(lambda: manifest, tmp_path)
        try:
            result = await host.invoke('cc-switch-bridge', 'accounts', 'list', {'app': 'codex'})
            assert result['structuredContent']['currentProviderId'] == 'p-a'
        finally:
            await host.close()
    asyncio.run(run())


def test_native_agent_visibility_change_requests_restart(manifest, tmp_path):
    async def run():
        from unittest.mock import AsyncMock
        notify = AsyncMock()
        session = SimpleNamespace(id='ses_visibility', name='fixture', adapter_config={'plugin_bindings': [
            {'plugin_id': 'cc-switch-bridge', 'enabled': True, 'mode': 'pan_native'}]})
        runtime = Runtime(lambda: manifest, lambda _: session, notify, data_root=tmp_path)
        try:
            assert len(await runtime.directory(session.id, 'native')) == 2
            plugin = next(p for p in manifest.plugins if p['id'] == 'cc-switch-bridge')
            plugin['agent_capabilities'] = ['cc-switch-bridge.quota']
            assert len(await runtime.directory(session.id, 'native')) == 1
            notify.assert_awaited_once_with(session.id)
        finally:
            await runtime.close()
    asyncio.run(run())

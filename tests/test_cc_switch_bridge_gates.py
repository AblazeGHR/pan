"""Non-manual gate: the only distinction is manual (Panel) vs non-manual.

No installed cc-switch command, real account or CLI database is used.
"""
import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.cc_switch_bridge.provider import Bridge, BridgeError, ProviderSettings
from packages.core.manifest_loader import REPO_ROOT, load_manifests
from packages.plugins.panels import PanelHost
from packages.plugins.runtime import Runtime


class FakeCli:
    """Thread-safe in-memory stand-in for the compatible GUI-fork CLI."""

    def __init__(self, provider_ids=None):
        self.ids = provider_ids or ['p-a', 'p-b', 'p-c']
        self.current = self.ids[0]
        self.switches = 0
        self.queries = []
        self.lock = threading.Lock()

    def __call__(self, args, json_output=True):
        with self.lock:
            if args[:2] == ['auth', 'status']:
                return {'default_account_id': 'acc-a', 'accounts': [
                    {'id': 'acc-a', 'login': 'alice@example.test'},
                    {'id': 'acc-b', 'login': 'bob@example.test'}]}
            if args[:2] == ['auth', 'default']:
                return None
            if args[3] == 'list':
                details = {'p-a': ('Main Profile', 'primary note', 'official', 'https://api.example.test/v1'),
                           'p-b': ('Backup Profile', 'backup note', 'third-party', 'https://backup.example.test/v1'),
                           'p-c': ('Spare Profile', 'spare note', '', None)}
                providers = []
                for provider_id in self.ids:
                    name, notes, category, api_url = details.get(provider_id, (provider_id, '', '', None))
                    row = {'id': provider_id, 'name': name, 'notes': notes, 'category': category}
                    if api_url:
                        row['apiUrl'] = api_url
                    providers.append(row)
                return {'currentProviderId': self.current, 'providers': providers}
            if args[3] == 'switch':
                self.current = args[4]
                self.switches += 1
                return None
            if args[3] == 'quota':
                self.queries.append(args[4])
                return {'status': 'ok', 'available': True, 'queriedAt': 1792254007000,
                        'result': {'type': 'subscription', 'quota': {'tiers': [
                            {'name': '5 小时', 'utilization': 70, 'resetsAt': '2026-10-19T12:00:00Z'}]}}}
            raise AssertionError(f'Unexpected command: {args}')


def bridge_at(tmp_path, cli):
    bridge = Bridge(tmp_path, runner=cli)
    return bridge


def config(bridge, provider_id, disabled, revision, app='codex'):
    return bridge.invoke('panel_config', {'app': app, 'id': provider_id,
        'nonManualDisabled': disabled, 'expectedRevision': revision})


def test_single_gate_roundtrip_keeps_revision_and_other_profiles(tmp_path):
    cli = FakeCli()
    bridge = bridge_at(tmp_path, cli)
    first = config(bridge, 'p-b', True, 0)
    assert first['revision'] == 1 and first['updatedAt'] > 0
    assert {p['id']: p['nonManualDisabled'] for p in first['providers']} == {
        'p-a': False, 'p-b': True, 'p-c': False}
    stored = json.loads((tmp_path / 'provider-flags.json').read_text(encoding='utf-8'))
    assert stored['apps']['codex']['revision'] == 1
    assert stored['apps']['codex']['providers'] == {'p-b': {'nonManualDisabled': True}}

    # A second write touches only its own profile and advances the revision once.
    second = config(bridge, 'p-c', True, 1)
    assert second['revision'] == 2
    gates = {p['id']: p['nonManualDisabled'] for p in second['providers']}
    assert gates == {'p-a': False, 'p-b': True, 'p-c': True}
    # Re-enabling one profile does not disturb the other gate.
    third = config(bridge, 'p-b', False, 2)
    gates = {p['id']: p['nonManualDisabled'] for p in third['providers']}
    assert gates == {'p-a': False, 'p-b': False, 'p-c': True}
    assert ProviderSettings(tmp_path).gates('codex')['p-c'] is True


def test_stale_revision_is_rejected_and_file_is_unchanged(tmp_path):
    bridge = bridge_at(tmp_path, FakeCli())
    config(bridge, 'p-b', True, 0)
    before = (tmp_path / 'provider-flags.json').read_text(encoding='utf-8')
    with pytest.raises(BridgeError) as conflict:
        config(bridge, 'p-c', True, 0)
    assert conflict.value.code == 'settings_conflict'
    assert (tmp_path / 'provider-flags.json').read_text(encoding='utf-8') == before


def test_concurrent_writes_stay_atomic_and_lose_no_update(tmp_path):
    ids = [f'p-{index}' for index in range(8)]
    bridge = bridge_at(tmp_path, FakeCli(ids))
    errors = []

    def write(provider_id):
        for _ in range(40):
            revision = ProviderSettings(tmp_path).app('codex')['revision']
            try:
                config(bridge, provider_id, True, revision)
                return
            except BridgeError as exc:
                if exc.code != 'settings_conflict':
                    errors.append(exc)
                    return
        errors.append(AssertionError(f'{provider_id} never won its revision'))

    threads = [threading.Thread(target=write, args=(provider_id,)) for provider_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    stored = json.loads((tmp_path / 'provider-flags.json').read_text(encoding='utf-8'))
    assert set(stored['apps']['codex']['providers']) == set(ids)
    assert stored['apps']['codex']['revision'] == len(ids)
    assert list(tmp_path.glob('provider-flags.*.tmp')) == []


def test_agent_views_hide_gated_profiles_completely(tmp_path):
    cli = FakeCli()
    bridge = bridge_at(tmp_path, cli)
    config(bridge, 'p-b', True, 0)

    agent_list = bridge.invoke('list', {'app': 'codex'})
    assert [p['id'] for p in agent_list['providers']] == ['p-a', 'p-c']
    dumped = json.dumps(agent_list, ensure_ascii=False)
    for leaked in ('p-b', 'Backup Profile', 'backup.example.test', 'backup note', 'third-party'):
        assert leaked not in dumped
    assert all('nonManualDisabled' not in p for p in agent_list['providers'])
    assert 'revision' not in agent_list

    # The id-only read is refused exactly like a missing profile.
    with pytest.raises(ValueError, match='配置已不存在') as refused:
        bridge.invoke('quota', {'app': 'codex', 'id': 'p-b'})
    assert 'Backup' not in str(refused.value)
    assert cli.queries == []

    panel = bridge.invoke('panel_list', {'app': 'codex'})
    assert [p['id'] for p in panel['providers']] == ['p-a', 'p-b', 'p-c']
    assert panel['providers'][1]['name'] == 'Backup Profile'
    assert panel['providers'][1]['nonManualDisabled'] is True
    assert panel['revision'] == 1
    # The Panel may still read the hidden profile's quota explicitly.
    assert bridge.invoke('panel_quota', {'app': 'codex', 'id': 'p-b'})['quota']['available'] is True


def test_non_manual_switch_refuses_gate_without_fallback_and_never_replays(tmp_path):
    cli = FakeCli()
    bridge = bridge_at(tmp_path, cli)
    config(bridge, 'p-b', True, 0)
    assert cli.current == 'p-a'

    for operation in ('switch', 'auto_switch'):
        with pytest.raises(BridgeError) as refused:
            bridge.invoke(operation, {'app': 'codex', 'id': 'p-b', 'expectedCurrentId': 'p-a'})
        assert refused.value.code == 'non_manual_disabled'
        # No fallback: neither the current profile nor any other one changed.
        assert cli.current == 'p-a' and cli.switches == 0

    # The ungated profile is admitted through both non-manual entry points.
    assert bridge.invoke('auto_switch', {'app': 'codex', 'id': 'p-c',
        'expectedCurrentId': 'p-a'})['currentProviderId'] == 'p-c'
    assert cli.switches == 1
    assert bridge.invoke('switch', {'app': 'codex', 'id': 'p-a',
        'expectedCurrentId': 'p-c'})['currentProviderId'] == 'p-a'


def test_manual_panel_switch_may_target_a_gated_profile(tmp_path):
    cli = FakeCli()
    bridge = bridge_at(tmp_path, cli)
    config(bridge, 'p-b', True, 0)
    updated = bridge.invoke('panel_switch', {'app': 'codex', 'id': 'p-b', 'expectedCurrentId': 'p-a'})
    assert updated['currentProviderId'] == 'p-b'
    assert cli.current == 'p-b' and cli.switches == 1
    # The agent view stays consistent and still hides the active profile's row.
    agent = bridge.invoke('list', {'app': 'codex'})
    assert [p['id'] for p in agent['providers']] == ['p-a', 'p-c']


@pytest.fixture
def manifest():
    value = load_manifests(['manifest.json', 'packages/mcp/manifest.json'])
    helper = next(s for s in value.mcp_servers if s.name == 'cc-switch-bridge-helper')
    helper.command = sys.executable
    helper.args = [str(REPO_ROOT / 'tests/fixtures/cc_switch_bridge_provider.py')]
    helper.env = {'PYTHONPATH': str(REPO_ROOT)}
    helper.cwd = str(REPO_ROOT)
    return value


def test_panel_config_gates_the_agent_projection_end_to_end(manifest, tmp_path):
    async def run():
        host = PanelHost(lambda: manifest, tmp_path)
        try:
            listed = await host.invoke('cc-switch-bridge', 'accounts', 'list', {'app': 'codex'})
            revision = listed['structuredContent']['revision']
            saved = await host.invoke('cc-switch-bridge', 'accounts', 'config',
                {'app': 'codex', 'id': 'p-b', 'nonManualDisabled': True, 'expectedRevision': revision},
                'fixture-gate-request-0001')
            assert saved['structuredContent']['revision'] == revision + 1
            assert [p['id'] for p in saved['structuredContent']['providers']] == ['p-a', 'p-b']

            session = SimpleNamespace(id='ses_gate', name='fixture', adapter_config={'plugin_bindings': [
                {'plugin_id': 'cc-switch-bridge', 'enabled': True, 'mode': 'gateway'}]})
            runtime = Runtime(lambda: manifest, lambda _: session, data_root=tmp_path)
            try:
                visible = await runtime.invoke(session.id, 'gateway', 'cc-switch-bridge.list',
                                               {'app': 'codex'}, plugin_id='cc-switch-bridge')
                assert [p['id'] for p in visible['structuredContent']['providers']] == ['p-a']
                hidden_quota = await runtime.invoke(session.id, 'gateway', 'cc-switch-bridge.quota',
                                                    {'app': 'codex', 'id': 'p-b'}, plugin_id='cc-switch-bridge')
                assert hidden_quota['isError']
                assert 'OpenAI Secondary' not in json.dumps(hidden_quota)
                refused = await runtime.invoke(session.id, 'gateway', 'cc-switch-bridge.auto_switch',
                    {'app': 'codex', 'id': 'p-b', 'expectedCurrentId': 'p-a'}, plugin_id='cc-switch-bridge')
                assert refused['isError']
            finally:
                await runtime.close()

            conflict = await host.invoke('cc-switch-bridge', 'accounts', 'config',
                {'app': 'codex', 'id': 'p-a', 'nonManualDisabled': True, 'expectedRevision': revision},
                'fixture-gate-request-0002')
            assert conflict['isError']
            assert 'settings_conflict' in conflict['content'][0]['text']
        finally:
            await host.close()
    asyncio.run(run())

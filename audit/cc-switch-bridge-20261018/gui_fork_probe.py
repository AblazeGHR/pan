"""Real GUI-Fork executable through Pan's real Panel and MCP providers, fake home only.

Only SQL schema is read from the production database. No production row is copied.
Run after building the independent Fork: python <this file> <console exe>.
"""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core.manifest_loader import load_manifests
from packages.plugins.panels import PanelHost
from packages.plugins.runtime import Runtime
from packages.web import server as web

CASE = Path(__file__).resolve().parent / (sys.argv[2] if len(sys.argv) > 2 else 'gui-fork-native')
assert CASE.resolve().parent == Path(__file__).resolve().parent
PRIVATE = CASE / 'private'
HOME = PRIVATE / 'home'
DATA = HOME / '.cc-switch'
DATA.mkdir(parents=True, exist_ok=True)
EXE = Path(sys.argv[1]).resolve()
assert EXE.is_file()
store = DATA / 'codex_oauth_auth.json'


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def seed():
    # The real v19 schema, with no real accounts, settings or provider values.
    original = Path.home() / '.cc-switch/cc-switch.db'
    with sqlite3.connect(original.as_uri() + '?mode=ro', uri=True) as source:
        statements = source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
    fixture_db = DATA / 'cc-switch.db'
    if fixture_db.exists():
        raise RuntimeError('Use a fresh private fixture directory; do not reuse a switched fixture')
    with sqlite3.connect(fixture_db) as target:
        for name, statement in statements:
            if not target.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone():
                target.execute(statement)
        target.execute('PRAGMA user_version=19')
        for index, label in enumerate(('a', 'b')):
            config = {'auth': {'OPENAI_API_KEY': 'fixture-key-' + label},
                      'config': 'model_provider = "fixture"\nmodel = "fixture-model"\n[model_providers.fixture]\nname = "Fixture"\nbase_url = "https://fixture.invalid/v1"\nwire_api = "responses"\n'}
            target.execute('INSERT INTO providers (id,app_type,name,settings_config,is_current,sort_index,notes) VALUES (?,?,?,?,?,?,?)',
                           ('fixture-' + label, 'codex', 'Fixture ' + label.upper(), json.dumps(config), int(index == 0), index, 'Isolated executable probe'))
    token = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    accounts = {name: {'account_id': name, 'email': name + '@example.test', 'refresh_token': 'fixture-refresh-' + name,
                       'authenticated_at': 1, 'chatgpt_account_id': 'fixture-workspace-' + name,
                       'id_token': token({'alg': 'none'}) + '.' + token({'sub': name}) + '.',
                       'token_updated_at_ms': 1000} for name in ('fixture-account-a', 'fixture-account-b')}
    store.write_text(json.dumps({'version': 2, 'accounts': accounts, 'default_account_id': 'fixture-account-a'}), encoding='utf-8')
    (DATA / 'settings.json').write_text('{}', encoding='utf-8')
    return accounts


async def main():
    original_accounts = seed()
    production_files = [Path.home() / name for name in ('.cc-switch/codex_oauth_auth.json', '.cc-switch/settings.json', '.codex/auth.json', '.codex/config.toml')]
    before = {str(path): fingerprint(path) for path in production_files}
    manifest = load_manifests(['manifest.json', 'packages/mcp/manifest.json'])
    helper = next(server for server in manifest.mcp_servers if server.name == 'cc-switch-bridge-helper')
    helper.env = {**(helper.env or {}), 'PAN_CC_SWITCH_CLI': str(EXE),
                  'CC_SWITCH_TEST_HOME': str(HOME), 'CC_SWITCH_CONFIG_DIR': str(DATA),
                  'CODEX_HOME': str(HOME / '.codex'), 'PYTHONPATH': str(ROOT)}
    helper.cwd = str(ROOT)
    host = PanelHost(lambda: manifest, PRIVATE / 'pan-data')
    web.app.state.plugin_panels = host
    async def panel(action, arguments, request_id=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url='http://fixture.local') as client:
            body = {'arguments': arguments}
            if request_id:
                body['requestId'] = request_id
            response = await client.post('/api/plugins/cc-switch-bridge/panels/accounts/actions/' + action, json=body)
            assert response.status_code == 200, response.status_code
            return response.json()
    try:
        listed = await panel('list', {'app': 'codex'})
        assert not listed.get('isError'), listed
        assert listed['structuredContent']['currentProviderId'] == 'fixture-a'
        with sqlite3.connect(DATA / 'cc-switch.db') as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 19, 'Listing unexpectedly migrated the database'
        quota = await panel('quota', {'app': 'codex', 'id': 'fixture-a'})
        assert not quota.get('isError'), quota
        assert quota['structuredContent']['quota']['status'] == 'not_available'
        switched = await panel('switch',
            {'app': 'codex', 'id': 'fixture-b', 'expectedCurrentId': 'fixture-a'}, 'fixture-gui-provider-0001')
        assert not switched.get('isError'), switched
        assert switched['structuredContent']['currentProviderId'] == 'fixture-b'
        selected = await panel('default_account',
            {'app': 'codex', 'id': 'fixture-account-b', 'expectedCurrentId': 'fixture-account-a'}, 'fixture-gui-account-0001')
        assert not selected.get('isError'), selected
        assert any(account['isDefault'] and account['id'] == 'fixture-account-b' for account in selected['structuredContent']['accounts'])
        after = json.loads(store.read_text(encoding='utf-8'))
        assert after['version'] == 2 and after['accounts'] == original_accounts
        native_config = (HOME / '.codex/config.toml').read_text(encoding='utf-8')
        # Official v4 preserves the ChatGPT login and puts third-party keys in
        # the provider table, instead of overwriting native auth.json.
        assert 'experimental_bearer_token = "fixture-key-b"' in native_config
        assert 'requires_openai_auth = false' in native_config
        assert not (HOME / '.codex/auth.json').exists()
    finally:
        await host.close()
        web.app.state.plugin_panels = None
    public_counts = {}
    for mode, view in [('standalone', 'standalone'), ('pan_native', 'native'), ('gateway', 'gateway')]:
        session = SimpleNamespace(id='fixture-' + mode, name='fixture', adapter_config={
            'plugin_bindings': [{'plugin_id': 'cc-switch-bridge', 'enabled': True, 'mode': mode}]})
        runtime = Runtime(lambda: manifest, lambda _: session, data_root=PRIVATE / ('mcp-' + mode))
        try:
            directory = await runtime.directory(session.id, view)
            assert len(directory) == 2, directory
            quota_name = next(entry['native_name'] if view == 'native' else entry['name'] for entry in directory if entry['name'].endswith('.quota'))
            result = await runtime.handle({'session_id': session.id, 'operation': 'call', 'view': view,
                                           'name': quota_name, 'arguments': {'app': 'codex'}})
            assert not result.get('isError'), result
            assert result['structuredContent']['currentProviderId'] == 'fixture-b'
            public_counts[mode] = len(directory)
        finally:
            await runtime.close()
    assert before == {str(path): fingerprint(path) for path in production_files}, 'Production credential/config files changed during fixture probe'
    with sqlite3.connect(DATA / 'cc-switch.db') as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
    assert version == 20
    result = {'passed': True, 'backend': 'production Pan ASGI API -> Panel -> stdio -> actual GUI Fork executable', 'fixtureOnly': True,
              'productionDataUnchanged': True, 'realAccountSwitches': 0, 'fixtureProviderSwitches': 1,
              'fixtureDefaultSwitches': 1, 'allAccountFieldsPreserved': True, 'databaseVersionAfter': version,
              'mcpPublicTools': public_counts}
    (CASE / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


asyncio.run(main())

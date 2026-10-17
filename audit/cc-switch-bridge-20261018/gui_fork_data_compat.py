"""Upgrade a private backup of actual GUI/legacy-CLI data; never query a real provider."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

case = Path(__file__).resolve().parent / 'gui-fork-data-compat'
private = case / 'private'
home = private / 'home'
data = home / '.cc-switch'
data.mkdir(parents=True, exist_ok=True)
source = Path.home() / '.cc-switch'
exe = Path(sys.argv[1]).resolve()
assert exe.is_file()
target = data / 'cc-switch.db'
assert not target.exists(), 'Use a fresh private database copy'
with sqlite3.connect((source / 'cc-switch.db').as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
oauth = source / 'codex_oauth_auth.json'
oauth_before = hashlib.sha256(oauth.read_bytes()).hexdigest()
(data / oauth.name).write_bytes(oauth.read_bytes())
(data / 'settings.json').write_text('{}', encoding='utf-8')
env = {**os.environ, 'CC_SWITCH_TEST_HOME': str(home), 'CC_SWITCH_CONFIG_DIR': str(data), 'CODEX_HOME': str(home / '.codex')}


def run(args):
    result = subprocess.run([str(exe), *args], capture_output=True, text=True, encoding='utf-8', timeout=30, env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, 'GUI Fork command failed in private migration copy'
    return json.loads(result.stdout)


tables = ('providers', 'provider_endpoints', 'mcp_servers', 'skills')
with sqlite3.connect(target) as db:
    before_version = db.execute('PRAGMA user_version').fetchone()[0]
    snapshots = {}
    for table in tables:
        if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
            columns = [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]
            snapshots[table] = (columns, sorted(db.execute('SELECT * FROM ' + table).fetchall(), key=repr))
    db.execute("INSERT INTO providers (id,app_type,name,settings_config) VALUES ('fixture-migration-probe','codex','Fixture migration probe','{}')")
quota = run(['--app', 'codex', 'provider', 'quota', 'fixture-migration-probe', '--json'])
assert quota['status'] == 'not_available'
with sqlite3.connect(target) as db:
    after_version = db.execute('PRAGMA user_version').fetchone()[0]
    assert after_version == 20
    for table, (columns, rows) in snapshots.items():
        selected = ','.join('"' + name.replace('"', '""') + '"' for name in columns)
        current = db.execute('SELECT ' + selected + ' FROM ' + table).fetchall()
        if table == 'providers':
            id_index = columns.index('id')
            current = [row for row in current if row[id_index] != 'fixture-migration-probe']
        assert sorted(current, key=repr) == rows, table + ' existing data changed'
assert (data / oauth.name).read_bytes() == oauth.read_bytes(), 'Copied OAuth records changed'
assert hashlib.sha256(oauth.read_bytes()).hexdigest() == oauth_before
for app in ('codex', 'claude', 'gemini'):
    listed = run(['--app', app, 'provider', 'list', '--json'])
    assert isinstance(listed['providers'], list)
result = {'passed': True, 'privateDatabaseCopyOnly': True, 'schemaBefore': before_version, 'schemaAfter': after_version,
          'originalProviderRows': len(snapshots['providers'][1]), 'preservedTables': list(snapshots),
          'allOriginalRowsPreserved': True, 'oauthCopyUnchanged': True, 'productionOAuthUnchanged': True,
          'realProviderQueries': 0, 'realAccountSwitches': 0}
(case / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))

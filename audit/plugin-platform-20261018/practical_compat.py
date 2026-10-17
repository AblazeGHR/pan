"""Read practical once; exercise only private worktree copies. Never dispatch jobs."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from collections import Counter
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SOURCE = Path('D:/project/Pan')
CASE = (ROOT / sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'audit/plugin-platform-20261018/practical-compat'
if not CASE.is_relative_to(ROOT):
    raise ValueError('Compatibility output must stay in this worktree')
PRIVATE = CASE / 'private'
PRIVATE.mkdir(parents=True, exist_ok=True)
for key in tuple(os.environ):
    if key.startswith('PAN_'):
        os.environ.pop(key)
os.environ['PAN_PORT'] = '59997'

from packages.core import config, session as sess, background_jobs
from packages.core.character import CharacterManager
from packages.core.adapters.mcp import build_mcp_servers
from packages.plugins.catalog import Catalog, session_bindings
from packages.web import server as web

def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()

def snapshot():
    copied = {}
    for directory in ('sessions', 'qq_inbox', 'qq_history', 'background_jobs/jobs'):
        source = SOURCE / 'data' / directory
        if not source.exists():
            continue
        for path in source.rglob('*'):
            if path.is_file():
                target = PRIVATE / 'data' / path.relative_to(SOURCE / 'data')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                copied[str(target.relative_to(PRIVATE))] = sha(target)
    cfg = json.loads((SOURCE / 'config.json').read_text(encoding='utf-8'))
    manifests = []
    for index, raw in enumerate(cfg.get('plugin_manifests', config.DEFAULT_PLUGIN_MANIFESTS)):
        path = Path(raw)
        if not path.is_absolute():
            path = SOURCE / path
        if path.is_dir():
            path /= 'manifest.json'
        try:
            relative = path.resolve().relative_to(SOURCE.resolve())
        except ValueError:
            relative = None
        if relative is not None and (ROOT / relative).is_file() and relative.name != 'private-manifest.json':
            manifests.append(str(ROOT / relative))
        elif path.is_file():
            # Preserve ${PLUGIN_DIR} semantics without changing source or copying secrets into reports.
            text = path.read_text(encoding='utf-8').replace('${PLUGIN_DIR}', str(path.parent).replace('\\', '/'))
            target = PRIVATE / f'manifest-{index}.json'
            target.write_text(text, encoding='utf-8')
            manifests.append(str(target))
    cfg['plugin_manifests'] = manifests
    (PRIVATE / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
    return copied, cfg

async def main():
    copied, cfg = snapshot()
    config.CONFIG_FILE = PRIVATE / 'config.json'
    sess.SESSION_DIR = PRIVATE / 'data/sessions'
    sess._cache.clear()
    sess._all_loaded = False
    manager = CharacterManager(str(PRIVATE / 'data'))
    manifest = manager.load_manifest(cfg['plugin_manifests'])
    catalog = Catalog(manifest)
    web._character_manager = manager
    web.broadcast = AsyncMock()
    web.worker.find_alive_worker_by_session = lambda _: None
    web._session_summary = lambda s: {'id': s.id}
    serialize = web._session_to_api
    web._session_to_api = lambda s: serialize(s, include_history=False, include_raw_usage=False,
        include_last_result=False, app_config=cfg, pin_projection={})
    result = {'source': str(SOURCE), 'source_head': os.popen('git -C D:/project/Pan rev-parse HEAD').read().strip(),
              'sessions': 0, 'with_qq': 0, 'mode_roundtrips': 0, 'legacy_descriptors': {},
              'templates': len(manifest.session_templates), 'jobs': 0, 'qq_actions': 0,
              'copied_files': len(copied), 'errors': [], 'source_used_read_only': True}
    names = Counter()
    for path in sorted(sess.SESSION_DIR.glob('*.json')):
        original = json.loads(path.read_text(encoding='utf-8'))
        sid = original['id']
        try:
            session = sess.get(sid, load_history=True)
            assert session is not None
            names.update(d.get('name', '') for d in session.adapter_config.get('mcp_servers', []))
            before = copy.deepcopy(session.to_dict())
            bindings = session_bindings(session, catalog)
            shown = web._session_plugin_fields(session)
            assert shown['pluginBindings'] == bindings
            # Old sessions continue to generate the same connection names until an explicit update.
            built = build_mcp_servers(session)
            assert set(built) == {d['name'] for d in session.adapter_config.get('mcp_servers', [])}
            assert session.to_dict() == before
            result['sessions'] += 1
            history = sess._history_path(sid)
            history_hash = sha(history) if history.exists() else None
            has_qq = any(b['plugin_id'] == 'qq' for b in bindings)
            result['with_qq'] += has_qq
            # Exercise migration/save/reload for every actual legacy selection, including no-QQ sessions.
            for mode in ('standalone', 'pan_native', 'gateway'):
                changed = [dict(b, mode=mode) if b['plugin_id'] == 'qq' else dict(b) for b in bindings]
                revision = session.adapter_config.get('plugin_selection_revision', 0)
                response = await web.api_update_session(sid, {'pluginBindings': changed,
                    'expectedPluginRevision': revision, 'forceMcp': True})
                assert 'error' not in response, response.get('error')
                assert response['pluginBindings'] == changed
                restored = sess.Session._from_data(json.loads(path.read_text(encoding='utf-8')))
                assert restored.adapter_config['plugin_bindings'] == changed
                for key, value in before.items():
                    if key not in {'adapter_config', 'updated_at', 'history', 'summary_projection'}:
                        assert restored.to_dict()[key] == value, key
                for key, value in before['adapter_config'].items():
                    if key not in {'mcp_servers', 'plugin_bindings', 'plugin_config_version', 'plugin_selection_revision'}:
                        assert restored.adapter_config[key] == value, key
                physical = build_mcp_servers(restored)
                assert physical or not any(b['enabled'] for b in changed)
                if history_hash:
                    assert sha(history) == history_hash, 'history JSONL changed'
                result['mode_roundtrips'] += 1
            sess._cache.pop(sid, None)
            reloaded = sess.get(sid, load_history=True)
            assert reloaded and reloaded.adapter_config['plugin_config_version'] == 2
        except Exception as exc:
            result['errors'].append({'session': sid, 'error': f'{type(exc).__name__}: {exc}'})
    for template in manifest.session_templates:
        try:
            bindings = catalog.bindings(template.plugin_bindings) if template.plugin_bindings is not None else catalog.legacy_bindings(template.mcp_servers or [])
            catalog.compile(bindings)
        except Exception as exc:
            result['errors'].append({'template': template.name, 'error': str(exc)})
    for path in (PRIVATE / 'data/background_jobs/jobs').glob('*.json'):
        raw = background_jobs._load_path(path)
        normalized = background_jobs._normalize_job(copy.deepcopy(raw))
        if not raw or 'jobId' not in raw:
            continue
        assert normalized and normalized.get('jobId') == raw.get('jobId')
        for key in ('action', 'status', 'targetSessionId', 'schedule', 'qq_report_targets'):
            if key in raw:
                assert normalized[key] == raw[key], key
        result['jobs'] += 1
        result['qq_actions'] += (raw.get('action') or {}).get('api') == 'send_qq'
    unchanged = ('data/qq_inbox/', 'data/qq_history/', 'data/background_jobs/')
    for name, checksum in copied.items():
        if name.replace('\\', '/').startswith(unchanged):
            assert sha(PRIVATE / name) == checksum, name
    result['legacy_descriptors'] = dict(names)
    result['passed'] = not result['errors']
    (CASE / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert result['passed'], 'Practical compatibility failed; inspect private result'

asyncio.run(main())

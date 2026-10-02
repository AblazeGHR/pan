"""Disposable real-HTTP history search fixture; never run on protected ports."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('search_fixture', Path(__file__).with_name('server.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def seed():
    store = fixture.session_store
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fixture._write_fixture_files()
    store._cache.clear()
    store._all_loaded = False
    primary = store.create('Search Acceptance', adapter='cbc', workdir=str(fixture.WORKDIR))
    for index in range(405):
        text = f'ordinary transcript {index}'
        if index == 7:
            text = 'DeepNeedle QQ target deepneedle'
        if index == 205:
            text = 'DeepNeedle middle target'
        if index == 404:
            text = '中文字面 +++ [literal]'
        store.append_history(primary, {'role': 'user' if index % 2 == 0 else 'assistant',
                                      'content': text, **({'source': 'qq'} if index == 7 else {})})
    store.append_history(primary, {'role': 'tool', 'content': 'DeepNeedle tool deepneedle deepneedle'})
    store.append_history(primary, {'role': 'thinking', 'content': 'DeepNeedle reasoning'})
    for role in ('error', 'system'):
        store.append_history(primary, {'role': role, 'content': 'DeepNeedle forbidden'})
    store.append_history(primary, {'role': 'user', 'content': 'DeepNeedle prompt', 'source': 'system_prompt'})
    store.save_full(primary)
    remote = store.create('Search Remote', adapter='cbc', workdir=str(fixture.WORKDIR))
    for index in range(601):
        store.append_history(remote, {'role': ('user', 'assistant', 'tool', 'thinking')[index % 4],
                                      'content': f'SharedNeedle sharedneedle result {index}'})
    store.save_full(remote)
    bench = store.create('Search Benchmark', adapter='cbc', workdir=str(fixture.WORKDIR))
    for index in range(10_000):
        store.append_history(bench, {'role': ('user', 'assistant', 'tool', 'thinking')[index % 4],
          'content': f'commonterm commonterm ordinary {index} ' + 'transcript evidence '*12 + (' RareNeedle' if index % 500 == 0 else '')})
    store.save_full(bench)
    live = sys.modules['packages.web.server']
    if os.environ.get('PAN_COMPARE_PR') == '1':
        source = subprocess.run(['gh', 'api', 'repos/Thelittlewinter233/pan/contents/packages/web/server.py?ref=bf811a7cc2f0271023b459729b7fb0079591ebaf',
                                 '-H', 'Accept: application/vnd.github.raw+json'], capture_output=True, check=True, timeout=30).stdout.decode('utf-8')
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_search_session_history')
        namespace = dict(live.__dict__)
        exec(compile(ast.get_source_segment(source, node), 'PR3-pinned-helper', 'exec'), namespace)
        helper = namespace['_search_session_history']
        @live.app.get('/__e2e/pr3-search')
        async def pr_search(sessionId: str, q: str):
            return await live._store_read(helper, sessionId, q, 100)
    @live.app.post('/__e2e/cold-registry')
    async def cold_registry():
        store._cache.clear()
        store._all_loaded = False
        return {'ok': True}
    (fixture.RUNTIME / 'seed-ids.json').write_text(json.dumps({'primary': primary.id, 'remote': remote.id, 'bench': bench.id}), encoding='utf-8')


fixture._seed_sessions = seed
fixture.main()

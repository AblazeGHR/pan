"""Disposable click timing fixture; comparison changes only queue tail decoding.

Uses the existing isolated server boundaries. Never targets real Session data.
"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from packages.web.e2e import server as fixture
from packages.core import session as store
from packages.web import server as web
original_seed = fixture._seed_sessions
spans = []
baseline = False
def seed():
    original_seed()
    ids = {}
    for name, count, links in [('Small', 300, False), ('Large', 30000, False), ('Large Links', 30000, True)]:
        rows = [
            {
                'role': 'user' if i % 2 == 0 else 'assistant',
                'content': f'Row {i}\n\n' + ('text diagnostic ' * 120)
                    + ('\n[notes](notes.md#L42)' if links else ''),
                'messageId': f'pan:{i:032x}',
            }
            for i in range(count)
        ]
        s = store.create(name, adapter='cbc', workdir=str(fixture.WORKDIR), history=rows)
        ids[name] = s.id
    store._cache.clear()
    store._all_loaded = False
    (fixture.RUNTIME / 'ids.json').write_text(json.dumps(ids))
fixture._seed_sessions = seed
def wrap(module, name):
    original = getattr(module, name)
    def timed(*args, **kwargs):
        if (name == '_history_page_from_jsonl' and baseline
                and kwargs.get('limit') == store.QUEUE_IDEMPOTENCY_INDEX_MAX_ENTRIES):
            # Reproduce the previous queue recovery path within this fixture.
            # Foreground tail paging and all product behavior stay identical.
            kwargs['known_total'] = None
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            spans.append({'name': name, 'ms': (time.perf_counter()-start)*1000})
    setattr(module, name, timed)
for name in ['_history_page_lookup', '_api_history']:
    wrap(web, name)
for name in ['history_page', '_history_page_from_jsonl']:
    wrap(store, name)
@web.app.post('/__e2e/profile/reset')
async def reset(payload: dict):
    global baseline
    baseline = payload.get('baseline', False)
    store._cache.clear()
    store._all_loaded = False
    spans.clear()
    return {'ok': True}
@web.app.get('/__e2e/profile/spans')
async def profile():
    return spans


if __name__ == '__main__':
    fixture.main()


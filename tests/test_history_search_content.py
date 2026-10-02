"""Content counting and independently selectable, disposable role indexes."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from types import SimpleNamespace
from threading import RLock

from packages.core import history_search_query
from packages.core.history_search_index import search_history


def make_session(rows):
    return SimpleNamespace(id='content', history=rows, history_epoch='epoch',
                           history_revision=1, _history_loaded=True,
                           _summary_lock=RLock(),
                           summary_projection={'history_total': len(rows)})


def row(role, text, number):
    return {'role': role, 'content': text, 'messageId': f'pan:{number:032x}'}


def search(path, session, query='needle', **options):
    return search_history(path, [session], [session.id], query,
                          load_session=lambda _: None, content_counts=True, **options)


def test_occurrence_counts_role_partitions_and_ordinal_seek(tmp_path):
    session = make_session([
        row('user', 'Needle needle', 1), row('assistant', 'needle', 2),
        row('tool', 'needle needle needle', 3), row('thinking', 'needle', 4),
        row('error', 'needle', 5), row('system', 'needle', 6),
        {'role': 'tool', 'content': 'needle', 'messageId': 'legacy:tool'},
    ])
    path = tmp_path / 'index.sqlite3'
    body = search(path, session, limit=1)
    assert (body['totalMatches'], body['totalMessages']) == (3, 2)
    assert body['hits'][0]['matchCount'] == 2
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT DISTINCT role FROM history_search_messages').fetchall() == [('assistant',), ('user',)]
        assert db.execute("SELECT count(*) FROM history_search_tool_fts WHERE history_search_tool_fts MATCH 'nee'").fetchone()[0] == 0
    full = search(path, session, roles=('user', 'assistant', 'tool', 'thinking'))
    assert (full['totalMatches'], full['totalMessages']) == (7, 4)
    assert [hit['matchStart'] for hit in full['hits']] == [0, 2, 3, 6]
    sought = search(path, session, roles=('user', 'assistant', 'tool', 'thinking'), match_index=5)
    assert sought['hits'][0]['role'] == 'tool'
    assert sought['hits'][0]['matchStart'] == 3
    assert search(path, session, roles=('tool',))['totalMatches'] == 3
    assert search(path, session)['totalMatches'] == 3


def test_nonoverlapping_literal_counts_and_compressed_repeated_block(tmp_path):
    session = make_session([row('tool', 'aaaaa', 1), row('thinking', '中' * 100_000, 2)])
    path = tmp_path / 'index.sqlite3'
    assert search(path, session, 'aa', roles=('tool',))['totalMatches'] == 2
    result = search(path, session, '中', roles=('thinking',))
    assert result['totalMatches'] == 100_000
    assert result['totalMessages'] == len(result['hits']) == 1
    search(path, session, 'aa', roles=('tool',))
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM history_search_query_chunks').fetchone()[0] == 2


def test_empty_roles_never_open_an_index(tmp_path):
    path = tmp_path / 'index.sqlite3'
    result = search(path, make_session([row('tool', 'needle', 1)]), roles=())
    assert result['totalMatches'] == 0
    assert not path.exists()


def test_unicode_casefold_matches_pr_semantics_with_original_offsets(tmp_path):
    session = make_session([row('assistant', 'Straße SS and Straße', 1)])
    path = tmp_path / 'index.sqlite3'
    assert search(path, session, 'STRASSE')['totalMatches'] == 2
    result = search(path, session, 'ss')
    assert result['totalMatches'] == 3
    assert result['hits'][0]['firstMatch'] == 4
    assert search(path, session, 'and')['hits'][0]['firstMatch'] == 10


def test_literal_candidates_do_not_drop_unusual_substrings(tmp_path):
    text = 'a\x00b x\nx e\u0301e \U0001f600\U0001f601\U0001f602 """ Straße'
    session = make_session([row('tool', text, 1)])
    path = tmp_path / 'index.sqlite3'
    for query in ('a\x00b', 'x\nx', 'e\u0301e', '\U0001f600\U0001f601\U0001f602', '"""', 'strasse'):
        result = search(path, session, query, roles=('tool',))
        assert result['totalMatches'] == text.casefold().count(query.casefold()), repr(query)


def test_warm_pages_do_not_repeat_content_matching(tmp_path, monkeypatch):
    path = tmp_path / 'index.sqlite3'
    session = make_session([row('assistant', 'needle needle', i) for i in range(1, 25)])
    first = search(path, session, limit=5)
    # Candidate SQL must not run again for the same version/query, even though
    # constructing a SQL string is harmless. Poison it to prove cache reuse.
    monkeypatch.setattr(history_search_query, '_candidate_sql', lambda *_: ('SELECT missing_column', ()))
    second = search(path, session, limit=5, after=first['nextAfter'])
    assert second['totalMatches'] == 48
    assert [h['messageIndex'] for h in second['hits']] == list(range(5, 10))


def test_cache_is_bounded_and_oversized_results_remain_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(history_search_query, 'MAX_CACHED_QUERIES', 3)
    monkeypatch.setattr(history_search_query, 'MAX_CACHED_REFERENCES', 4)
    path = tmp_path / 'index.sqlite3'
    session = make_session([row('user', 'needle alpha beta gamma delta', i) for i in range(1, 7)])
    page = search(path, session, limit=2)
    assert page['totalMatches'] == 6
    second = search(path, session, limit=2, after=page['nextAfter'])
    assert [h['messageIndex'] for h in second['hits']] == [2, 3]
    sought = search(path, session, limit=1, match_index=5)
    assert sought['hits'][0]['messageIndex'] == 5
    # Each oversized query is served without retaining unlimited references.
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM history_search_query_chunks').fetchone()[0] == 0
    session.history = [row('user', 'alpha beta gamma delta', 1)]
    session.history_revision += 1
    session.summary_projection['history_total'] = 1
    for query in ('alpha', 'beta', 'gamma', 'delta'):
        assert search(path, session, query)['totalMatches'] == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM history_search_queries').fetchone()[0] == 3
        assert db.execute('SELECT count(*) FROM history_search_query_chunks').fetchone()[0] == 3


def test_concurrent_partition_enable_and_eviction_preserve_results(tmp_path, monkeypatch):
    monkeypatch.setattr(history_search_query, 'MAX_CACHED_QUERIES', 2)
    session = make_session([row(role, 'needle alpha beta gamma', i)
                            for i, role in enumerate(('user', 'assistant', 'tool', 'thinking'), 1)])
    path = tmp_path / 'index.sqlite3'
    def run(i):
        role = ('user', 'assistant', 'tool', 'thinking')[i % 4]
        query = ('needle', 'alpha', 'beta', 'gamma')[i % 4]
        result = search(path, session, query, roles=(role,))
        assert result['totalMatches'] == 1
        assert result['hits'][0]['role'] == role
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, range(40)))
    assert search(path, session, roles=('user', 'assistant', 'tool', 'thinking'))['totalMatches'] == 4


def test_existing_disposable_schema_rebuilt_without_modifying_history(tmp_path):
    path = tmp_path / 'index.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE history_search_messages (old_column TEXT)')
    session = make_session([row('tool', 'needle', 1)])
    before = [dict(item) for item in session.history]
    assert search(path, session, roles=('tool',))['totalMatches'] == 1
    assert session.history == before
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        # External-content FTS has no duplicate content table.
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='history_search_tool_fts_content'").fetchall()
        for table in ('history_search_fts', 'history_search_tool_fts', 'history_search_thinking_fts'):
            db.execute(f"INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 1)")
            db.execute(f"INSERT INTO {table}({table}) VALUES ('rebuild')")
        assert db.execute("SELECT count(*) FROM history_search_fts WHERE history_search_fts MATCH 'nee'").fetchone()[0] == 0


def test_append_populates_every_previously_enabled_partition(tmp_path):
    session = make_session([row('user', 'needle', 1), row('tool', 'needle', 2)])
    path = tmp_path / 'index.sqlite3'
    search(path, session, roles=('tool',))
    search(path, session)
    session.history.append(row('tool', 'needle needle', 3))
    session.history_revision += 1
    session.summary_projection['history_total'] += 1
    assert search(path, session)['totalMatches'] == 1
    assert search(path, session, roles=('tool',))['totalMatches'] == 3


def test_chunk_boundaries_keep_pages_and_occurrence_seek_complete(tmp_path):
    session = make_session([row('assistant', 'needle needle', i) for i in range(1, 521)])
    path = tmp_path / 'index.sqlite3'
    hits, after = [], None
    while True:
        page = search(path, session, limit=100, after=after)
        assert page['totalMatches'] == 1040
        hits.extend(page['hits'])
        if not page['hasMore']:
            break
        after = page['nextAfter']
    assert [hit['messageIndex'] for hit in hits] == list(range(520))
    for ordinal in (0, 254, 255, 256, 511, 512, 1039, 1040):
        page = search(path, session, limit=100, match_index=ordinal)
        expected = [] if ordinal == 1040 else list(range(ordinal//2, min(520, ordinal//2+100)))
        assert [hit['messageIndex'] for hit in page['hits']] == expected
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM history_search_query_chunks').fetchone()[0] == 5


def test_positionless_candidates_are_verified_as_complete_literals(tmp_path):
    session = make_session([row('user', 'abcdefghi', 1), row('tool', 'abc elsewhere def and ghi', 2)])
    result = search(tmp_path / 'index.sqlite3', session, 'abcdefghi', roles=('user', 'tool'))
    assert result['totalMatches'] == 1
    assert result['hits'][0]['messageIndex'] == 0


def test_unselected_suffix_id_collision_falls_back_to_complete_rebuild(tmp_path):
    session = make_session([row('user', 'needle', 1)])
    path = tmp_path / 'index.sqlite3'
    assert search(path, session)['totalMatches'] == 1
    session.history.append(row('tool', 'needle latest', 1))
    session.history_revision += 1
    session.summary_projection['history_total'] += 1
    assert search(path, session)['totalMatches'] == 0


def test_pan_id_fast_validation_preserves_canonical_uuid_rules():
    import uuid
    from packages.core.session import is_pan_message_id
    for number in range(256):
        identity = uuid.UUID(int=number)
        assert is_pan_message_id('pan:'+identity.hex)
        assert is_pan_message_id('pan:'+str(identity))
    for value in (None, 1, '', 'legacy:1', 'pan:'+('A'*32), 'pan:'+('a'*31),
                  'pan:{00000000-0000-0000-0000-000000000000}', 'pan:'+('g'*32)):
        assert not is_pan_message_id(value)


def test_dense_queries_choose_scan_but_sparse_queries_keep_fts(tmp_path, monkeypatch):
    original = history_search_query._candidate_sql
    plans = []
    def capture(*args):
        result = original(*args)
        plans.append(result[0])
        return result
    monkeypatch.setattr(history_search_query, '_candidate_sql', capture)
    session = make_session([row('tool', 'needle commonterm'+(' sparseword' if i == 1 else ''), i)
                            for i in range(1, 1001)])
    path = tmp_path / 'index.sqlite3'
    assert search(path, session, 'commonterm', roles=('tool',))['totalMatches'] == 1000
    assert ' MATCH ' not in plans[-1]
    assert search(path, session, 'sparseword', roles=('tool',))['totalMatches'] == 1
    assert ' MATCH ' in plans[-1]


def test_verified_append_materializes_only_suffix_text_blocks(tmp_path, monkeypatch):
    from packages.core import history_search_index
    session = make_session([row('user', 'needle', i) for i in range(1, 1001)])
    path = tmp_path / 'index.sqlite3'
    search(path, session)
    original = history_search_index._SearchMessage
    visited = []
    def capture(**fields):
        visited.append(fields['message_index'])
        return original(**fields)
    monkeypatch.setattr(history_search_index, '_SearchMessage', capture)
    session.history.append(row('user', 'needle', 1001))
    session.history_revision += 1
    session.summary_projection['history_total'] += 1
    assert search(path, session)['totalMatches'] == 1001
    assert visited == [1000]

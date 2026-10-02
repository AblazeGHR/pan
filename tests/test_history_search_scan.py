from types import SimpleNamespace
from contextlib import contextmanager
import hashlib
import hmac

import pytest

from packages.core.history_search_scan import scan_history, cursor_key
from packages.core.history_search_index import HistorySearchCursorError


def session(rows, name='a'):
    return SimpleNamespace(id=name, history=rows, history_epoch='epoch', history_revision=1)


def row(role, text, number):
    return {'role': role, 'content': text, 'messageId': f'pan:{number:032x}'}


def test_roles_exact_occurrences_unicode_and_exclusions():
    rows = [row(role, '中中 needle needle !?+@', i+1)
            for i, role in enumerate(('user', 'assistant', 'tool', 'thinking', 'error', 'system'))]
    rows += [{'role': 'user', 'content': 'needle', 'messageId': 'legacy:old'},
             {**row('user', 'needle', 100), 'source': 'system_prompt'}]
    subject = session(rows)
    for query, count in [('needle', 8), ('中', 8), ('!?+@', 4)]:
        result = scan_history([subject], query, roles=['user', 'assistant', 'tool', 'thinking'])
        assert result['totalMatches'] == count
        assert result['totalMessages'] == 4
    assert scan_history([subject], 'needle', roles=['tool'])['totalMatches'] == 2
    assert scan_history([subject], 'needle', roles=[])['hits'] == []
    result = scan_history([session([row('user', 'Straße STRASSE', 1)])], 'strasse')
    assert result['totalMatches'] == 2
    assert result['hits'][0]['firstMatch'] == 0


def test_last_id_wins_without_counting_stream_deltas():
    subject = session([row('assistant', 'needle', 1), row('assistant', 'needle needle', 1),
                       row('tool', 'needle', 2), row('tool', 'gone', 2)])
    result = scan_history([subject], 'needle', roles=['assistant', 'tool'])
    assert result['totalMatches'] == 2
    assert result['totalMessages'] == 1
    assert result['hits'][0]['messageIndex'] == 1


def test_full_pagination_seek_and_id_relocation():
    subjects = [session([row('user', 'needle needle', i+1) for i in range(237)]),
                session([row('tool', 'needle', i+1000) for i in range(31)], 'b')]
    hits, after = [], None
    while True:
        page = scan_history(subjects, 'needle', roles=['user', 'tool'], limit=67, after=after)
        assert page['totalMatches'] == 505
        assert page['matchingSessionIds'] == ['a', 'b']
        hits.extend(page['hits'])
        if not page['hasMore']:
            break
        after = page['nextAfter']
    assert len(hits) == 268
    assert len({(hit['sessionId'], hit['messageId']) for hit in hits}) == 268
    sought = scan_history(subjects, 'needle', roles=['user', 'tool'], match_index=473, limit=1)
    assert sought['hits'][0]['messageIndex'] == 236
    assert sought['hits'][0]['matchStart'] == 472
    relocated = scan_history(subjects, 'needle', roles=['user', 'tool'], message_id=f'pan:{1010:032x}')
    assert relocated['hits'][0]['messageIndex'] == 10
    assert relocated['totalMatches'] == 505


def test_matching_session_scope_is_complete_even_beyond_result_page_and_respects_roles():
    subjects = [session([row('user', 'needle', n+1) for n in range(150)], 'first'),
                session([row('thinking', 'needle needle', 200)], 'later'),
                session([row('assistant', 'nothing', 201)], 'empty')]
    result = scan_history(subjects, 'needle', roles=['user', 'thinking'], limit=1)
    assert len(result['hits']) == 1
    assert result['matchingSessionIds'] == ['first', 'later']
    assert scan_history(subjects, 'needle', roles=['user'], limit=1)['matchingSessionIds'] == ['first']
    assert scan_history(subjects, '', roles=['user'])['matchingSessionIds'] == []
    assert scan_history(subjects, 'needle', roles=[])['matchingSessionIds'] == []


def test_signed_cursor_key_and_empty_search_do_not_open_source():
    def no_source(_):
        raise AssertionError('empty search must not load history')
    assert scan_history([session([])], '', source=no_source)['hits'] == []
    assert scan_history([session([])], 'needle', roles=[], source=no_source)['hits'] == []
    signature = hmac.new(cursor_key(), b'bound', hashlib.sha256).digest()
    assert scan_history([session([])], 'needle', cursor_auth=('bound', signature))['hits'] == []
    with pytest.raises(HistorySearchCursorError):
        scan_history([session([])], 'needle', cursor_auth=('other', signature))


def test_reference_cache_reuses_only_matching_source_stamp_and_history_version(monkeypatch):
    from packages.core import history_search_scan as scan
    monkeypatch.setattr(scan, '_matches_cache', scan.OrderedDict())
    monkeypatch.setattr(scan, '_cache_references', 0)
    subject = session([row('tool', 'needle needle', 1)])
    calls = []
    stamp = [1]
    @contextmanager
    def source(_):
        def iterate():
            calls.append('read')
            yield 0, subject.history[0], 0
        yield iterate(), subject.history.__getitem__, ('test-canonical-file', stamp[0])
    assert scan_history([subject], 'needle', roles=['tool'], source=source)['totalMatches'] == 2
    assert scan_history([subject], 'needle', roles=['tool'], source=source)['totalMatches'] == 2
    assert calls == ['read']
    subject.history[0]['content'] = 'needle'
    stamp[0] += 1
    assert scan_history([subject], 'needle', roles=['tool'], source=source)['totalMatches'] == 1
    subject.history_revision += 1
    assert scan_history([subject], 'needle', roles=['tool'], source=source)['totalMatches'] == 1
    assert calls == ['read', 'read', 'read']
    assert scan._cache_references == 3
    monkeypatch.setattr(scan, 'MAX_CACHE_ENTRIES', 1)
    scan_history([subject], 'need', roles=['tool'], source=source)
    assert len(scan._matches_cache) == 1
    assert scan._cache_references == 1


def test_global_twenty_session_pagination_does_not_thrash_small_per_session_cache(monkeypatch):
    from packages.core import history_search_scan as scan
    monkeypatch.setattr(scan, '_matches_cache', scan.OrderedDict())
    monkeypatch.setattr(scan, '_cache_references', 0)
    subjects = [session([row('tool', 'needle', 1)], f'scope-{index}') for index in range(20)]
    reads = []
    @contextmanager
    def source(subject):
        def iterate():
            reads.append(subject.id)
            yield 0, subject.history[0], 0
        yield iterate(), subject.history.__getitem__, ('cold-source', subject.id)
    first = scan_history(subjects, 'needle', roles=['tool'], limit=10, source=source)
    second = scan_history(subjects, 'needle', roles=['tool'], limit=10, source=source, after=first['nextAfter'])
    assert len(reads) == 20
    assert len(first['hits']) == len(second['hits']) == 10
    assert not second['hasMore']

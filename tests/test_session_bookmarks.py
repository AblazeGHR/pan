"""Durable message bookmark storage, identity and concurrency contracts.

These tests exercise the real sidecar files under the test-isolated Session
store (``tests/conftest.py`` points ``SESSION_DIR`` at ``tmp_path``), real
disk re-reads after cache clears, real cross-process-style replacement, and
the ticket/cancellation independence of the bookmark store.
"""

import asyncio
import copy
import json
import os
import threading

import pytest

from packages.core import session as store
from packages.core import session_bookmarks as bookmarks


def _session_with_blocks(name='bookmarks', roles=('user', 'assistant', 'tool', 'thinking')):
    session = store.create(name, adapter='cbc')
    ids = []
    for role in roles:
        store.append_history(session, {'role': role, 'content': f'{role} body block'})
        ids.append(session.history[-1]['messageId'])
    store.save_full(session)
    return session, ids


def _read_file(session_id):
    return json.loads(bookmarks._path(session_id).read_text(encoding='utf-8'))


def _cold(session_id):
    """Simulate a restart: drop every in-process cache for this Session."""
    store._cache.pop(session_id, None)
    bookmarks._CACHE.pop(session_id, None)
    store._all_loaded = False


# ---------------------------------------------------------------- round trip


def test_add_remove_round_trip_survives_real_disk_reread():
    session, ids = _session_with_blocks()
    result = bookmarks.add_bookmark(session.id, ids[1])
    assert result['changed'] is True and result['revision'] == 1
    assert result['bookmarks'] == {
        ids[1]: {'messageId': ids[1], 'createdAt': result['bookmarks'][ids[1]]['createdAt']}}

    on_disk = _read_file(session.id)
    assert on_disk['version'] == 1 and on_disk['revision'] == 1
    assert set(on_disk['bookmarks']) == {ids[1]}

    _cold(session.id)
    restored = bookmarks.read_bookmarks(session.id)
    assert restored['state'] == 'present' and restored['revision'] == 1
    assert set(restored['bookmarks']) == {ids[1]}
    assert bookmarks.bookmarked_message_ids(session.id) == frozenset({ids[1]})

    removed = bookmarks.remove_bookmark(session.id, ids[1])
    assert removed['changed'] is True and removed['revision'] == 2
    assert removed['bookmarks'] == {}
    _cold(session.id)
    assert bookmarks.read_bookmarks(session.id)['bookmarks'] == {}
    assert _read_file(session.id)['revision'] == 2


def test_add_and_remove_are_idempotent_and_write_nothing_on_noop():
    session, ids = _session_with_blocks()
    assert bookmarks.add_bookmark(session.id, ids[0])['changed'] is True
    path = bookmarks._path(session.id)
    stamp = path.stat().st_mtime_ns
    payload = path.read_bytes()

    again = bookmarks.add_bookmark(session.id, ids[0])
    assert again['changed'] is False and again['revision'] == 1
    assert path.stat().st_mtime_ns == stamp and path.read_bytes() == payload

    assert bookmarks.remove_bookmark(session.id, ids[0])['changed'] is True
    stamp = path.stat().st_mtime_ns
    payload = path.read_bytes()
    absent = bookmarks.remove_bookmark(session.id, ids[0])
    assert absent['changed'] is False and absent['revision'] == 2
    assert path.stat().st_mtime_ns == stamp and path.read_bytes() == payload


def test_absent_session_projects_no_flags_and_reads_empty():
    session, _ids = _session_with_blocks()
    envelope = bookmarks.read_bookmarks(session.id)
    assert envelope == {'revision': 0, 'bookmarks': {}, 'updatedAt': None, 'state': 'absent'}
    assert bookmarks.bookmarked_message_ids(session.id) == frozenset()
    assert bookmarks.dangling_bookmarks(session.id) == []


# ------------------------------------------------------------------- identity


def test_only_durable_pan_identities_are_accepted():
    session, ids = _session_with_blocks()
    for bad in (
        f'legacy:{session.id}:epoch:0',
        'msg_legacy',
        'pan:not-a-uuid',
        ids[0] + 'x',
        '',
        None,
        123,
        'pan:' + '0' * 31,           # wrong length
    ):
        with pytest.raises(bookmarks.BookmarkTargetUnresolved) as error:
            bookmarks.add_bookmark(session.id, bad)
        assert error.value.code == 'invalid_message_id'
    with pytest.raises(bookmarks.BookmarkTargetUnresolved) as error:
        bookmarks.add_bookmark(session.id, 'pan:' + 'a' * 32)   # well-formed, absent
    assert error.value.code == 'message_not_found'
    assert not bookmarks._path(session.id).exists()


def test_bookmarks_never_cross_sessions():
    first, first_ids = _session_with_blocks('first')
    second, _second_ids = _session_with_blocks('second')
    bookmarks.add_bookmark(first.id, first_ids[0])
    assert bookmarks.bookmarked_message_ids(first.id) == frozenset({first_ids[0]})
    assert bookmarks.bookmarked_message_ids(second.id) == frozenset()
    assert not bookmarks._path(second.id).exists()
    with pytest.raises(bookmarks.BookmarkTargetUnresolved) as error:
        bookmarks.add_bookmark(second.id, first_ids[0])
    assert error.value.code == 'message_not_found'
    assert bookmarks.bookmarked_message_ids(second.id) == frozenset()


def test_cold_session_resolves_target_outside_the_loaded_window():
    session, ids = _session_with_blocks(
        'cold window', roles=('user',) + ('assistant',) * 40)
    target = ids[2]                      # far outside the 20-row main-file tail
    _cold(session.id)
    cold = store.list_all(load_history=False)[0]
    assert not cold._history_loaded and cold.history == []
    resolved = bookmarks.resolve_bookmark_target(session.id, target)
    assert resolved == {'messageId': target, 'role': 'assistant', 'source': 'jsonl'}
    assert bookmarks.add_bookmark(session.id, target)['changed'] is True
    assert bookmarks.bookmarked_message_ids(session.id) == frozenset({target})


def test_legacy_session_without_jsonl_resolves_from_main_file_history():
    session, ids = _session_with_blocks('legacy main')
    store._history_path(session.id).unlink()
    _cold(session.id)
    resolved = bookmarks.resolve_bookmark_target(session.id, ids[1])
    assert resolved['source'] == 'main' and resolved['role'] == 'assistant'
    assert bookmarks.add_bookmark(session.id, ids[1])['changed'] is True


def test_ambiguous_history_is_refused_not_guessed():
    session, ids = _session_with_blocks('ambiguous')
    path = store._history_path(session.id)
    row = json.loads(path.read_text(encoding='utf-8').splitlines()[1])
    duplicate = dict(row)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(duplicate, ensure_ascii=False) + '\n')
    _cold(session.id)
    with pytest.raises(bookmarks.BookmarkTargetUnresolved) as error:
        bookmarks.add_bookmark(session.id, ids[1])
    assert error.value.code == 'message_not_found'
    assert 'ambiguous' in str(error.value)


def test_non_body_rows_are_not_bookmarkable():
    session = store.create('system rows', adapter='cbc')
    store.append_history(session, {'role': 'user', 'content': 'hello'})
    store.append_history(session, {'role': 'assistant', 'content': 'reply'})
    store.append_history(session, {'role': 'system', 'content': 'notice'})
    store.append_history(session, {'role': 'user', 'content': 'queued', 'source': 'system_prompt'})
    store.append_history(session, {'role': 'tool', 'content': 'Read(x)'})
    store.append_history(session, {'role': 'thinking', 'content': 'reasoning'})
    store.save_full(session)
    rows = session.history
    assert store.is_pan_message_id(rows[0].get('messageId'))
    assert store.is_pan_message_id(rows[1].get('messageId'))
    # Machinery rows are durable and resolvable, but not bookmark content.
    for index in (2, 3, 4, 5):
        resolved = rows[index].get('messageId')
        if resolved is None:
            continue
        with pytest.raises(bookmarks.BookmarkTargetUnresolved) as error:
            bookmarks.add_bookmark(session.id, resolved)
        assert error.value.code == 'message_not_bookmarkable'
    assert bookmarks.resolve_bookmark_target(session.id, rows[0]['messageId'])['role'] == 'user'
    assert bookmarks.resolve_bookmark_target(session.id, rows[1]['messageId'])['role'] == 'assistant'


def test_tool_and_thinking_are_rejected_with_a_distinct_code():
    session, ids = _session_with_blocks('roles')
    for message_id in (ids[2], ids[3]):     # tool, thinking
        with pytest.raises(bookmarks.BookmarkTargetUnresolved) as error:
            bookmarks.add_bookmark(session.id, message_id)
        assert error.value.code == 'message_not_bookmarkable'
        assert 'assistant text blocks' in str(error.value)
    assert bookmarks.add_bookmark(session.id, ids[0])['changed'] is True
    assert bookmarks.add_bookmark(session.id, ids[1])['changed'] is True


# ------------------------------------------------------- corruption and CAS


def test_unreadable_sidecar_is_never_treated_as_empty():
    session, ids = _session_with_blocks('damaged')
    path = bookmarks._path(session.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1, "revision": 1, "bookmarks": {"pan:', encoding='utf-8')
    damaged = path.read_bytes()
    for call in (
        lambda: bookmarks.read_bookmarks(session.id),
        lambda: bookmarks.add_bookmark(session.id, ids[0]),
        lambda: bookmarks.remove_bookmark(session.id, ids[0]),
    ):
        with pytest.raises(bookmarks.BookmarkUnreadable):
            call()
    assert path.read_bytes() == damaged      # no write may destroy the file

    # A wrong version is damage too, not a format upgrade.
    path.write_text(json.dumps({'version': 99, 'revision': 1, 'bookmarks': {}}), encoding='utf-8')
    with pytest.raises(bookmarks.BookmarkUnreadable):
        bookmarks.read_bookmarks(session.id)

    # Repairing the file restores service without any silent data loss.
    path.write_text(json.dumps({
        'version': 1, 'revision': 5, 'updatedAt': '2026-10-14T00:00:00',
        'bookmarks': {ids[0]: {'messageId': ids[0], 'createdAt': '2026-10-14T00:00:00'}},
    }), encoding='utf-8')
    bookmarks._CACHE.pop(session.id, None)
    assert bookmarks.read_bookmarks(session.id)['revision'] == 5
    assert bookmarks.remove_bookmark(session.id, ids[0])['changed'] is True


def test_projection_degrades_to_no_flags_while_history_keeps_serving(caplog):
    session, ids = _session_with_blocks('damaged projection')
    path = bookmarks._path(session.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('not json at all', encoding='utf-8')
    with caplog.at_level('WARNING'):
        assert bookmarks.bookmarked_message_ids(session.id) == frozenset()
    assert 'unreadable sidecar' in caplog.text
    page = store.history_page(session.id, limit=50)
    assert len(page['history']) == 4       # history serving is unaffected


def test_stale_base_revision_conflicts_and_attaches_current_envelope():
    session, ids = _session_with_blocks('cas')
    bookmarks.add_bookmark(session.id, ids[0])
    with pytest.raises(bookmarks.BookmarkConflict) as error:
        bookmarks.add_bookmark(session.id, ids[1], base_revision=0)
    current = error.value.current
    assert current['revision'] == 1 and set(current['bookmarks']) == {ids[0]}
    assert bookmarks.read_bookmarks(session.id)['revision'] == 1
    ok = bookmarks.add_bookmark(session.id, ids[1], base_revision=1)
    assert ok['changed'] is True and ok['revision'] == 2
    with pytest.raises(bookmarks.BookmarkConflict):
        bookmarks.remove_bookmark(session.id, ids[0], base_revision=1)


def test_invalid_base_revision_is_rejected_before_any_write():
    session, ids = _session_with_blocks('cas invalid')
    for bad in (True, '1', -1, 1.5):
        with pytest.raises(ValueError):
            bookmarks.add_bookmark(session.id, ids[0], base_revision=bad)
    assert not bookmarks._path(session.id).exists()


def test_disk_failure_rolls_back_without_touching_state(monkeypatch):
    session, ids = _session_with_blocks('disk full')
    bookmarks.add_bookmark(session.id, ids[0])
    path = bookmarks._path(session.id)
    before = path.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError('disk full')

    # A scoped context, not monkeypatch.undo(): undoing every patch would also
    # revert the storage-isolation fixture's SESSION_DIR.
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'replace', fail_replace)
        with pytest.raises(OSError):
            bookmarks.add_bookmark(session.id, ids[1])
    assert path.read_bytes() == before
    assert bookmarks.bookmarked_message_ids(session.id) == frozenset({ids[0]})
    assert bookmarks.add_bookmark(session.id, ids[1])['changed'] is True


def test_cache_observes_external_atomic_replacement():
    session, ids = _session_with_blocks('external')
    bookmarks.add_bookmark(session.id, ids[0])
    path = bookmarks._path(session.id)
    external = path.with_name('external-bookmarks.json')
    external.write_text(json.dumps({
        'version': 1, 'revision': 9, 'updatedAt': '2026-10-14T00:00:00',
        'bookmarks': {ids[1]: {'messageId': ids[1], 'createdAt': '2026-10-14T00:00:00'}},
    }), encoding='utf-8')
    external.replace(path)
    assert bookmarks.read_bookmarks(session.id)['revision'] == 9
    assert bookmarks.bookmarked_message_ids(session.id) == frozenset({ids[1]})


def test_oversized_set_is_refused_on_write_and_on_read():
    session, ids = _session_with_blocks('bounds')
    path = bookmarks._path(session.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    forged = {
        f'pan:{index:032x}': {'messageId': f'pan:{index:032x}', 'createdAt': 'x'}
        for index in range(bookmarks.MAX_BOOKMARKS_PER_SESSION + 1)
    }
    path.write_text(json.dumps({'version': 1, 'revision': 1, 'bookmarks': forged}), encoding='utf-8')
    with pytest.raises(bookmarks.BookmarkUnreadable):
        bookmarks.read_bookmarks(session.id)


def test_write_bound_rejects_one_bookmark_too_many(monkeypatch):
    session, ids = _session_with_blocks('bounds write')
    path = bookmarks._path(session.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    at_limit = {
        f'pan:{index:032x}': {'messageId': f'pan:{index:032x}', 'createdAt': 'x'}
        for index in range(bookmarks.MAX_BOOKMARKS_PER_SESSION)
    }
    path.write_text(json.dumps({'version': 1, 'revision': 1, 'bookmarks': at_limit}), encoding='utf-8')
    bookmarks._CACHE.pop(session.id, None)
    with pytest.raises(bookmarks.BookmarkTooMany):
        bookmarks.add_bookmark(session.id, ids[0])


# ---------------------------------------------------------------- concurrency


def test_concurrent_dual_clients_lose_no_update():
    session, ids = _session_with_blocks(
        'dual clients', roles=('user',) + ('assistant',) * 30)
    errors = []

    def worker(index):
        message_id = ids[index]
        try:
            for attempt in range(200):
                try:
                    envelope = bookmarks.read_bookmarks(session.id)
                    bookmarks.add_bookmark(session.id, message_id,
                                           base_revision=envelope['revision'])
                    return
                except bookmarks.BookmarkConflict:
                    continue
            errors.append('never converged')
        except Exception as exc:  # noqa: BLE001 - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    envelope = bookmarks.read_bookmarks(session.id)
    assert set(envelope['bookmarks']) == set(ids[:12])
    assert envelope['revision'] == 12


def test_concurrent_add_and_remove_converge_to_a_consistent_envelope():
    session, ids = _session_with_blocks(
        'add remove race', roles=('user',) + ('assistant',) * 10)
    for message_id in ids[:5]:
        bookmarks.add_bookmark(session.id, message_id)
    barrier = threading.Barrier(6)
    errors = []

    def worker(index):
        message_id = ids[index % 5]
        try:
            barrier.wait()
            for attempt in range(200):
                try:
                    envelope = bookmarks.read_bookmarks(session.id)
                    if index < 3:
                        bookmarks.add_bookmark(session.id, ids[5 + index],
                                               base_revision=envelope['revision'])
                    else:
                        bookmarks.remove_bookmark(session.id, message_id,
                                                  base_revision=envelope['revision'])
                    return
                except bookmarks.BookmarkConflict:
                    continue
            errors.append('never converged')
        except Exception as exc:  # noqa: BLE001 - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    envelope = bookmarks.read_bookmarks(session.id)
    # Threads 0-2 add ids[5..7]; threads 3-5 remove ids[3], ids[4], ids[0].
    # Every committed change is visible exactly once and the revision counts
    # exactly the number of applied writes: no lost or duplicated update.
    expected = set(ids[1:3]) | set(ids[5:8])
    assert set(envelope['bookmarks']) == expected
    assert envelope['revision'] == 5 + 6


# ------------------------------------------------------------- reimport/epoch


def _reimport_with_changed_block(session, ids, index):
    # A native reimport parses provider rows fresh: incoming rows carry no Pan
    # identity, so only native-id / unique-content matches survive.
    rows = copy.deepcopy(session.history)
    for row in rows:
        row.pop('messageId', None)
    rows[index]['content'] = 'edited by a native reimport'
    replacement = store.assign_pan_message_ids(rows, previous_history=session.history)
    store.replace_history(session, replacement)
    store.save_full(session)
    return [row['messageId'] for row in session.history]


def test_bookmark_dangles_after_ambiguous_reimport_and_is_reported_not_dropped():
    session, ids = _session_with_blocks('reimport ambiguous')
    bookmarks.add_bookmark(session.id, ids[1])
    new_ids = _reimport_with_changed_block(session, ids, 1)
    assert new_ids[1] != ids[1] and new_ids[0] == ids[0]

    dangling = bookmarks.dangling_bookmarks(session.id)
    assert dangling == [ids[1]]
    assert bookmarks.bookmarked_message_ids(session.id) == frozenset({ids[1]})
    # The stale key is kept on disk and never re-matched by content.
    assert set(_read_file(session.id)['bookmarks']) == {ids[1]}
    # It stays removable so the user can clean it up.
    removed = bookmarks.remove_bookmark(session.id, ids[1])
    assert removed['changed'] is True
    assert bookmarks.dangling_bookmarks(session.id) == []
    # The re-identified block is a fresh, verifiable target.
    assert bookmarks.add_bookmark(session.id, new_ids[1])['changed'] is True
    assert bookmarks.dangling_bookmarks(session.id) == []


def test_bookmark_survives_reimport_that_preserves_identity():
    session, ids = _session_with_blocks('reimport stable')
    bookmarks.add_bookmark(session.id, ids[1])
    rows = copy.deepcopy(session.history)     # same content: identity preserved
    for row in rows:
        row.pop('messageId', None)
    replacement = store.assign_pan_message_ids(rows, previous_history=session.history)
    store.replace_history(session, replacement)
    store.save_full(session)
    assert [row['messageId'] for row in session.history] == ids
    assert bookmarks.dangling_bookmarks(session.id) == []
    assert bookmarks.bookmarked_message_ids(session.id) == frozenset({ids[1]})


# ------------------------------------------------------- deletion and tickets


def test_session_delete_removes_the_bookmark_file():
    session, ids = _session_with_blocks('deleted')
    bookmarks.add_bookmark(session.id, ids[0])
    path = bookmarks._path(session.id)
    assert path.is_file()
    store.delete(session.id)
    assert not path.exists()
    with pytest.raises(bookmarks.BookmarkSessionMissing):
        bookmarks.read_bookmarks(session.id)
    with pytest.raises(bookmarks.BookmarkSessionMissing):
        bookmarks.add_bookmark(session.id, ids[0])
    with pytest.raises(bookmarks.BookmarkSessionMissing):
        bookmarks.remove_bookmark(session.id, ids[0])


def test_delete_serializes_against_inflight_writes():
    session, ids = _session_with_blocks('delete race')
    started = threading.Event()
    release = threading.Event()
    errors = []
    original = bookmarks._write_locked

    def slow_write(*args, **kwargs):
        started.set()
        release.wait(5)
        return original(*args, **kwargs)

    bookmarks._write_locked = slow_write

    def writer():
        try:
            bookmarks.add_bookmark(session.id, ids[0])
        except Exception as exc:  # noqa: BLE001 - surfaced through errors
            errors.append(repr(exc))

    thread = threading.Thread(target=writer)
    thread.start()
    started.wait(5)
    store.delete(session.id)          # must wait for the in-flight write
    release.set()
    thread.join(5)
    bookmarks._write_locked = original
    assert errors == []               # the write committed before deletion
    assert not bookmarks._path(session.id).exists()


def test_cancelled_session_save_ticket_does_not_disturb_bookmarks():
    session, ids = _session_with_blocks('ticket cancel')
    bookmarks.add_bookmark(session.id, ids[0])
    path = bookmarks._path(session.id)
    before = path.read_bytes()

    async def scenario():
        outcome = store.DurableOutcome()
        task = asyncio.create_task(store.save_async(session, outcome=outcome))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return outcome

    outcome = asyncio.run(scenario())
    # The verdict is observable: either the writer retired (succeeded) or the
    # future itself never ran (cancelled).  A caller may only treat
    # ``succeeded`` as committed.
    assert outcome.succeeded or outcome.cancelled
    assert path.read_bytes() == before
    assert bookmarks.add_bookmark(session.id, ids[1])['changed'] is True
    assert bookmarks.remove_bookmark(session.id, ids[0])['changed'] is True


def test_failed_session_save_leaves_bookmarks_intact(monkeypatch):
    session, ids = _session_with_blocks('ticket fail')
    bookmarks.add_bookmark(session.id, ids[0])
    path = bookmarks._path(session.id)
    before = path.read_bytes()

    def fail_save(_session, **_kwargs):
        raise OSError('metadata write failed')

    with monkeypatch.context() as scoped:
        scoped.setattr(store, '_save_body', fail_save)
        with pytest.raises(OSError):
            asyncio.run(store.save_async(session))
    assert path.read_bytes() == before
    assert bookmarks.bookmarked_message_ids(session.id) == frozenset({ids[0]})
    assert bookmarks.add_bookmark(session.id, ids[1])['changed'] is True

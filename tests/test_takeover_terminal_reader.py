"""A Windows metadata reader must not permanently lose a fast CLI terminal."""
import asyncio
import json
import sys

import pytest

from packages.core import session as sess, worker
from test_legal_offline import setup, begin, runtime_registry


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows delete sharing semantics')
def test_terminal_commit_survives_metadata_reader_release(monkeypatch):
    async def run():
        s, w = setup()
        await begin(s, w)
        w._current_seq = 1
        reader = sess._path(s.id).open('rb')
        replace = sess.os.replace
        conflicts = []
        def observed_replace(source, target):
            try:
                return replace(source, target)
            except PermissionError as exc:
                # Same ordinary read handle used by durable(runtime, sid).
                # Release it only after the actual kernel sharing conflict.
                conflicts.append(exc.winerror)
                reader.close()
                raise
        monkeypatch.setattr(sess.os, 'replace', observed_replace)
        try:
            terminal = await worker._persist_terminal_state(w, s, 'done', 'establish-native-session')
            assert conflicts == [5]
            assert terminal['result'] == 'establish-native-session'
            data = json.loads(sess._path(s.id).read_text(encoding='utf-8'))
            assert data['last_result']['status'] == 'done'
            assert data['result_cursor'] == 1 and data['legal_offline']
            assert w._terminal_handled
        finally:
            reader.close()
    asyncio.run(run())


def test_permanent_metadata_failure_keeps_terminal_rollback(monkeypatch):
    async def run():
        s, w = setup()
        await begin(s, w)
        before = sess._path(s.id).read_bytes()
        calls = []
        def denied(*args):
            calls.append(args)
            error = PermissionError('permanent sharing denial')
            error.winerror = 5
            raise error
        monkeypatch.setattr(sess.os, 'replace', denied)
        with pytest.raises(PermissionError, match='permanent sharing denial'):
            await worker._persist_terminal_state(w, s, 'done', 'not durable')
        assert len(calls) == (6 if sys.platform == 'win32' else 1)
        assert all(pair == calls[0] for pair in calls)
        assert sess._path(s.id).read_bytes() == before
        assert s.last_result is None and s.result_cursor == 0
        assert not w._terminal_handled and not s.legal_offline
    asyncio.run(run())


def test_nonsharing_metadata_failure_is_not_retried(monkeypatch):
    calls = []
    def fail(*args):
        calls.append(args)
        raise OSError('disk write failed')
    monkeypatch.setattr(sess.os, 'replace', fail)
    with pytest.raises(OSError, match='disk write failed'):
        sess._replace_session_metadata('owned.tmp', 'owned.json')
    assert len(calls) == 1

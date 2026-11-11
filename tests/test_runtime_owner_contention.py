"""Local owner contenders queue before SQLite's cross-process busy deadline."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from packages.jobs import runtime_owner


def test_same_profile_owner_instances_wait_before_opening_sqlite(tmp_path, monkeypatch):
    first, second = (runtime_owner.OwnerFence(tmp_path) for _ in range(2))
    original = runtime_owner.sqlite3.connect
    opened = threading.Event()
    def short_busy_timeout(*args, **kwargs):
        kwargs['timeout'] = .05
        opened.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime_owner.sqlite3, 'connect', short_busy_timeout)
    entered = threading.Event()
    def contender():
        entered.set()
        with second.transaction() as db:
            db.execute("INSERT INTO metadata VALUES('second','committed')")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with first.transaction() as db:
            db.execute("INSERT INTO metadata VALUES('first','committed')")
            opened.clear()
            future = pool.submit(contender)
            assert entered.wait(2)
            # A local waiter must not start its SQLite busy budget while the
            # current transaction still owns this profile's database.
            assert not opened.wait(.2)
        future.result(timeout=3)
    with first.transaction() as db:
        assert dict(db.execute('SELECT key,value FROM metadata')) == {
            'first': 'committed', 'second': 'committed'}


def test_nested_owner_transactions_share_rollback_and_release_local_waiters(tmp_path):
    first, second = (runtime_owner.OwnerFence(tmp_path) for _ in range(2))
    with first.transaction():
        pass
    with pytest.raises(RuntimeError, match='abort'):
        with first.transaction() as outer:
            with second.transaction() as inner:
                assert inner is outer
                inner.execute("INSERT INTO metadata VALUES('rolled-back','value')")
            raise RuntimeError('abort')
    with ThreadPoolExecutor(max_workers=1) as pool:
        def read():
            with second.transaction() as db:
                return db.execute('SELECT * FROM metadata').fetchall()
        assert pool.submit(read).result(timeout=3) == []

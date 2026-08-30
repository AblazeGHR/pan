"""Shared pytest isolation for repository-level tests.

Tests construct real ``Session`` objects and some exercise code paths that call
``save_async``.  Keep those writes inside pytest's temporary directory so a
test run cannot populate the application's real ``data/sessions`` store.  The
Pan service intentionally loads every JSON file in that store on startup, and
the global watchdog may then treat a test's ``queue_pending`` as real work.
"""

import pytest

from packages.core import session as _sess


@pytest.fixture(autouse=True)
def isolate_session_storage(tmp_path, monkeypatch):
    """Give each test a fresh Session directory and cache."""
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(_sess, "_all_loaded", False)
    _sess._cache.clear()
    yield
    _sess._cache.clear()

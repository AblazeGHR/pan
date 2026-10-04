"""Shared pytest isolation for repository-level tests.

Tests construct real ``Session`` objects and some exercise code paths that call
``save_async``.  Keep those writes inside pytest's temporary directory so a
test run cannot populate the application's real ``data/sessions`` store.  The
Pan service intentionally loads every JSON file in that store on startup, and
the global watchdog may then treat a test's ``queue_pending`` as real work.
"""

import os

import pytest

# Before application imports, remove settings inherited from a live Pan/Job.
# The explicit PAN_TEST_* namespace is reserved for test-only controls. Tests
# needing a PAN_* setting must set it locally with monkeypatch after bootstrap.
# Patching DEFAULT_ROOT alone is insufficient when an inherited env overrides it.
for _key in tuple(os.environ):
    if _key.upper().startswith("PAN_") and not _key.upper().startswith("PAN_TEST_"):
        os.environ.pop(_key)

from packages.core import session as _sess
from packages.core import worker as _worker
from packages.core import background_jobs as _jobs
from packages.core import config as _config
from packages.scheduler import store as _scheduler
from packages.core.terminal import registry as _terminal_registry


@pytest.fixture(autouse=True)
def isolate_session_storage(tmp_path, monkeypatch):
    """Default all application stores to test-local paths, not just Sessions.

    Narrower fixtures may override these paths, but must use owned temporary
    roots. This also protects direct pytest runs that bypass the wrapper script.
    """
    monkeypatch.setattr(_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(_jobs, "DEFAULT_ROOT", tmp_path / "background_jobs")
    monkeypatch.setattr(_scheduler, "DEFAULT_ROOT", tmp_path / "background_jobs")
    monkeypatch.setattr(_scheduler, "LEGACY_TASKS_ROOT", tmp_path / "scheduler")
    monkeypatch.setattr(_terminal_registry, "DEFAULT_REGISTRY_ROOT", tmp_path / "terminals")
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(_sess, "_all_loaded", False)
    # TestClient 等 lifespan 的 shutdown 段会调 drain_recoveries 置位
    # _shutdown_started；不复位会静默拦掉后续所有测试的 recovery 调度。
    monkeypatch.setattr(_worker, "_shutdown_started", False)
    _sess._cache.clear()
    yield
    _sess._cache.clear()

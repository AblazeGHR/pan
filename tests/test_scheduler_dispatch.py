"""R6-G: old dispatch module cannot activate or dispatch either owner."""
import importlib
import pytest


@pytest.mark.parametrize('entrypoint', ['start_loop', 'tick_once', 'run_now', '_ensure_registered',
                                       '_consume', '_emit', '_emit_strict', 'stop_loop', 'drain'])
def test_retired_dispatch_import_is_refused_before_worker_or_store(monkeypatch, entrypoint):
    from packages.core import worker
    from packages.scheduler.creators import CreatorStore
    def forbidden(*args, **kwargs):
        pytest.fail('Removed scheduler engine reached runtime or worker')
    monkeypatch.setattr(worker, 'assign', forbidden)
    monkeypatch.setattr(CreatorStore, '__init__', forbidden)
    with pytest.raises(ModuleNotFoundError) as exc:
        getattr(importlib.import_module('packages.scheduler.engine'), entrypoint)
    assert exc.value.name == 'packages.scheduler.engine'

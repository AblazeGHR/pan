"""Real owned startup shares one Inbox facade, retaining its FULL transactions."""
import inspect
import json
from pathlib import Path

from tests.test_real_queue_http_e2e import _PanRuntime


def test_startup_initializes_inbox_once_per_owned_profile(tmp_path, monkeypatch):
    module = inspect.getmodule(_PanRuntime)
    monkeypatch.setattr(module, 'SERVER_LAUNCHER',
                        Path(__file__).parent / 'support/inbox_startup_probe.py')
    # Separate profiles must not share a cache or skip each other's schema.
    for name in ('first', 'second'):
        runtime = _PanRuntime(tmp_path / name)
        try:
            runtime.start()
            rows = [json.loads(line) for line in
                    (runtime.data_root / 'inbox-initializations.jsonl').read_text(
                        encoding='utf-8').splitlines()]
            startup = [row for row in rows if row['startup']]
            assert len(startup) == 1
            assert all(Path(row['path']) == runtime.data_root / 'user_inbox/cards.sqlite'
                       for row in rows)
        finally:
            runtime.stop()

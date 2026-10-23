import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import pytest
from packages.automation.store import Ledger
from pathlib import Path

def old_release(self, event_id):
    def change(state):
        for run in state['runs'].values():
            if run['event']['id'] == event_id:
                run['admitted'] = True
                run['revision'] += 1
    self.mutate(change)
Ledger.release_event = old_release
code=pytest.main(['tests/test_event_store.py','-k','pending_old_plugin or empty_event_release','--junitxml=.validation/ci39976748349/red-backlog.xml','-q'])
Path('.validation/ci39976748349/red-backlog.exit').write_text(str(code))
raise SystemExit(code)

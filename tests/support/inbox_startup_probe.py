"""Count real Inbox initialization transactions in an owned HTTP runtime."""
import json
import os
import traceback
from pathlib import Path

from packages.interactions.store import InteractionStore

root = Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
root.mkdir(parents=True, exist_ok=True)
initialize = InteractionStore.__init__


def observed(self, *args, **kwargs):
    initialize(self, *args, **kwargs)
    with (root / 'inbox-initializations.jsonl').open('a', encoding='utf-8') as output:
        output.write(json.dumps({'path': str(self.path.resolve()),
                                 'startup': any(frame.name == '_owned_lifespan'
                                                for frame in traceback.extract_stack())}) + '\n')


InteractionStore.__init__ = observed

from tests.support.isolated_http_server import main

main()

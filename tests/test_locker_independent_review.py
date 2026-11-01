"""Independent safety barriers. Red assertions deliberately remain blocking."""
import asyncio
import json
import hashlib
import shutil
from unittest.mock import AsyncMock

import pytest

from packages.automation.service import Service
from packages.core import worker, session
from packages.interactions.trusted import create as create_bound
from packages.interactions.contracts import InteractionError
from packages.interactions.runtime import bind
from packages.interactions.store import InteractionStore, UserPrincipal
from tests.test_automation_core import action, trigger, until


def test_old_user_and_unknown_recipient_copy_migration(tmp_path):
    source = InteractionStore(tmp_path/'original.sqlite')
    cards = []
    for key, recipient in [('old-open', 'user'), ('old-completed', 'user'), ('foreign', 'unmapped')]:
        cards.append(source.create({'ownerSessionId': 'source', 'recipientId': recipient, 'requestKey': key,
            'card': {'title': key, 'body': 'body', 'answerInput': {}}}))
    source.respond(UserPrincipal('user'), cards[1]['id'], {'requestId': 'old', 'expectedRevision': 0, 'answer': 'done'})
    with source.db() as db:
        db.execute('DROP TABLE response_outbox')
        original_rows = [tuple(row) for row in db.execute('SELECT * FROM cards ORDER BY id')]
    digest = hashlib.sha256(source.path.read_bytes()).hexdigest()
    shutil.copy2(source.path, tmp_path/'copy.sqlite')
    migrated = InteractionStore(tmp_path/'copy.sqlite')
    assert {c['id'] for c in migrated.list_for_user(UserPrincipal('user'))} == {cards[0]['id'], cards[1]['id']}
    assert migrated.get_for_user(UserPrincipal('unmapped'), cards[2]['id'])['recipientId'] == 'unmapped'
    with migrated.db() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM cards ORDER BY id')] == original_rows
        assert db.execute('SELECT count(*) FROM response_outbox').fetchone()[0] == 0
    assert hashlib.sha256(source.path.read_bytes()).hexdigest() == digest

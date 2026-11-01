"""Product UserAuth -> directed EventStore/Engine -> safe local Pause."""
import asyncio
import json
import hashlib
import shutil
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from packages.automation.service import Service
from packages.interactions.api import register, get_store
from packages.interactions.runtime import bind
from packages.interactions.store import UserPrincipal, InteractionStore
from packages.core import session, worker
from tests.test_automation_core import action, trigger, until
from packages.interactions.trusted import create as create_bound
from packages.interactions.contracts import InteractionError
import pytest




def test_notification_crash_reconciles_only_and_migration_preserves_foreign_recipient(tmp_path):
    async def run():
        original=InteractionStore(tmp_path/'legacy.sqlite')
        card=original.create({'ownerSessionId':'owner','recipientId':'legacy-alias','requestKey':'old',
                           'card':{'title':'legacy','body':'body','answerInput':{}}})
        with original.db() as db:
            db.execute('DROP TABLE response_outbox')  # Pre-wiring database shape.
            old_row=tuple(db.execute('SELECT * FROM cards').fetchone())
        digest=hashlib.sha256(original.path.read_bytes()).hexdigest()
        shutil.copy2(original.path,tmp_path/'copy.sqlite')
        store=InteractionStore(tmp_path/'copy.sqlite')
        with store.db() as db:
            assert tuple(db.execute('SELECT * FROM cards').fetchone())==old_row
        assert store.list_for_user(UserPrincipal('user'))==[]
        store.respond(UserPrincipal('legacy-alias'),card['id'],{'requestId':'r','expectedRevision':0,'answer':'private'})
        receipts={};calls=[]
        class Queue:
            async def put(self,sid,qid,message):
                calls.append(qid);receipts[qid]={'status':'accepted'}
                raise RuntimeError('after durable Queue commit')
            async def lookup(self,sid,qid):return receipts.get(qid)
        await store.deliver_responses(Queue())
        reopened=InteractionStore(store.path)
        await reopened.deliver_responses(Queue())
        await reopened.deliver_responses(Queue())
        assert len(calls)==1
        with reopened.db() as db:
            assert db.execute('SELECT state,payload FROM response_outbox').fetchone()[:]==('accepted',None)
        assert reopened.list_for_user(UserPrincipal('user'))==[]
        assert hashlib.sha256(original.path.read_bytes()).hexdigest()==digest
    asyncio.run(run())

"""Owned HTTP probe auth/Trigger ports; neither is a real user/provider login."""
import json
import os
import sys
from pathlib import Path
import sqlite3
from packages.web import server
from packages.interactions.store import UserPrincipal
from packages.interactions.bridge import TriggerReferenceAdapter
from packages.interactions.contracts import canonical,identity

root=Path(sys.argv[sys.argv.index('--data-root')+1]).resolve()
root.mkdir(parents=True,exist_ok=True)
receipt_path=root/'synthetic-trigger.sqlite'
with sqlite3.connect(receipt_path) as db:
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,event TEXT,receipt TEXT)')


def principal(request):
    token=os.environ['PAN_TEST_INTERACTION_TOKEN']
    return UserPrincipal('test-user') if request.headers.get('authorization')=='Bearer '+token else None


def submit(event,trigger_id,*,expected_revision):
    assert trigger_id=='synthetic-trigger' and expected_revision==1
    result={'status':'accepted','receiptId':identity('receipt_',event['id'])}
    with sqlite3.connect(receipt_path) as db:
        previous=db.execute('SELECT event FROM receipts WHERE id=?',(event['id'],)).fetchone()
        assert previous is None or previous[0]==canonical(event)
        db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?)',(event['id'],canonical(event),canonical(result)))
    return result


def lookup(event_id):
    with sqlite3.connect(receipt_path) as db:
        row=db.execute('SELECT receipt FROM receipts WHERE id=?',(event_id,)).fetchone()
    return json.loads(row[0]) if row else None

server.app.state.user_inbox_principal=principal
server.app.state.interaction_trigger_adapter=TriggerReferenceAdapter(
    get_trigger=lambda tid:{'id':tid,'revision':1,'enabled':True} if tid=='synthetic-trigger' else None,
    authorize=lambda event,trigger:event['payload']['recipientId']=='test-user',submit=submit,lookup=lookup)

@server.app.get('/__test/interaction-identity')
async def identity_route():
    from packages.interactions.api import get_store
    return {'pid':os.getpid(),'cardPath':str(get_store().path),'receiptPath':str(receipt_path),
            'auth':'synthetic-test-only','trigger':'synthetic-test-only'}


@server.app.post('/__test/interaction-trigger')
async def prepare_trigger(data:dict):
    # The synthetic transport still uses the production fixed-choice binding.
    # Install real definitions for the owned Session instead of bypassing the
    # resolver/verification introduced by the Locker trusted-choice contract.
    from tests.test_automation_core import action, trigger
    service=server.app.state.automation
    definition=action([data['ownerSessionId']])
    service.engine.actions.put('actions',definition)
    service.ledger.put({**trigger('synthetic-trigger'),
        'eventTypes':['pan.interaction.responded']},0,authorized=True)
    return {'ok':True}

# Reuse the existing fully isolated lifecycle and empty QQ gateway manifest.
import tests.support.agent_inbox_http_server

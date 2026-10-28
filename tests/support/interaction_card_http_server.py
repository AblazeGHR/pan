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
    header = request.headers.get('authorization', '')
    # The browser now uses the shared local-key login. Retain the synthetic
    # recipient mapping while requiring a real owned-profile auth session.
    authorized = server.app.state.automation.auth.authorized(header)
    return UserPrincipal('test-user') if header=='Bearer '+token or authorized else None


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
    from unittest.mock import patch
    from packages.jobs.final_service import object_ref
    service=server.app.state.automation
    host=service.final_jobs
    actor={'type':'user','id':'local'}
    action=next(a for a in host.actions(actor=actor)['items'] if a['id']=='product_session_message_v2')
    def enable(kind,item):
        return host.control(kind,item['id'],{'operation':'enable','expectedRevision':item.get('configRevision',item.get('revision')),
            'expectedStateRevision':item['stateRevision'],'requestKey':'enable-'+item['id'],'parameters':{}},actor=actor)['result']['object']
    job=host.write('job',None,{'definition':{'name':'Owned Card Job','steps':[{'stepId':'send',
        'actionRef':object_ref('action',action),'arguments':{'sessionId':{'from':'literal','value':data['ownerSessionId']},
        'text':{'from':'literal','value':'Owned Card response'}},'completion':{'contract':'durable-queue-accepted'}}],
        'retention':{'policyRef':'test'}},'expectedRevision':0,'requestKey':'card-job'},actor=actor)['result']['object']
    enable('job',job)
    # Preserve the original HTTP test's fixed public Trigger identity while
    # exercising the real native create/enable paths. Only ID allocation is
    # deterministic; no definition or owner fence is bypassed.
    from packages.jobs.final_service import new_id
    def allocate(prefix):
        return 'synthetic-trigger' if prefix=='trigger_' else new_id(prefix)
    with patch('packages.jobs.final_service.new_id',side_effect=allocate):
        trigger=host.write('trigger',None,{'definition':{'name':'Owned Card Trigger','eventTypes':['pan.interaction.responded'],
            'creatorSelector':{'mode':'registeredType','providerType':'core'},'targetJobId':job['id']},
            'expectedRevision':0,'requestKey':'card-trigger'},actor=actor)['result']['object']
    enable('trigger',trigger)
    from packages.interactions import runtime
    runtime.LOCAL_RECIPIENT='test-user'  # Existing test-only recipient mapping.
    adapter=runtime.bind(service)
    production_submit=adapter.submit
    async def record(event,tid,*,expected_revision):
        result=await production_submit(event,tid,expected_revision=expected_revision)
        if result.get('status')=='accepted':
            with sqlite3.connect(receipt_path) as db:
                previous=db.execute('SELECT event FROM receipts WHERE id=?',(event['id'],)).fetchone()
                assert previous is None or previous[0]==canonical(event)
                db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?)',(event['id'],canonical(event),canonical(result)))
        return result
    adapter.submit=record
    server.app.state.interaction_trigger_adapter=adapter
    return {'ok':True}

# Reuse the existing fully isolated lifecycle and empty QQ gateway manifest.
import tests.support.agent_inbox_http_server

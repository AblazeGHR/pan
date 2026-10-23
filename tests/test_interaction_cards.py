"""Deterministic response, concurrency, failure and compatibility contracts."""
import asyncio
import concurrent.futures
import pytest
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.contracts import InteractionError, validate


def request(**definition):
    return dict(ownerSessionId='ses_owner', recipientId='user', requestKey='request',
                card={'title':'Question', 'body':'body', **definition})


def response(**data):
    return {'requestId':'once', 'expectedRevision':0, **data}


def test_creation_and_one_response_retry_conflict(tmp_path):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    first = store.create(request(answerInput={}))
    assert store.create(request(answerInput={})) == first
    with pytest.raises(InteractionError, match='conflict'):
        store.create(request(body='changed'))
    user = UserPrincipal('user')
    done = store.respond(user, first['id'], response(answer='hello'))
    assert done['status'] == 'responded'
    assert store.respond(user, first['id'], response(answer='hello')) == done
    with pytest.raises(InteractionError):
        store.respond(user, first['id'], response(answer='different'))
    with pytest.raises(InteractionError):
        store.respond(user, first['id'], {**response(answer='hello'), 'requestId':'second'})
    assert store.get_for_owner('ses_owner', first['id'])['response']['answer'] == 'hello'


def test_optional_inputs_and_no_implicit_approval(tmp_path):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    user = UserPrincipal('user')
    read_only = store.create(request())
    with pytest.raises(InteractionError):
        store.respond(user, read_only['id'], response())
    choice = store.create({**request(choices=[{'id':'yes','label':'Yes'}]), 'requestKey':'choice'})
    btw_card = store.create({**request(choices=[{'id':'yes','label':'Yes'}]), 'requestKey':'btw'})
    btw = store.respond(user, btw_card['id'], response(answer='A note without selecting an action'))
    assert btw['response']['answer'] == 'A note without selecting an action'
    assert btw['delivery']['state'] == 'not_requested'
    assert 'triggerRef' not in store.event_for_owner('ses_owner', btw_card['id'])['payload']
    with pytest.raises(InteractionError):
        store.respond(user, choice['id'], response(choiceId='unknown'))
    done = store.respond(user, choice['id'], response(choiceId='yes'))
    assert done['delivery']['state'] == 'not_requested'
    both = store.create({**request(answerInput={}, choices=[{'id':'no','label':'No'}]), 'requestKey':'both'})
    assert store.respond(user, both['id'], response(answer='',choiceId='no'))['response']['answer'] == ''


def test_identity_and_optimistic_revision(tmp_path):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    card = store.create(request(answerInput={}))
    assert store.list_for_user(UserPrincipal('other')) == []
    with pytest.raises(InteractionError):
        store.respond(UserPrincipal('other'), card['id'], response(answer='wrong user'))
    with pytest.raises(InteractionError):
        store.respond(UserPrincipal('user'), card['id'], {**response(answer='stale'),'expectedRevision':1})
    with pytest.raises(InteractionError):
        store.get_for_owner('ses_other', card['id'])
    assert store.get_for_user(UserPrincipal('user'), card['id'])['response'] is None


def test_two_store_instances_atomic_response(tmp_path):
    a = InteractionStore(tmp_path / 'cards.sqlite')
    b = InteractionStore(tmp_path / 'cards.sqlite')
    card = a.create(request(answerInput={}))
    def submit(store, answer):
        try:
            return store.respond(UserPrincipal('user'), card['id'], response(answer=answer))['status']
        except InteractionError:
            return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(submit, a, 'a'), pool.submit(submit, b, 'b')]
        assert sorted(f.result() for f in futures) == ['conflict','responded']


class Adapter:
    def __init__(self):
        self.rows = {}
        self.calls = 0
        self.crash = False
        self.reject = False
    async def authorize(self, event):
        return True
    async def lookup(self, event_id):
        return self.rows.get(event_id)
    async def accept(self, event):
        self.calls += 1
        if self.reject:
            return {'status':'rejected'}
        self.rows[event['id']] = {'status':'accepted', 'receiptId':'receipt'}
        if self.crash:
            raise asyncio.CancelledError()
        return self.rows[event['id']]


def pending(tmp_path):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    card = store.create(request(choices=[{'id':'go','label':'Go','triggerRef':{'id':'trigger','revision':1}}]))
    done = store.respond(UserPrincipal('user'), card['id'], response(choiceId='go'))
    assert done['delivery']['state'] == 'pending'
    return store, done


def test_optional_trigger_unavailable_then_durable_acceptance(tmp_path):
    store, done = pending(tmp_path)
    user = UserPrincipal('user')
    assert asyncio.run(store.dispatch(user,done['id'],None))['delivery']['state'] == 'unavailable'
    adapter = Adapter()
    accepted = asyncio.run(store.dispatch(user,done['id'],adapter))
    assert accepted['delivery'] == {'state':'accepted','receiptId':'receipt'}
    asyncio.run(store.dispatch(user,done['id'],adapter))
    assert adapter.calls == 1
    validate('Event',store.event_for_owner('ses_owner',done['id']))


def test_crash_after_adapter_accept_and_cold_reconcile(tmp_path):
    store, done = pending(tmp_path)
    user, adapter = UserPrincipal('user'), Adapter()
    adapter.crash = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(store.dispatch(user,done['id'],adapter))
    cold = InteractionStore(tmp_path / 'cards.sqlite')
    assert asyncio.run(cold.dispatch(user,done['id'],adapter))['delivery']['state'] == 'accepted'
    assert adapter.calls == 1


def test_unknown_no_blind_retry_and_rejected_can_retry(tmp_path):
    store, done = pending(tmp_path)
    user, adapter = UserPrincipal('user'), Adapter()
    adapter.crash = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(store.dispatch(user,done['id'],adapter))
    adapter.rows.clear()
    adapter.crash = False
    assert asyncio.run(store.dispatch(user,done['id'],adapter))['delivery']['state'] == 'unknown'
    assert adapter.calls == 1


def test_rejected_and_authorization_are_not_execution(tmp_path):
    store, done = pending(tmp_path)
    user, adapter = UserPrincipal('user'), Adapter()
    adapter.reject = True
    assert asyncio.run(store.dispatch(user,done['id'],adapter))['delivery']['state'] == 'rejected'
    adapter.reject = False
    assert asyncio.run(store.dispatch(user,done['id'],adapter))['delivery']['state'] == 'accepted'
    assert adapter.calls == 2

def test_job_compatibility_preserves_ids_and_never_executes(tmp_path):
    from packages.interactions.bridge import create_job_card,job_response,job_reference
    store=InteractionStore(tmp_path/'cards.sqlite')
    snapshot={'jobId':'job_native','taskId':'sch_old','status':'running','command':['must-not-run'],
              'creatorSessionId':'ses_owner'}
    original=dict(snapshot)
    allowed=lambda job,owner:job.get('creatorSessionId')==owner
    card=create_job_card(store,snapshot,owner_session_id='ses_owner',recipient_id='user',interaction_key='step',
                         card={'title':'Continue?','body':'','answerInput':{}},authorize=allowed,run_id='run1')
    assert snapshot==original
    ref=card['definition']['correlation']['job']
    assert ref=={'jobId':'job_native','legacyTaskId':'sch_old','executionOwner':'legacy-registry','runId':'run1'}
    assert 'command' not in str(card)
    assert job_response(card)['status']=='waiting_response'
    done=store.respond(UserPrincipal('user'),card['id'],response(answer='answer'))
    assert job_response(done)['status']=='response_recorded' and snapshot['status']=='running'
    with pytest.raises(InteractionError):
        create_job_card(store,snapshot,owner_session_id='ses_other',recipient_id='user',interaction_key='step',
                         card={'title':'x','body':''},authorize=allowed)
    assert job_reference({'taskId':'sch_old'})['jobId']=='sch_old'
    assert job_reference({'jobId':'job_remote','executionOwner':'independent-job-service'})['executionOwner']=='independent-job-service'


def test_directed_trigger_revision_and_permission_recheck(tmp_path):
    from packages.interactions.bridge import TriggerReferenceAdapter
    store,card=pending(tmp_path)
    trigger={'id':'trigger','revision':1,'enabled':True}
    calls=[]
    async def submit(event,trigger_id,expected_revision):
        calls.append((event,trigger_id,expected_revision))
        return {'status':'accepted','receiptId':'directed'}
    adapter=TriggerReferenceAdapter(get_trigger=lambda tid:dict(trigger),authorize=lambda event,t:True,
                                    submit=submit,lookup=lambda event_id:None)
    trigger['enabled']=False
    with pytest.raises(InteractionError):
        asyncio.run(store.dispatch(UserPrincipal('user'),card['id'],adapter))
    assert not calls
    trigger['enabled']=True
    trigger['revision']=2
    with pytest.raises(InteractionError):
        asyncio.run(store.dispatch(UserPrincipal('user'),card['id'],adapter))
    assert not calls
    trigger['revision']=1
    assert asyncio.run(store.dispatch(UserPrincipal('user'),card['id'],adapter))['delivery']['receiptId']=='directed'
    assert calls[0][1:] == ('trigger',1)
    assert calls[0][0]['payload']['triggerRef']=={'id':'trigger','revision':1}


def test_typed_user_router_opt_in_does_not_change_locker_default(tmp_path):
    from packages.core.agent_inbox import Inbox,Router,InboxError
    from packages.interactions.bridge import UserInboxRouterAdapter
    store=InteractionStore(tmp_path/'cards.sqlite')
    box=Inbox(tmp_path/'locker.sqlite',None)
    with pytest.raises(InboxError,match='unsupported'):
        asyncio.run(Router(box).route({'kind':'user_inbox'},'id',{'text':'old'}))
    router=Router(box,user_inbox=UserInboxRouterAdapter(store,'ses_owner',lambda owner:True))
    result=asyncio.run(router.route({'kind':'user_inbox','recipientId':'user'},'id',
                                   {'interactionCard':{'title':'typed','body':'hello'}}))
    assert result['status']=='stored' and box.list()==[]
    with pytest.raises(InteractionError):
        asyncio.run(router.route({'kind':'user_inbox','recipientId':'user'},'other',{'text':'not a Card'}))


def test_capacity_schema_payload_and_card_response_rollback(tmp_path):
    store=InteractionStore(tmp_path/'cards.sqlite',capacity=1)
    card=store.create(request(answerInput={'maxLength':2}))
    with pytest.raises(InteractionError,match='capacity'):
        store.create({**request(),'requestKey':'second'})
    assert store.create(request(answerInput={'maxLength':2}))['id']==card['id']
    with pytest.raises(InteractionError):
        store.respond(UserPrincipal('user'),card['id'],response(answer='long'))
    assert store.get_for_user(UserPrincipal('user'),card['id'])['revision']==0
    with pytest.raises(RuntimeError):
        with store.db() as db:
            db.execute('UPDATE cards SET revision=55 WHERE id=?',(card['id'],))
            raise RuntimeError('fault before commit')
    assert InteractionStore(store.path).get_for_user(UserPrincipal('user'),card['id'])['revision']==0
    with pytest.raises(InteractionError):
        store.create(request(choices=[{'id':'x','label':'x'},{'id':'x','label':'again'}]))
    with pytest.raises(InteractionError):
        store.create(request(notification={'url':'https://not-executable.invalid'}))


def test_parallel_dispatch_claims_exactly_one_submission(tmp_path):
    a,card=pending(tmp_path)
    b=InteractionStore(a.path)
    user=UserPrincipal('user')
    adapter=Adapter()
    async def race():
        entered,release=asyncio.Event(),asyncio.Event()
        original=adapter.accept
        async def delayed(event):
            entered.set()
            await release.wait()
            return await original(event)
        adapter.accept=delayed
        task=asyncio.create_task(a.dispatch(user,card['id'],adapter))
        await entered.wait()
        assert (await b.dispatch(user,card['id'],adapter))['delivery']['state']=='unknown'
        release.set()
        await task
    asyncio.run(race())
    assert adapter.calls==1
    assert a.get_for_user(user,card['id'])['delivery']['state']=='accepted'

def test_user_scoped_cursor_never_hides_later_cards_or_leaks(tmp_path):
    store=InteractionStore(tmp_path/'cards.sqlite')
    first=store.create(request())
    second=store.create({**request(),'requestKey':'second'})
    hidden=store.create({**request(),'recipientId':'other','requestKey':'hidden'})
    user=UserPrincipal('user')
    assert [c['id'] for c in store.list_for_user(user,limit=1)]==[first['id']]
    assert [c['id'] for c in store.list_for_user(user,after=first['id'])]==[second['id']]
    with pytest.raises(InteractionError):
        store.list_for_user(user,after=hidden['id'])

def test_response_event_transaction_rolls_back_together_on_fault(tmp_path,monkeypatch):
    from contextlib import contextmanager
    store=InteractionStore(tmp_path/'cards.sqlite')
    card=store.create(request(answerInput={}))
    original=store.db
    @contextmanager
    def interrupted():
        with original() as db:
            yield db
            raise RuntimeError('interrupted before SQLite commit')
    monkeypatch.setattr(store,'db',interrupted)
    with pytest.raises(RuntimeError):
        store.respond(UserPrincipal('user'),card['id'],response(answer='must rollback'))
    cold=InteractionStore(store.path)
    assert cold.get_for_user(UserPrincipal('user'),card['id'])['response'] is None
    assert cold.get_for_user(UserPrincipal('user'),card['id'])['revision']==0
    assert cold.event_for_owner('ses_owner',card['id']) is None

def test_unavailable_port_cannot_erase_another_instances_inflight_claim(tmp_path,monkeypatch):
    a,card=pending(tmp_path)
    b=InteractionStore(a.path)
    original=a._settle
    def concurrent_claim(card_id,result):
        with b.db() as db:
            db.execute('UPDATE cards SET delivery=? WHERE id=?',('{"state":"in_flight"}',card_id))
        original(card_id,result)
    monkeypatch.setattr(a,'_settle',concurrent_claim)
    user=UserPrincipal('user')
    assert asyncio.run(a.dispatch(user,card['id'],None))['delivery']['state']=='in_flight'
    adapter=Adapter()
    assert asyncio.run(b.dispatch(user,card['id'],adapter))['delivery']['state']=='unknown'
    assert adapter.calls==0

"""Opt-in Job projection and a directed Trigger port, with no executor fallback."""
import inspect
from .contracts import InteractionError, identity, validate


async def resolve(value):
    return await value if inspect.isawaitable(value) else value


def job_reference(snapshot, *, run_id=None, step_id=None):
    """Strip a trusted existing Job snapshot to correlation, never an Action spec."""
    if not isinstance(snapshot,dict):
        raise InteractionError('invalid','Job snapshot required')
    job_id=snapshot.get('jobId') or snapshot.get('taskId')
    owner=snapshot.get('executionOwner','legacy-registry')
    ref={'jobId':job_id,'executionOwner':owner}
    if snapshot.get('taskId'):
        ref['legacyTaskId']=snapshot['taskId']
    if run_id is not None:
        ref['runId']=run_id
    if step_id is not None:
        ref['stepId']=step_id
    validate('JobRef',ref)
    return ref


def create_job_card(store,snapshot,*,owner_session_id,recipient_id,interaction_key,card,
                    authorize,run_id=None,step_id=None):
    """The caller supplies Job authorization; Job IDs/createdBy are not authority."""
    if not callable(authorize) or authorize(snapshot,owner_session_id) is not True:
        raise InteractionError('forbidden','Job interaction request not authorized')
    validate('Id',interaction_key)
    ref=job_reference(snapshot,run_id=run_id,step_id=step_id)
    definition=dict(card)
    correlation=dict(definition.get('correlation',{}))
    if 'job' in correlation and correlation['job']!=ref:
        raise InteractionError('conflict','Job correlation conflict')
    correlation['job']=ref
    definition['correlation']=correlation
    key=identity('job_interaction_',[ref,interaction_key])
    return store.create(dict(ownerSessionId=owner_session_id,recipientId=recipient_id,requestKey=key,card=definition))


def job_response(card):
    """Compatibility waiter view: response recorded does not mean Job resumed."""
    validate('Card',card)
    return {'cardId':card['id'],'job':card['definition'].get('correlation',{}).get('job'),
            'status':'response_recorded' if card['response'] is not None else 'waiting_response',
            'response':card['response'],'triggerDelivery':card['delivery']}


class TriggerReferenceAdapter:
    """Directed runtime port. submit must atomically enforce the supplied revision.

    No generic Event Bus broadcast: that could match unrelated Triggers. The
    destination runtime owns enabled state, execution policy and target gates.
    Callbacks are registered Python code, never client URLs/commands.
    """
    def __init__(self,*,get_trigger,authorize,submit,lookup):
        self.get_trigger,self.policy,self.submit,self.find_receipt=get_trigger,authorize,submit,lookup

    async def _target(self,event):
        validate('Event',event)
        ref=event['payload'].get('triggerRef')
        if not ref:
            return None
        trigger=await resolve(self.get_trigger(ref['id']))
        if (not isinstance(trigger,dict) or trigger.get('id')!=ref['id'] or trigger.get('enabled') is not True
                or type(trigger.get('revision')) is not int or trigger['revision']<1
                or ('revision' in ref and trigger['revision']!=ref['revision'])):
            return None
        if await resolve(self.policy(event,trigger)) is not True:
            return None
        return trigger

    async def authorize(self,event):
        return await self._target(event) is not None

    async def accept(self,event):
        target=await self._target(event)  # recheck before the runtime's atomic admission
        if target is None:
            return {'status':'rejected'}
        return await resolve(self.submit(event,target['id'],expected_revision=target['revision']))

    async def lookup(self,event_id):
        return await resolve(self.find_receipt(event_id))


class UserInboxRouterAdapter:
    """Opt-in typed Card destination; caller binds trusted producer Session."""
    def __init__(self,store,owner_session_id,authorize):
        self.store,self.owner,self.authorize=store,owner_session_id,authorize

    async def route(self,target,source_message_id,content):
        if await resolve(self.authorize(self.owner)) is not True:
            raise InteractionError('forbidden','Producer not authorized')
        if set(target)!={'kind','recipientId'} or target['kind']!='user_inbox' or set(content)!={'interactionCard'}:
            raise InteractionError('invalid','User Inbox requires an explicit typed Interaction Card')
        card=self.store.create(dict(ownerSessionId=self.owner,recipientId=target['recipientId'],
                                    requestKey=source_message_id,card=content['interactionCard']))
        return {'status':'stored','card':card}

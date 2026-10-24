"""Complete Job configuration and reference transactions in the Action registry.

No Job execution loop lives here. Cross-file creation uses durable intents.
"""
import copy
import hashlib
import json
import uuid

import jsonschema

from packages.actions.store import validate_action

MAX_TEMPLATES = 256
MAX_RECEIPTS = 10000
MAX_SNAPSHOTS = 128
MAX_DOCUMENT_BYTES = 1024 * 1024

POLICY_DEFAULTS={'trigger':{'kind':'schedule'},
    'recovery':{'enabled':False,'monitoring':False,'maxAttempts':3,'backoffSec':[5,15,60],'stableResetSec':300},
    'overlap':{'mode':'forbid','parallelLimit':3},
    'notification':{'on':['completed','failed','cancelled','unknown','uncertain','cleaning'],'target':'job'},
    'completion':{'kind':'ordinary'}}


def conservative_config(raw):
    result=copy.deepcopy(raw)
    for key,value in POLICY_DEFAULTS.items():result.setdefault(key,copy.deepcopy(value))
    return result


class Conflict(ValueError):
    pass


class CreationPending(RuntimeError):
    def __init__(self,job_id,error):
        super().__init__('Job creation pending; reuse the same key: '+job_id+'; '+str(error))
        self.job_id=job_id


class ReferenceError(ValueError):
    def __init__(self,action_id,position,message):
        super().__init__(message)
        self.action_id,self.position=action_id,position


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def strict(value, fields, scope):
    if not isinstance(value, dict) or set(value)-set(fields):
        raise ValueError('Unknown or invalid '+scope+' fields')
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_DOCUMENT_BYTES:
        raise ValueError(scope+' exceeds 1 MiB')


def revision(actual, expected):
    if type(expected) is not int or expected != actual:
        raise Conflict('Revision changed; reload before confirming')


def filled_arguments(action, value):
    if not isinstance(value, dict) or set(value)-set(action['input_schema'].get('properties', {})):
        raise ValueError('arguments contains undeclared Action fields')
    result = copy.deepcopy(value)
    for key, spec in action['input_schema'].get('properties', {}).items():
        if key not in result and isinstance(spec, dict) and 'default' in spec:
            result[key] = copy.deepcopy(spec['default'])
    try:jsonschema.Draft202012Validator(action['input_schema']).validate(result)
    except jsonschema.ValidationError as exc:raise ValueError('Invalid Action arguments') from exc
    return result


def check_action_graph(state, action, seen=()):
    aid = action['id']
    if aid in seen or len(seen) >= 8:
        raise ValueError('Job Template/Action reference cycle or depth exceeds 8')
    if action['execution']['kind'] != 'job_template':
        return
    ex = action['execution']
    template = state.get('jobTemplates', {}).get(ex['templateId'])
    if not template or template['revision'] != ex['templateRevision']:
        raise ReferenceError(aid,'/actions/'+aid+'/execution/templateId','Missing or changed fixed Job Template: '+ex['templateId'])
    target = state['actions'].get(template['config']['actionId'])
    if not target:
        target_id=template['config']['actionId']
        raise ReferenceError(target_id,'/jobTemplates/'+template['id']+'/config/actionId','Missing Action reference: '+target_id)
    filled_arguments(validate_action(target), template['config']['arguments'])
    check_action_graph(state, target, (*seen, aid))


def config_value(raw, state):
    strict(raw, {'name','description','actionId','arguments','schedule','trigger','recovery','overlap','notification','completion','enabled','maxRuns','misfirePolicy'}, 'Job config')
    value = copy.deepcopy(raw)
    if not isinstance(value.get('name'), str) or not value['name'].strip() or len(value['name']) > 200:
        raise ValueError('Job name required (1..200 characters)')
    value.setdefault('description', '')
    value.setdefault('arguments', {})
    value.setdefault('enabled', False)
    value.setdefault('maxRuns', None)
    value.setdefault('misfirePolicy', 'fire_now')
    value.setdefault('trigger', {'kind':'schedule'})
    value.setdefault('recovery', {'enabled':False,'monitoring':False,'maxAttempts':3,'backoffSec':[5,15,60],'stableResetSec':300})
    value.setdefault('overlap', {'mode':'forbid','parallelLimit':3})
    value.setdefault('notification', {'on':['completed','failed','cancelled','unknown','uncertain','cleaning'],'target':'job'})
    value.setdefault('completion', {'kind':'ordinary'})
    if not isinstance(value['description'], str) or type(value['enabled']) is not bool:
        raise ValueError('Invalid Job description or enabled')
    if value['maxRuns'] is not None and (type(value['maxRuns']) is not int or value['maxRuns'] < 1):
        raise ValueError('maxRuns must be positive integer or null')
    if value['misfirePolicy'] not in ('fire_now','skip'):
        raise ValueError('Invalid misfirePolicy')
    trigger=value['trigger']
    if not isinstance(trigger,dict) or set(trigger)-{'kind','query','eventTypes'} or trigger.get('kind') not in ('immediate','once','interval','cron','event','schedule'):
        raise ValueError('Invalid Job trigger')
    if trigger['kind']=='event':
        types=trigger.get('eventTypes')
        if not isinstance(types,list) or not 1<=len(types)<=32 or any(not isinstance(t,str) or not t or len(t)>200 for t in types) or len(set(types))!=len(types):
            raise ValueError('Event trigger requires 1..32 distinct eventTypes')
        if not isinstance(trigger.get('query'),dict):raise ValueError('Event trigger requires structured query')
        from packages.actions.session_query import validate_query
        validate_query(trigger['query'])
    elif set(trigger)-{'kind'}:
        raise ValueError('query/eventTypes are allowed only for event triggers')
    recovery=value['recovery']
    if (not isinstance(recovery,dict) or set(recovery)-{'enabled','monitoring','maxAttempts','backoffSec','stableResetSec'}
            or type(recovery.get('enabled')) is not bool or type(recovery.get('monitoring')) is not bool
            or type(recovery.get('maxAttempts')) is not int or not 1<=recovery['maxAttempts']<=10
            or type(recovery.get('stableResetSec')) is not int or not 0<=recovery['stableResetSec']<=86400
            or not isinstance(recovery.get('backoffSec'),list) or len(recovery['backoffSec'])!=3
            or any(type(v) is not int or not 0<=v<=86400 for v in recovery['backoffSec'])):
        raise ValueError('Invalid recovery policy')
    if recovery['enabled'] and not recovery['monitoring']:
        raise ValueError('Recovery is allowed only for monitoring Actions')
    completion=value['completion']
    if not isinstance(completion,dict) or set(completion)-{'kind','signalType'} or completion.get('kind') not in ('ordinary','monitoring'):
        raise ValueError('Invalid completion contract')
    if ('signalType' in completion or completion['kind']=='monitoring') and (not isinstance(completion.get('signalType'),str) or not 1<=len(completion['signalType'])<=128):
        raise ValueError('Completion signalType requires 1..128 characters')
    if completion['kind']=='monitoring' and not recovery['monitoring']:
        raise ValueError('Monitoring completion requires monitoring recovery declaration')
    overlap=value['overlap']
    if (not isinstance(overlap,dict) or set(overlap)-{'mode','parallelLimit'}
            or overlap.get('mode') not in ('forbid','queue','parallel')
            or type(overlap.get('parallelLimit')) is not int or not 1<=overlap['parallelLimit']<=32):
        raise ValueError('Invalid overlap policy')
    if overlap['mode']=='parallel' and recovery['monitoring']:
        raise ValueError('Monitoring Actions cannot use parallel overlap')
    notification=value['notification']
    if (not isinstance(notification,dict) or set(notification)-{'on','target','sessionIds'}
            or not isinstance(notification.get('on'),list)
            or any(v not in ('completed','failed','cancelled','unknown','uncertain','cleaning') for v in notification['on'])
            or notification.get('target')!='job'):
        raise ValueError('Invalid Job notification policy')
    recipients=notification.get('sessionIds',[])
    if not isinstance(recipients,list) or len(recipients)>128 or any(not isinstance(s,str) or not s for s in recipients) or len(set(recipients))!=len(recipients):
        raise ValueError('Notification sessionIds requires at most128 distinct Session IDs')
    aid = value.get('actionId')
    if not isinstance(aid, str) or aid not in state['actions']:
        raise ValueError('Missing Action reference at /config/actionId: '+str(aid))
    action = validate_action(state['actions'][aid])
    if action['execution']=={'kind':'system_job','operation':'main-lifecycle'}:
        raise ValueError('Main lifecycle is available only through its protected supervisor entry point')
    completion_policy(action,value)
    value['arguments'] = filled_arguments(action, value['arguments'])
    check_action_graph(state, action)
    from packages.jobs.api import _validate_scheduled_specs
    if trigger['kind'] in ('immediate','event') and value.get('schedule') == []:
        return value
    if not isinstance(value.get('schedule'), list) or not 1 <= len(value['schedule']) <= 32:
        raise ValueError('schedule requires 1..32 complete specs')
    error = _validate_scheduled_specs(value['schedule'])
    if error:
        raise ValueError(error)
    if trigger['kind'] in ('once','interval','cron') and any(spec['kind'] != trigger['kind'] for spec in value['schedule']):
        raise ValueError('Trigger kind must agree with every schedule spec')
    return value


def completion_policy(action,config):
    if config.get('completion',{}).get('kind')=='monitoring' and not action.get('monitoring'):
        raise ValueError('Action does not declare monitoring capability')
    if config.get('recovery',{}).get('enabled') and not action.get('recoverable'):
        raise ValueError('Action does not declare recoverable capability')


def generation(state, aid):
    return state.get('actionGenerations', {}).get(aid, 0)


def integrity(state, config, approved_generation):
    config=conservative_config(config)
    aid = config['actionId']
    issues = []
    try:
        action = state['actions'].get(aid)
        if not action:
            raise ValueError('Missing Action reference: '+aid)
        filled_arguments(validate_action(action), config['arguments'])
        # Validate additive policies without rejecting a persisted, already-fired
        # once date. No migration write, date shift, enable or recovery is implied.
        from datetime import datetime,timedelta
        kind=config['trigger'].get('kind') if isinstance(config.get('trigger'),dict) else None
        spec={'kind':'once','at':(datetime.now()+timedelta(days=1)).isoformat()} if kind=='once' else {'kind':'cron','cron':'0 * * * *'} if kind=='cron' else {'kind':'interval','intervalSec':60}
        config_value({**config,'schedule':[spec]},state)
        check_action_graph(state, action)
    except (ValueError, jsonschema.ValidationError) as exc:
        issues.append({'actionId':getattr(exc,'action_id',aid), 'position':getattr(exc,'position','/config/actionId'), 'error':str(exc)[:1000]})
    return {'status':'incomplete' if issues else 'complete','issues':issues,
            'approvalRequired':generation(state,aid) != approved_generation}


def references(state, aid):
    return ([{'jobId':jid, 'position':'/config/actionId'} for jid,r in state.get('jobReferences',{}).items() if aid in r.get('actionIds',[r['actionId']])]
            + [{'templateId':t['id'],'position':'/config/actionId'} for t in state.get('jobTemplates',{}).values() if t['config']['actionId']==aid])


class Templates:
    def __init__(self, actions):
        self.actions = actions

    def transaction(self, change):
        return self.actions.mutate(change, versioned=False)

    def list(self):
        state = self.actions.read()
        return [self.view(t,state) for t in state.get('jobTemplates',{}).values()]

    @staticmethod
    def view(item,state):
        result = copy.deepcopy(item)
        result['integrity'] = integrity(state,item['config'],item['referenceGeneration'])
        result.pop('enabled', None)  # Legacy field is ignored; Templates are passive configuration.
        result['usable'] = result['integrity']['status']=='complete' and not result['integrity']['approvalRequired']
        return result

    def put(self, raw, expected, authorized=False, item_id=None):
        strict(raw, {'id','name','description','config'}, 'Job Template')
        def change(state):
            item = copy.deepcopy(raw)
            tid = item.setdefault('id',item_id or 'jtpl_'+uuid.uuid4().hex[:24])
            if not isinstance(tid,str) or not tid.startswith('jtpl_') or len(tid)>64 or (item_id and tid != item_id):
                raise ValueError('Invalid Job Template ID')
            items = state.setdefault('jobTemplates',{})
            old = items.get(tid)
            revision(old['revision'] if old else 0,expected)
            if not old and len(items)>=MAX_TEMPLATES:
                raise ValueError('Job Template capacity reached')
            if not isinstance(item.get('name'),str) or not item['name'].strip():
                raise ValueError('Template name required')
            item.setdefault('description','')
            if not isinstance(item['description'],str):
                raise ValueError('Invalid Template metadata')
            item['config'] = config_value(item.get('config'),state)
            aid = item['config']['actionId'];gen = generation(state,aid)
            repairing = old and (old['config']['actionId']!=aid or old['referenceGeneration']!=gen)
            if not authorized and (item['config']['enabled'] or repairing):
                raise PermissionError('Authorized caller required for enabled Job config or reference repair')
            versions = state.setdefault('templateVersions',{})
            if tid not in versions and len(versions)>=MAX_RECEIPTS:
                raise ValueError('Job Template revision tombstone capacity reached')
            item.update(revision=versions.get(tid,0)+1, referenceGeneration=gen)
            versions[tid]=item['revision'];items[tid]=item
            check_action_graph(state,state['actions'][aid])
            return self.view(item,state)
        return self.transaction(change)

    def delete(self, tid, expected):
        def change(state):
            item = state.get('jobTemplates',{}).get(tid)
            if not item:raise KeyError(tid)
            revision(item['revision'],expected)
            del state['jobTemplates'][tid]
            state.setdefault('templateVersions',{})[tid]=item['revision']+1
        self.transaction(change)

    def reserve(self, tid, expected, key, overrides=None, *, job_root, admitted_template=None, authority=None):
        if not isinstance(key,str) or not 1<=len(key)<=200:
            raise ValueError('idempotencyKey requires 1..200 characters')
        overrides = {} if overrides is None else overrides
        strict(overrides,{'name','description'},'instantiate overrides')
        if any(not isinstance(v,str) for v in overrides.values()) or ('name' in overrides and not overrides['name'].strip()):
            raise ValueError('Invalid display overrides')
        fingerprint = digest({'templateId':tid,'revision':expected,'overrides':overrides})
        receipt_key = digest(key)
        def change(state):
            receipts = state.setdefault('jobInstantiations',{})
            old = receipts.get(receipt_key)
            if old:
                if old['fingerprint']!=fingerprint:raise Conflict('Idempotency key belongs to another request')
                return copy.deepcopy(old)
            if len(receipts)>=MAX_RECEIPTS:raise ValueError('Instantiation receipt capacity reached')
            item = admitted_template or state.get('jobTemplates',{}).get(tid)
            if not item:raise KeyError(tid)
            revision(item['revision'],expected)
            view = self.view(item,state) if admitted_template is None else item
            if not view['usable']:raise Conflict('Template incomplete or requires user repair')
            config = (config_value({**item['config'],**overrides},state) if admitted_template is None
                      else copy.deepcopy({**item['config'],**overrides}))
            if not state['actions'][config['actionId']]['enabled'] or state.get('actionLifecycle',{}).get(config['actionId']) is False:
                raise Conflict('Action disabled; new Job refused')
            job_id = 'job_'+digest('template-instance:'+key)[:24]
            intent = dict(key=receipt_key,fingerprint=fingerprint,jobId=job_id,templateId=tid,templateRevision=expected,
                          config=config,referenceGeneration=item['referenceGeneration'],jobRoot=job_root,phase='reserved',
                          authorization=copy.deepcopy(authority or {'type':'user'}))
            receipts[receipt_key]=intent
            state.setdefault('jobReferences',{})[job_id]={'actionId':config['actionId'],'position':'/config/actionId','jobRoot':job_root}
            return copy.deepcopy(intent)
        return self.transaction(change)

    def complete_intent(self, key):
        def change(state):
            item = state['jobInstantiations'][key]
            if item['phase']=='reserved':
                item['configDigest']=digest(item.pop('config'));item['phase']='created'
        self.transaction(change)

    def snapshot(self, job, execution):
        def change(state):
            config=job['config'];aid=config['actionId']
            status=integrity(state,config,job['referenceGeneration'])
            if status['status']!='complete' or status['approvalRequired']:
                raise Conflict('Action reference incomplete or requires user repair: '+aid)
            action=validate_action(state['actions'][aid])
            if not action['enabled'] or state.get('actionLifecycle',{}).get(aid) is False or action['effect']=='high_risk':
                raise ValueError('Action disabled or requires approval policy')
            snapshots=state.setdefault('jobExecutions',{})
            if len(snapshots)>=MAX_SNAPSHOTS:raise ValueError('Temporary snapshot capacity reached')
            args=filled_arguments(action,config['arguments'])
            snap=dict(definition=action,arguments=args,context=copy.deepcopy(execution['context']),
                      actionId=aid,actionRevision=state.get('actionRevisions',{}).get(aid,0),
                      registryRevision=state['revision'],contextDigest=digest(execution['context']),
                      digest=digest(action),argumentsDigest=digest(args),checkpoint={},jobId=job['jobId'],taskId=job['taskId'],jobRoot=execution['jobRoot'])
            if action['execution']['kind']=='job_template':
                fixed=state['jobTemplates'][action['execution']['templateId']]
                view=self.view(fixed,state)
                if not view['usable']:raise Conflict('Fixed Job Template unavailable')
                snap['fixedTemplate']=copy.deepcopy(view)
            strict(snap, set(snap), 'execution snapshot')
            snapshots[execution['id']]=snap
            return copy.deepcopy(snap)
        return self.transaction(change)

    def checkpoint(self,eid,fields):
        def change(state):state['jobExecutions'][eid]['checkpoint'].update(copy.deepcopy(fields))
        self.transaction(change)

    def clear_snapshot(self,eid):
        def change(state):state.setdefault('jobExecutions',{}).pop(eid,None)
        self.transaction(change)

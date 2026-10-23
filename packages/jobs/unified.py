"""Compatibility projection for legacy Job records into the unified model."""
from __future__ import annotations
import copy
import math
import re


def projected_schedule(job):
    """Normalize only proven legacy shapes, never recalculate a historic date."""
    value=job.get('schedule',[])
    if isinstance(value,list):
        fields={'kind','at','intervalSec','interval_sec','anchor','cron','timezone','enabled','misfirePolicy','graceSec'}
        if len(value)>32 or any(not isinstance(e,dict) or e.get('kind') not in ('once','interval','cron') or set(e)-fields-{'id','nextFireAt','lastFireAt'} for e in value):
            raise ValueError('Unrecognized stored schedule entry; edit through legacy API')
        return [{k:copy.deepcopy(v) for k,v in e.items() if k in fields} for e in value]
    if not isinstance(value,dict):raise ValueError('Unrecognized legacy schedule shape')
    kind=value.get('type')
    allowed={'once':{'type','at','delaySeconds'},'interval':{'type','intervalSeconds'},'weekly':{'type','weekday','time','timezone'}}
    if set(value)-allowed.get(kind,set()):raise ValueError('Unknown legacy schedule fields; explicit review required')
    if kind=='interval':
        seconds=value.get('intervalSeconds')
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:raise ValueError('Invalid legacy interval')
        return [{'kind':'interval','intervalSec':seconds}]
    if kind=='once':
        at=value.get('at') or job.get('nextRunAt')
        if not isinstance(at,str) or not at:raise ValueError('Relative once schedule lacks persisted occurrence; choose a new date explicitly')
        return [{'kind':'once','at':at}]
    if kind=='weekly':
        weekday=value.get('weekday');clock=value.get('time')
        if type(weekday) is not int or weekday not in range(7) or not isinstance(clock,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9](?::00)?',clock):
            raise ValueError('Weekly weekday/time cannot be represented exactly by minute cron')
        hours,minutes=map(int,clock.split(':')[:2])
        return [{'kind':'cron','cron':f'{minutes} {hours} * * {(weekday+1)%7}',**({'timezone':value['timezone']} if value.get('timezone') else {})}]
    raise ValueError('Unknown legacy schedule type; no automatic conversion')

LEGACY_ACTIONS={
    'background-process':'system_background_process',
    'session-message':'system_session_message',
    'session-broadcast':'system_session_broadcast',
    'scheduled-task':'system_scheduled_task',
    'main-lifecycle':'system_main_lifecycle',
}

# These are the fields understood by the legacy readers.  Everything else is
# carried in the read-only compatibility projection so a migration review can
# account for data without making it executable.  In particular, an unknown
# ``enabled``-like field can never promote the projected Job: ``config.enabled``
# is always forced false below.
_LEGACY_PROJECTION_FIELDS = {
    'id', 'jobId', 'kind', 'name', 'description', 'enabled', 'status',
    'schedule', 'nextRunAt', 'maxRuns', 'misfirePolicy', 'lastExecution',
    'targetSessionId', 'targetSessionIds', 'source', 'sourceSessionId',
    'sourceStruct', 'targetStruct', 'text', 'message', 'content',
    'arguments', 'params', 'action', 'config', 'run', 'createdAt',
    'updatedAt', 'revision', 'owner', 'generation',
}


def _unknown_legacy_fields(job):
    """Return unknown legacy data for audit without changing execution shape."""
    return {key: copy.deepcopy(value) for key, value in job.items()
            if key not in _LEGACY_PROJECTION_FIELDS}

def project(job):
    """Return a read-only unified view without rewriting legacy storage."""
    if not isinstance(job,dict):raise ValueError('Job record must be an object')
    if isinstance(job.get('config'),dict) and job.get('action'):
        return copy.deepcopy(job)
    kind=job.get('kind','scheduled-task')
    aid=LEGACY_ACTIONS.get(kind,'system.legacy_'+str(kind).replace('-','_'))
    from .system_actions import arguments
    issues=[]
    try:params=arguments(job)
    except (KeyError,TypeError):params={};issues.append('Missing declared legacy arguments')
    try:schedule=projected_schedule(job)
    except ValueError as exc:schedule=[];issues.append(str(exc))
    if kind not in LEGACY_ACTIONS:issues.append('Unknown legacy kind; Action reference unavailable')
    if kind=='main-lifecycle':issues.append('Protected supervisor entry only; not a generic Job migration')
    # An unconvertible schedule must remain invalid for new Job admission, even
    # if a client accidentally ignores review diagnostics. Never invent immediate.
    trigger={'kind':'schedule'} if schedule or issues else {'kind':'immediate'}
    unknown_fields = _unknown_legacy_fields(job)
    return {'jobId':job.get('jobId') or job.get('id'),'legacyKind':kind,
            'action':{'api':'system_action','actionId':aid,'arguments':copy.deepcopy(params)},
            'config':{'name':job.get('name') or kind,'description':job.get('description',''),
                      'actionId':aid,'arguments':copy.deepcopy(params),'schedule':schedule,
                      'trigger':trigger,
                      'recovery':{'enabled':False,'monitoring':False,'maxAttempts':3,'backoffSec':[5,15,60],'stableResetSec':300},
                      'overlap':{'mode':'forbid','parallelLimit':3},
                      'notification':{'on':['completed','failed','cancelled','unknown','uncertain','cleaning'],'target':'job'},
                      'completion':{'kind':'ordinary'},'enabled':False,
                      'maxRuns':job.get('maxRuns'),'misfirePolicy':job.get('misfirePolicy','fire_now')},
            'run':{'status':job.get('status'),'runId':job.get('lastExecution',{}).get('runId') if isinstance(job.get('lastExecution'),dict) else None},
            'compatibility':{'legacyKind':kind,'migration':'system-executor','reviewRequired':True,
                             'migrationIssues':issues,
                             # Preserve unknown input for a human/explicit
                             # migrator; never merge it into config or Action
                             # arguments.  This is intentionally a view-only
                             # audit channel and does not rewrite the source.
                             'unknownFields':unknown_fields}}

"""Compatibility projection for legacy Job records into the unified model."""
from __future__ import annotations
import copy

LEGACY_ACTIONS={
    'background-process':'system_background_process',
    'session-message':'system_session_message',
    'session-broadcast':'system_session_broadcast',
    'scheduled-task':'system_scheduled_task',
    'main-lifecycle':'system_main_lifecycle',
}

def project(job):
    """Return a read-only unified view without rewriting legacy storage."""
    if not isinstance(job,dict):raise ValueError('Job record must be an object')
    if isinstance(job.get('config'),dict) and job.get('action'):
        return copy.deepcopy(job)
    kind=job.get('kind','scheduled-task')
    aid=LEGACY_ACTIONS.get(kind,'system.legacy_'+str(kind).replace('-','_'))
    from .system_actions import arguments
    try:params=arguments(job)
    except (KeyError,TypeError):params={}
    return {'jobId':job.get('jobId') or job.get('id'),'legacyKind':kind,
            'action':{'api':'system_action','actionId':aid,'arguments':copy.deepcopy(params)},
            'config':{'name':job.get('name') or kind,'description':job.get('description',''),
                      'actionId':aid,'arguments':copy.deepcopy(params),'schedule':copy.deepcopy(job.get('schedule',[])),
                      'trigger':{'kind':'schedule'},
                      'recovery':{'enabled':False,'monitoring':False,'maxAttempts':3,'backoffSec':[5,15,60],'stableResetSec':300},
                      'overlap':{'mode':'forbid','parallelLimit':3},
                      'notification':{'on':['completed','failed','cancelled','unknown','uncertain','cleaning'],'target':'job'},
                      'completion':{'kind':'ordinary'},'enabled':bool(job.get('enabled',False)),
                      'maxRuns':job.get('maxRuns'),'misfirePolicy':job.get('misfirePolicy','fire_now')},
            'run':{'status':job.get('status'),'runId':job.get('lastExecution',{}).get('id') if isinstance(job.get('lastExecution'),dict) else None},
            'compatibility':{'legacyKind':kind,'migration':'system-executor'}}

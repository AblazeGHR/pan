"""User-controlled complete Job Templates and Action Job configuration endpoints."""
from fastapi import APIRouter, Request, HTTPException
import sqlite3
from packages.plugins.lifecycle import io
from .action_runtime import AuthorizationRevoked
from . import chains
from .action_templates import strict, Conflict, CreationPending, references
from packages.automation.authorization import require, require_write, authority

router=APIRouter(prefix='/api/jobs',tags=['action-jobs'])


def runtime(request):
    svc=getattr(request.app.state,'automation',None)
    value=getattr(svc,'job_runtime',None)
    if value is None or value.closed:raise HTTPException(503,'Job Action runtime unavailable')
    return value


async def user(request,required=False):
    await require(request)
    return True


async def require_capability(request, kind, capability):
    return await require(request, kind, capability)


def body(data,allowed,required):
    try:strict(data,allowed,'request')
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    if not set(required)<=set(data):raise HTTPException(400,'Missing required request fields')


async def invoke(fn,*args,_emit=False,**kwargs):
    try:
        value=await io(fn,*args,**kwargs)
        # Canonical Job writers already emit complete public projections. Read
        # helpers must never broadcast a raw Job or duplicate mutation events.
        if _emit and isinstance(value,dict) and value.get('deleted') and value.get('jobId'):
            from packages.core import background_jobs
            background_jobs._scheduled_task_emit({'type':'job.deleted','jobId':value['jobId']})
        return value
    except PermissionError as exc:raise HTTPException(401,str(exc)) from exc
    except CreationPending as exc:raise HTTPException(503,{'message':str(exc),'jobId':exc.job_id,'pending':True}) from exc
    except (OSError,sqlite3.Error) as exc:raise HTTPException(503,'Persistence failed; reload state before retrying: '+str(exc)) from exc
    except AuthorizationRevoked as exc:raise HTTPException(403,str(exc)) from exc
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@router.get('/action-jobs')
async def list_jobs(request:Request):
    await require_capability(request, 'job', 'read')
    rt=runtime(request)
    return {'jobs':await invoke(rt.list),'diagnostics':list(rt.diagnostics)}

@router.get('/action-jobs/{job_id}/runs')
async def runs(job_id:str,request:Request):
    await require_capability(request, 'job', 'read')
    rt=runtime(request)
    try:job=await invoke(rt.get,job_id)
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    return {'jobId':job_id,'run':job.get('runId'),'attempts':job.get('attempts',[]),
            'lastExecution':job.get('lastExecution'),'status':job.get('status'),
            'activeExecutions':chains.public(rt.active(job))}


@router.get('/action-jobs/schema')
async def schema(request:Request,actionId:str|None=None):
    await require_capability(request, 'job', 'read')
    from .action_templates import MAX_DOCUMENT_BYTES,MAX_TEMPLATES,MAX_RECEIPTS,MAX_SNAPSHOTS
    state=await invoke(runtime(request).actions.read)
    arguments={'type':'object'}
    if actionId is not None:
        if actionId not in state['actions']:raise HTTPException(404,'Action missing')
        arguments={**state['actions'][actionId]['input_schema'],'additionalProperties':False}
    schedules=[]
    common={'kind':{'type':'string'},'timezone':{'type':'string'},'enabled':{'type':'boolean'},
            'misfirePolicy':{'enum':['fire_now','skip']},'graceSec':{'type':'number','minimum':0,'maximum':86400}}
    for kind,fields,required in [
        ('once',{'at':{'type':'string'}},['at']),
        ('interval',{'intervalSec':{'type':'number','exclusiveMinimum':0},'interval_sec':{'type':'number','exclusiveMinimum':0},'anchor':{'type':'string'}},[]),
        ('cron',{'cron':{'type':'string'}},['cron'])]:
        spec={'type':'object','additionalProperties':False,'properties':{**common,**fields,'kind':{'const':kind}},'required':['kind',*required]}
        if kind=='interval':spec['oneOf']=[{'required':['intervalSec'],'not':{'required':['interval_sec']}},{'required':['interval_sec'],'not':{'required':['intervalSec']}}]
        schedules.append(spec)
    metadata={'name':{'type':'string','minLength':1,'maxLength':200},'description':{'type':'string'}}
    config_schema={'type':'object','additionalProperties':False,'required':['name','actionId','schedule'],
        'properties':{**metadata,'actionId':{'type':'string'},'arguments':arguments,
        'schedule':{'type':'array','minItems':0,'maxItems':32,'items':{'oneOf':schedules}},
        'trigger':{'type':'object','additionalProperties':False,'properties':{'kind':{'enum':['manual','immediate','once','interval','cron','event','schedule']},'query':{'type':'object'},'eventTypes':{'type':'array','minItems':1,'maxItems':32,'uniqueItems':True,'items':{'type':'string','minLength':1,'maxLength':200}}},'required':['kind']},
        'recovery':{'type':'object','additionalProperties':False,'properties':{'enabled':{'type':'boolean'},'monitoring':{'type':'boolean'},'maxAttempts':{'type':'integer','minimum':1,'maximum':10},'backoffSec':{'type':'array','minItems':3,'maxItems':3,'items':{'type':'integer','minimum':0,'maximum':86400}},'stableResetSec':{'type':'integer','minimum':0,'maximum':86400}}},
        'overlap':{'type':'object','additionalProperties':False,'properties':{'mode':{'enum':['forbid','queue','parallel']},'parallelLimit':{'type':'integer','minimum':1,'maximum':32}}},
        'notification':{'type':'object','additionalProperties':False,'properties':{'on':{'type':'array','items':{'enum':['completed','failed','cancelled','unknown','uncertain','cleaning']}},'target':{'const':'job'},'sessionIds':{'type':'array','maxItems':128,'uniqueItems':True,'items':{'type':'string','minLength':1}}}},
        'completion':{'type':'object','additionalProperties':False,'properties':{'kind':{'enum':['ordinary','monitoring']},'signalType':{'type':'string'}}},
        'enabled':{'type':'boolean','default':False},'maxRuns':{'type':['integer','null'],'minimum':1},
        'misfirePolicy':{'enum':['fire_now','skip']}}}
    config_schema['required']=['name','schedule']
    config_schema['anyOf']=[{'required':['actionId']},{'required':['steps']}]
    config_schema['properties']['steps']=chains.SCHEMA['steps']
    return {'version':1,'chains':chains.SCHEMA,'config':config_schema,'template':{'type':'object','additionalProperties':False,
        'required':['name','config'],'properties':{**metadata,'id':{'type':'string','pattern':'^jtpl_','maxLength':64},
        'config':config_schema}},
        'instantiate':{'type':'object','additionalProperties':False,'required':['expectedRevision','idempotencyKey'],
        'properties':{'expectedRevision':{'type':'integer','minimum':1},'idempotencyKey':{'type':'string','minLength':1,'maxLength':128},
        'overrides':{'type':'object','additionalProperties':False,'properties':metadata}}},
        'limits':{'documentBytes':MAX_DOCUMENT_BYTES,'templates':MAX_TEMPLATES,'receipts':MAX_RECEIPTS,'snapshots':MAX_SNAPSHOTS,'referenceDepth':8}}


@router.get('/action-references/{action_id}')
async def reference_list(action_id:str,request:Request):
    await require_capability(request, 'action', 'read')
    rt=runtime(request)
    state=await invoke(rt.actions.read)
    return {'actionId':action_id,'references':references(state,action_id)}


@router.post('/action-jobs')
async def create_job(data:dict,request:Request):
    await require_write(request, 'job', None, data.get('config', {}))
    body(data,{'config'},{'config'})
    return {'job':await invoke(runtime(request).create,data['config'],await user(request),await authority(request),_emit=True)}


@router.put('/action-jobs/{job_id}')
async def put_job(job_id:str,data:dict,request:Request):
    await require(request)
    current = await invoke(runtime(request).get, job_id)
    await require_write(request, 'job', current['config'], data.get('config', {}))
    body(data,{'config','expectedRevision'},{'config','expectedRevision'})
    return {'job':await invoke(runtime(request).update,job_id,data['config'],data['expectedRevision'],await user(request),await authority(request),_emit=True)}


@router.post('/action-jobs/{job_id}/run-now')
async def run_now(job_id:str,data:dict,request:Request):
    await require_capability(request, 'job', 'execute')
    body(data,{'expectedRevision'},{'expectedRevision'});await user(request,True)
    try:return {'job':await runtime(request).launch(job_id,data['expectedRevision'],authority=await authority(request))}
    except AuthorizationRevoked as exc:raise HTTPException(403,str(exc)) from exc
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except OSError as exc:raise HTTPException(503,'Persistence failed; reload state before retrying: '+str(exc)) from exc


@router.post('/action-jobs/{job_id}/cancel')
async def cancel(job_id:str,data:dict,request:Request):
    await require_capability(request, 'job', 'lifecycle')
    body(data,{'expectedRevision'},{'expectedRevision'});await user(request,True)
    try:return {'job':await runtime(request).cancel(job_id,data['expectedRevision'])}
    except AuthorizationRevoked as exc:raise HTTPException(403,str(exc)) from exc
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except OSError as exc:raise HTTPException(503,'Persistence failed; reload state before retrying: '+str(exc)) from exc


@router.post('/action-jobs/{job_id}/lifecycle')
async def job_lifecycle(job_id: str, data: dict, request: Request):
    await require_capability(request, 'job', 'lifecycle')
    body(data, {'enabled','expectedRevision'}, {'enabled','expectedRevision'})
    if type(data['enabled']) is not bool:raise HTTPException(400, 'enabled must be boolean')
    return {'job': await invoke(runtime(request).set_enabled, job_id, data['enabled'],
                                data['expectedRevision'], await authority(request), _emit=True)}


@router.post('/action-jobs/{job_id}/pause')
@router.post('/action-jobs/{job_id}/resume')
async def pause_resume(job_id: str, data: dict, request: Request):
    await require_capability(request, 'job', 'lifecycle')
    body(data, {'expectedRevision'}, {'expectedRevision'})
    return {'job': await invoke(runtime(request).set_paused, job_id,
                                request.url.path.endswith('/pause'), data['expectedRevision'], await authority(request), _emit=True)}


@router.delete('/action-jobs/{job_id}')
async def delete_job(job_id:str,expectedRevision:int,request:Request):
    await require_capability(request, 'job', 'delete')
    return await invoke(runtime(request).delete,job_id,expectedRevision,_emit=True)


@router.post('/action-jobs/{job_id}/confirm-recovery')
@router.post('/action-jobs/{job_id}/retry')
async def confirm_recovery(job_id:str,data:dict,request:Request):
    await require_capability(request, 'job', 'execute')
    body(data,{'expectedRevision','confirm'},{'expectedRevision','confirm'});await user(request,True)
    if data['confirm'] is not True:raise HTTPException(400,'Explicit confirm:true required')
    try:return {'job':await runtime(request).confirm_recovery(job_id,data['expectedRevision'],request.url.path.endswith('/retry'),await authority(request))}
    except AuthorizationRevoked as exc:raise HTTPException(403,str(exc)) from exc
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except OSError as exc:raise HTTPException(503,'Persistence failed; reload before retrying') from exc


@router.get('/job-templates')
async def list_templates(request:Request):
    await require_capability(request, 'jobTemplate', 'read')
    return {'templates':await invoke(runtime(request).templates.list)}


@router.post('/job-templates')
async def create_template(data:dict,request:Request):
    await require_capability(request, 'jobTemplate', 'create')
    body(data,{'item','expectedRevision'},{'item','expectedRevision'})
    return {'item':await invoke(runtime(request).templates.put,data['item'],data['expectedRevision'],await user(request))}


@router.put('/job-templates/{template_id}')
async def update_template(template_id:str,data:dict,request:Request):
    await require_capability(request, 'jobTemplate', 'updateConfig')
    body(data,{'item','expectedRevision'},{'item','expectedRevision'})
    return {'item':await invoke(runtime(request).templates.put,data['item'],data['expectedRevision'],await user(request),template_id)}


@router.delete('/job-templates/{template_id}')
async def delete_template(template_id:str,expectedRevision:int,request:Request):
    await require_capability(request, 'jobTemplate', 'delete')
    await invoke(runtime(request).templates.delete,template_id,expectedRevision)
    return {'deleted':True}


@router.post('/job-templates/{template_id}/instantiate')
async def instantiate(template_id:str,data:dict,request:Request):
    await require_capability(request, 'jobTemplate', 'use')
    await require_capability(request, 'job', 'create')
    body(data,{'expectedRevision','idempotencyKey','overrides'},{'expectedRevision','idempotencyKey'})
    await user(request,True)
    key=data['idempotencyKey']
    if not isinstance(key,str) or not 1<=len(key)<=128:raise HTTPException(400,'idempotencyKey requires1..128 characters')
    source = await authority(request)
    state = await invoke(runtime(request).actions.read)
    template = state.get('jobTemplates',{}).get(template_id)
    if template and template['config'].get('enabled'):
        await require_capability(request, 'job', 'lifecycle')
    return {'job':await invoke(runtime(request).instantiate,template_id,data['expectedRevision'],source['type']+':'+key,data.get('overrides'),authority=source,_emit=True)}

"""User-controlled complete Job Templates and Action Job configuration endpoints."""
from fastapi import APIRouter, Request, HTTPException
from packages.plugins.lifecycle import io
from .action_templates import strict, Conflict, CreationPending, references

router=APIRouter(prefix='/api/jobs',tags=['action-jobs'])


def runtime(request):
    svc=getattr(request.app.state,'automation',None)
    value=getattr(svc,'job_runtime',None)
    if value is None or value.closed:raise HTTPException(503,'Job Action runtime unavailable')
    return value


async def user(request,required=False):
    svc=getattr(request.app.state,'automation',None)
    ok=bool(svc and await io(svc.auth.authorized,request.headers.get('authorization','')))
    if required and not ok:raise HTTPException(401,'User Bearer required')
    return ok


def body(data,allowed,required):
    try:strict(data,allowed,'request')
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    if not set(required)<=set(data):raise HTTPException(400,'Missing required request fields')


async def invoke(fn,*args,**kwargs):
    try:
        value=await io(fn,*args,**kwargs)
        if isinstance(value,dict) and value.get('jobId'):
            from packages.core import background_jobs
            background_jobs._scheduled_task_emit({'type':'job.updated','jobId':value['jobId'],'job':value})
        return value
    except PermissionError as exc:raise HTTPException(401,str(exc)) from exc
    except CreationPending as exc:raise HTTPException(503,{'message':str(exc),'jobId':exc.job_id,'pending':True}) from exc
    except OSError as exc:raise HTTPException(503,'Persistence failed; reload state before retrying: '+str(exc)) from exc
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@router.get('/action-jobs')
async def list_jobs(request:Request):
    rt=runtime(request)
    return {'jobs':await invoke(rt.list),'diagnostics':list(rt.diagnostics)}

@router.get('/action-jobs/{job_id}/runs')
async def runs(job_id:str,request:Request):
    rt=runtime(request)
    try:job=await invoke(rt.get,job_id)
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    return {'jobId':job_id,'run':job.get('runId'),'attempts':job.get('attempts',[]),
            'lastExecution':job.get('lastExecution'),'status':job.get('status')}


@router.get('/action-jobs/schema')
async def schema(request:Request,actionId:str|None=None):
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
        'schedule':{'type':'array','minItems':1,'maxItems':32,'items':{'oneOf':schedules}},
        'trigger':{'type':'object','additionalProperties':False,'properties':{'kind':{'enum':['immediate','once','interval','cron','event','schedule']},'query':{'type':'object'}},'required':['kind']},
        'recovery':{'type':'object','additionalProperties':False,'properties':{'enabled':{'type':'boolean'},'monitoring':{'type':'boolean'},'maxAttempts':{'type':'integer','minimum':1,'maximum':10},'backoffSec':{'type':'array','minItems':3,'maxItems':3,'items':{'type':'integer','minimum':0,'maximum':86400}},'stableResetSec':{'type':'integer','minimum':0,'maximum':86400}}},
        'overlap':{'type':'object','additionalProperties':False,'properties':{'mode':{'enum':['forbid','queue','parallel']},'parallelLimit':{'type':'integer','minimum':1,'maximum':32}}},
        'notification':{'type':'object','additionalProperties':False,'properties':{'on':{'type':'array','items':{'enum':['completed','failed','cancelled','unknown','uncertain','cleaning']}},'target':{'const':'job'}}},
        'completion':{'type':'object','additionalProperties':False,'properties':{'kind':{'enum':['ordinary','monitoring']},'signalType':{'type':'string'}}},
        'enabled':{'type':'boolean','default':False},'maxRuns':{'type':['integer','null'],'minimum':1},
        'misfirePolicy':{'enum':['fire_now','skip']}}}
    return {'version':1,'config':config_schema,'template':{'type':'object','additionalProperties':False,
        'required':['name','config'],'properties':{**metadata,'id':{'type':'string','pattern':'^jtpl_','maxLength':64},
        'enabled':{'type':'boolean','default':False},'config':config_schema}},
        'instantiate':{'type':'object','additionalProperties':False,'required':['expectedRevision','idempotencyKey'],
        'properties':{'expectedRevision':{'type':'integer','minimum':1},'idempotencyKey':{'type':'string','minLength':1,'maxLength':128},
        'overrides':{'type':'object','additionalProperties':False,'properties':metadata}}},
        'limits':{'documentBytes':MAX_DOCUMENT_BYTES,'templates':MAX_TEMPLATES,'receipts':MAX_RECEIPTS,'snapshots':MAX_SNAPSHOTS,'referenceDepth':8}}


@router.get('/action-references/{action_id}')
async def reference_list(action_id:str,request:Request):
    rt=runtime(request)
    state=await invoke(rt.actions.read)
    return {'actionId':action_id,'references':references(state,action_id)}


@router.post('/action-jobs')
async def create_job(data:dict,request:Request):
    body(data,{'config'},{'config'})
    return {'job':await invoke(runtime(request).create,data['config'],await user(request))}


@router.put('/action-jobs/{job_id}')
async def put_job(job_id:str,data:dict,request:Request):
    body(data,{'config','expectedRevision'},{'config','expectedRevision'})
    return {'job':await invoke(runtime(request).update,job_id,data['config'],data['expectedRevision'],await user(request))}


@router.post('/action-jobs/{job_id}/run-now')
async def run_now(job_id:str,data:dict,request:Request):
    body(data,{'expectedRevision'},{'expectedRevision'});await user(request,True)
    try:return {'job':await runtime(request).launch(job_id,data['expectedRevision'])}
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except OSError as exc:raise HTTPException(503,'Persistence failed; reload state before retrying: '+str(exc)) from exc


@router.post('/action-jobs/{job_id}/cancel')
async def cancel(job_id:str,data:dict,request:Request):
    body(data,{'expectedRevision'},{'expectedRevision'});await user(request,True)
    try:return {'job':await runtime(request).cancel(job_id,data['expectedRevision'])}
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except OSError as exc:raise HTTPException(503,'Persistence failed; reload state before retrying: '+str(exc)) from exc


@router.delete('/action-jobs/{job_id}')
async def delete_job(job_id:str,expectedRevision:int,request:Request):
    await user(request,True)
    return await invoke(runtime(request).delete,job_id,expectedRevision)


@router.get('/job-templates')
async def list_templates(request:Request):return {'templates':await invoke(runtime(request).templates.list)}


@router.post('/job-templates')
async def create_template(data:dict,request:Request):
    body(data,{'item','expectedRevision'},{'item','expectedRevision'})
    return {'item':await invoke(runtime(request).templates.put,data['item'],data['expectedRevision'],await user(request))}


@router.put('/job-templates/{template_id}')
async def update_template(template_id:str,data:dict,request:Request):
    body(data,{'item','expectedRevision'},{'item','expectedRevision'})
    return {'item':await invoke(runtime(request).templates.put,data['item'],data['expectedRevision'],await user(request),template_id)}


@router.delete('/job-templates/{template_id}')
async def delete_template(template_id:str,expectedRevision:int,request:Request):
    await user(request,True)
    await invoke(runtime(request).templates.delete,template_id,expectedRevision)
    return {'deleted':True}


@router.post('/job-templates/{template_id}/instantiate')
async def instantiate(template_id:str,data:dict,request:Request):
    body(data,{'expectedRevision','idempotencyKey','overrides'},{'expectedRevision','idempotencyKey'})
    await user(request,True)
    key=data['idempotencyKey']
    if not isinstance(key,str) or not 1<=len(key)<=128:raise HTTPException(400,'idempotencyKey requires1..128 characters')
    return {'job':await invoke(runtime(request).instantiate,template_id,data['expectedRevision'],'user:'+key,data.get('overrides'))}

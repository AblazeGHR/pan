// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { transferableAbortController } from 'node:util';
import AutomationView from './AutomationView';
import { useAutomationAuth } from '@/stores/automationStore';
import type { EventCreator } from '@/types/automationJobs';
import { newId } from '@/types/automationJobs';
import type { CanonicalObject } from '@/types/canonicalJobs';

export const fullConfig = { rules: [
  { ruleId:'fraction', type:'interval', seconds:'0.125', anchor:'2026-11-01T00:00:00.125Z', advanceMode:'grid' },
  { ruleId:'weekly', type:'weekly', weekday:0, time:'01:30:00', timezone:'America/New_York', dstPolicy:'gap-skip-fold-second' },
  { ruleId:'cron', type:'cron', expression:'0 9 * * *', timezone:'Asia/Shanghai', dstPolicy:'gap-shift-fold-first' },
  { ruleId:'once', type:'once', at:'2027-01-01T00:00:00.123456Z' },
], misfirePolicy:'skip', graceSeconds:'1.25', pausePolicy:'advance', driveLimit:{maxEvents:17, initialProducedCount:0} };
export const provider = { providerRef:{type:'time',id:'scheduler',generation:1,releaseDigest:'digest'},configSchemaRef:{id:'scheduler.config',revision:1,digest:'schema'},eventTypes:['scheduler.time.v2'],requiredCapabilities:['time.rules.v2'],configSchema:{type:'object'} };
let sources: EventCreator[], jobs: CanonicalObject[], triggers: CanonicalObject[];
let writes: {url:string;method:string;body:Record<string,unknown>;headers:HeadersInit|undefined}[];
let reads: string[], failure:number, network:boolean;
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
const receipt=(object:CanonicalObject,key:string)=>({operationId:'op_'+key,requestKey:key,status:'completed',objectRefs:[],links:{},result:{object}});
function referenceState(id:string) { const refs=triggers.filter(t=>t.state!=='retired' && (t.creatorSelector as {ids:string[]}).ids.includes(id)); return {references:refs.map(t=>({triggerId:t.id,name:t.name,jobId:'job_review',jobName:'Morning review',revision:t.revision,stateRevision:t.stateRevision,enabled:t.state==='enabled'})),referenceDigest:JSON.stringify(refs.map(t=>[t.id,t.revision,t.stateRevision]))}; }
function host() { const router=createMemoryRouter([{path:'/',element:<AutomationView/>},{path:'/actions',element:<h1>Actions destination</h1>}]); return render(<RouterProvider router={router}/>); }
beforeEach(()=>{
  vi.stubGlobal('AbortController', function () { return transferableAbortController(); });
  sources=[{id:'creator_shared',name:'Shared timer',revision:7,stateRevision:2,state:'enabled',...provider,config:structuredClone(fullConfig)}];
  jobs=[{id:'job_review',name:'Morning review',state:'enabled',stateRevision:1,configRevision:4,steps:[],retention:{policyRef:'default'}}];
  triggers=[{id:'trigger_shared',name:'Review trigger',revision:3,stateRevision:1,state:'enabled',targetJobId:'job_review',eventTypes:['scheduler.time.v2'],creatorSelector:{mode:'ids',ids:['creator_shared']}}];
  writes=[];reads=[];failure=0;network=false;
  useAutomationAuth.setState({token:'owner-token',authenticated:true,expiresAt:Date.now()+60000});
  vi.spyOn(window,'confirm').mockReturnValue(true);
  vi.stubGlobal('fetch',vi.fn(async(url:string,init:RequestInit={})=>{
    const method=init.method??'GET';
    if(method==='GET'){
      reads.push(url);
      if(url==='/api/jobs/actions')return response({items:[{id:'action_form',name:'Form message',revision:2,state:'published',enabled:true,inputSchema:{type:'object',properties:{text:{type:'string'}},required:['text']},defaultCompletion:'durable-queue-accepted',completionContracts:[{id:'durable-queue-accepted'}]}],references:{action_form:{id:'action_form',revision:2,digest:'f'.repeat(64)}},nextCursor:null});
      if(url.includes('/operations'))return response(receipt(sources[0]!,new URL(url,'http://owned').searchParams.get('requestKey')??'lookup'));
      if(url==='/api/event-creators/providers')return response({items:[provider]});
      if(url.endsWith('/references'))return response(referenceState(url.split('/')[3]!));
      const kind=url.split('/')[2]?.split('?')[0];
      const items=kind==='event-creators'?sources.filter(s=>s.state!=='retired'):kind==='jobs'?jobs:kind==='triggers'?triggers:[];
      if (url.split('/')[3] && !url.includes('?')) return response(items.find(item=>item.id===url.split('/')[3]));
      return response({items,nextCursor:null});
    }
    const body=JSON.parse(String(init.body??'{}')) as Record<string,unknown>;
    writes.push({url,method,body,headers:init.headers});
    if(network){network=false;throw new TypeError('receipt lost');}
    if(failure){const status=failure;failure=0;return response({error:{code:status===409?'reference_conflict':status===403?'authorization_denied':'storage_unavailable'}},status);}
    const kind=url.split('/')[2],id=url.split('/')[3];
    let object:CanonicalObject;
    if(kind==='event-creators'){
      if(method==='POST'&&!id){object={id:'creator_new',state:'draft',stateRevision:0,revision:1,...body.definition as object} as EventCreator;sources.push(object as EventCreator);}
      else { const index=sources.findIndex(s=>s.id===id); const old=sources[index]!;
        object=method==='PUT'?{...old,...body.definition as object,revision:old.revision+1}:{...old,state:method==='DELETE'?'retired':body.operation==='enable'||body.operation==='resume'?'enabled':body.operation==='pause'?'paused':'disabled',stateRevision:old.stateRevision+1};sources[index]=object as EventCreator; }
    }else if(kind==='triggers') {object={id:'trigger_new',state:'draft',stateRevision:0,revision:1,...body.definition as object} as CanonicalObject;triggers.push(object);}
    else {object={id:'job_new',state:'draft',stateRevision:0,configRevision:1,...body.definition as object} as CanonicalObject;jobs.push(object);}
    return response(receipt(object,String(body.requestKey)),method==='POST'&&!id?201:200);
  }));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
async function editor(){host();fireEvent.click(screen.getByRole('tab',{name:'Event Register'}));fireEvent.click(await screen.findByRole('button',{name:'Edit Shared timer'}));}

it('navigates exactly three tabs and follows canonical Job/Trigger/source references',async()=>{
  host();const tabs=screen.getAllByRole('tab');expect(tabs.map(t=>t.textContent)).toEqual(['Job','Trigger','Event Register']);
  fireEvent.keyDown(tabs[0]!,{key:'End'});expect(tabs[2]!.getAttribute('aria-selected')).toBe('true');
  fireEvent.click(await screen.findByRole('button',{name:'Review trigger'}));expect(tabs[1]!.getAttribute('aria-selected')).toBe('true');
  fireEvent.click(tabs[2]!);fireEvent.click(screen.getByRole('button',{name:'Morning review'}));expect(tabs[0]!.getAttribute('aria-selected')).toBe('true');
  expect(reads.some(p=>p.includes('scheduler/event-sources')||p.includes('action-jobs'))).toBe(false);
});
it('round trips every rule, fraction, timezone, DST, anchor, limit and provider identity',async()=>{
  await editor();fireEvent.change(screen.getByLabelText('Scheduler name'),{target:{value:'Renamed'}});fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));
  await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0]!.url).toBe('/api/event-creators/creator_shared');
  expect(writes[0]!.body).toMatchObject({expectedRevision:7,expectedReferences:referenceState('creator_shared').referenceDigest,definition:{name:'Renamed',config:fullConfig,providerRef:provider.providerRef,eventTypes:provider.eventTypes}});
  expect(new Headers(writes[0]!.headers).get('Authorization')).toBe('Bearer owner-token');expect(writes[0]!.body.definition).not.toHaveProperty('producedCount');
});
it('reviews shared source impact and preserves a conflicting local draft',async()=>{
  await editor();expect((screen.getByRole('button',{name:'Save Scheduler'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Scheduler name'),{target:{value:'Local source'}});fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));failure=409;fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));
  await screen.findByRole('alert');expect((screen.getByLabelText('Scheduler name') as HTMLInputElement).value).toBe('Local source');expect(writes[0]!.body.expectedRevision).toBe(7);
});
it('explicitly rebases changed references and requires impact review again',async()=>{
  await editor();fireEvent.change(screen.getByLabelText('Scheduler name'),{target:{value:'Kept draft'}});fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));
  sources[0]!.revision=8;triggers[0]!.revision=4;failure=409;fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));
  fireEvent.click(await screen.findByRole('button',{name:'Keep draft against latest source revision'}));expect((screen.getByRole('button',{name:'Save Scheduler'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));await waitFor(()=>expect(writes).toHaveLength(2));expect(writes[1]!.body.expectedRevision).toBe(8);
  expect((writes[1]!.body.definition as {name:string}).name).toBe('Kept draft');
});
it('disables only the source through lifecycle CAS and blocks deleting a shared source',async()=>{
  await editor();expect((screen.getByRole('button',{name:'Delete Scheduler'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('Scheduler enabled'));fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));
  await waitFor(()=>expect(writes).toHaveLength(2));expect(writes[1]!.body).toMatchObject({operation:'disable',expectedRevision:8,expectedStateRevision:2});expect(writes.every(w=>w.url.startsWith('/api/event-creators'))).toBe(true);
});
it('retires only unreferenced disabled sources and preserves history semantics',async()=>{
  triggers=[];sources[0]!.state='disabled';await editor();fireEvent.click(screen.getByRole('button',{name:'Delete Scheduler'}));await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0]).toMatchObject({method:'DELETE',url:'/api/event-creators/creator_shared',body:{expectedRevision:7,expectedStateRevision:2,expectedReferences:'[]'}});
});
it('creates a separate Creator with server-supplied provider metadata and explicit enable',async()=>{
  host();fireEvent.click(screen.getByRole('tab',{name:'Event Register'}));fireEvent.click(await screen.findByRole('button',{name:'New Scheduler'}));fireEvent.change(screen.getByLabelText('Scheduler name'),{target:{value:'New timer'}});fireEvent.click(screen.getByLabelText('Scheduler enabled'));fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));
  await waitFor(()=>expect(writes).toHaveLength(2));expect(writes[0]!.body).toMatchObject({expectedRevision:0,definition:{name:'New timer',providerRef:provider.providerRef}});expect(writes[1]!.body.operation).toBe('enable');
});
it('queries an unconfirmed original key and never automatically resends',async()=>{
  await editor();fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));network=true;fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));await screen.findByRole('button',{name:'Query original operation'});const key=String(writes[0]!.body.requestKey);
  fireEvent.click(screen.getByRole('button',{name:'Query original operation'}));await waitFor(()=>expect(reads.some(p=>p.includes(encodeURIComponent(key)))).toBe(true));expect(writes).toHaveLength(1);
});
it('keeps multiple rule JSON intact on denial and does not send enabled state in definitions',async()=>{
  await editor();fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));failure=403;fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));await screen.findByRole('alert');expect(JSON.parse((screen.getByLabelText('Scheduler config JSON') as HTMLTextAreaElement).value)).toEqual(fullConfig);expect(writes[0]!.body.definition).not.toHaveProperty('enabled');
});
it('rejects invalid JSON locally without losing the draft or sending a write',async()=>{
  await editor();fireEvent.change(screen.getByLabelText('Scheduler config JSON'),{target:{value:'{invalid'}});fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));await screen.findByRole('alert');expect(writes).toEqual([]);expect((screen.getByLabelText('Scheduler config JSON') as HTMLTextAreaElement).value).toBe('{invalid');
});
it('selects shared Creator IDs and submits the reviewed source revision with a canonical Trigger',async()=>{
  host();fireEvent.click(screen.getByRole('tab',{name:'Trigger'}));fireEvent.click(await screen.findByRole('button',{name:'创建 Triggers'}));fireEvent.change(screen.getByLabelText('名称'),{target:{value:'New subscriber'}});await screen.findByRole('option',{name:/Shared timer/});
  fireEvent.change(screen.getByLabelText('Target canonical Job'),{target:{value:'job_review'}});const select=screen.getByLabelText('Scheduler sources') as HTMLSelectElement;select.options[0]!.selected=true;fireEvent.change(select);fireEvent.click(screen.getByRole('button',{name:'保存定义'}));
  await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0]!.body).toMatchObject({expectedCreatorRevisions:{creator_shared:7},definition:{creatorSelector:{mode:'ids',ids:['creator_shared']},targetJobId:'job_review',eventTypes:['scheduler.time.v2']}});
});
it('preserves simultaneous Job and source drafts behind one navigation guard',async()=>{
  await editor();fireEvent.change(screen.getByLabelText('Scheduler name'),{target:{value:'Unsaved source'}});fireEvent.click(screen.getByRole('tab',{name:'Job'}));fireEvent.click(screen.getByRole('button',{name:'创建 Jobs'}));fireEvent.change(screen.getByLabelText('名称'),{target:{value:'Unsaved Job'}});fireEvent.click(screen.getByRole('tab',{name:'Event Register'}));expect((screen.getByLabelText('Scheduler name') as HTMLInputElement).value).toBe('Unsaved source');
  vi.mocked(window.confirm).mockReturnValue(false);fireEvent.click(screen.getByRole('link',{name:'Manage Actions'}));await waitFor(()=>expect(window.confirm).toHaveBeenCalledTimes(1));expect(writes).toEqual([]);
});
it('keeps stable random request IDs without randomUUID on remote HTTP contexts',()=>{
  vi.stubGlobal('crypto',{getRandomValues:crypto.getRandomValues.bind(crypto)});const ids=Array.from({length:10},()=>newId('step'));expect(new Set(ids).size).toBe(10);expect(ids.every(id=>/^step_[a-f0-9]{32}$/.test(id))).toBe(true);
});

it('preserves typed literals, stable step IDs, input/Event/output mappings and policies in the canonical Job',async()=>{
  const steps=[{stepId:'first',actionRef:{id:'action_a',revision:4,digest:'a'.repeat(64)},arguments:{literal:{from:'literal',value:{enabled:true,count:2,tags:['x']}},text:{from:'input',path:'message'}},completion:{contract:'durable-queue-accepted'}},{stepId:'second',actionRef:{id:'action_b',revision:2,digest:'b'.repeat(64)},arguments:{text:{from:'event',path:'payload.text'},result:{from:'step',stepId:'first',path:'messageId'}},completion:{contract:'process-exit'}}];
  const policies={overlap:{mode:'queue',maxQueued:3},retention:{policyRef:'kept'}};
  Object.assign(jobs[0]!,{steps,...policies});host();fireEvent.click(await screen.findByRole('button',{name:'编辑 Morning review'}));
  const area=screen.getByLabelText('定义 JSON（名称单独填写）') as HTMLTextAreaElement;
  expect(JSON.parse(area.value)).toMatchObject({steps,...policies});fireEvent.change(screen.getByLabelText('名称'),{target:{value:'Edited chain'}});fireEvent.click(screen.getByRole('button',{name:'保存定义'}));
  await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0]!.body).toMatchObject({expectedRevision:4,definition:{name:'Edited chain',steps,...policies}});expect(writes[0]!.body.definition).not.toHaveProperty('stateRevision');
});

it('keeps OR events, nested OR/NOT conditions and Event arguments while editing a canonical subscription',async()=>{
  const fields={eventTypes:['scheduler.time.v2','system.signal'],condition:{or:[{field:'payload.count',op:'gt',value:2},{not:{field:'payload.text',op:'eq',value:'ignore'}}]},arguments:{text:{from:'event',path:'payload.text'},typed:{from:'literal',value:[false,3]}},eventFilter:{'payload.scope':'owned'}};
  Object.assign(triggers[0]!,fields);host();fireEvent.click(screen.getByRole('tab',{name:'Trigger'}));fireEvent.click(await screen.findByRole('button',{name:'编辑 Review trigger'}));fireEvent.change(screen.getByLabelText('名称'),{target:{value:'Edited filter'}});fireEvent.click(screen.getByRole('button',{name:'保存定义'}));
  await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0]!.body).toMatchObject({expectedRevision:3,expectedCreatorRevisions:{creator_shared:7},definition:{...fields,targetJobId:'job_review',creatorSelector:{mode:'ids',ids:['creator_shared']}}});
});

it('retains invalid local JSON across explicit canonical conflict comparison/rebase and can load the server',async()=>{
  host();fireEvent.click(await screen.findByRole('button',{name:'编辑 Morning review'}));fireEvent.change(screen.getByLabelText('名称'),{target:{value:'Local Job'}});failure=409;fireEvent.click(screen.getByRole('button',{name:'保存定义'}));await screen.findByRole('alert');
  jobs[0]!.configRevision=5;jobs[0]!.name='Server Job';fireEvent.change(screen.getByLabelText('定义 JSON（名称单独填写）'),{target:{value:'{invalid'}});fireEvent.click(screen.getByRole('button',{name:'Compare server definition'}));await screen.findByText('Server Job');fireEvent.click(screen.getByRole('button',{name:'Keep draft against server revision'}));
  expect((screen.getByLabelText('定义 JSON（名称单独填写）') as HTMLTextAreaElement).value).toBe('{invalid');fireEvent.click(screen.getByRole('button',{name:'保存定义'}));expect(writes).toHaveLength(1);
  fireEvent.change(screen.getByLabelText('定义 JSON（名称单独填写）'),{target:{value:'{"steps":[],"retention":{"policyRef":"kept"}}'}});failure=409;fireEvent.click(screen.getByRole('button',{name:'保存定义'}));await screen.findByRole('region',{name:'Compare canonical configuration'});fireEvent.click(screen.getByRole('button',{name:'Load server definition'}));expect((screen.getByLabelText('名称') as HTMLInputElement).value).toBe('Server Job');expect(writes).toHaveLength(2);
});

it('blocks duplicate submits and departure while the canonical definition receipt is pending',async()=>{
  host();fireEvent.click(await screen.findByRole('button',{name:'编辑 Morning review'}));const original=vi.mocked(fetch).getMockImplementation()!;let finish!:()=>void;
  vi.mocked(fetch).mockImplementation((url,init)=>init?.method==='PUT'?new Promise<Response>(resolve=>{finish=()=>resolve(response(receipt(jobs[0]!,'held')));return original(url,init).then(()=>undefined);}):original(url,init));
  const form=screen.getByRole('form',{name:'编辑 Jobs'});fireEvent.submit(form);fireEvent.submit(form);await waitFor(()=>expect(writes).toHaveLength(1));fireEvent.click(screen.getByRole('link',{name:'Manage Actions'}));expect(window.confirm).not.toHaveBeenCalled();expect(screen.queryByText('Actions destination')).toBeNull();finish();await waitFor(()=>expect(screen.queryByRole('form')).toBeNull());
});

it('requires explicit full definition and Action parameter review before canonical enable',async()=>{
  jobs[0]!.state='draft';host();fireEvent.click(await screen.findByRole('button',{name:'Enable Morning review'}));expect((screen.getByRole('button',{name:'Confirm enable'}) as HTMLButtonElement).disabled).toBe(true);expect(writes).toEqual([]);
  fireEvent.click(screen.getByLabelText('I reviewed this definition, Actions, parameters and source references.'));fireEvent.click(screen.getByRole('button',{name:'Confirm enable'}));await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0]!.url).toBe('/api/jobs/job_review/operations');expect(writes[0]!.body).toMatchObject({operation:'enable',expectedRevision:4,expectedStateRevision:1});
});

it('saves an ordinary Scheduler entirely through time-rule fields with canonical CAS and original-key recovery',async()=>{
  await editor();fireEvent.change(screen.getByLabelText('Interval seconds'),{target:{value:'0.250000000000000001'}});fireEvent.change(screen.getByLabelText('Grace period (seconds)'),{target:{value:'1.000000000000000001'}});fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));network=true;fireEvent.click(screen.getByRole('button',{name:'Save Scheduler'}));
  await screen.findByRole('button',{name:'Query original operation'});expect(writes).toHaveLength(1);expect(writes[0]!.body).toMatchObject({expectedRevision:7,expectedReferences:referenceState('creator_shared').referenceDigest,definition:{config:{...fullConfig,rules:[{...fullConfig.rules[0],seconds:'0.250000000000000001'},...fullConfig.rules.slice(1)],graceSeconds:'1.000000000000000001'}}});
  expect((screen.getByLabelText('Interval seconds') as HTMLInputElement).value).toBe('0.250000000000000001');expect((screen.getByRole('button',{name:'Save Scheduler'}) as HTMLButtonElement).disabled).toBe(true);fireEvent.click(screen.getByRole('button',{name:'Query original operation'}));await waitFor(()=>expect(reads.some(path=>path.includes(encodeURIComponent(String(writes[0]!.body.requestKey))))).toBe(true));expect(writes).toHaveLength(1);
});

it('creates a Job from Action and typed binding controls then links a Trigger through source and condition controls',async()=>{
  host();fireEvent.click(await screen.findByRole('button',{name:'创建 Jobs'}));fireEvent.change(screen.getByLabelText('名称'),{target:{value:'Plain form Job'}});await screen.findByRole('option',{name:'Form message · Revision 2'});fireEvent.change(screen.getByLabelText('Action for step 1'),{target:{value:'action_form'}});fireEvent.change(screen.getByLabelText('text value'),{target:{value:'plain text, no schema'}});fireEvent.click(screen.getByRole('button',{name:'保存定义'}));await waitFor(()=>expect(writes).toHaveLength(1));
  expect(writes[0]!.body).toMatchObject({expectedRevision:0,definition:{name:'Plain form Job',steps:[{stepId:'step1',actionRef:{id:'action_form',revision:2,digest:'f'.repeat(64)},arguments:{text:{from:'literal',value:'plain text, no schema'}},completion:{contract:'durable-queue-accepted'}}]}});
  await waitFor(()=>expect(screen.queryByRole('form')).toBeNull());fireEvent.click(screen.getByRole('tab',{name:'Trigger'}));fireEvent.click(screen.getByRole('button',{name:'创建 Triggers'}));fireEvent.change(screen.getByLabelText('名称'),{target:{value:'Plain form subscriber'}});await screen.findByRole('option',{name:/Plain form Job/});fireEvent.change(screen.getByLabelText('Target canonical Job'),{target:{value:'job_new'}});const select=screen.getByLabelText('Scheduler sources') as HTMLSelectElement;select.options[0]!.selected=true;fireEvent.change(select);fireEvent.click(screen.getByLabelText('Check State after Event'));fireEvent.change(screen.getByLabelText('State field'),{target:{value:'workers.runningCount'}});fireEvent.click(screen.getByRole('button',{name:'保存定义'}));await waitFor(()=>expect(writes).toHaveLength(2));expect(writes[1]!.body).toMatchObject({expectedCreatorRevisions:{creator_shared:7},definition:{targetJobId:'job_new',creatorSelector:{mode:'ids',ids:['creator_shared']},condition:{field:'workers.runningCount',op:'eq',value:0}}});expect(writes.every(w=>!w.url.includes('scheduler/event-sources'))).toBe(true);
});

it('saves retention.pinned through canonical Job definition CAS without a runtime pin override',async()=>{
  jobs[0]!.steps=[{stepId:'kept',actionRef:{id:'action_form',revision:2,digest:'f'.repeat(64)},arguments:{text:{from:'literal',value:'existing message'}},completion:{contract:'durable-queue-accepted'}}];
  host();fireEvent.click(await screen.findByRole('button',{name:'编辑 Morning review'}));fireEvent.click(screen.getByLabelText('Pin Job retention'));fireEvent.click(screen.getByRole('button',{name:'保存定义'}));await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0]!.url).toBe('/api/jobs/job_review');expect(writes[0]!.body).toMatchObject({expectedRevision:4,definition:{retention:{policyRef:'default',pinned:true}}});expect(writes.some(w=>w.url.endsWith('/pin'))).toBe(false);
});

/** Production-bundle visual/keyboard check. Virtual origin only; no server/port. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium, expect } from '@playwright/test';

const root=path.resolve(import.meta.dirname,'../../..');
const dist=path.resolve(import.meta.dirname,'../dist');
const out=path.join(root,'artifacts/job-chain-phase3/browser');
await fs.mkdir(out,{recursive:true});
const origin='http://pan-phase3.test';
const action=(id,name)=>({id,name,description:name,enabled:true,effect:'write_local',output_format:'json',input_schema:{type:'object',properties:{},additionalProperties:false},execution:{kind:'session_operation',operation:'pause_everything',selector:{mode:'explicit',ids:['fixture-session']}}});
const actions=[action('pause','Pause selected Sessions'),action('review','Summarize review')];
const baseConfig={name:'Morning review',description:'Prepare a focused review when the shared morning timer fires.',enabled:true,actionId:'pause',arguments:{},steps:[{stepId:'prepare',actionId:'pause',arguments:{}},{stepId:'summarize',actionId:'review',arguments:{}}],trigger:{kind:'manual'},schedule:[],overlap:{mode:'queue',parallelLimit:3},recovery:{enabled:false,monitoring:false,maxAttempts:3,backoffSec:[5,15,60],stableResetSec:300},completion:{kind:'ordinary'},notification:{on:['completed','failed','uncertain'],target:'job'}};
const owned={id:'trg_morning',name:'Morning review · Team morning',enabled:true,revision:3,eventTypes:['scheduler.time.v1'],eventFilter:{'payload.scheduleId':'schedule_morning'},actionId:'pause',arguments:{},executionMode:'serial',targetJobId:'job_review',ownerJobId:'job_review',ownerLinkId:'morning',createdBy:{type:'user'},createdAt:'2026-10-25T08:00:00Z',updatedAt:'2026-10-25T08:00:00Z'};
const source={id:'schedule_morning',name:'Team morning',enabled:true,revision:4,owner:'scheduler-event-v1',schedule:{kind:'cron',cron:'0 9 * * 1-5',timezone:'Asia/Shanghai'},misfirePolicy:'fire_now',graceSec:300,nextFireAt:'2026-10-26T09:00:00',referencesVersion:'fixture-references',references:[{triggerId:owned.id,name:owned.name,jobId:'job_review',enabled:true}]};
const definitions=[{type:'scheduler.time.v1',available:true,schemaVersion:1,source:{type:'core',id:'scheduler'},schema:{type:'object',required:['scheduleId','scheduleRevision','scheduledAt','emittedAt','eventId']}},{type:'workers.all_idle',available:true,schemaVersion:1,source:{type:'core',id:'workers'},schema:{type:'object',properties:{runningCount:{const:0}}}},{type:'project.review_ready',available:false,schemaVersion:1,source:{type:'plugin',id:'project'},schema:{type:'object'}}];
const snapshot={revision:6,triggers:{[owned.id]:owned,idle:{id:'idle',name:'Pause when everyone is idle',enabled:false,revision:1,eventTypes:['workers.all_idle'],condition:{field:'workers.runningCount',op:'eq',value:0},actionId:'pause',arguments:{},executionMode:'serial',createdBy:{type:'user'},createdAt:'2026-10-25T08:00:00Z',updatedAt:'2026-10-25T08:00:00Z'}},runs:{},definitions,dependencies:{[owned.id]:{available:true,unavailable:[]},idle:{available:true,unavailable:[]}},diagnostics:[],limits:{}};
const evidence={origin,transport:'Playwright route.fulfill / routeWebSocket; no TCP listener',screenshots:[],overflows:[],keyboard:[],pageErrors:[],requests:[],legacyEntry:false};
const browser=await chromium.launch({headless:true,channel:'msedge'});
let latestPage;
try {
  for (const width of [1440,390,320]) {
    const context=await browser.newContext({viewport:{width,height:1050},deviceScaleFactor:1,colorScheme:'dark'});
    await context.addInitScript(()=>localStorage.setItem('pan:theme','dark'));
    await context.routeWebSocket('**',socket=>socket.onMessage(()=>{}));
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!==origin)return route.abort();
      if(url.pathname.startsWith('/api/')) {
        evidence.requests.push({width,path:url.pathname,method:route.request().method()});
        let data={ok:true};
        if(url.pathname==='/api/automation/login')data={token:'visual-fixture-owner',expiresIn:43200};
        else if(url.pathname==='/api/actions')data={revision:2,actions,groups:[]};
        else if(url.pathname==='/api/automation')data=snapshot;
        else if(url.pathname==='/api/jobs/action-jobs')data={jobs:[{jobId:'job_review',name:'Morning review',configRevision:5,config:baseConfig,enabled:true,status:'scheduled',lastStatus:'completed',runCount:12,integrity:{status:'complete',approvalRequired:false,issues:[]}}]};
        else if(url.pathname==='/api/scheduler/event-sources')data={sources:[source,{...source,id:'schedule_weekend',name:'Weekend digest',enabled:false,revision:1,schedule:{kind:'interval',intervalSec:86400,timezone:'UTC'},nextFireAt:'2026-10-26T12:00:00',references:[]}],payloadSchema:definitions[0].schema};
        else if(url.pathname==='/api/automation/job-edits')data={operations:[]};
        else if(url.pathname==='/api/sessions')data={sessions:[],total:0};
        else if(url.pathname==='/api/workspaces')data={workspaces:[]};
        else if(url.pathname==='/api/workers')data={workers:[]};
        else if(url.pathname==='/api/cli/status')data={adapters:[],hasAvailable:true};
        else if(url.pathname==='/api/adapters')data={adapters:[]};
        else if(url.pathname==='/api/plugins/panels')data={panels:[]};
        else if(url.pathname==='/api/main/startup-recovery')data={status:'none',candidates:[]};
        else if(url.pathname==='/api/jobs')data={ok:true,jobs:[],total:0};
        else if(url.pathname==='/api/settings/ui')data={};
        return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
      }
      const relative=url.pathname.startsWith('/react/assets/')?url.pathname.slice('/react/'.length):'index.html';
      const file=path.resolve(dist,relative);
      assert.ok(file.startsWith(dist+path.sep),'asset path escaped build');
      try {await route.fulfill({status:200,path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});}
      catch {await route.abort();}
    });
    const page=await context.newPage();
    latestPage=page;
    page.on('pageerror',error=>evidence.pageErrors.push({width,message:error.message}));
    page.on('dialog',dialog=>dialog.accept());
    async function shot(name,scrollTarget) {
      if(scrollTarget)await scrollTarget.scrollIntoViewIfNeeded();
      await page.evaluate(()=>document.fonts.ready);
      await page.waitForTimeout(100);
      const overflow=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,panel:document.querySelector('[aria-label="Automation management"]')?.scrollWidth,offenders:[...document.querySelectorAll('[aria-label="Automation management"] section, [aria-label="Automation management"] article, [aria-label="Automation management"] fieldset, [aria-label="Automation management"] form')].filter(el=>el.getClientRects().length&&el.clientWidth>0&&el.scrollWidth>el.clientWidth+1).map(el=>({name:el.getAttribute('aria-label')??el.tagName,width:el.clientWidth,scroll:el.scrollWidth}))}));
      evidence.overflows.push({width,name,...overflow});
      assert.ok(overflow.document<=width+1,JSON.stringify({name,...overflow}));
      assert.ok((overflow.panel??0)<=width+1,JSON.stringify({name,...overflow}));
      assert.deepEqual(overflow.offenders,[],JSON.stringify({name,...overflow}));
      const file=`${name}-${width}.png`;
      await page.screenshot({path:path.join(out,file),fullPage:true});
      evidence.screenshots.push(file);
    }
    await page.goto(origin+'/react/automation');
    await page.getByLabel('Local user key').fill('visual-fixture-key');
    await page.getByRole('button',{name:'Log in',exact:true}).click();
    await page.getByRole('article',{name:'Job Morning review',exact:true}).waitFor();
    await page.getByRole('tab',{name:'Job',exact:true}).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab',{name:'Trigger',exact:true})).toHaveAttribute('aria-selected','true');
    await page.keyboard.press('End');
    await expect(page.getByRole('tab',{name:'Event Register',exact:true})).toHaveAttribute('aria-selected','true');
    await page.keyboard.press('Home');
    await expect(page.getByRole('tab',{name:'Job',exact:true})).toHaveAttribute('aria-selected','true');
    const outline=await page.getByRole('tab',{name:'Job',exact:true}).evaluate(el=>getComputedStyle(el).outlineWidth);
    assert.ok(parseFloat(outline)>0,'keyboard focus outline missing');
    evidence.keyboard.push({width,arrows:true,homeEnd:true,focusOutline:outline});
    await shot('job');
    await page.getByRole('tab',{name:'Trigger',exact:true}).click();
    await page.getByText('Managed by Job: Morning review',{exact:true}).waitFor();
    await shot('trigger');
    await page.getByRole('tab',{name:'Event Register',exact:true}).click();
    await page.getByRole('article',{name:'Scheduler Team morning',exact:true}).waitFor();
    await shot('event-register');
    await page.getByRole('button',{name:'Edit Team morning',exact:true}).click();
    await shot('source-impact',page.getByRole('region',{name:'Scheduler impact',exact:true}));
    await page.getByRole('button',{name:'Close Scheduler editor',exact:true}).click();
    await page.getByRole('tab',{name:'Job',exact:true}).click();
    await page.getByRole('button',{name:'New Job',exact:true}).click();
    await page.getByLabel('Job name',{exact:true}).fill('Project review chain');
    await page.getByRole('button',{name:'Add step',exact:true}).click();
    await page.getByLabel('Action for step 1').selectOption('pause');
    await page.getByRole('button',{name:'Add step',exact:true}).click();
    await page.getByLabel('Action for step 2').selectOption('review');
    await shot('chain-editor',page.getByRole('region',{name:'Action chain',exact:true}));
    await page.getByRole('button',{name:'Add time trigger',exact:true}).click();
    await page.getByLabel('Scheduler Event').selectOption('schedule_morning');
    await shot('existing-scheduler-flow',page.getByRole('region',{name:'Time trigger 1',exact:true}));
    await page.getByRole('button',{name:'New Scheduler',exact:true}).click();
    await page.getByLabel('Scheduler name',{exact:true}).fill('Project morning timer');
    await page.getByLabel('Time rule').selectOption('cron');
    await page.getByLabel('Cron expression',{exact:true}).fill('30 9 * * 1-5');
    await shot('new-scheduler-flow',page.getByRole('region',{name:'Time trigger 1',exact:true}));
    await page.getByRole('button',{name:'Close editor',exact:true}).click();
    if(width===1440) {
      const oldEntry=page.getByRole('button',{name:'Jobs',exact:true});
      if(await oldEntry.count()) {await oldEntry.click();await page.waitForURL('**/react/jobs');evidence.legacyEntry=true;}
      else {await page.goto(origin+'/react/jobs');await page.getByText('Jobs',{exact:true}).first().waitFor();evidence.legacyEntry=true;}
    }
    await context.close();
  }
  assert.deepEqual(evidence.pageErrors,[]);
  assert.equal(evidence.screenshots.length,21);
} catch(error) {
  evidence.failure=String(error);
  if(latestPage&&!latestPage.isClosed()) {
    await latestPage.screenshot({path:path.join(out,'failure.png'),fullPage:true});
    await fs.writeFile(path.join(out,'failure.html'),await latestPage.content());
  }
  throw error;
} finally {
  await browser.close();
  await fs.writeFile(path.join(out,'manifest.json'),JSON.stringify(evidence,null,2));
}
console.log(`Verified ${evidence.screenshots.length} screenshots, no horizontal overflow or page errors; legacy entry=${evidence.legacyEntry}`);

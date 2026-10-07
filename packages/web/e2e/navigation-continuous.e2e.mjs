/* global window, document, performance, PerformanceObserver, requestAnimationFrame, process, fetch, console */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import { spawn, execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const root = path.resolve(process.argv[3] || path.resolve(import.meta.dirname, '../../..'));
const mode = process.argv[2] || 'fixed';
const snapshotPath=process.argv[4];
assert.ok(snapshotPath,'Explicit private snapshot required');
const privateSnapshot=JSON.parse(await fs.readFile(snapshotPath,'utf8'));
const original=mode==='continuous-original', rejected=mode==='continuous-rejected';
const output = path.join(root, 'packages/web/test-results', `navigation-full-${mode}-${Date.now()}`);
const runtime = await fs.mkdtemp(path.join(os.tmpdir(), 'pan-nav-browser-'));
await fs.mkdir(output, { recursive: true });
const port = await new Promise(resolve => {
  const probe = net.createServer();
  probe.listen(0, '127.0.0.1', () => { const p = probe.address().port; probe.close(() => resolve(p)); });
});
assert.ok(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe';
const server = spawn(python, [path.join(import.meta.dirname, 'navigationServer.py')], {
  cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime, PAN_NAV_CHECKOUT: root, PAN_NAV_SNAPSHOT:snapshotPath },
});
let logs = '';
server.stdout.on('data', value => { logs += value; });
server.stderr.on('data', value => { logs += value; });
const report = { mode, sha: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root }).toString().trim(),
  root, runtime, port, thresholds:{localFirstMs:250,realCompleteMs:1500,cachedMs:250,compactParseMs:16,notes:'Local isolated regression guards: first/cache under a quarter second; full real snapshot below 1.5s; JSON parse below one 60Hz frame. Not remote-network latency promises. Long Tasks use browser 50ms definition.'}, scenarios: [], errors: [], console: [], protectedRequests: [], passed: false };
let browser, serverProcessIdentity;
async function poll(read, check, timeout = 15000) {
  for (const end = Date.now() + timeout; Date.now() < end;) {
    const value = await read(); if (check(value)) return value; await delay(40);
  }
  throw new Error('Condition timed out');
}
async function visibleRange(page) {
  return page.evaluate(()=>{
    const state=window.__panSessionStore.getState(),transcript=state.sessionTranscripts[state.currentSessionId];
    const scroller=document.querySelector('.chat-view-stage .overflow-auto'),bounds=scroller.getBoundingClientRect();
    const keys=[...scroller.querySelectorAll('[data-scroll-anchor-key][data-index]')].filter(row=>{const r=row.getBoundingClientRect();return r.bottom>bounds.top&&r.top<bounds.bottom;}).map(row=>row.dataset.scrollAnchorKey);
    const offsets=[...transcript.window.rows].filter(([,message])=>keys.some(key=>key.includes(`:${message.messageId}:`))).map(([offset])=>offset);
    return {start:Math.min(...offsets),end:Math.max(...offsets),scrollTop:scroller.scrollTop};
  });
}
function preview(content) {
  const text=content.trimStart();
  const withoutHeader=(/^\/\/\/\/by (?:pan system|system)(?=\s|:|$)/.test(text)
    ?text.replace(/^\/\/\/\/by (?:pan system|system)(?=\s|:|$)/,'').replace(/^[^\S\r\n]*:/,'')
    :text.replace(/^(?:@@@@by agent|\/\/\/\/by agent|@@@@by qq)\s*:\s*[^\r\n]*(?:\r?\n|$)/,''));
  const normalized=withoutHeader.trimStart().replace(/\s+/g,' ').trim();
  return normalized.length<=120?normalized:`${normalized.slice(0,119).trimEnd()}…`;
}
function candidates(history,settings={showMetaAgent:true,showTaskAgent:true,showQQ:true}) {
  return history.flatMap((message,offset)=>{
    const c=(message.content||'').trimStart();
    if((!settings.showMetaAgent&&c.startsWith('////by agent'))||(!settings.showTaskAgent&&c.startsWith('@@@@by agent'))||(!settings.showQQ&&c.startsWith('@@@@by qq')))return [];
    let kind=null;
    if(c.startsWith('@@@@by agent'))kind='worker';
    else if(['user','assistant','system'].includes(message.role)&&/^\/\/\/\/by (?:pan system|system)(?=\s|:|$)/.test(c))kind='system';
    else if(message.role==='user')kind=message.source==='agent'?(c.startsWith('////by agent')||message.taskIdSource==='active'?'maMsg':'maAssign'):'user';
    return kind?[{offset,messageId:message.messageId,kind}]:[];
  });
}
function nearest(targets,range){
  const center=(range.start+range.end)/2,d=i=>Math.max(range.start-i,i-range.end,0);
  return [...targets].sort((a,b)=>d(a.offset)-d(b.offset)||Math.abs(a.offset-center)-Math.abs(b.offset-center)||b.offset-a.offset)[0];
}
try {
  await poll(async()=>{try{return(await fetch(`${base}/api/sessions?summary=1`)).ok;}catch{return false;}},Boolean);
  serverProcessIdentity=await(await fetch(`${base}/__e2e/identity`)).json();
  browser=await chromium.launch({headless:true});report.chromium=browser.version();
  const setup=await browser.newContext();
  const listing=await(await setup.request.get(`${base}/api/sessions?summary=1`)).json();const sessions=listing.sessions;
  const copied=sessions.find(session=>session.name==='Private Navigation Snapshot');assert.ok(copied);
  await setup.request.put(`${base}/api/settings/ui`,{data:{showMessageNavigationRail:true,showTaskAgent:true,showMetaAgent:true,showQQ:true,mergeConsecutiveNonBodyBlocks:false,historyPageSize:200}});
  // Independent snapshot oracle: original IDs remain durable in the copy.
  const oracle=candidates(privateSnapshot.history);
  report.originalPathRevision=original?'223354ae with expanded-prop host compatibility':null;
  report.snapshot={rows:privateSnapshot.history.length,targets:oracle.length,sha256:execFileSync('python',['-c','import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())',snapshotPath]).toString().trim()};
  if(!original&&!rejected){
    let covered=0;
    for(let before=200;covered<privateSnapshot.history.length;before+=200){
      const end=Math.min(before,privateSnapshot.history.length);
      const response=await setup.request.get(`${base}/api/sessions/${copied.id}/navigation?before=${end}&limit=200`);
      assert.equal(response.status(),200);const data=await response.json();
      data.history.forEach((row,i)=>{
        const originalRow=privateSnapshot.history[data.start+i];
        assert.equal(row.messageId,originalRow.messageId,`projection identity ${data.start+i}`);
        assert.equal(row.navigationPreview===preview(originalRow.content||''),true,`preview parity ${data.start+i}`);
        assert.equal(row.content===Array.from((originalRow.content||'').trimStart()).slice(0,64).join(''),true,`classifier head ${data.start+i}`);
      });
      covered=end;
    }
    report.projectionParityRows=covered;
  }
  await setup.close();
  for(const test of (original||rejected?['middle']:['middle','top','tail','retry','automatic','takeover','epoch','append','mobile','session-switch'])){
    const mobile=test==='mobile';
    const context=await browser.newContext({viewport:{width:mobile?390:1120,height:mobile?844:900},isMobile:mobile,hasTouch:mobile});
    const trace=path.join(runtime,`${test}-trace.private.zip`);
    await context.tracing.start({screenshots:true,snapshots:true,sources:true});
    const page=await context.newPage();
    page.on('pageerror',e=>report.errors.push(e.message));
    page.on('console',m=>{if(['warning','error'].includes(m.type()))report.console.push({type:m.type(),text:m.text().slice(0,200)});});
    page.on('request',request=>{if(/:(8767|8768)(\/|$)/.test(request.url()))report.protectedRequests.push(request.url());});
    let testOracle=oracle;
    const scenario={test,requests:[],decodedBytes:0,wireBytes:0,largestResponse:0,maxConcurrent:0};report.scenarios.push(scenario);
    await page.addInitScript(()=>{window.__navMetrics={longTasks:[],frames:[],decodes:[]};new PerformanceObserver(list=>window.__navMetrics.decodes.push(...list.getEntries().filter(e=>e.name==='message-navigation-decode').map(e=>e.duration))).observe({type:'measure',buffered:false});new PerformanceObserver(list=>window.__navMetrics.longTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});});
    try{
      await page.goto(`${base}/react/?panE2E=1`);
      if(mobile)await page.getByRole('button',{name:'打开侧边栏'}).click();
      await page.locator('[data-session-card-id]').filter({hasText:copied.name}).first().click();
      if(mobile)await page.getByTestId('mobile-sidebar-close').click();
      await page.waitForFunction(()=>window.__panSessionStore.getState().currentMessages.length>0&&!window.__panSessionStore.getState().historyLoading);
      const scroller=page.locator('.chat-view-stage .overflow-auto').first();
      // Populate only the needed real canonical window, never change message
      // identities. Body request cost belongs to reading preparation separately.
      if(test!=='tail')await page.evaluate(total=>window.__panSessionStore.getState().ensureMessageLoaded(total-1,total),privateSnapshot.history.length);
      await page.evaluate(()=>window.__panSessionStore.setState({hasMoreMessages:false}));
      await scroller.hover();await page.mouse.wheel(0,-120);await delay(200);
      await scroller.evaluate((el,f)=>{el.scrollTop=(el.scrollHeight-el.clientHeight)*f;},test==='top'?0:test==='tail'?1:0.5);
      await delay(1000);
      const before=await visibleRange(page);assert.ok(Number.isFinite(before.start));
      if(!original&&test!=='tail')await page.evaluate(range=>{
        const state=window.__panSessionStore.getState(),transcript=state.sessionTranscripts[state.currentSessionId];
        // Fault/nearby fixture retains only the visible canonical neighbourhood;
        // rendered messages stay real and identities remain unchanged.
        window.__navSavedRows=new Map(transcript.window.rows);
        transcript.window.rows=new Map([...transcript.window.rows].filter(([offset])=>offset>=range.start-10&&offset<=range.end+10));
      },before);
      scenario.viewport={start:before.start,end:before.end};scenario.expected=nearest(oracle,before)?.offset;
      let active=0,calls=0,fail=test==='retry';const bodies=[];
      const routePattern=`**/api/sessions/${copied.id}/${original||rejected?'history':'navigation'}?*`;
      await page.route(routePattern,async route=>{
        calls++;active++;scenario.maxConcurrent=Math.max(scenario.maxConcurrent,active);scenario.requests.push(route.request().url().replace(base,''));
        try{
          if(fail||(test==='automatic'&&calls===1))await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Injected compact-index temporary outage'})});
          else {if(['epoch','takeover','session-switch'].includes(test))await delay(300);await route.continue();}
        }catch{/* aborted old generation */}finally{active--;}
      });
      page.on('response',response=>{
        if(!response.url().includes(`/${original||rejected?'history':'navigation'}?`))return;
        bodies.push((async()=>{try{const raw=await response.body();scenario.decodedBytes+=raw.length;scenario.largestResponse=Math.max(scenario.largestResponse,raw.length);}catch{/* aborted response */}})());
      });
      await page.evaluate(()=>{
        window.__navMetrics.longTasks=[];window.__navMetrics.decodes=[];const start=performance.now();let last=start;
        const frame=now=>{window.__navMetrics.frames.push(now-last);last=now;if(now-start<5000)requestAnimationFrame(frame);};requestAnimationFrame(frame);
      });
      const resourceStart=await page.evaluate(()=>performance.now());
      const started=performance.now();
      const open=async()=>{if(mobile)await page.getByTestId('mobile-message-navigation-toggle').click();else await page.locator('.message-navigation-dock__handle').hover();};
      await open();
      const rail=page.locator('.message-navigation-rail');
      const status=()=>rail.getAttribute('data-index-status');
      const selected=()=>page.locator('.is-viewport-target').getAttribute('data-canonical-offset').catch(()=>null);
      if(test==='session-switch'){
        await poll(()=>calls,n=>n>0);
        const other=sessions.find(session=>session.id!==copied.id&&session.name!=='Dense Navigation'&&session.name!=='Sparse Navigation');
        await page.locator(`[data-session-card-id="${other.id}"]`).click();await delay(700);
        assert.equal(await page.evaluate(()=>window.__panSessionStore.getState().currentSessionId),other.id);
        const oldIds=new Set(oracle.map(target=>target.messageId));
        const shownIds=await page.locator('.message-navigation-marker').evaluateAll(markers=>markers.map(m=>m.dataset.messageId));
        assert.ok(shownIds.every(id=>!oldIds.has(id)));scenario.lateOldIdentityRejected=true;scenario.passed=true;continue;
      }
      if(test==='takeover'){
        await scroller.hover();await page.mouse.wheel(0,-100);await delay(150);
        // Pointer over the rail keeps it open while the user owns reading.
        await page.locator('.message-navigation-dock__handle').hover();
      }
      if(test==='retry'){
        await page.getByRole('button',{name:'Retry message navigation'}).waitFor();assert.equal(calls,3);
        await delay(700);assert.equal(calls,3);
        assert.ok(await page.locator('.message-navigation-marker').count()>0,'keep available targets');
        fail=false;await page.getByRole('button',{name:'Retry message navigation'}).dblclick();
      }
      if(test==='epoch'){
        const replaced=privateSnapshot.history.map((m,i)=>({...m,messageId:`isolated-new-epoch-${i}`}));
        await context.request.post(`${base}/__e2e/replace-history`,{data:{sessionId:copied.id,messages:replaced}});
        await page.evaluate(()=>window.__panSessionStore.getState().refreshCurrentSessionHistory());
        scenario.epochReplacement=true;
      }
      if(rejected){
        await poll(status,value=>value==='ready'||value==='error');
        for(let steps=0;await status()!=='error'&&steps<10;steps++){
          const earlier=page.getByRole('button',{name:'Load earlier navigation'});
          if(!await earlier.count())break;
          await earlier.click();await poll(status,value=>value!=='loading');
        }
        await page.getByRole('button',{name:'Retry message navigation'}).waitFor({timeout:10000});
        scenario.failureObserved=true;scenario.errorTitle=await page.getByRole('button',{name:'Retry message navigation'}).getAttribute('title');
        const firstCalls=calls;await page.getByRole('button',{name:'Retry message navigation'}).click();
        await poll(()=>calls,n=>n>firstCalls);await poll(status,s=>s==='error');scenario.retryStillFails=true;
      }else{
        if(!original&&test!=='takeover'&&test!=='epoch'){
          await poll(selected,s=>Number(s)===scenario.expected,10000);scenario.firstMs=performance.now()-started;
        }
        await poll(status,s=>s==='ready',20000);scenario.fullMs=performance.now()-started;
        if(test==='append'){
          const appendStarted=performance.now();
          await context.request.post(`${base}/__e2e/append-history`,{data:{sessionId:copied.id,messages:[{role:'user',content:'isolated large-body append '+ 'x'.repeat(2_000_000)},{role:'system',content:'////by pan system: isolated append'}]}});
          const appendResponse=await context.request.get(`${base}/api/sessions/${copied.id}/history?before=${privateSnapshot.history.length+2}&limit=2`);
          const appendPage=await appendResponse.json();
          testOracle=[...oracle,...candidates(appendPage.history).map(t=>({...t,offset:t.offset+privateSnapshot.history.length}))];
          await page.evaluate(()=>window.__panSessionStore.getState().refreshCurrentSessionHistory());
          await poll(()=>rail.getAttribute('data-history-total'),s=>Number(s)===privateSnapshot.history.length+2);
          await poll(status,s=>s==='ready');scenario.appendMs=performance.now()-appendStarted;
          scenario.appendVerified=true;
        }
        scenario.indexedTargets=Number(await rail.getAttribute('data-indexed-targets'));
        if(!original)assert.equal(scenario.indexedTargets,testOracle.length,'full target count');
        assert.equal(await page.getByRole('button',{name:'Load earlier navigation'}).count(),0);
        assert.equal(await page.getByRole('button',{name:'Load later navigation'}).count(),0);
        assert.equal(await page.getByRole('button',{name:'Load more nearby navigation'}).count(),0);
        // Full scroll traversal of a single continuous list. No network fetch
        // can be triggered by traversal after completion. Compare every ID.
        const list=page.locator('.message-navigation-list'),seen=new Map();
        const requestCount=calls;
        for(let y=0,guard=0;;guard++){
          assert.ok(guard<2000);
          await list.evaluate((el,y)=>{el.scrollTop=y;},y);await delay(20);
          const rows=await page.locator('.message-navigation-marker').evaluateAll(markers=>markers.map(m=>({offset:Number(m.dataset.canonicalOffset??(window.__panSessionStore.getState().sessionTranscripts[window.__panSessionStore.getState().currentSessionId].window.total-1-Number(m.dataset.fromEnd))),messageId:m.dataset.messageId,kind:m.dataset.kind})));
          rows.forEach(row=>seen.set(row.offset,row));
          const geometry=await list.evaluate(el=>({top:el.scrollTop,height:el.clientHeight,total:el.scrollHeight}));
          if(geometry.top+geometry.height>=geometry.total-1)break;
          y+=Math.max(100,geometry.height/2);
        }
        assert.deepEqual([...seen.keys()].sort((a,b)=>a-b),testOracle.map(t=>t.offset),'every target is continuously reachable');
        if(!original)for(const target of testOracle){const row=seen.get(target.offset);assert.equal(row.kind,target.kind);if(test!=='epoch')assert.equal(row.messageId,target.messageId);else assert.equal(row.messageId,`isolated-new-epoch-${target.offset}`);}
        assert.equal(calls,requestCount,'scroll cannot trigger pagination');scenario.visitedTargets=seen.size;
        scenario.bodyDelta=(await visibleRange(page)).scrollTop-before.scrollTop;
        if(!['takeover','epoch','append'].includes(test))assert.ok(Math.abs(scenario.bodyDelta)<=1,'navigation cannot move chat body');
        scenario.maxRendered=await page.locator('.message-navigation-marker').count();
        if(!original)assert.ok(scenario.maxRendered<=100);
        scenario.metrics=await page.evaluate(()=>({longTasks:window.__navMetrics.longTasks,maxFrame:Math.max(0,...window.__navMetrics.frames)}));
        if(!original&&['middle','top','tail'].includes(test)){
          await page.mouse.move(5,5);await delay(200);
          const reopen=performance.now(),cacheRequests=calls;await open();
          await poll(selected,s=>Number(s)===scenario.expected);scenario.cachedMs=performance.now()-reopen;
          assert.equal(calls,cacheRequests);assert.ok(scenario.cachedMs<report.thresholds.cachedMs);
          assert.ok(scenario.firstMs<report.thresholds.localFirstMs);assert.ok(scenario.fullMs<report.thresholds.realCompleteMs);assert.deepEqual(scenario.metrics.longTasks,[]);
        }
        if(test==='middle'||mobile){
          await page.evaluate(()=>{const state=window.__panSessionStore.getState();if(window.__navSavedRows)state.sessionTranscripts[state.currentSessionId].window.rows=window.__navSavedRows;});
          const ordinal=testOracle.findIndex(target=>target.offset===scenario.expected);
          await list.evaluate((el,{ordinal,height})=>{el.scrollTop=Math.max(0,ordinal*height-el.clientHeight/2);},{ordinal,height:mobile?45:27});await delay(50);
          const marker=page.locator(`.message-navigation-marker[data-canonical-offset="${scenario.expected}"]`);
          if(mobile){
            const rect=await marker.boundingBox();assert.ok(rect);const cdp=await context.newCDPSession(page);
            const point={x:rect.x+rect.width/2,y:rect.y+rect.height/2,id:1};const bodyBefore=await scroller.evaluate(el=>el.scrollTop);
            await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[point]});await delay(500);
            assert.equal(await page.locator('[data-preview-mode="scrub"]').count(),1);
            const otherOffset=await page.locator('.message-navigation-marker:not(.is-viewport-target)').evaluateAll(markers=>markers.find(m=>{const r=m.getBoundingClientRect();return r.y>70&&r.bottom<window.innerHeight-70;})?.dataset.canonicalOffset);
            const other=page.locator(`.message-navigation-marker[data-canonical-offset="${otherOffset}"]`);const otherRect=await other.boundingBox();
            if(otherRect&&otherRect.y>0){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:otherRect.x+otherRect.width/2,y:otherRect.y+otherRect.height/2,id:1}]});await delay(50);}
            await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await delay(120);
            assert.equal(await scroller.evaluate(el=>el.scrollTop),bodyBefore);assert.equal(await page.locator('[data-preview-mode="scrub"]').count(),0);scenario.scrubReleaseNoJump=true;
            await marker.tap();
          }else await marker.click();
          await poll(()=>page.locator(`.message-navigation-marker.is-jumped[data-canonical-offset="${scenario.expected}"]`).count(),n=>n===1);
          const clickedRange=await visibleRange(page);assert.ok(clickedRange.start<=scenario.expected&&clickedRange.end>=scenario.expected);scenario.clickJumpVerified=true;
          assert.equal(await page.locator('.message-navigation-jump-error').count(),0);
        }
        if(test==='retry')assert.equal(scenario.maxConcurrent,1);
        if(test==='automatic')assert.ok(calls>=2);
      }
      await Promise.allSettled(bodies);
      const resources=await page.evaluate(({start,legacy})=>performance.getEntriesByType('resource').filter(entry=>entry.startTime>=start&&entry.name.includes(legacy?'/history?':'/navigation?')).map(entry=>({encoded:entry.encodedBodySize,decoded:entry.decodedBodySize,duration:entry.duration})),{start:resourceStart,legacy:original||rejected});
      scenario.decode=await page.evaluate(()=>{const entries=window.__navMetrics.decodes;return {count:entries.length,totalMs:entries.reduce((sum,duration)=>sum+duration,0),maxMs:Math.max(0,...entries)};});
      if(!original&&!rejected)assert.ok(scenario.decode.maxMs<report.thresholds.compactParseMs);
      scenario.resourceTiming=resources;scenario.wireBytes=resources.reduce((sum,r)=>sum+r.encoded,0);
      await page.screenshot({path:path.join(runtime,`${test}.private.png`)});
      scenario.passed=true;
      if(['epoch','append'].includes(test))await context.request.post(`${base}/__e2e/replace-history`,{data:{sessionId:copied.id,messages:privateSnapshot.history}});
    }finally{
      await context.tracing.stop({path:trace});await context.close();
      await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(report,null,2));
    }
  }
  if(!original&&!rejected)for(const density of ['dense','sparse']){
    const context=await browser.newContext({viewport:{width:1120,height:900}});
    await context.tracing.start({screenshots:true,snapshots:true,sources:true});
    const dense=sessions.find(session=>session.name===(density==='dense'?'Dense Navigation':'Sparse Navigation'));
    const total=density==='dense'?10000:24000;
    const denseMessages=Array.from({length:total},(_,i)=>({role:density==='dense'||i%4000===0||i===total-1?'user':'assistant',content:`isolated ${density} ${i}`}));
    await context.request.post(`${base}/__e2e/append-history`,{data:{sessionId:dense.id,messages:denseMessages}});
    const identities=[];
    for(let before=1000;before<=total;before+=1000){const data=await(await context.request.get(`${base}/api/sessions/${dense.id}/history?before=${before}&limit=1000&searchJump=true`)).json();identities.push(...data.history.map(row=>({id:row.messageId,role:row.role})));}
    const page=await context.newPage();page.on('pageerror',error=>report.errors.push(error.message));
    const scenario={test:`${density}-continuous`,total};report.scenarios.push(scenario);
    try{
      await page.goto(`${base}/react/?panE2E=1`);await page.locator('[data-session-card-id]').filter({hasText:dense.name}).first().click();
      await page.waitForFunction(()=>window.__panSessionStore.getState().currentMessages.length>0&&!window.__panSessionStore.getState().historyLoading);
      await delay(500);const before=await visibleRange(page);
      const targetIdentities=identities.flatMap((row,offset)=>row.role==='user'?[{offset,messageId:row.id}]:[]);
      const expected=nearest(targetIdentities,before).offset;
      await page.evaluate(()=>{window.__densePerf={tasks:[],frames:[]};new PerformanceObserver(list=>window.__densePerf.tasks.push(...list.getEntries().map(e=>e.duration))).observe({type:'longtask',buffered:false});let prev=performance.now();function tick(now){window.__densePerf.frames.push(now-prev);prev=now;requestAnimationFrame(tick);}requestAnimationFrame(tick);});
      let requests=0;page.on('request',request=>{if(request.url().includes('/navigation?'))requests++;});
      const started=performance.now();await page.locator('.message-navigation-dock__handle').hover();
      await poll(()=>page.locator('.is-viewport-target').getAttribute('data-canonical-offset').catch(()=>null),value=>Number(value)===expected);
      scenario.firstMs=performance.now()-started;
      await poll(()=>page.locator('.message-navigation-rail').getAttribute('data-index-status'),value=>value==='ready',30000);
      scenario.fullMs=performance.now()-started;scenario.requests=requests;
      assert.equal(Number(await page.locator('.message-navigation-rail').getAttribute('data-indexed-targets')),targetIdentities.length);
      const list=page.locator('.message-navigation-list'),visited=new Map();let maxRendered=0;
      for(let top=0,guard=0;;guard++){
        assert.ok(guard<1000);await list.evaluate((el,y)=>{el.scrollTop=y;},top);await delay(10);
        const rows=await page.locator('.message-navigation-marker').evaluateAll(markers=>markers.map(m=>({offset:Number(m.dataset.canonicalOffset),id:m.dataset.messageId})));
        maxRendered=Math.max(maxRendered,rows.length);rows.forEach(row=>visited.set(row.offset,row.id));
        const g=await list.evaluate(el=>({top:el.scrollTop,height:el.clientHeight,total:el.scrollHeight}));
        if(g.top+g.height>=g.total-1)break;top+=g.height/2;
      }
      assert.equal(visited.size,targetIdentities.length);for(const target of targetIdentities)assert.equal(visited.get(target.offset),target.messageId);
      scenario.metrics=await page.evaluate(()=>({longTasks:window.__densePerf.tasks,maxFrame:Math.max(...window.__densePerf.frames)}));
      assert.equal(requests,scenario.requests);assert.ok(maxRendered<=100);scenario.maxRendered=maxRendered;scenario.visited=visited.size;
      assert.equal((await visibleRange(page)).scrollTop,before.scrollTop);scenario.passed=true;
    }finally{await context.tracing.stop({path:path.join(runtime,`${density}-trace.private.zip`)});await context.close();}
  }
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.protectedRequests,[]);report.passed=true;
} catch (error) { report.failure = error.stack; process.exitCode = 1; }
finally {
  await browser?.close();
  const identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'));
  report.serverIdentity = { ...identity, launcherPid: server.pid }; assert.equal(identity.port, port); assert.equal(path.resolve(identity.checkout), root);
  assert.equal(identity.pid, serverProcessIdentity.pid);
  const stop = 'import json,sys,psutil; v=json.loads(sys.argv[1]); p=psutil.Process(v["pid"]); assert abs(p.create_time()-v["processCreatedAt"])<0.001; assert any(c.laddr.port==v["port"] and c.status=="LISTEN" for c in p.net_connections(kind="inet")); p.terminate()';
  execFileSync(python, ['-c', stop, JSON.stringify(serverProcessIdentity)], { windowsHide: true });
  await poll(async () => new Promise(resolve => { const socket = net.connect({ host: '127.0.0.1', port }); socket.once('connect', () => { socket.destroy(); resolve(false); }); socket.once('error', () => resolve(true)); }), Boolean, 5000);
  report.cleanup = { portFree: true, ownedPid: identity.pid };
  await fs.writeFile(path.join(output, 'server.log'), logs);
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ output,sha:report.sha,passed:report.passed,failure:report.failure,scenarios:report.scenarios.length,cleanup:report.cleanup }));
}

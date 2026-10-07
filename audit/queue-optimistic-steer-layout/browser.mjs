import { chromium } from '../../packages/web/node_modules/@playwright/test/index.mjs';
import { writeFileSync } from 'node:fs';
const browser = await chromium.launch({headless:true});
const page = await browser.newPage();
// Every API/WS request is blocked or supplied locally. No Vite proxy request
// reaches a canonical service.
await page.route('**/api/**', route => route.fulfill({json:{}}));
await page.routeWebSocket(/.*/, ws => ws.close());
await page.route('http://127.0.0.1:18766/', route => route.fulfill({contentType:'text/html',body:'<!doctype html><html><body></body></html>'}));
await page.goto('http://127.0.0.1:18766/');
const evidence = await page.evaluate(async () => {
  const {useQueueStore:q} = await import('/stores/queueStore.ts');
  const {useSessionStore:s} = await import('/stores/sessionStore.ts');
  const {useWorkerStore:w} = await import('/stores/workerStore.ts');
  const out=[];
  for (const state of ['paused','missing','recovering','running','locked','reports-paused']) {
    const sid='experiment-'+state;
    s.setState({currentSessionId:sid,currentMessages:[],sessions:[{id:sid,adapter:'codex'}]});
    w.setState({workers:state==='missing'?{}:{[sid]:{sessionId:sid,status:state==='running'?'running':state==='recovering'?'queued':'idle'}}});
    const item={id:'q-'+state,queueItemId:'q-'+state,kind:'task',text:'not delivered '+state,source:'user',meta:{dispatchState:'queued',revision:1,locked:state==='locked'}};
    const old=window.fetch;
    window.fetch=async()=>new Response(JSON.stringify({ok:true,item,items:[item],queueRevision:1,queuePaused:state==='paused',agentReportsPaused:state==='reports-paused'}),{headers:{'Content-Type':'application/json'}});
    const ok=await q.getState().enqueue(item.text,undefined,sid);
    window.fetch=old;
    out.push({input:state,http:'persisted queued',ok,queue:q.getState().queues[sid],ui:s.getState().currentMessages});
  }
  for (const mode of ['http-failure','ws-before-http','session-switch','duplicate-receipt']) {
    const sid='edge-'+mode, other='edge-other-'+mode;
    s.setState({currentSessionId:sid,currentMessages:[],sessions:[{id:sid,adapter:'codex'},{id:other,adapter:'codex'}]});
    const item={id:'q-'+mode,queueItemId:'q-'+mode,kind:'task',text:'full body '+mode,source:'agent',meta:{dispatchState:'queued',revision:1}};
    q.setState({queueRevisions:{...q.getState().queueRevisions,[sid]:1}});
    const old=window.fetch; let resolve;
    window.fetch=()=>new Promise(r=>{resolve=r;});
    const pending=q.getState().enqueue(item.text,undefined,sid,'client-'+mode);
    if(mode==='ws-before-http'){
      q.getState().applyQueueEvent({type:'queue.item_delivered',sessionId:sid,queueItemIds:[item.id],queueRevision:2,messages:[{role:'user',content:item.text,messageId:'pan:'+('a'.repeat(32)),queueItemIds:[item.id],source:'agent'}]});
    }
    if(mode==='session-switch')s.setState({currentSessionId:other,currentMessages:[]});
    resolve(new Response(JSON.stringify(mode==='http-failure'?{error:'isolated HTTP failure'}:{ok:true,item,items:[item],queueRevision:1,queuePaused:false}),{status:mode==='http-failure'?500:200,headers:{'Content-Type':'application/json'}}));
    const ok=await pending;
    if(mode==='duplicate-receipt'){
      const event={type:'queue.item_delivered',sessionId:sid,queueItemIds:[item.id],queueRevision:2,messages:[{role:'user',content:item.text,messageId:'pan:'+('b'.repeat(32)),queueItemIds:[item.id],source:'agent'}]};
      q.getState().applyQueueEvent(event);q.getState().applyQueueEvent(event);
    }
    window.fetch=old;
    out.push({input:mode,ok,queue:q.getState().queues[sid]||[],currentSessionId:s.getState().currentSessionId,ui:s.getState().currentMessages});
  }
  return out;
});
writeFileSync(new URL(process.argv[2]||'baseline-ui.json',import.meta.url),JSON.stringify(evidence,null,2));
console.log(JSON.stringify(evidence.map(x=>({input:x.input,ok:x.ok,queue:x.queue.length,chat:x.ui.length}))));
await browser.close();

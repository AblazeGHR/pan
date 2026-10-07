import { chromium } from '../../packages/web/node_modules/@playwright/test/index.mjs';
import { writeFileSync } from 'node:fs';
const browser=await chromium.launch({headless:true});
const results=[];
for (const [name,width,height] of [['desktop',1280,800],['mobile',390,740],['small',320,280]]) {
  const page=await browser.newPage({viewport:{width,height}});
  await page.addInitScript(()=>{
    window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;
    window.__vite_plugin_react_preamble_installed__=true;
  });
  await page.route('http://127.0.0.1:18766/',route=>route.fulfill({contentType:'text/html',body:'<html class="dark"><head><meta name="viewport" content="width=device-width, initial-scale=1"><script type="module">import "/index.css";</script></head><body></body></html>'}));
  const errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  const item={id:'q-edit',queueItemId:'q-edit',kind:'task',source:'user',text:'preview',createdAt:1,meta:{dispatchState:'queued',revision:3}};
  let saved=null;
  let released=0;
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    let data={};
    if(url.pathname.endsWith('/edit')) data={ok:true,text:'全文正文\n第二行',bodyFormat:'text',revision:3,expiresAt:Date.now()/1000+120};
    else if(url.pathname.endsWith('/edit/release')) {released++;data={ok:true};}
    else if(url.pathname.endsWith('/q-edit')&&req.method()==='PATCH') {saved=req.postDataJSON();data={ok:true,items:[item],queueRevision:4};}
    else if(url.pathname.endsWith('/queue')) data={items:[item],queueRevision:1,queuePaused:false,agentReportsPaused:false};
    else if(url.pathname.endsWith('/adapters')) data={adapters:[{name:'codex',supportsSteer:true},{name:'cbc',supportsSteer:false}]};
    else if(url.pathname.endsWith('/sessions')) data={sessions:[]};
    await route.fulfill({json:data});
  });
  await page.routeWebSocket(/.*/,ws=>ws.close());
  await page.goto('http://127.0.0.1:18766/');
  const componentSource=await (await page.request.get('http://127.0.0.1:18766/components/chat/InputRow.tsx')).text();
  const mainSource=await (await page.request.get('http://127.0.0.1:18766/main.tsx')).text();
  // Use Vite's actual per-dependency URLs so Router/React contexts have one
  // module instance even after incremental dependency optimization.
  const moduleUrls=[componentSource.match(/from "([^"]*\/react\.js[^"]*)"/)[1],
    mainSource.match(/from "([^"]*react-dom_client\.js[^"]*)"/)[1],
    componentSource.match(/from "([^"]*react-router-dom\.js[^"]*)"/)[1]];
  const storeUrls=Object.fromEntries(['session','queue','adapter','worker'].map(key=>[key,componentSource.match(new RegExp('from "([^"]*\\/stores\\/'+key+'Store\\.ts[^"]*)"'))[1]]));
  await page.evaluate(async ({item,moduleUrls,storeUrls})=>{
    const {default:React}=await import(moduleUrls[0]);
    const client=await import(moduleUrls[1]);
    const createRoot=client.createRoot||client.default.createRoot;
    const router=await import(moduleUrls[2]);
    const MemoryRouter=router.MemoryRouter||router.default.MemoryRouter;
    const {InputRow}=await import('/components/chat/InputRow.tsx');
    const {useSessionStore:s}=await import(storeUrls.session);
    const {useQueueStore:q}=await import(storeUrls.queue);
    const {useAdapterStore:a}=await import(storeUrls.adapter);
    const {useWorkerStore:w}=await import(storeUrls.worker);
    const sid='component';
    s.setState({currentSessionId:sid,currentMessages:[],inputDrafts:{[sid]:'普通草稿保留'},sessions:[{id:sid,name:'Fixture',adapter:'codex',model:null,history:[],workerStatus:'running'}]});
    q.setState({queues:{[sid]:[item]},queueRevisions:{[sid]:1},queuePaused:{[sid]:false},queuePauseLoaded:{[sid]:true},panelOpen:false});
    a.setState({adapters:[{name:'codex',supportsSteer:true}],currentAdapter:'codex'});
    w.setState({workers:{[sid]:{id:'w',sessionId:sid,status:'running'}}});
    const mount=document.createElement('div');
    mount.id='experiment-root';
    mount.style='position:fixed;inset:0;display:flex;flex-direction:column;justify-content:flex-end;background:var(--color-bg-primary);z-index:10000';
    document.body.append(mount);
    createRoot(mount).render(React.createElement(MemoryRouter,null,React.createElement(InputRow)));
    window.experiment={s,q,a,w};
  },{item,moduleUrls,storeUrls});
  await page.waitForSelector('#experiment-root [data-testid="rich-text-composer"]',{timeout:10000}).catch(async error=>{
    console.log(JSON.stringify({errors,html:await page.locator('#experiment-root').innerHTML()}));throw error;
  });
  await page.evaluate(()=>window.experiment.q.getState().startEdit('q-edit'));
  await page.waitForSelector('#experiment-root [data-testid="queue-rich-text-composer"]',{timeout:10000}).catch(async error=>{
    console.log(JSON.stringify({errors,html:await page.locator('#experiment-root').innerHTML(),state:await page.evaluate(()=>({sid:window.experiment.s.getState().currentSessionId,queue:window.experiment.q.getState().queues,edit:window.experiment.q.getState().edits}))}));throw error;
  });
  const measure=()=>page.evaluate(()=>{
    const root=document.querySelector('#experiment-root');
    const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
    const editor=root.querySelector('[data-testid="queue-rich-text-composer"]');
    const cancel=root.querySelector('[aria-label="取消队列编辑"]'),save=root.querySelector('[aria-label="保存队列编辑"]');
    return {editor:rect(editor),cancel:rect(cancel),save:rect(save),header:rect(cancel.parentElement.parentElement),bodyScroll:document.body.scrollTop,rootScroll:root.scrollTop,
      text:editor.textContent,ordinaryDraft:window.experiment.s.getState().inputDrafts.component,buttonsVisible:save.getBoundingClientRect().bottom<=innerHeight&&cancel.getBoundingClientRect().top>=0};
  });
  const initial=await measure();
  // A long attachment/error area must scroll independently of the header.
  await page.evaluate(()=>window.experiment.q.setState({edits:{component:{...window.experiment.q.getState().edits.component,error:'长错误 '.repeat(300),attachments:Array.from({length:20},(_,i)=>({occurrenceId:'a'+i,id:'a'+i,displayName:'附件'+i,status:'error',error:'失败'}))}}}));
  const crowded=await measure();
  await page.evaluate(()=>window.experiment.q.setState({edits:{component:{...window.experiment.q.getState().edits.component,error:undefined,attachments:[]}}}));
  const editor=page.locator('#experiment-root [data-testid="queue-rich-text-composer"]');
  await editor.fill('编辑内容\n'+('正文滚动\n'.repeat(40)));
  await editor.hover();
  await page.mouse.wheel(0,300);
  await page.waitForTimeout(60);
  const scrolling=await page.evaluate(()=>({editorScroll:document.querySelector('#experiment-root [data-testid="queue-rich-text-composer"]').scrollTop,rootScroll:document.querySelector('#experiment-root').scrollTop,bodyScroll:document.body.scrollTop}));
  let fullscreen=null,resize=null;
  if(width<768){
    await page.locator('#experiment-root [data-testid="queue-edit-fullscreen"]').click();
    fullscreen=await measure();
    await page.screenshot({path:new URL(name+'-fullscreen.png',import.meta.url).pathname.replace(/^\//,'')});
    await page.locator('#experiment-root [data-testid="queue-edit-fullscreen"]').click();
  }else{
    const handle=page.locator('#experiment-root [data-testid="desktop-composer-resize"]');
    const before=await handle.boundingBox();
    await page.mouse.move(before.x+100,before.y+2);await page.mouse.down();await page.mouse.move(before.x+100,before.y-60);await page.mouse.up();
    resize=await measure();
  }
  await page.screenshot({path:new URL(name+'.png',import.meta.url).pathname.replace(/^\//,'')});
  await page.locator('#experiment-root [aria-label="取消队列编辑"]').click();
  await page.waitForSelector('#experiment-root [data-testid="queue-rich-text-composer"]',{state:'detached'});
  const cancelRestore=await page.evaluate(()=>({draft:window.experiment.s.getState().inputDrafts.component,ordinaryText:document.querySelector('#experiment-root [data-testid="rich-text-composer"]').textContent}));
  results.push({name,initial,crowded,scrolling,fullscreen,resize,cancelRestore,released,saved,errors});
  await page.close();
}
writeFileSync(new URL('component-results.json',import.meta.url),JSON.stringify(results,null,2));
console.log(JSON.stringify(results));
await browser.close();

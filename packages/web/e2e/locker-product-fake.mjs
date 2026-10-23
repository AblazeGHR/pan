import { chromium, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, extname } from 'node:path';
const evidence = fileURLToPath(new URL('../../../audit/locker-product-integration-20261023', import.meta.url));
await mkdir(evidence, { recursive:true });
const dist=fileURLToPath(new URL('../dist/', import.meta.url));
const server=createServer(async(req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  if(pathname.startsWith('/api/') || pathname.startsWith('/ws')){res.writeHead(503);res.end();return;}
  const file=pathname.startsWith('/react/assets/') ? resolve(dist, pathname.slice('/react/'.length)) : resolve(dist,'index.html');
  if(!file.startsWith(dist)){res.writeHead(404);res.end();return;}
  try{const data=await readFile(file);res.setHeader('content-type',({'.js':'text/javascript','.css':'text/css','.html':'text/html'})[extname(file)]||'application/octet-stream');res.end(data);}catch{res.writeHead(404);res.end();}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const port=server.address().port;
if([8767,8768].includes(port))throw Error('protected port');
const base=`http://127.0.0.1:${port}`;
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:900}});
const errors=[], requests=[], checks=[];
page.on('pageerror',e=>errors.push(e.message));
const make=(id,owner,extra={})=>({id,lockerId:`locker-${owner}`,ownerSessionId:`ses_${owner}`,createdAt:'2026-10-23T01:00:00Z',read:false,revision:0,status:'open',definition:{title:`Review ${id}`,body:'## Review body\n\n**Markdown** with a source-scoped attachment.\n\n```ts\nconst ready = true;\n```',summaryBody:'A'.repeat(260),answerInput:{label:'Answer or BTW'},attachments:[{id:'att_'+ 'a'.repeat(32),name:'review-notes.txt'}],choices:[]},response:null,delivery:{state:'not_requested'},rejectionDelivery:{state:'not_requested'},...extra});
let cards=[make('answer','one'),make('approval','two',{definition:{title:'Approve staging handoff',body:'Please review the isolated staging handoff.',summaryBody:'B'.repeat(260),choices:[{id:'approve',label:'Approve',triggerRef:{id:'trigger-1'}},{id:'reject',label:'Reject'}]},trustedChoices:{approve:{summary:'Queue staging review',targetIds:['ses_target'],actionId:'review',actionDigest:'d'.repeat(64),triggerRevision:1,arguments:{}}}}),make('old','old',{status:'rejected',response:{outcome:'reject',answer:'Keep frontend aggregation.'}})];
const lockers=[['one','Design agent \u00b7 a very long source name for width checks',['workspace-a'],true],['two','Release agent',['workspace-a'],true],['old','Archived source',[],false]].map(([id,name,workspaceIds,available])=>({id:`locker-${id}`,sessionId:`ses_${id}`,sourceName:name,workspaceIds,available,total:1,unread:1}));
const sessions = ['one','two'].map(id => ({id:`ses_${id}`, name:`Source ${id}`, adapter:'codex', workerStatus:'offline', history:[],historyTotal:0,workspaceIds:['workspace-a'],updatedAt:0}));
let fail=false;
await page.route('**/*',async route=>{
 const req=route.request(),u=new URL(req.url());
 if(u.origin!==base){await route.abort();return;}
 if(!u.pathname.startsWith('/api/')){await route.continue();return;}
 const path=u.pathname, body=req.postData()?req.postDataJSON():null;
 requests.push({path,method:req.method(),body});
 let json={sessions:[],workers:[],workspaces:[],panels:[],jobs:[],adapters:[]};
 if(path==='/api/sessions')json={sessions};
 if(/\/api\/sessions\/[^/]+\/history/.test(path))json={messages:[],total:0,start:0};
 if(path==='/api/workspaces')json={workspaces:[{id:'workspace-a',name:'Workspace A',dirs:[]}]};
 if(path==='/api/automation/login')json={token:'fake-test-only',expiresIn:3600};
 if(path==='/api/user-lockers')json={lockers};
 if(path==='/api/user-inbox'){if(fail){await route.fulfill({status:503,json:{detail:{code:'unavailable'}}});return;}json={cards,nextAfter:null};}
 if(path.startsWith('/api/user-inbox/')){
  const id=path.split('/')[3], previous=cards.find(c=>c.id===id);
  if(req.method()==='DELETE'){cards=cards.filter(c=>c.id!==id);json={deleted:true};}
  else if(path.endsWith('/read')){Object.assign(previous,{read:body.read});json={card:previous};}
  else if(path.endsWith('/submit')||path.endsWith('/reject')){Object.assign(previous,{revision:previous.revision+1,status:path.endsWith('/reject')?'rejected':'responded',response:{outcome:path.endsWith('/reject')?'reject':'submit',answer:body.answer,choiceId:body.choiceId}});json={card:previous};}
 }
 await route.fulfill({json});
});
const screenshot=async name=>page.screenshot({path:resolve(evidence,name+'.png')});
try{
 await page.goto(base+'/react/locker');
 await expect(page.getByRole('main',{name:'Locker'})).toBeVisible();
 await expect(page.getByLabel('Workspace',{exact:true})).toHaveCount(0);
 await expect(page.locator('[data-card-id="answer"]')).toBeVisible();
 await expect(page.getByTestId('locker-sidebar-panel').getByRole('navigation',{name:'Locker sources'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Quick new session'})).toHaveCount(0);
 await expect(page.locator('[data-card-id=answer]').getByRole('link',{name:'review-notes.txt'})).toHaveAttribute('href', '/api/attachments/ref/att_'+ 'a'.repeat(32) + '?session_id=ses_one');
 checks.push('production route with shared Sidebar slot, real WorkspaceRail, Markdown and scoped opaque attachment');
 await page.getByLabel('Local user key').fill('fake-only');await page.getByRole('button',{name:'Log in',exact:true}).click();await expect(page.getByText('Log out',{exact:true})).toBeAttached();
 await screenshot('desktop-dark');
 await page.getByTestId('workspace-rail-handle').click();
 await page.getByTestId('workspace-tab-workspace-a').click();
 await expect(page.getByTestId('locker-source-locker-old')).toHaveCount(0);
 await expect(page.getByRole('button',{name:/All Lockers/})).toBeVisible();
 await page.getByTestId('workspace-tab-ungrouped').click();
 await expect(page.getByTestId('locker-source-locker-one')).toHaveCount(0);
 await page.getByTitle('全部会话（虚拟工作区，不产生归属）',{exact:true}).click();
 await page.getByTestId('workspace-rail-handle').click();
 checks.push('real WorkspaceRail handle, workspace and ungrouped aggregate switching');
 const answer=page.locator('[data-card-id="answer"]');
 await answer.getByRole('textbox').fill('Keep the draft across Locker views.');
 await page.getByTestId('locker-source-locker-two').click();await expect(answer).toBeHidden();
 await page.getByTestId('locker-source-locker-one').click();await expect(answer.getByRole('textbox')).toHaveValue('Keep the draft across Locker views.');
 await page.getByRole('button',{name:/All Lockers/}).click();
 await page.getByTestId('locker-drag-locker-two').dragTo(page.getByTestId('locker-source-locker-one'));
 expect(await page.locator('[data-testid^=locker-source-]').first().getAttribute('data-testid')).toBe('locker-source-locker-two');
 await expect(page.getByRole('button',{name:/All Lockers/}).locator('[draggable=true]')).toHaveCount(0);
 checks.push('draft survives source/aggregate filters; shared drag gutter reorders sources, aggregate has no drag');
 const approval=page.locator('[data-card-id="approval"]');
 await approval.getByRole('radio',{name:'Approve',exact:true}).check();await expect(approval.getByText('Queue staging review',{exact:true}).last()).toBeVisible();await approval.getByRole('textbox').fill('Review only.');await approval.getByRole('button',{name:'Submit',exact:true}).click();await expect(approval.getByText('Read-only')).toBeVisible();
 expect(requests.find(r=>r.path.endsWith('/approval/submit')).body).toMatchObject({choiceId:'approve',answer:'Review only.',expectedRevision:0});
 checks.push('trusted choice target, BTW, real submit payload, terminal readonly');
 await answer.getByRole('button',{name:'Reject',exact:true}).click();await answer.getByRole('textbox',{name:'Reason for rejection'}).fill('Keep this scoped to the preview.');await answer.getByRole('button',{name:'Confirm rejection'}).click();await expect(answer.getByText('Keep this scoped to the preview.',{exact:true})).toBeVisible();
 checks.push('reject reason mapped to answer');
 await answer.getByRole('button',{name:'Delete record'}).click();await expect(answer).toHaveCount(0);checks.push('terminal deletion via DELETE');
 await page.getByTestId('locker-source-locker-two').getByRole('button',{name:'Read all',exact:true}).click();
 expect(requests.some(r=>r.path.endsWith('/approval/read')&&r.body.read===true)).toBe(true);checks.push('Read all writes original message identity');
 await page.reload();await expect(approval.getByText('Read-only')).toBeVisible();checks.push('server state reloaded');
 for(const width of [1440,768,390,320]){
  await page.setViewportSize({width,height:900});
  for(const theme of ['dark','light']){
   await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
   const overflow=await page.evaluate(()=>[document.documentElement,document.body,...document.querySelectorAll('.locker-view,.locker-timeline')].filter(e=>e.clientWidth>0&&e.scrollWidth>e.clientWidth+1).map(e=>e.className));expect(overflow).toEqual([]);
   await screenshot(`${width}-${theme}`);
  }
 }
 await page.getByRole('button',{name:'打开侧边栏'}).click();await expect(page.getByRole('navigation',{name:'Locker sources'})).toBeVisible();await screenshot('320-source-drawer');await page.getByTestId('mobile-sidebar-close').click();checks.push('1440/768/390/320 themes, no horizontal overflow, shared narrow source drawer');
 fail=true;await page.getByRole('button',{name:'Refresh Locker'}).click();await expect(page.getByRole('alert').filter({hasText:'unavailable'})).toBeVisible();fail=false;await page.getByRole('button',{name:'Refresh Locker'}).click();await expect(approval).toBeVisible();checks.push('failure plus real Refresh retry');
 await page.setViewportSize({width:1440,height:900});
 await page.getByTestId('locker-source-locker-two').getByRole('button',{name:'Navigate to chat'}).click();
 await expect(page).toHaveURL(base+'/react');
 expect(requests.some(r=>r.path==='/api/sessions/ses_two/history')).toBe(true);
 checks.push('Navigate to chat selects the designated source Session through real sessionStore');
 expect(errors).toEqual([]);
 console.log(JSON.stringify({passed:checks.length,checks,errors,port},null,2));
}catch(e){await screenshot('browser-failure');throw e;}finally{await writeFile(resolve(evidence,'browser.json'),JSON.stringify({checks,errors,requests,port},null,2));await browser.close();await new Promise(r=>server.close(r));}



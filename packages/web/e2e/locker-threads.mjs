// Production assets + routed synthetic API. No HTTP listener or Pan service.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const evidence = fileURLToPath(new URL('../test-results/p11-attached-reminder/', import.meta.url));
await mkdir(evidence, {recursive: true});
const browser = await chromium.launch({headless: true});
const errors = [], checks = [];
const at = (i) => new Date(Date.UTC(2026, 9, 25, 0, i)).toISOString();
const message = (i, read = true) => ({kind:'message', id:`m${i}`, lockerId:'locker', ownerSessionId:'owner',
  recipientId:'user', revision:0, createdAt:at(i), read,
  message:{messageId:`m${i}`, role:'assistant', content:`Timeline message ${i}\n\nReview the current task and its attached reminders.`, ts:at(i)}});
const reminder = (id, parent, time = 1000) => ({id, parent, direction:'user', kind:'question', ownerSessionId:'owner',
  lockerId:'locker', createdAt:at(time), answeredAt:at(time+1), body:`Reminder ${id}`, attachments:[], revision:1,
  status:'answered', answer:'Answer stays on the original card.', read:false});
let messages, cards, userCards, requests, failCreate;
async function open(width, count = 80, unread = 30) {
  messages = Array.from({length:count}, (_,i) => message(i, i !== unread));
  cards = [{id:'interactive', lockerId:'locker', ownerSessionId:'owner', createdAt:at(31), read:true, revision:1,
    status:'no_answer_needed', definition:{title:'Review complete',body:'A terminal parent keeps its reminders.',summaryBody:''},
    response:null, delivery:{state:'not_requested'}}];
  userCards = [reminder('attached', {type:'locker_message',id:'m30'}),
    reminder('early', {type:'locker_message',id:'m30'}, 900),
    reminder('nested', {type:'user_card',id:'early'}),
    reminder('interactive-child', {type:'interaction_card',id:'interactive'})];
  requests = []; failCreate = false;
  const page = await browser.newPage({viewport:{width,height:900}});
  page.on('pageerror', (error) => errors.push(error.message));
  await page.routeWebSocket('**/*', () => {});
  await page.route('**/*', async (route) => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== 'http://localhost:19281') return route.abort();
    if (path.startsWith('/api/')) {
      const body = req.postData() ? req.postDataJSON() : null;
      requests.push({path, method:req.method(), body});
      let json = {sessions:[],workers:[],workspaces:[],panels:[],jobs:[],adapters:[]};
      if(path==='/api/automation/login') json={token:'synthetic-test-only',expiresIn:3600};
      if(path==='/api/user-lockers') json={lockers:[{id:'locker',sessionId:'owner',sourceName:'Locker thread test',workspaceIds:[],available:true,total:count+cards.length+userCards.length,unread:5}]};
      if(path==='/api/user-inbox') json={cards,nextAfter:null};
      if(path==='/api/user-locker-messages') json={messages,nextAfter:null};
      if(path==='/api/reminding-cards') {
        if(req.method()==='POST') {
          if(failCreate) return route.fulfill({status:404,json:{detail:{code:'not_found',message:'Parent unavailable'}}});
          const card={...reminder(`new${userCards.length}`,body.parent),body:body.body,kind:body.kind,
            status:'pending',answer:null,answeredAt:null,read:true,revision:0};
          userCards.push(card); json={card};
        } else json={cards:userCards};
      }
      return route.fulfill({json});
    }
    const file = path.startsWith('/react/assets/') ? resolve(dist,path.slice('/react/'.length)) : resolve(dist,'index.html');
    if(!file.startsWith(dist)) return route.abort();
    return route.fulfill({body:await readFile(file), contentType:({'.js':'text/javascript','.css':'text/css','.html':'text/html'})[extname(file)] || 'application/octet-stream'});
  });
  await page.goto('http://localhost:19281/react/locker');
  await page.getByLabel('Local user key').fill('synthetic-only');
  await page.getByRole('button',{name:'Log in',exact:true}).click();
  await expect(page.locator(`[data-locker-entry="m${count-1}"]`)).toBeVisible();
  return page;
}
try {
  for (const width of [1440,390]) {
    const page = await open(width);
    const timeline = page.getByRole('region',{name:'Locker messages'});
    const divider = page.getByRole('separator');
    await expect(divider).toBeVisible();
    expect(await divider.evaluate((node)=>node.parentElement.dataset.lockerEntry)).toBe('m30');
    const order = await page.locator('[data-locker-entry]').evaluateAll(nodes=>nodes.filter(n=>!n.hidden).map(n=>n.dataset.lockerEntry));
    expect(order.slice(order.indexOf('m30'),order.indexOf('m30')+5)).toEqual(['m30','early','nested','attached','interactive']);
    await expect(page.getByRole('button',{name:'Scroll to bottom',exact:true})).toBeVisible();
    expect(await page.locator('[data-reminding-card-id="attached"]').count()).toBe(1);
    expect(await page.locator('[data-parent-id]').evaluateAll(nodes=>new Set(nodes.filter(n=>!n.hidden).map(n=>Math.round(n.getBoundingClientRect().left))).size)).toBe(1);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:resolve(evidence,`locker-${width}.png`)});
    const chain = page.locator('[data-locker-entry="early"] .locker-parent-chain');
    await expect(chain).toHaveAttribute('aria-label', 'Attached to locker_message m30');
    await expect(chain.locator('.locker-parent-chain-label')).toHaveText('m30');
    const geometry = await chain.evaluate(node => {
      const line = node.querySelector('.locker-parent-chain-line');
      const box = node.getBoundingClientRect(), stroke = line.getBoundingClientRect();
      return {offset: Math.abs((box.left + box.width / 2) - (stroke.left + stroke.width / 2)),
        width: stroke.width, height: stroke.height, border: getComputedStyle(line).borderLeftStyle,
        horizontal: getComputedStyle(node).borderTopWidth};
    });
    expect(geometry.offset).toBeLessThan(1);
    expect(geometry.width).toBe(2);
    expect(geometry.height).toBe(32);
    expect(geometry.border).toBe('dashed');
    expect(geometry.horizontal).toBe('0px');
    await expect(page.locator('[data-locker-entry="m30"]').getByRole('button',{name:'Attach a reminder card',exact:true})).toHaveCount(0);
    await expect(page.locator('[data-locker-entry="interactive"]').getByRole('button',{name:'Attach a reminder card',exact:true})).toHaveCount(0);
    await page.getByRole('button',{name:'Scroll to bottom',exact:true}).click();
    await expect.poll(async()=>timeline.evaluate(node=>Math.abs(node.scrollHeight-node.scrollTop-node.clientHeight))).toBeLessThan(2);
    await expect(page.getByRole('button',{name:'Scroll to bottom',exact:true})).toHaveCount(0);
    checks.push(`${width}: divider at m30, sorted flat chains, one answered row, smooth bottom button, no horizontal overflow`);
    // Linked leaves can grow the chain once; parents with children have no entry.
    const parent=page.locator('[data-locker-entry="nested"]');
    await parent.getByRole('button',{name:'Attach a reminder card',exact:true}).click();
    await parent.getByLabel('Reminder body').fill('Created from nested parent');
    await parent.getByRole('button',{name:'Create',exact:true}).click();
    await expect(page.locator('[data-parent-id="nested"]')).toBeVisible();
    expect(requests.find(r=>r.method==='POST'&&r.path==='/api/reminding-cards').body.parent).toEqual({type:'user_card',id:'nested'});
    await expect(parent.getByRole('button',{name:'Attach a reminder card',exact:true})).toHaveCount(0);
    const terminal=page.locator('[data-locker-entry="m79"]');
    await terminal.getByRole('button',{name:'Attach a reminder card',exact:true}).click();
    await terminal.getByLabel('Reminder body').fill('Will fail locally'); failCreate=true;
    await terminal.getByRole('button',{name:'Create',exact:true}).click();
    await expect(terminal.getByRole('alert')).toContainText('not_found');
    await expect(terminal.getByLabel('Reminder body')).toBeEnabled();
    checks.push(`${width}: centered vertical dashed connector with aria/id; occupied parents hide creation; linked leaf inserts once; invalid parent error stays in form`);
    await page.close();
  }
  const page = await open(1440,400,0);
  await expect(page.getByRole('status').filter({hasText:'已向前加载 5 页'})).toBeVisible();
  expect(await page.locator('[data-locker-entry="m0"]').isVisible()).toBe(false);
  // 401 chronological roots => six pages keep m100..m399 plus the interaction root.
  expect(await page.locator('[data-locker-entry]').evaluateAll(nodes=>nodes.filter(n=>!n.hidden&&!n.dataset.parentId).length)).toBe(300);
  const earliest=await page.locator('[data-locker-entry]').evaluateAll(nodes=>nodes.find(n=>!n.hidden)?.dataset.lockerEntry);
  expect(earliest).toBe('m100');
  await page.screenshot({path:resolve(evidence,'load-limit-1440.png')});
  checks.push('5 older pages maximum, earliest loaded row shown with visible fallback notice');
  await page.close();
  const readPage = await open(390,80,-1);
  // Attached unread cards do not introduce a chronological divider or move initial position.
  await expect(readPage.getByRole('separator')).toHaveCount(0);
  await expect.poll(async()=>readPage.getByRole('region',{name:'Locker messages'}).evaluate(n=>n.scrollHeight-n.scrollTop-n.clientHeight)).toBeLessThan(2);
  checks.push('no chronological unread: bottom and no divider, attached unread remains independent');
  await readPage.close();
  expect(errors).toEqual([]);
  await writeFile(resolve(evidence,'browser.json'),JSON.stringify({checks,errors,mode:'production dist, synthetic routed API, no listener/service'},null,2));
  console.log(JSON.stringify({checks,errors},null,2));
} finally { await browser.close(); }

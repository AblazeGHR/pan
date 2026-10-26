// Production DOM/scroll geometry against the owned HTTP runtime; no interception.
import {chromium, expect} from '@playwright/test';
import fs from 'node:fs/promises';
const [base,out,fixturePath] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned random port required');
const f=JSON.parse(await fs.readFile(fixturePath,'utf8'));
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1280,height:800}});
const errors=[],writes=[],checks=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(r.method()==='PUT'&&r.url().endsWith('/read'))writes.push(r.url());});
const timeline=page.getByRole('region',{name:'Locker messages'});
const rows=timeline.locator('article:visible');
const geometry=()=>timeline.evaluate(n=>({top:n.scrollTop,height:n.scrollHeight,client:n.clientHeight}));
const refresh=async()=>{
  await page.getByRole('button',{name:'Refresh Locker'}).click();
  await expect(page.getByRole('button',{name:'Refresh Locker'})).toBeEnabled();
};
try {
  await page.goto(base+'/react/locker');
  await page.getByLabel('Local user key',{exact:true}).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button',{name:'Log in',exact:true}).click();
  await expect(rows).toHaveCount(f.ids.length);
  await expect.poll(async()=>timeline.locator(`[data-locker-entry="${f.ids[0]}"]`).evaluate(n=>Math.abs(n.getBoundingClientRect().top-n.parentElement.getBoundingClientRect().top))).toBeLessThan(3);
  expect(await rows.evaluateAll(ns=>ns.map(n=>n.dataset.cardId||n.dataset.lockerMessageId))).toEqual(f.ids);
  const divider=timeline.getByRole('separator');
  expect(await divider.evaluate(n=>n.parentElement.dataset.lockerEntry)).toBe(f.ids[0]);
  await expect(page.getByRole('button',{name:'Scroll to bottom',exact:true})).toBeVisible();
  expect(writes).toEqual([]);
  checks.push('all unread: full chronological window, first unread at top with divider, no read writes');
  await page.screenshot({path:out+'/scroll-first-unread.png'});
  await page.getByRole('button',{name:'Scroll to bottom',exact:true}).click();
  await expect.poll(async()=>{const g=await geometry();return g.height-g.top-g.client;}).toBeLessThan(3);
  await expect(page.getByRole('button',{name:'Scroll to bottom',exact:true})).toHaveCount(0);
  expect(writes).toEqual([]);
  checks.push('scroll-to-bottom button moves the real timeline to bottom without consuming unread');
  await rows.first().getByRole('button',{name:'Mark read',exact:true}).click();
  await expect(rows.first().getByRole('button',{name:'Mark unread',exact:true})).toBeVisible();
  await page.getByTestId('locker-source-'+f.locker).click();
  expect(await divider.evaluate(n=>n.parentElement.dataset.lockerEntry)).toBe(f.ids[1]);
  await expect.poll(async()=>timeline.locator(`[data-locker-entry="${f.ids[1]}"]`).evaluate(n=>Math.abs(n.getBoundingClientRect().top-n.parentElement.getBoundingClientRect().top))).toBeLessThan(3);
  expect(writes).toHaveLength(1);
  checks.push('explicit first-row read: source entry targets second chronological row and moves divider');
  await page.getByTestId('locker-source-'+f.locker).getByRole('button',{name:'Read all',exact:true}).click();
  await expect.poll(()=>writes.length).toBe(f.ids.length);
  await expect(rows.getByRole('button',{name:'Mark read',exact:true})).toHaveCount(0);
  await expect(rows.getByRole('button',{name:'Mark card as read',exact:true})).toHaveCount(0);
  await expect(divider).toHaveCount(0);
  await page.locator('.locker-source-item.aggregate').click();
  await expect(rows).toHaveCount(50);
  await expect.poll(async()=>{const g=await geometry();return g.height-g.top-g.client;}).toBeLessThan(3);
  expect(await rows.evaluateAll(ns=>ns.map(n=>n.dataset.cardId||n.dataset.lockerMessageId))).toEqual(f.ids.slice(-50));
  await page.getByTestId('locker-source-'+f.locker).click();
  await expect(rows).toHaveCount(50);
  await expect.poll(async()=>{const g=await geometry();return g.height-g.top-g.client;}).toBeLessThan(3);
  checks.push('Read all: aggregate and source with no unread enter at latest 50/bottom without divider');
  // Reaching the top dispatches an actual browser scroll event. The existing
  // first row should retain its offset after 50 older rows become visible.
  const anchor=rows.first();
  const anchorId=await anchor.getAttribute('data-locker-message-id')||await anchor.getAttribute('data-card-id');
  const beforeOffset=await timeline.evaluate(n=>{
    n.scrollTop=0;
    return n.querySelector('article:not([hidden])').getBoundingClientRect().top-n.getBoundingClientRect().top;
  });
  await expect(rows).toHaveCount(100);
  const anchored=timeline.locator(`[data-locker-message-id="${anchorId}"], [data-card-id="${anchorId}"]`);
  const offset=await anchored.evaluate(n=>n.getBoundingClientRect().top-n.closest('.locker-timeline').getBoundingClientRect().top);
  expect(Math.abs(offset-beforeOffset)).toBeLessThan(3);
  checks.push('scroll-top display pagination prepends 50 older mixed rows and keeps original anchor');
  await page.screenshot({path:out+'/scroll-prepend-anchor.png'});
  const before=await geometry();
  const newId='late-arrival';
  const result=await page.request.post(base+'/__test/locker/accept',{headers:{'x-locker-fixture':f.nonce},data:{
    sourceSessionId:f.source,recipientId:'user',message:{messageId:newId,role:'assistant',content:'New arrival must not pull the reader',ts:new Date().toISOString()}
  }});
  expect(result.ok()).toBe(true);
  const arrival=(await result.json()).message;
  await refresh();
  expect(Math.abs((await geometry()).top-before.top)).toBeLessThan(8);
  expect((await geometry()).height-(await geometry()).top-(await geometry()).client).toBeGreaterThan(100);
  expect(writes).toHaveLength(f.ids.length);
  checks.push('refresh with new arrival while reading old rows preserves position and sends no read mutations');
  await page.locator('.locker-source-item.aggregate').click();
  await expect.poll(async()=>{const g=await geometry();return g.height-g.top-g.client;}).toBeLessThan(3);
  expect(await divider.evaluate(n=>n.parentElement.dataset.lockerEntry)).toBe(arrival.id);
  expect(writes).toHaveLength(f.ids.length);
  checks.push('switching to aggregate targets the new first unread without marking read');
  const newest=rows.last();
  await newest.getByRole('button',{name:'Mark read',exact:true}).click();
  await expect(newest.getByRole('button',{name:'Mark unread',exact:true})).toBeVisible();
  expect(writes).toHaveLength(f.ids.length+1);
  await page.reload();
  await expect(rows).toHaveCount(50);
  await expect(page.getByRole('button',{name:'Log in',exact:true})).toHaveCount(0);
  await expect(divider).toHaveCount(0);
  await expect.poll(async()=>{const g=await geometry();return g.height-g.top-g.client;}).toBeLessThan(3);
  await expect(rows.last().getByRole('button',{name:'Mark unread',exact:true})).toBeVisible();
  expect(writes).toHaveLength(f.ids.length+1);
  checks.push('explicit user read survives browser reload and automatic Cookie session restoration without further writes');
  expect(errors).toEqual([]);
} catch(e) {await page.screenshot({path:out+'/scroll-failure.png'});throw e;}
finally {await fs.writeFile(out+'/scroll-browser.json',JSON.stringify({checks,errors,writes},null,2));await browser.close();}

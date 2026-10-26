// Production frontend against an owned real service. No API interception.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
const [base, out, fixturePath, phase] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned random-port runtime required');
const f = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
const browser = await chromium.launch({headless: true});
const page = await browser.newPage({viewport: {width:1440,height:960}});
const errors = [], api = [], checks = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', req => {const u = new URL(req.url()); if (u.pathname.startsWith('/api/')) api.push({method:req.method(),path:u.pathname});});
const card = id => page.locator(`[data-card-id="${id}"]`);
const msg = id => page.locator(`[data-locker-message-id="${id}"]`);
const fixtureHeaders = {'x-locker-fixture': f.nonce};
const login = async () => {
  const response = page.waitForResponse(response => response.url() === base+'/api/automation/login' && response.request().method() === 'POST');
  await page.getByLabel('Local user key',{exact:true}).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button',{name:'Log in',exact:true}).click();
  const token = (await (await response).json()).token;
  await expect(msg(f.ordinary)).toBeVisible();
  return {Authorization:`Bearer ${token}`};
};
const refresh = async () => {
  const response = page.waitForResponse(response => response.url().includes('/api/user-locker-messages?') && response.status() === 200);
  await page.getByRole('button',{name:'Refresh Locker'}).click();
  await response;
  await expect(page.getByRole('button',{name:'Refresh Locker'})).toBeEnabled();
};
try {
  await page.goto(base+'/react/locker');
  await expect(msg(f.ordinary)).toHaveCount(0);
  let auth = await login();
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    document.querySelector('.locker-timeline')?.scrollTo(0, 0);
  });
  await page.screenshot({path:`${out}/initial-${phase}-1440.png`});
  checks.push('real UserAuth login and production Locker APIs, no interception');
  if (phase === 'exercise') {
    const cards = (await (await page.request.get(base+'/api/user-inbox',{headers:auth})).json()).cards;
    const messages = (await (await page.request.get(base+'/api/user-locker-messages',{headers:auth})).json()).messages;
    const expected = [...cards,...messages].sort((a,b)=>Date.parse(a.createdAt)-Date.parse(b.createdAt)||a.id.localeCompare(b.id)).map(item=>item.id);
    const timeline=page.getByRole('region',{name:'Locker messages'});
    await expect(timeline.locator('article:visible')).toHaveCount(expected.length);
    expect(await timeline.locator('[data-locker-entry]:visible').evaluateAll(rows=>rows.map(row=>row.dataset.lockerEntry))).toEqual(expected);
    expect(await timeline.getByRole('separator').evaluate(node=>node.parentElement.dataset.lockerEntry)).toBe(expected[0]);
    await expect(card(f.approval).getByTestId('locker-card-id')).toHaveAttribute('data-card-id-value',f.approval);
    expect(new Set(messages.map(row=>row.lockerId)).size).toBe(2);
    expect(messages.find(row=>row.id===f.ordinary).lockerId).toBe(cards.find(row=>row.id===f.approval).lockerId);
    await expect(msg(f.ordinary).getByRole('button',{name:'Submit'})).toHaveCount(0);
    await expect(card(f.approval).getByRole('button',{name:'Delete record'})).toHaveCount(0);
    checks.push('real dedicated messages and Cards stably mixed; same Locker, two sources; no fake Card wrapper');
    const downloadEvent = page.waitForEvent('download');
    await msg(f.ordinary).getByRole('link',{name:'review.txt',exact:true}).click();
    const download = await downloadEvent;
    expect(await fs.readFile(await download.path(),'utf8')).toBe('owned attachment bytes');
    checks.push('Markdown attachment click downloads actual protected bytes');
    await page.getByTestId('workspace-rail-handle').click();
    await page.getByTestId(`workspace-tab-${f.workspace}`).click();
    await expect(msg(f.ordinary)).toBeVisible();
    await page.getByTestId('workspace-tab-ungrouped').click();
    await expect(msg(f.ordinary)).toBeHidden();
    await page.getByTitle('全部会话（虚拟工作区，不产生归属）',{exact:true}).click();
    await page.getByTestId('workspace-rail-handle').click();
    await expect(msg(f.ordinary)).toBeVisible();
    checks.push('actual WorkspaceRail filters ordinary messages and Cards together');
    await expect(msg(f.ordinary).getByRole('button',{name:'Mark read',exact:true})).toBeVisible();
    await expect(card(f.approval).getByRole('button',{name:'Mark card as read'})).toBeVisible();
    expect(api.filter(row=>row.method==='PUT' && row.path.endsWith('/read'))).toEqual([]);
    checks.push('viewing, scrolling and Workspace switching do not mark ordinary messages or Cards read');
    await msg(f.ordinary).getByRole('button',{name:'Mark read',exact:true}).click();
    await expect(msg(f.ordinary).getByRole('button',{name:'Mark unread'})).toBeVisible();
    await msg(f.ordinary).getByRole('button',{name:'Mark unread'}).click();
    await expect(msg(f.ordinary).getByRole('button',{name:'Mark read',exact:true})).toBeVisible();
    await refresh();
    await expect(msg(f.ordinary).getByRole('button',{name:'Mark read',exact:true})).toBeVisible();
    const afterRead = (await (await page.request.get(base+`/api/user-locker-messages/${f.ordinary}`,{headers:auth})).json()).message;
    expect(afterRead.read).toBe(false);
    checks.push('manual unread persists through real refresh without a consumption cursor');
    await page.getByTestId('locker-source-'+messages[0].lockerId).getByRole('button',{name:'Read all',exact:true}).click();
    await expect(msg(f.ordinary).getByRole('button',{name:'Mark unread'})).toBeVisible();
    expect((await (await page.request.get(base+`/api/user-locker-messages/${f.ordinary}`,{headers:auth})).json()).message.read).toBe(true);
    checks.push('source Read all writes the original ordinary-message row');
    await card(f.approval).getByRole('radio',{name:'Approve Pause',exact:true}).check();
    await card(f.approval).getByRole('textbox').fill('Proceed with isolated Pause.');
    await card(f.approval).getByRole('button',{name:'Submit',exact:true}).click();
    await expect(card(f.approval).getByText('Read-only')).toBeVisible();
    await card(f.reply).getByRole('textbox',{name:'What should happen?',exact:true}).fill('A standalone answer.');
    await card(f.reply).getByRole('group',{name:/Optional proposal/}).getByRole('button',{name:'拒绝此题',exact:true}).click();
    await card(f.reply).getByRole('textbox',{name:'Optional proposal：拒绝原因',exact:true}).fill('Needs more context.');
    await card(f.reply).getByRole('button',{name:'Submit',exact:true}).click();
    await expect(card(f.reply).getByText('Read-only')).toBeVisible();
    const savedReply=(await (await page.request.get(base+`/api/user-inbox/${f.reply}`,{headers:auth})).json()).card;
    expect(savedReply.response.answers).toEqual({detail:{text:'A standalone answer.'},decline:{rejected:true,reason:'Needs more context.'}});
    for (const [id,reason] of [[f.reason,'Please narrow the proposal.'],[f.silent,'']]) {
      await card(id).getByRole('button',{name:'Reject',exact:true}).click();
      if (reason) await card(id).getByRole('textbox',{name:'Reason for rejection'}).fill(reason);
      await card(id).getByRole('button',{name:'Confirm rejection'}).click();
      await expect(card(id).getByText('Read-only')).toBeVisible();
    }
    await card(f.silent).getByRole('button',{name:'Delete record'}).click();
    await expect(card(f.silent)).toHaveCount(0);
    checks.push('real choice submit, per-question answer/reject with exact persisted response, reason/no-reason card rejection and terminal deletion');
    const deleted = await page.request.delete(base+`/api/sessions/${f.two}`,{headers:{Origin:base}});
    expect(deleted.ok()).toBe(true);
    await refresh();
    await expect(msg(f.archived)).toBeVisible();
    await expect(page.getByText('Source unavailable',{exact:true})).toBeVisible();
    checks.push('deleted source retains its ordinary record and unavailable Locker');
    await page.getByTestId('workspace-rail-handle').click();
    await page.getByTestId(`workspace-tab-${f.workspace}`).click();
    await expect(msg(f.archived)).toBeVisible();
    await page.getByTitle('全部会话（虚拟工作区，不产生归属）',{exact:true}).click();
    await page.getByTestId('workspace-rail-handle').click();
    for (const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:960});
      for (const theme of ['dark','light']) {
        await page.evaluate(theme=>{
          document.documentElement.dataset.theme=theme;
          window.scrollTo(0,0);
          document.querySelector('.locker-timeline')?.scrollTo(0,0);
        },theme);
        expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(width);
        await page.screenshot({path:`${out}/${width}-${theme}.png`});
      }
    }
    checks.push('1440/768/390/320 light/dark production layout without horizontal overflow');
    await page.setViewportSize({width:1440,height:960});
    // The fixture holds the actual GET response after production serialization.
    // Logout aborts that generation; releasing it must not restore any rows.
    await page.request.post(base+'/__test/locker/gate/arm',{headers:fixtureHeaders});
    await page.getByRole('button',{name:'Refresh Locker'}).click();
    await expect.poll(async()=> (await (await page.request.post(base+'/__test/locker/gate/status',{headers:fixtureHeaders})).json()).captured).toBe(true);
    await page.locator('.locker-auth > summary').click();
    await page.getByRole('button',{name:'Log out',exact:true}).click();
    await expect(page.locator('.locker-timeline article')).toHaveCount(0);
    await expect(page.locator('[data-testid^="locker-source-"]')).toHaveCount(0);
    await page.request.post(base+'/__test/locker/gate/release',{headers:fixtureHeaders});
    await expect(msg(f.ordinary)).toHaveCount(0);
    auth = await login();
    // Expire the server token without modifying frontend memory. The next
    // real protected GET must return 401 and clear all old-user display data.
    expect((await page.request.post(base+'/api/automation/logout',{headers:auth})).ok()).toBe(true);
    await page.getByRole('button',{name:'Refresh Locker'}).click();
    await expect(page.getByRole('button',{name:'Log in',exact:true})).toBeVisible();
    await expect(page.locator('.locker-timeline article')).toHaveCount(0);
    await expect(page.locator('[data-testid^="locker-source-"]')).toHaveCount(0);
    auth = await login();
    checks.push('logout, captured late GET, successor login and actual 401 leave no old-user rows or sources');
    await expect(msg(f.archived).getByRole('button',{name:'Mark read',exact:true})).toBeVisible();
    await expect(msg(f.archived).getByRole('button',{name:'Mark read',exact:true})).toBeVisible();
  } else {
    await expect(msg(f.archived)).toBeVisible();
    await expect(msg(f.ordinary).getByRole('button',{name:'Mark unread'})).toBeVisible();
    await expect(msg(f.archived).getByRole('button',{name:'Mark read',exact:true})).toBeVisible();
    await expect(card(f.approval).getByText('Read-only')).toBeVisible();
    await expect(card(f.reason).getByText('Please narrow the proposal.')).toBeVisible();
    await expect(card(f.reply).getByText('已拒绝此题：Needs more context.',{exact:true})).toBeVisible();
    await expect(card(f.silent)).toHaveCount(0);
    await expect(page.getByText('Source unavailable',{exact:true})).toBeVisible();
    await msg(f.archived).getByRole('button',{name:'Delete record'}).click();
    await expect(msg(f.archived)).toHaveCount(0);
    checks.push('cold restart retains ordinary/Card states, source deletion metadata; ordinary terminal cleanup');
  }
  expect(errors).toEqual([]);
  console.log(JSON.stringify({phase,passed:checks.length,checks,errors},null,2));
} catch (error) {
  await page.screenshot({path:`${out}/failure-${phase}.png`}); throw error;
} finally {
  await fs.writeFile(`${out}/browser-${phase}.json`,JSON.stringify({phase,checks,errors,api,coreApiInterception:false},null,2));
  await browser.close();
}

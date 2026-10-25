import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
const [base, out, cardId, taskId, childId] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned instance only');
const browser = await chromium.launch({headless: true});
const evidence = {sizes: [], errors: []};
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.on('pageerror', (error) => evidence.errors.push(error.message));
  await page.goto(base + '/react/locker');
  await page.getByLabel('Local user key', {exact: true}).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', {name: 'Log in', exact: true}).click();
  const rows = page.locator(`[data-reminding-card-id="${cardId}"]`);
  await expect(rows).toHaveCount(1);
  const timeline=page.getByRole('region',{name:'Locker messages'});
  await expect(timeline.locator('article:visible')).toHaveCount(3);
  expect(await timeline.locator('[data-locker-entry]:visible').evaluateAll(ns=>ns.map(n=>n.dataset.lockerEntry))).toEqual([cardId,childId,taskId]);
  await expect(page.locator(`[data-locker-entry="${childId}"]`)).toHaveAttribute('data-parent-id',cardId);
  await expect(page.locator(`[data-reminding-card-id="${childId}"]`)).toHaveCount(1);
  await expect(rows.getByRole('region', {name: 'Agent answer'})).toContainText('Real MCP answer');
  await expect(rows.getByTestId('reminding-card-id')).toHaveAttribute('data-card-id-value',cardId);
  await expect(rows.locator('textarea,input')).toHaveCount(0);
  expect(await timeline.getByRole('separator').evaluate(n=>n.parentElement.dataset.lockerEntry)).toBe(cardId);
  await rows.getByRole('button', {name:'Mark reminding card as read',exact:true}).click();
  await expect(rows.getByRole('button', {name:'Mark reminding card as unread',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Refresh Locker'}).click();
  await expect(page.getByRole('button',{name:'Refresh Locker'})).toBeEnabled();
  await expect(rows).toHaveCount(1);
  await expect(rows.getByRole('button', {name:'Mark reminding card as unread',exact:true})).toBeVisible();
  expect(await timeline.getByRole('separator').evaluate(n=>n.parentElement.dataset.lockerEntry)).toBe(taskId);
  await expect(page.locator(`[data-reminding-card-id="${childId}"]`).getByRole('button',{name:'Mark reminding card as read',exact:true})).toBeVisible();
  await rows.getByRole('button', {name:'Mark reminding card as unread',exact:true}).click();
  await expect(rows.getByRole('button', {name:'Mark reminding card as read',exact:true})).toBeVisible();
  evidence.singleProjectionAndAttachedOrder = [cardId,childId,taskId];
  for (const width of [1440,390,320]) {
    await page.setViewportSize({width,height:1000});
    await rows.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({path:`${out}/locker-${width}.png`});
    evidence.sizes.push({view:'locker',width,scrollWidth:await page.evaluate(() => document.documentElement.scrollWidth)});
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'Navigate to chat',exact:true}).first().click();
  await page.locator('#send-queue-button').click();
  await expect(page.getByRole('button',{name:'create reminding card',exact:true})).toBeVisible();
  await page.screenshot({path:`${out}/composer-queue-open-1440.png`});
  const queueActionRect = await page.getByRole('button',{name:'create reminding card',exact:true}).evaluate((el) => {
    const r = el.getBoundingClientRect();
    return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};
  });
  expect(queueActionRect.height).toBeGreaterThan(0);
  expect(queueActionRect.top).toBeGreaterThanOrEqual(0);
  expect(queueActionRect.bottom).toBeLessThanOrEqual(1000);
  const entry = page.getByRole('button', {name:'create reminding card',exact:true});
  await expect(entry).toBeEnabled();
  await entry.click();
  await expect(page.getByRole('button',{name:'create',exact:true})).toBeVisible();
  const geometry = await page.evaluate(() => {
    const rect = (selector) => {
      const r = document.querySelector(selector)?.getBoundingClientRect();
      return r ? {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height} : null;
    };
    return {
      composer: rect('[data-testid="input-row"]'),
      kind: rect('[data-testid="reminding-card-kind-row"]'),
      editor: rect('[data-testid="rich-text-composer"]'),
      create: rect('button[title^="create ("]'),
      cancel: (() => {
        const button = [...document.querySelectorAll('button')].find((candidate) => candidate.textContent?.trim() === 'cancel');
        if (!button) return null;
        const r = button.getBoundingClientRect();
        return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};
      })(),
    };
  });
  expect(geometry.composer && geometry.kind && geometry.editor && geometry.create && geometry.cancel).toBeTruthy();
  expect(geometry.composer.top).toBeGreaterThanOrEqual(0);
  expect(geometry.composer.bottom).toBeLessThanOrEqual(1000);
  for (const key of ['kind','editor','create','cancel']) {
    const r = geometry[key];
    expect(r.top).toBeGreaterThanOrEqual(geometry.composer.top);
    expect(r.bottom).toBeLessThanOrEqual(geometry.composer.bottom);
  }
  expect(geometry.create.bottom).toBeLessThanOrEqual(geometry.cancel.top);
  expect(geometry.kind.bottom).toBeLessThanOrEqual(geometry.editor.top);
  evidence.desktopGeometry = geometry;
  const editor = page.getByTestId('rich-text-composer');
  await editor.fill('## Review the implementation\n\nPlease explain how the reminder respects Illegal and Pause.');
  await page.getByTestId('client-attachment-input').setInputFiles({name:'review-notes.txt',mimeType:'text/plain',buffer:Buffer.from('Visual acceptance evidence')});
  await expect(page.getByTestId('attachment-upload-progress')).toContainText('已完成');
  for (const width of [1440,390,320]) {
    await page.setViewportSize({width,height:1000});
    await expect(page.getByRole('button',{name:'create',exact:true})).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({path:`${out}/composer-question-${width}.png`});
    await page.getByRole('radio',{name:'Task',exact:true}).click();
    await expect(page.getByRole('button',{name:'create',exact:true})).toBeVisible();
    await page.screenshot({path:`${out}/composer-task-${width}.png`});
    await page.getByRole('radio',{name:'Question',exact:true}).click();
    evidence.sizes.push({view:'composer',width,scrollWidth:await page.evaluate(() => document.documentElement.scrollWidth)});
  }
  await editor.press('Escape');
  await expect(page.getByRole('button',{name:'Send',exact:true})).toBeVisible();
  await expect(editor).toContainText('Review the implementation');
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#send-queue-button').click();
  await expect(page.getByRole('button',{name:'create reminding card',exact:true})).toBeVisible();
  const reopenedEntry = page.getByRole('button', {name:'create reminding card',exact:true});
  await reopenedEntry.click();
  const sent = page.waitForResponse((response) => response.url() === base+'/api/reminding-cards' && response.request().method() === 'POST');
  await page.locator('button[title^="create ("]').evaluate((button) => (button).click());
  const response = await sent; expect(response.status()).toBe(200);
  const created = (await response.json()).card;
  expect(created.attachments).toHaveLength(1); expect(created.status).toBe('pending');
  await page.evaluate(() => { history.pushState({}, '', '/react/locker'); window.dispatchEvent(new PopStateEvent('popstate')); });
  const pending = page.locator(`[data-reminding-card-id="${created.id}"]`);
  await expect(pending).toHaveCount(1);
  await expect(pending).toContainText('Awaiting answer');
  await expect(pending.getByRole('button',{name:/Mark reminding card/})).toHaveCount(0);
  await pending.scrollIntoViewIfNeeded();
  await page.screenshot({path:`${out}/locker-pending-320.png`});
  await pending.getByRole('button',{name:'Delete reminding card',exact:true}).click();
  await expect(pending).toHaveCount(0);
  evidence.browserCreateAttachmentDelete = true;
  expect(evidence.errors).toEqual([]);
  evidence.chromiumVersion = browser.version();
} finally {
  await fs.writeFile(out+'/visual.json',JSON.stringify(evidence,null,2));
  await browser.close();
}

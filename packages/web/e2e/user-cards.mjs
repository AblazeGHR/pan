import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
const [base, out, cardId] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned instance only');
const browser = await chromium.launch({headless: true});
const evidence = {sizes: [], errors: []};
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.on('pageerror', (error) => evidence.errors.push(error.message));
  await page.goto(base + '/react/locker');
  await page.getByLabel('Local user key', {exact: true}).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', {name: 'Log in', exact: true}).click();
  const rows = page.locator(`[data-user-card-id="${cardId}"]`);
  await expect(rows).toHaveCount(2);
  expect(await rows.nth(0).innerText()).toBe(await rows.nth(1).innerText());
  await expect(rows.nth(0).getByRole('region', {name: 'Agent answer'})).toContainText('Real MCP answer');
  await expect(rows.nth(0).locator('textarea,input')).toHaveCount(0);
  await rows.nth(1).getByRole('button', {name:'Mark user card as read',exact:true}).click();
  await expect(rows.nth(0).getByRole('button', {name:'Mark user card as unread',exact:true})).toBeVisible();
  await rows.nth(0).getByRole('button', {name:'Mark user card as unread',exact:true}).click();
  await expect(rows.nth(1).getByRole('button', {name:'Mark user card as read',exact:true})).toBeVisible();
  for (const width of [1440,390,320]) {
    await page.setViewportSize({width,height:1000});
    await rows.nth(1).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({path:`${out}/locker-${width}.png`});
    evidence.sizes.push({view:'locker',width,scrollWidth:await page.evaluate(() => document.documentElement.scrollWidth)});
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'Navigate to chat',exact:true}).first().click();
  await expect(page.getByRole('button',{name:'Create user card',exact:true})).toBeVisible();
  const entry = page.getByRole('button', {name:'Create user card',exact:true});
  await expect(entry).toBeEnabled();
  await entry.click();
  await expect(page.getByRole('button',{name:'Send question card',exact:true})).toBeVisible();
  const editor = page.getByTestId('rich-text-composer');
  await editor.fill('## Review the implementation\n\nPlease explain how the reminder respects Illegal and Pause.');
  await page.getByTestId('client-attachment-input').setInputFiles({name:'review-notes.txt',mimeType:'text/plain',buffer:Buffer.from('Visual acceptance evidence')});
  await expect(page.getByTestId('attachment-upload-progress')).toContainText('已完成');
  for (const width of [1440,390,320]) {
    await page.setViewportSize({width,height:1000});
    await expect(page.getByRole('button',{name:'Send question card',exact:true})).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({path:`${out}/composer-question-${width}.png`});
    await page.getByRole('radio',{name:'Task',exact:true}).click();
    await expect(page.getByRole('button',{name:'Send task card',exact:true})).toBeVisible();
    await page.screenshot({path:`${out}/composer-task-${width}.png`});
    await page.getByRole('radio',{name:'Question',exact:true}).click();
    evidence.sizes.push({view:'composer',width,scrollWidth:await page.evaluate(() => document.documentElement.scrollWidth)});
  }
  await editor.press('Escape');
  await expect(page.getByRole('button',{name:'Send',exact:true})).toBeVisible();
  await expect(editor).toContainText('Review the implementation');
  await entry.click();
  const sent = page.waitForResponse((response) => response.url() === base+'/api/user-cards' && response.request().method() === 'POST');
  await page.getByRole('button',{name:'Send question card',exact:true}).click();
  const response = await sent; expect(response.status()).toBe(200);
  const created = (await response.json()).card;
  expect(created.attachments).toHaveLength(1); expect(created.status).toBe('pending');
  await page.evaluate(() => { history.pushState({}, '', '/react/locker'); window.dispatchEvent(new PopStateEvent('popstate')); });
  const pending = page.locator(`[data-user-card-id="${created.id}"]`);
  await expect(pending).toHaveCount(1);
  await expect(pending).toContainText('Awaiting answer');
  await expect(pending.getByRole('button',{name:/Mark user card/})).toHaveCount(0);
  await pending.scrollIntoViewIfNeeded();
  await page.screenshot({path:`${out}/locker-pending-320.png`});
  await pending.getByRole('button',{name:'Delete user card',exact:true}).click();
  await expect(pending).toHaveCount(0);
  evidence.browserCreateAttachmentDelete = true;
  expect(evidence.errors).toEqual([]);
  evidence.chromiumVersion = browser.version();
} finally {
  await fs.writeFile(out+'/visual.json',JSON.stringify(evidence,null,2));
  await browser.close();
}

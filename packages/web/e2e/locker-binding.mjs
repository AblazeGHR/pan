import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
const [base, out, cardId, targetId] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned instance only');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto(base + '/react/locker');
  await page.getByLabel('Local user key', { exact: true }).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  const row = page.locator(`[data-card-id="${cardId}"]`);
  await expect(row).toBeVisible();
  const trusted = row.getByRole('region', { name: 'Trusted execution pause' });
  await expect(trusted).toContainText('pause_everything');
  await expect(trusted).toContainText(targetId);
  await expect(trusted).toContainText('Action digest:');
  await expect(row.getByRole('option', { name: 'Keep target running' })).toHaveCount(1);
  const sizes = [];
  for (const width of [1280, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await trusted.scrollIntoViewIfNeeded();
    await expect(trusted).toBeVisible();
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(scrollWidth).toBe(width);
    const box = await trusted.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(width);
    sizes.push({ width, scrollWidth, trustedBox: box });
    await page.screenshot({ path: `${out}/trusted-${width}.png` });
  }
  await row.getByLabel('Answer', { exact: true }).fill('ANSWER-SECRET');
  await row.getByLabel('Choice', { exact: true }).selectOption('pause');
  await row.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(row.getByText('Trigger: accepted', { exact: true })).toBeVisible();
  await expect(row.getByLabel('Choice', { exact: true })).toBeDisabled();
  expect(errors).toEqual([]);
  await fs.writeFile(out + '/browser.json', JSON.stringify({ chromiumVersion: browser.version(), sizes, errors,
    cardId, targetId, trustedSummary: await trusted.innerText(), approvedInProductionPanel: true }, null, 2));
} finally { await browser.close(); }

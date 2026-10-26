/* Production Chromium check against the owned combined HTTP test instance. */
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
const [base, input, out] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw new Error('owned port required');
const data = JSON.parse(await fs.readFile(input, 'utf8'));
const browser = await chromium.launch({headless:true});
const errors = [];

try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror', e => errors.push(e.message));
  await page.route(/^https?:\/\/fonts\./, route => route.abort());
  await page.goto(base + '/react/actions');
  if (!process.env.PAN_TEST_JOB_KEY) throw new Error('Owned user key required');
  await page.getByRole('button',{name:'App settings',exact:true}).click();
  await page.getByRole('tab',{name:'Authorization',exact:true}).click();
  await page.getByLabel('Current local password',{exact:true}).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button',{name:'Log in',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Profile-wide Agent permissions',exact:true})).toBeVisible();
  await expect(page.getByRole('checkbox')).toHaveCount(22);
  await expect(page.getByLabel('action.manageSystemActions',{exact:true})).not.toBeChecked();
  await expect(page.getByLabel('jobTemplate.lifecycle',{exact:true})).toHaveCount(0);
  await expect(page.getByLabel('jobTemplate.execute',{exact:true})).toHaveCount(0);
  await page.getByLabel('job.read',{exact:true}).check();
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/automation/authorization/permissions') && r.request().method()==='PUT' && r.status()===200),page.getByRole('button',{name:'Save permissions',exact:true}).click()]);
  await page.getByLabel('job.read',{exact:true}).uncheck();
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/automation/authorization/permissions') && r.request().method()==='PUT' && r.status()===200),page.getByRole('button',{name:'Save permissions',exact:true}).click()]);
  await page.screenshot({path:out + '/production-authorization.png',fullPage:true});
  await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
  await expect(page.getByRole('button', {name:'Combined query pause',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Edit Combined query pause',exact:true}).click();
  await expect(page.getByLabel('Query field').first()).toBeVisible();
  await expect(page.getByLabel('Query text value').first()).toHaveValue('combined-target');
  await page.screenshot({path:out + '/production-action.png',fullPage:true});
  await page.goto(base + '/react/automation');
  // A full navigation restores the Cookie session from the earlier settings login.
  await expect(page.getByRole('button',{name:'Log in',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Log out',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Automation',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Runs',exact:true}).click();
  await page.getByRole('button',{name:'Refresh Runs',exact:true}).click();
  const article = page.getByRole('article').filter({hasText:data.runId});
  await expect(article).toContainText('succeeded');
  await expect(article).toContainText('producer.ping');
  await expect(article).toContainText('Query selection: 1 matched');
  await article.getByText('Fixed target IDs',{exact:true}).click();
  await expect(article).toContainText(data.targetId);
  await article.getByText('Per-target results',{exact:true}).click();
  await expect(article).toContainText('"ok": true');
  await expect(page.getByRole('button',{name:/retry|resend/i})).toHaveCount(0);
  if (data.uncertainId) {
    const uncertain = page.getByRole('article').filter({hasText:data.uncertainId});
    await expect(uncertain).toContainText('uncertain');
    await expect(uncertain).toContainText('Reconcile each target');
    const recovered = page.getByRole('article').filter({hasText:data.queuedId});
    await expect(recovered).toContainText('succeeded');
  }
  await page.screenshot({path:out + '/production-run.png',fullPage:true});
  if (errors.length) throw new Error(JSON.stringify(errors));
  await fs.writeFile(out + '/browser.json',JSON.stringify({ok:true,base,...data,errors,chromiumVersion:browser.version()},null,2),'utf8');
  console.log('Production Chromium UTF-8 \u{1f9ea}');
} finally { await browser.close(); }

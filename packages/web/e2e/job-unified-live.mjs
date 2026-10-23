/* Real production UI against an owned HTTP/fakeCLI instance; no API routing mocks. */
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
const [base, inputPath, out] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned isolated port required');
const input = JSON.parse(await fs.readFile(inputPath, 'utf8'));
if (!process.env.PAN_TEST_JOB_KEY) throw Error('Owned fixture key required');
const browser = await chromium.launch({headless:true});
const errors = [];
let page;
try {
  page = await browser.newPage({viewport:{width:1280,height:1000}});
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base + '/react/jobs');
  await page.getByRole('button',{name:'New Job',exact:true}).click();
  await page.getByLabel('Local user key',{exact:true}).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button',{name:'Log in',exact:true}).click();
  await expect(page.getByRole('button',{name:'Log out',exact:true})).toBeVisible();
  const fill = async name => {
    await page.getByLabel('Name',{exact:true}).fill(name);
    await page.getByLabel('Action reference',{exact:true}).selectOption('system_session_message');
    await page.getByLabel('Declared arguments (JSON)',{exact:true}).fill(JSON.stringify({sessionId:input.controlId,text:name}));
    await page.getByLabel('Trigger',{exact:true}).selectOption('immediate');
    await page.getByLabel('Complete schedule specs (JSON)',{exact:true}).fill('[]');
    await page.getByLabel('Enable after user confirmation',{exact:true}).check();
  };
  await fill('Unified production UI message');
  await page.getByRole('button',{name:'Save unified Job',exact:true}).click();
  await expect(page.getByTestId('jobs-list-scroll').getByText('Unified production UI message',{exact:true})).toBeVisible();
  await expect.poll(async () => {
    const data=await (await page.request.get(base+'/api/jobs')).json();
    return data.jobs.find(job=>job.name==='Unified production UI message')?.status;
  },{timeout:15000}).toBe('completed');
  // A dirty editor must keep its opened CAS revision after a real WS update.
  await page.getByTestId('jobs-list-scroll').getByText('Unified production UI message',{exact:true}).click();
  await page.getByRole('button',{name:'Edit',exact:true}).click();
  await page.getByLabel('Name',{exact:true}).fill('Retained conflicting draft');
  const existing=(await (await page.request.get(base+'/api/jobs')).json()).jobs.find(j=>j.name==='Unified production UI message');
  const login=await (await page.request.post(base+'/api/automation/login',{data:{key:process.env.PAN_TEST_JOB_KEY}})).json();
  const changed=await page.request.put(base+'/api/jobs/action-jobs/'+existing.jobId,{headers:{Authorization:'Bearer '+login.token},
    data:{config:{...existing.unified.config,name:'Concurrent remote config'},expectedRevision:existing.configRevision}});
  if (!changed.ok()) throw Error('Owned concurrent update rejected: '+changed.status());
  await expect(page.getByText('Concurrent remote config',{exact:true}).first()).toBeVisible();
  await page.getByRole('button',{name:'Save unified Job',exact:true}).click();
  await expect(page.getByText('409: Revision changed. Reload before confirming. Draft kept.',{exact:true})).toBeVisible();
  await expect(page.getByLabel('Name',{exact:true})).toHaveValue('Retained conflicting draft');
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('button',{name:'New Job',exact:true}).click();
  await page.getByLabel('Create',{exact:true}).selectOption('template');
  await fill('Unified production complete Template');
  await page.getByLabel('Enable Template instantiation capability',{exact:true}).check();
  await page.getByRole('button',{name:'Save complete Template',exact:true}).click();
  await expect(page.getByRole('button',{name:'Instantiate fixed Template',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Instantiate fixed Template',exact:true}).click();
  await expect(page.getByTestId('jobs-list-scroll').getByText('Unified production complete Template',{exact:true})).toBeVisible();
  await expect.poll(async () => {
    const data=await (await page.request.get(base+'/api/jobs')).json();
    return data.jobs.find(job=>job.name==='Unified production complete Template')?.status;
  },{timeout:15000}).toBe('completed');
  await page.getByRole('button',{name:'New Job',exact:true}).click();
  await page.getByLabel('Action reference',{exact:true}).selectOption('system_background_process');
  await page.getByLabel('Completion',{exact:true}).selectOption('monitoring');
  await page.getByLabel('Signal type',{exact:true}).fill('owned.done');
  await page.getByLabel('Recovery',{exact:true}).selectOption('on');
  const sizes=[];
  for (const toast of await page.getByRole('button',{name:'Dismiss toast',exact:true}).all()) await toast.click();
  await expect(page.getByRole('button',{name:'Dismiss toast',exact:true})).toHaveCount(0);
  for (const width of [1280,390,320]) {
    await page.setViewportSize({width,height:1000});
    await expect(page.getByTestId('unified-job-form')).toBeVisible();
    await expect(page.getByLabel('Overlap',{exact:true})).toBeVisible();
    const geometry=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
    if (geometry.scrollWidth>width) throw Error('Horizontal overflow: '+JSON.stringify(geometry));
    await page.screenshot({path:out+`/jobs-${width}.png`,fullPage:true}); sizes.push(geometry);
    await page.getByRole('button',{name:'Save unified Job',exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:out+`/jobs-policies-${width}.png`,fullPage:true});
    await page.getByLabel('Name',{exact:true}).scrollIntoViewIfNeeded();
  }
  if (errors.length) throw Error(JSON.stringify(errors));
  await fs.writeFile(out+'/browser.json',JSON.stringify({ok:true,chromiumVersion:browser.version(),sizes,errors},null,2),'utf8');
  console.log('Unified Job production Chromium complete');
} catch (error) {
  if (page) {
    await page.screenshot({path:out+'/browser-failure.png',fullPage:true});
    await fs.writeFile(out+'/browser-failure.txt',await page.locator('body').innerText(),'utf8');
  }
  throw error;
} finally { await browser.close(); }

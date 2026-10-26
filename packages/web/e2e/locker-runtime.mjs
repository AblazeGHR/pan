import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
const [base, out, owner] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned instance only');
const browser=await chromium.launch({headless:true});
try {
  const page=await browser.newPage({viewport:{width:1280,height:1000}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/react/locker');
  await page.getByLabel('Local user key',{exact:true}).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button',{name:'Log in',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production wiring',exact:true})).toBeVisible();
  await page.getByRole('radio',{name:'Pause',exact:true}).check();
  await page.getByRole('button',{name:'Submit',exact:true}).click();
  await page.getByText('Delivery details',{exact:true}).click();
  await expect(page.getByText('Trigger delivery: accepted',{exact:true})).toBeVisible();
  const create=async(key)=>page.request.post(base+'/api/interaction-cards',{headers:{Origin:base},data:{ownerSessionId:owner,recipientId:'user',
    requestKey:key,card:{title:'Reject probe',body:'Retained body',summaryBody:'Safe reject',
      choices:[{id:'pause',label:'Pause',triggerRef:{id:'selected',revision:1}}]}}});
  const created=await create('browser-reject');
  expect(created.status()).toBe(200);
  const card=(await created.json()).card;
  await page.getByRole('button',{name:'Refresh Locker',exact:true}).click();
  const row=page.locator(`[data-card-id="${card.id}"]`);
  await row.getByRole('button',{name:'Reject',exact:true}).click();
  await row.getByRole('textbox',{name:'Reason for rejection',exact:true}).fill('Owned rejection probe');
  await row.getByRole('button',{name:'Confirm rejection',exact:true}).click();
  await expect(row.getByRole('region',{name:'Completed interaction'})).toContainText('Rejected');
  await row.getByRole('button',{name:'Delete record',exact:true}).click();
  await expect(row).toHaveCount(0);
  const sizes=[];
  for(const width of [1280,390,320]) {
    await page.setViewportSize({width,height:1000});
    await expect(page.getByRole('main',{name:'Locker'})).toBeVisible();
    const scrollWidth=await page.evaluate(()=>document.documentElement.scrollWidth);
    expect(scrollWidth).toBe(width);sizes.push({width,scrollWidth});
    await page.screenshot({path:`${out}/locker-${width}.png`});
  }
  expect(errors).toEqual([]);
  await fs.writeFile(out+'/browser.json',JSON.stringify({chromiumVersion:browser.version(),sizes,errors,realLogin:true,choiceSubmit:true,rejectDelete:true},null,2));
} finally {await browser.close();}

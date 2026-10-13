/* global Buffer, console, process, URL */
// Synthetic data only. No service requests, browser state, or Session writes.
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { performance } from 'node:perf_hooks';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const require = createRequire(new URL('../../web/package.json', import.meta.url));
const ts = require('typescript');
const args = process.argv.slice(2);
const ref = args.includes('--ref') ? args[args.indexOf('--ref') + 1] : undefined;
const browserMode = args.includes('--browser');
async function moduleUrl(relative) {
  const url = new URL(relative, import.meta.url);
  const code = ref ? execFileSync('git', ['show', `${ref}:packages/web/src/${relative.split('/src/')[1]}`],
    { cwd: fileURLToPath(new URL('../../../', import.meta.url)), encoding: 'utf8' }) : await readFile(url, 'utf8');
  const js = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
}
const filterUrl = await moduleUrl('../../web/src/utils/sessionFilters.ts');
const reportUrl = await moduleUrl('../../web/src/components/chat/manualTaskReport.ts');
function run(filters, reports) {
function measure(name, fn) {
  fn();
  const samples = [];
  for (let i = 0; i < 15; i++) {
    const start = performance.now(); fn(); samples.push(performance.now() - start);
  }
  samples.sort((a, b) => a - b);
  return { name, medianMs: samples[7], p95Ms: samples[14] };
}
const results = [];
for (const n of [500, 2000, 5000]) {
  const sessions = Array.from({ length: n }, (_, i) => ({ id: `s${i}`, managedBy: i ? 's0' : undefined, workspaceIds: ['w'] }));
  results.push(measure(`workspace-members-${n}`, () => filters.workspaceMemberIds(sessions, 'w')));
}
for (const n of [16, 64, 256, 1000]) {
  const messages = Array.from({ length: n }, (_, i) => [
    { role: 'user', content: '', source: 'agent', taskIdSource: 'assign', taskId: `t${i}`, messageId: `u${i}` },
    { role: 'assistant', content: 'answer', messageId: `a${i}` },
  ]).flat();
  const terminals = Array.from({ length: n }, (_, i) => ({ taskId: `t${i}`, taskSeq: i, historyEpoch: 'e', historyRevision: (i + 1) * 2 }));
  const offsets = new Map(messages.map((m, i) => [m, i]));
  results.push(measure(`manual-report-${n}`, () => reports.findManualReportTargets(messages,
    { id: 's', managedBy: 'manager', terminalResults: terminals },
    { epoch: 'e', revision: messages.length, total: messages.length, offsetOf: m => offsets.get(m) })));
}
return results;
}
let results;
let runtime = `node ${process.version}`;
if (browserMode) {
  const { chromium } = require('@playwright/test');
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    results = await page.evaluate(async ({ filterUrl, reportUrl, source }) => {
      const filters = await import(filterUrl);
      const reports = await import(reportUrl);
      return new Function(`return (${source})`)()(filters, reports);
    }, { filterUrl, reportUrl, source: run.toString() });
    runtime = `chromium ${browser.version()}`;
  } finally { await browser.close(); }
} else results = run(await import(filterUrl), await import(reportUrl));
console.log(JSON.stringify({ runtime, ref: ref ?? 'working-tree', samples: 15, results }, null, 2));

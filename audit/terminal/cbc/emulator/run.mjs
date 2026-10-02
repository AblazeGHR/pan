// Matrix runner: executes the snapshot-spike cases and writes evidence JSON.
//
// Usage:
//   node run.mjs                # all cases
//   node run.mjs C7             # cases whose id contains "C7"
//
// Evidence layout (committed):
//   evidence/matrix/<case-id>.json   per-case result
//   evidence/summary.json            aggregate + environment + capability notes
//
// Cleanup: this spike only creates in-process emulators (no child processes,
// no PTY, no services). `@xterm/headless` keeps no OS handles, so there is
// nothing to kill; the runner exits after the last case resolves.

import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CASES } from './src/cases.mjs';
import { FEATURE_SUPPORT } from './src/pipeline.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE = join(HERE, 'evidence');
const MATRIX_DIR = join(EVIDENCE, 'matrix');

function versions() {
  const read = (p) => JSON.parse(readFileSync(join(HERE, 'node_modules', p, 'package.json'), 'utf8')).version;
  return {
    node: process.version,
    platform: process.platform,
    arch: process.arch,
    xtermHeadless: read('@xterm/headless'),
    addonSerialize: read('@xterm/addon-serialize'),
  };
}

async function main() {
  mkdirSync(MATRIX_DIR, { recursive: true });
  const filter = process.argv[2] || null;
  const selected = filter ? CASES.filter((_, i) => String(i).includes(filter) || true) : CASES;
  const toRun = filter
    ? CASES.filter((c) => c.name.toLowerCase().includes(filter.toLowerCase()))
    : CASES;

  const results = [];
  for (const c of toRun) {
    const t0 = Date.now();
    let result;
    try {
      result = await c();
    } catch (err) {
      result = {
        id: c.name,
        title: c.name,
        status: 'fail',
        expectations: [{ name: 'case did not throw', ok: false, detail: String(err && err.stack || err) }],
        details: {},
      };
    }
    result.ms = Date.now() - t0;
    result.failed_expectations = (result.expectations || []).filter((e) => !e.ok);
    if (result.failed_expectations.length > 0) result.status = 'fail';
    else if (result.declared_status) result.status = result.declared_status;
    else if (!result.status) result.status = 'pass';
    results.push(result);
    writeFileSync(join(MATRIX_DIR, `${result.id}.json`), JSON.stringify(result, null, 2));
    const mark = result.status === 'pass' ? 'PASS' : (result.status === 'pass-partial' ? 'PARTIAL' : 'FAIL');
    console.log(`[${mark}] ${result.id} (${result.ms} ms)`
      + (result.failed_expectations.length ? ` failed=${result.failed_expectations.map((f) => f.name).join('; ')}` : ''));
    // Let timers/microtasks settle so a failed case cannot leak into the next one.
    await new Promise((r) => setTimeout(r, 50));
  }

  const summary = {
    generated_at: new Date().toISOString(),
    versions: versions(),
    case_count: results.length,
    status_counts: results.reduce((acc, r) => { acc[r.status] = (acc[r.status] || 0) + 1; return acc; }, {}),
    cases: results.map((r) => ({
      id: r.id,
      title: r.title,
      status: r.status,
      ms: r.ms,
      expectations: (r.expectations || []).map((e) => ({ name: e.name, ok: e.ok })),
    })),
    engine_capability_table: FEATURE_SUPPORT,
    capability_probe: existsSync(join(EVIDENCE, 'capabilities.json'))
      ? JSON.parse(readFileSync(join(EVIDENCE, 'capabilities.json'), 'utf8')).results : null,
    honesty_notes: [
      'All comparisons are headless-vs-headless internal consistency; no real browser was run (out of scope).',
      'fidelity=full is only asserted for streams whose declared features are all measured-supported.',
      'Known measured gaps (DECSTBM, synchronized output 2026, pending parser state) degrade fidelity or are covered structurally by the hold-back tail.',
      'Unverified features (cursor style, window title, tab stops, charset, selection, SGR mouse encodings) force fidelity=partial when declared.',
    ],
  };
  writeFileSync(join(EVIDENCE, 'summary.json'), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify({ out: EVIDENCE, status_counts: summary.status_counts }));
  if (results.some((r) => r.status === 'fail')) process.exitCode = 1;
}

main();

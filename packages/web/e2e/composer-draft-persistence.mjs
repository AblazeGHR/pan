/* global Buffer, process, console */
/* Composer draft persistence — request-volume and hot-path cost measurement.
 *
 * Isolated by construction: it transforms and runs the REAL store module
 * (src/stores/composerDraftStore.ts) against a virtual clock and a stubbed
 * transport.  No Pan server, no browser, no real user data, no network, no
 * filesystem.  Nothing here is a claim about production latency; it measures
 * exactly the two things the design has to get right:
 *
 *   1. how many HTTP requests each interaction pattern produces, and
 *   2. how much main-thread time the per-keystroke path costs.
 *
 * Run: node e2e/composer-draft-persistence.mjs
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.join(here, '..');
const require = createRequire(path.join(webRoot, 'package.json'));
// The TypeScript compiler is a direct dependency of this package, so the
// transform below needs no install and no path guessing.
const ts = require('typescript');

// ── virtual clock ────────────────────────────────────────────────────────
// Installed before the store module is evaluated so its setTimeout / Date.now
// calls are driven by the simulation rather than wall time.
let vnow = 0;
let nextTimer = 1;
const timers = new Map();
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, delay = 0) => {
  const id = nextTimer++;
  timers.set(id, { at: vnow + Math.max(0, delay), fn });
  return id;
};
globalThis.clearTimeout = (id) => {
  timers.delete(id);
};
// Captured before the override: the harness itself needs a real clock to bound
// its drain loops, while the store sees the virtual one.
const realNow = Date.now.bind(Date);
Date.now = () => vnow;

function advance(ms) {
  const target = vnow + ms;
  for (;;) {
    let next = null;
    for (const [id, t] of timers) {
      if (t.at <= target && (next === null || t.at < next[1].at)) next = [id, t];
    }
    if (!next) break;
    timers.delete(next[0]);
    vnow = next[1].at;
    next[1].fn();
  }
  vnow = target;
}
const sleepReal = (ms) => new Promise((r) => realSetTimeout(r, ms));

// ── stub transport ───────────────────────────────────────────────────────
// Records every request the store makes and answers from a scripted server
// model (per-Session revision + latency + failure injection), so the
// scheduler's real async/await interleavings execute rather than being
// simulated.
//
// Revisions are per-Session because drafts are: the server keeps one counter
// per draft file, so a single global counter here would make unrelated
// Sessions look as though they had conflicting.
const net = {
  requests: [],
  revisions: new Map(),
  latencyMs: 0,
  failNext: 0,
  conflictOnSeq: null,
  seq: 0,
  peakInFlight: 0,
  inFlight: 0,
  /** When set, the next PUT is held open until the scenario releases it. */
  holdNext: false,
};

function makeDraft(text) {
  return { text, parts: [{ type: 'text', value: text }], attachments: [] };
}

async function putSessionDraft(sessionId, draft, baseRevision) {
  const seq = net.seq++;
  net.requests.push({
    kind: 'PUT',
    sessionId,
    at: vnow,
    bytes: Buffer.byteLength(JSON.stringify(draft ?? null), 'utf8'),
    seq,
  });
  // Count true request concurrency: this is the quantity the design bounds.
  net.inFlight += 1;
  net.peakInFlight = Math.max(net.peakInFlight, net.inFlight);
  try {
    if (net.latencyMs) await sleepReal(net.latencyMs);
    const current = net.revisions.get(sessionId) ?? 0;
    const conflict =
      net.conflictOnSeq === seq || (current !== 0 && baseRevision !== current);
    if (conflict) {
      // A conflict reports the authoritative revision; it does not advance it.
      // The other tab's write already happened before this request arrived.
      throw new DraftConflictError(makeDraft('SERVER-WINS'), current);
    }
    if (net.failNext > 0) {
      net.failNext -= 1;
      throw new Error('simulated network failure');
    }
    const revision = current + 1;
    net.revisions.set(sessionId, revision);
    return { revision, draft, updatedAt: null };
  } finally {
    net.inFlight -= 1;
  }
}

async function getSessionDraft(sessionId) {
  net.requests.push({ kind: 'GET', sessionId, at: vnow, bytes: 0, seq: net.seq++ });
  if (net.latencyMs) await sleepReal(net.latencyMs);
  return { revision: net.revisions.get(sessionId) ?? 0, draft: null, updatedAt: null };
}

class DraftConflictError extends Error {
  constructor(current, currentRevision) {
    super('Draft changed elsewhere');
    this.name = 'DraftConflictError';
    this.current = current;
    this.currentRevision = currentRevision;
  }
}

// Compile the real store with only the transport swapped out.  The `type`-
// only import is erased and the value import is replaced by a stub, so the
// module graph is exactly one file: the shipped store.
//
// The stub reaches the harness's classes through globals rather than defining
// its own, so `error instanceof DraftConflictError` inside the store is a real
// check against the class the transport actually throws.
const stubApiUrl =
  'data:text/javascript;base64,' +
  Buffer.from(
    `export const putSessionDraft = (...a) => globalThis.__put(...a);
export const getSessionDraft = (...a) => globalThis.__get(...a);
export const DraftConflictError = globalThis.__ConflictClass;
export class ApiRequestError extends Error {}
`,
    'utf8',
  ).toString('base64');

const storeSource = readFileSync(
  path.join(webRoot, 'src', 'stores', 'composerDraftStore.ts'),
  'utf8',
);
const transpiled = ts
  .transpileModule(storeSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    fileName: 'composerDraftStore.ts',
  })
  .outputText
  .replace(/from\s+['"]@\/services\/api['"]/g, `from '${stubApiUrl}'`);

globalThis.__put = putSessionDraft;
globalThis.__get = getSessionDraft;
globalThis.__ConflictClass = DraftConflictError;

const storeUrl = 'data:text/javascript;base64,' + Buffer.from(transpiled, 'utf8').toString('base64');
const store = await import(storeUrl);
const { TIMING, recordDraft, loadDraft, canApplyLoadedDraft } = store;

function resetNet() {
  net.requests = [];
  net.revisions.clear();
  net.latencyMs = 0;
  net.failNext = 0;
  net.conflictOnSeq = null;
  net.seq = 0;
  net.peakInFlight = 0;
  net.inFlight = 0;
  net.holdNext = false;
  timers.clear();
  // Reset the virtual clock too, so a scenario's timestamps are relative to its
  // own start and cross-scenario comparisons are meaningless (as intended).
  vnow = 0;
}

// Let the JS event loop run the store's await continuations between clock
// advances, so real promise interleaving happens.  `net.latencyMs` of *real*
// time is honoured here, which is what makes the slow-network scenario's
// in-flight bound real rather than assumed.
//
// Uses the REAL clock: Date.now is virtualized for the store, so using it here
// would never terminate.
const drain = async (ms = 0) => {
  const deadline = realNow() + ms;
  for (;;) {
    await sleepReal(0);
    if (realNow() >= deadline) break;
  }
};

/** Advance the virtual clock, then let real pending promises settle. */
const step = async (ms) => {
  advance(ms);
  await drain(net.latencyMs ? net.latencyMs + 5 : 1);
};

const results = [];
const report = (name, detail) => results.push({ name, ...detail });

// DRAFT_DEBUG=1 prints every request with its virtual timestamp, which is how
// the "did an extra write happen?" questions below get answered rather than
// guessed at.
const DEBUG = process.env.DRAFT_DEBUG === '1';
function traceRequests(label) {
  if (!DEBUG) return;
  console.log(`  [trace ${label}] ` + JSON.stringify(
    net.requests.map((r) => ({ k: r.kind, s: r.sessionId, t: r.at, seq: r.seq })),
  ));
}

// ── 1. ordinary continuous typing ────────────────────────────────────────
// 200 ms per keystroke (a brisk but ordinary typist), 60 s with no pause long
// enough to let a trailing debounce fire on its own.
async function continuousTyping() {
  resetNet();
  const sid = 'continuous';
  const KEYS = 300;
  for (let i = 1; i <= KEYS; i += 1) {
    recordDraft(sid, makeDraft('x'.repeat(i)));
    await step(200);
  }
  await drain(net.latencyMs + 5);
  const puts = net.requests.filter((r) => r.kind === 'PUT');
  report('A. continuous typing, 60s / 300 keystrokes, no pause', {
    keystrokes: KEYS,
    'requests (PUT)': puts.length,
    'reduction vs one request per keystroke': `${(KEYS / Math.max(1, puts.length)).toFixed(0)}x`,
    'sustained rate': `${((puts.length / 60) * 60).toFixed(1)} req/min`,
    'total request bytes': puts.reduce((s, r) => s + r.bytes, 0),
    'peak concurrent PUTs': net.peakInFlight,
  });
  // Inter-key gaps between the writes actually issued, to show the min-interval
  // floor and max-wait ceiling are both doing work.
  const gaps = puts.slice(1).map((r, i) => r.at - puts[i].at);
  report('A2. gaps between the writes of scenario A (ms)', {
    'min gap (min-interval floor = 2000)': gaps.length ? Math.min(...gaps) : 'n/a',
    'max gap (max-wait ceiling = 5000)': gaps.length ? Math.max(...gaps) : 'n/a',
  });
}

// ── 2. typing with natural pauses ────────────────────────────────────────
async function typingWithPauses() {
  resetNet();
  const sid = 'pauses';
  let chars = 0;
  for (let burst = 0; burst < 10; burst += 1) {
    for (let k = 0; k < 20; k += 1) {
      chars += 1;
      recordDraft(sid, makeDraft('y'.repeat(chars)));
      await step(180);
    }
    await step(1500);
  }
  await drain(net.latencyMs + 5);
  const puts = net.requests.filter((r) => r.kind === 'PUT');
  report('B. typing with 1.5s pauses, 200 keystrokes over ~20s', {
    keystrokes: 200,
    'requests (PUT)': puts.length,
    'peak concurrent PUTs': net.peakInFlight,
  });
}

// ── 3. type then stop (the most common real interaction) ─────────────────
async function typeThenIdle() {
  resetNet();
  const sid = 'idle';
  for (let i = 1; i <= 40; i += 1) {
    recordDraft(sid, makeDraft('z'.repeat(i)));
    await step(150);
  }
  const stoppedAt = vnow;
  // Let every already-scheduled write land and settle BEFORE the idle window
  // starts.  A write that was pending when typing stopped is not a poll; only
  // requests issued after this point would be.
  await step(TIMING.MAX_WAIT_MS + TIMING.DEBOUNCE_MS);
  await drain(5);
  const settled = net.requests.length;
  const lastAt = net.requests.at(-1)?.at ?? 0;
  // The user is now idle. Nothing may ever be issued again.
  await step(60_000);
  traceRequests('C');
  report('C. type 40 chars, then stop and idle 60s', {
    'requests while typing + final save': settled,
    'requests in the following 60s of idle (must be 0)': net.requests.length - settled,
    'final write, ms after the last keystroke': lastAt - stoppedAt,
  });
}

// ── 4. cold load vs cache hit ───────────────────────────────────────────
async function coldLoadAndCacheHit() {
  resetNet();
  const before1 = net.requests.length;
  await loadDraft('sess-a');
  await drain(5);
  const first = net.requests.length - before1;
  // A→B→A: the third selection of A must reuse the remembered read.
  const before2 = net.requests.length;
  await loadDraft('sess-a');
  await drain(5);
  const repeat = net.requests.length - before2;
  // Three Sessions, one of them selected twice concurrently: a cold dashboard
  // costs one read per selected Session — not a scan, not one per click.
  const before3 = net.requests.length;
  await Promise.all([loadDraft('sess-b'), loadDraft('sess-b'), loadDraft('sess-c')]);
  await drain(5);
  const three = net.requests.length - before3;
  report('D. cold load: first / A→B→A repeat / 3 Sessions (4 selections)', {
    'first load of a Session': first,
    'repeat load of the same Session (must be 0)': repeat,
    '3 distinct Sessions, 4 selections': three,
  });
}

// ── 5. slow network ─────────────────────────────────────────────────────
async function slowNetwork() {
  resetNet();
  net.latencyMs = 40;
  const sid = 'slow';
  for (let i = 1; i <= 60; i += 1) {
    recordDraft(sid, makeDraft('s'.repeat(i)));
    await step(200);
  }
  await drain(net.latencyMs + 20);
  report('E. 60 keystrokes over 12s, 40ms RTT', {
    keystrokes: 60,
    'requests (PUT)': net.requests.filter((r) => r.kind === 'PUT').length,
    'peak concurrent PUTs (design bound: 1)': net.peakInFlight,
  });
}

// ── 6. failing network: bounded retry, then stop ─────────────────────────
async function failingNetwork() {
  resetNet();
  net.failNext = 10_000; // never recovers during the window
  const sid = 'failing';
  recordDraft(sid, makeDraft('important unsent text'));
  // Let the initial write and the whole retry budget play out.
  for (let i = 0; i < TIMING.RETRY_ATTEMPTS + 2; i += 1) await step(TIMING.RETRY_DELAY_MS);
  await drain(10);
  const attempts = net.requests.length;
  // Long idle afterwards: a failed draft must not become a poller.
  await step(300_000);
  report('F. network failing: 1 keystroke, then nothing', {
    'attempts made (1 initial + bounded retries)': attempts,
    'retry budget': TIMING.RETRY_ATTEMPTS,
    'requests in the following 5 min (must be 0)': net.requests.length - attempts,
  });
}

// ── 7. two tabs: CAS conflict, and that it is recoverable ───────────────
// Each phase advances past MAX_WAIT, not just DEBOUNCE: the min-interval floor
// can legitimately push a write out to MIN_INTERVAL_MS, and a step that is too
// short would silently never issue the request this scenario exists to test.
const PHASE_MS = TIMING.MAX_WAIT_MS + TIMING.DEBOUNCE_MS;

async function conflict() {
  resetNet();
  const sid = 'conflict';
  recordDraft(sid, makeDraft('my local text'));
  await step(PHASE_MS);
  await drain(5);
  const ourRevision = net.revisions.get(sid) ?? 0;
  const issuedBeforeConflict = net.requests.length;
  // Another tab saved first: the server moved to a revision this tab has not
  // seen, so our next write is built on a stale base.
  const otherTabRevision = ourRevision + 1;
  net.revisions.set(sid, otherTabRevision);
  recordDraft(sid, makeDraft('my local text, edited again'));
  await step(PHASE_MS);
  await drain(20);
  const afterConflict = net.revisions.get(sid) ?? 0;
  const conflictedRequestIssued = net.requests.length > issuedBeforeConflict;
  // A conflict must not be a dead end.  The next real edit adopts the other
  // tab's revision and saves on top of it; the local text must not be stranded.
  recordDraft(sid, makeDraft('my local text, edited once more'));
  await step(PHASE_MS);
  await drain(20);
  const afterNextEdit = net.revisions.get(sid) ?? 0;

  report('G. another tab wrote first (CAS conflict)', {
    'a request was actually issued to conflict': conflictedRequestIssued,
    'our revision before the other tab saved': ourRevision,
    "the other tab's revision": otherTabRevision,
    'revision after our conflicted write (must be unchanged by us)': afterConflict,
    'did we force-write over the other tab': afterConflict === otherTabRevision ? 'no' : 'YES (BUG)',
    'after one more edit, the revision advanced': afterNextEdit > afterConflict ? 'yes' : 'NO — STRANDED',
    'local text stranded unsaved': afterNextEdit > afterConflict ? 'no' : 'YES (BUG)',
  });

}

// ── 8. request payload size ─────────────────────────────────────────────
// A fresh Session id per size: the store keeps per-Session state, and reusing
// one id across sizes would measure the min-interval floor, not the payload.
async function payloadSize() {
  for (const chars of [100, 5_000, 50_000]) {
    resetNet();
    const sid = `size-${chars}`;
    recordDraft(sid, makeDraft('q'.repeat(chars)));
    await step(TIMING.DEBOUNCE_MS);
    await drain(5);
    const put = net.requests.find((r) => r.kind === 'PUT');
    results.push({
      name: `H. draft of ${chars} chars of text`,
      'requests': net.requests.length,
      'request bytes (JSON)': put ? put.bytes : 0,
    });
  }
}

// ── 9. one Session must not slow another ────────────────────────────────
async function perSessionIsolation() {
  resetNet();
  // Gate A's response on an explicit release so it is provably still in flight
  // while B saves, rather than relying on the drain to not have finished it.
  let releaseA;
  const heldA = new Promise((resolve) => {
    releaseA = resolve;
  });
  const origPut = putSessionDraft;
  globalThis.__put = async (sessionId, ...rest) => {
    if (sessionId === 'iso-a') {
      net.requests.push({ kind: 'PUT', sessionId, at: vnow, bytes: 0, seq: net.seq++ });
      net.inFlight += 1;
      net.peakInFlight = Math.max(net.peakInFlight, net.inFlight);
      try {
        await heldA; // A stays open for the whole scenario
        net.revisions.set(sessionId, (net.revisions.get(sessionId) ?? 0) + 1);
        return { revision: net.revisions.get(sessionId), draft: null, updatedAt: null };
      } finally {
        net.inFlight -= 1;
      }
    }
    return origPut(sessionId, ...rest);
  };
  recordDraft('iso-a', makeDraft('a'.repeat(50)));
  await step(TIMING.DEBOUNCE_MS);
  const aInFlight = net.inFlight > 0;
  // Switch to B and type while A is provably still open.
  recordDraft('iso-b', makeDraft('b'.repeat(10)));
  await step(TIMING.DEBOUNCE_MS);
  await drain(10);
  const bPutsWhileAOpen = net.requests.filter(
    (r) => r.sessionId === 'iso-b' && r.kind === 'PUT',
  ).length;
  releaseA();
  await drain(30);
  globalThis.__put = origPut;
  const aPuts = net.requests.filter((r) => r.sessionId === 'iso-a' && r.kind === 'PUT').length;
  report('I. Session A held mid-save while the user types in Session B', {
    'A had a request still in flight when B saved': aInFlight,
    'PUTs for A': aPuts,
    'PUTs for B while A was still open': bPutsWhileAOpen,
    'B saved without waiting for A (must be 1)': bPutsWhileAOpen === 1 ? 'yes' : 'NO — blocked',
  });
  // A step shorter than the timing policy can silently skip the write this
  // scenario exists to test, which would make the "yes" above vacuous.
  if (!aInFlight || bPutsWhileAOpen !== 1) {
    report('I!. WARNING: scenario I did not exercise what it claims', {
      problem: 'the timing window was too short for a request to be issued',
    });
  }
}

// ── 10. canApplyLoadedDraft guards ───────────────────────────────────────
function raceGuards() {
  const sid = 'race-guard';
  recordDraft(sid, makeDraft('local typing'));
  const sameEpoch = canApplyLoadedDraft(sid, 7, 7);
  const otherEpoch = canApplyLoadedDraft(sid, 7, 8);
  report('J. canApplyLoadedDraft race guards (all must read "blocked")', {
    'same epoch, but the user typed since the load started': sameEpoch ? 'APPLIED (BUG)' : 'blocked',
    'stale epoch (the A→B→A switch case)': otherEpoch ? 'APPLIED (BUG)' : 'blocked',
  });
}

// ── 11. send clears the composer: the text must not come back ───────────
// The riskiest ordering in this feature.  A save carrying the pre-send text is
// in flight; the user hits send; the composer clears; the tombstone goes out
// after.  If ordering or versioning were wrong, the reload would show text the
// user already sent.
async function sendThenReload() {
  resetNet();
  const sid = 'send';
  let releaseSave;
  const heldSave = new Promise((resolve) => {
    releaseSave = resolve;
  });
  const origPut = putSessionDraft;
  globalThis.__put = async (sessionId, ...rest) => {
    if (net.holdNext) {
      net.holdNext = false;
      net.requests.push({ kind: 'PUT', sessionId, at: vnow, bytes: 0, seq: net.seq++ });
      net.inFlight += 1;
      net.peakInFlight = Math.max(net.peakInFlight, net.inFlight);
      try {
        await heldSave;
        const revision = (net.revisions.get(sessionId) ?? 0) + 1;
        net.revisions.set(sessionId, revision);
        return { revision, draft: null, updatedAt: null };
      } finally {
        net.inFlight -= 1;
      }
    }
    return origPut(sessionId, ...rest);
  };
  // Type the message, then let its save start but not finish.
  recordDraft(sid, makeDraft('the message being sent'));
  net.holdNext = true;
  await step(TIMING.DEBOUNCE_MS);
  const heldOpen = net.inFlight > 0;
  // The user hits send: the composer clears while that save is still open.
  recordDraft(sid, null);
  await step(TIMING.DEBOUNCE_MS);
  const writesWhileHeld = net.requests.length;
  releaseSave();
  await drain(20);
  globalThis.__put = origPut;
  await step(TIMING.MAX_WAIT_MS + TIMING.DEBOUNCE_MS);
  await drain(10);
  // What does a fresh client see for this Session?
  const observed = await loadDraft(sid);
  report('K. send while a save is in flight, then reload', {
    'a save was genuinely still open at send time': heldOpen,
    'writes issued while the old save was open': writesWhileHeld,
    'a fresh client reads back the draft': observed === null ? 'null (correct)' : JSON.stringify(observed),
    'sent text resurrected': observed ? 'YES (BUG)' : 'no',
  });
  if (!heldOpen) {
    report('K!. WARNING: scenario K did not exercise what it claims', {
      problem: 'no save was in flight when the composer cleared',
    });
  }
}

// ── 12. per-keystroke hot path cost, measured without harness overhead ───
// Everything above drains the event loop between keystrokes, which dominates
// the number.  This measures only recordDraft() itself, in a tight loop.
function hotPathCost() {
  resetNet();
  const sid = 'hot';
  const N = 20_000;
  // Warm up so the measurement is not dominated by first-call JIT.
  for (let i = 0; i < 1_000; i += 1) recordDraft(sid, makeDraft('w'.repeat(i)));
  const samples = [];
  for (let round = 0; round < 5; round += 1) {
    const t0 = process.hrtime.bigint();
    for (let i = 0; i < N; i += 1) recordDraft(sid, makeDraft('w'.repeat((i % 900) + 1)));
    samples.push(Number(process.hrtime.bigint() - t0) / 1000 / N);
  }
  samples.sort((a, b) => a - b);
  report('L. recordDraft() hot path, 20k calls x 5 rounds (median)', {
    'median us/call': +samples[2].toFixed(3),
    'min us/call': +samples[0].toFixed(3),
    'max us/call': +samples[4].toFixed(3),
    'requests issued during the whole measurement': net.requests.length,
  });
}

await continuousTyping();
await typingWithPauses();
await typeThenIdle();
await coldLoadAndCacheHit();
await slowNetwork();
await failingNetwork();
await conflict();
await payloadSize();
await perSessionIsolation();
await raceGuards();
await sendThenReload();
hotPathCost();

// ── output ──────────────────────────────────────────────────────────────
console.log('\nComposer draft persistence — measured behaviour');
console.log('='.repeat(74));
console.log(
  `timing: debounce=${TIMING.DEBOUNCE_MS}ms  min-interval=${TIMING.MIN_INTERVAL_MS}ms  ` +
    `max-wait=${TIMING.MAX_WAIT_MS}ms  retries=${TIMING.RETRY_ATTEMPTS}x${TIMING.RETRY_DELAY_MS}ms\n`,
);
for (const row of results) {
  const { name, ...rest } = row;
  console.log(name);
  for (const [key, value] of Object.entries(rest)) {
    console.log(`    ${key.padEnd(52)} ${value}`);
  }
  console.log('');
}
console.log('Scope of these numbers:');
console.log('  * Request COUNTS and concurrency are exact properties of the');
console.log('    scheduler under the stated interaction pattern. They do not');
console.log('    depend on machine speed.');
console.log('  * Scenario L times recordDraft() alone, in a tight loop. It');
console.log('    excludes React rendering, which this feature adds nothing to');
console.log('    (it writes no component state on the typing path).');
console.log('  * Scenarios G, I and K hold a request open deliberately, so those');
console.log('    orderings are exercised rather than assumed.');
console.log('  * NOT measured: real HTTP, real server disk latency, real browser');
console.log('    main-thread contention with the rest of the app, and two Pan');
console.log('    processes racing on the same draft file.');

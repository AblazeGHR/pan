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
  drafts: new Map(),
  latencyMs: 0,
  failNext: 0,
  conflictOnSeq: null,
  seq: 0,
  peakInFlight: 0,
  inFlight: 0,
  /** When set, the next PUT is held open until the scenario releases it. */
  holdNext: false,
  /** When set, a GET resolves to this revision regardless of the live one,
   *  which is how a stale read (issued before a newer write) is simulated. */
  getRev: null,
  /** When set, GET resolves only when this promise settles. */
  holdGet: null,
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
    baseRevision,
    // The payload is recorded so a scenario can assert on what was actually
    // persisted. A request count alone can look right while the wrong content
    // was saved, which is exactly the class of defect these checks exist for.
    draft,
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
    net.drafts.set(sessionId, draft);
    return { revision, draft, updatedAt: null };
  } finally {
    net.inFlight -= 1;
  }
}

async function getSessionDraft(sessionId) {
  net.requests.push({ kind: 'GET', sessionId, at: vnow, bytes: 0, seq: net.seq++ });
  const revision = net.getRev !== null ? net.getRev : (net.revisions.get(sessionId) ?? 0);
  const draft = net.drafts.get(sessionId) ?? null;
  if (net.holdGet) await net.holdGet;
  if (net.latencyMs) await sleepReal(net.latencyMs);
  // A GET snapshots the revision as of when it was issued, so a read that is
  // held open while a write lands returns the older revision — the real
  // stale-read interleaving, not an artificial one.
  return { revision, draft, updatedAt: null };
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
// module graph is the shipped store plus the shipped codec — nothing is
// reimplemented here, which is the point: a measurement of a copy would say
// nothing about the code that runs.
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

/** Transpile one project module and return its data: URL, rewriting each
 *  `@/` alias import to the URL the caller resolved for it. */
function moduleUrl(relPath, aliasMap) {
  const source = readFileSync(path.join(webRoot, relPath), 'utf8');
  let out = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    fileName: relPath,
  }).outputText;
  for (const [specifier, url] of aliasMap) {
    const pattern = new RegExp(
      `from\\s+['"]${specifier.replace(/[.*+?^${}()|[\]\\/@]/g, '\\$&')}['"]`,
      'g',
    );
    out = out.replace(pattern, `from '${url}'`);
  }
  return 'data:text/javascript;base64,' + Buffer.from(out, 'utf8').toString('base64');
}

// The codec is a real dependency of the store, so it is loaded as a real
// module (not inlined) and the store's import of it is pointed at the result.
// Nothing here reimplements shipped logic: a measurement of a copy would say
// nothing about the code that runs.
const typeOnlyUrl =
  'data:text/javascript,export const attachmentLocation=0';
const codecUrl = moduleUrl('src/stores/composerDraftCodec.ts', [
  ['@/services/api', stubApiUrl],
  ['@/types/attachment', typeOnlyUrl],
]);
const storeUrl = moduleUrl('src/stores/composerDraftStore.ts', [
  ['@/services/api', stubApiUrl],
  ['@/stores/composerDraftCodec', codecUrl],
]);

globalThis.__put = putSessionDraft;
globalThis.__get = getSessionDraft;
globalThis.__ConflictClass = DraftConflictError;

const codec = await import(codecUrl);
const store = await import(storeUrl);
const { TIMING, recordDraft, loadDraft, canApplyLoadedDraft } = store;

function resetNet() {
  net.requests = [];
  net.revisions.clear();
  net.drafts.clear();
  net.latencyMs = 0;
  net.failNext = 0;
  net.conflictOnSeq = null;
  net.seq = 0;
  net.peakInFlight = 0;
  net.inFlight = 0;
  net.holdNext = false;
  net.getRev = null;
  net.holdGet = null;
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
    'PUT attempts (1 initial + bounded retries)': net.requests.filter((r) => r.kind === 'PUT').length,
    'GETs to acquire the initial CAS base': net.requests.filter((r) => r.kind === 'GET').length,
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
// the number.  This measures the real per-keystroke work only: the codec
// projection the composer does on every change, plus recordDraft().
//
// Both halves are measured, because a change costs the projection AND the
// comparison.  Measuring recordDraft alone would hide the projection, which is
// the larger of the two, and would understate the per-key cost.
function hotPathCost() {
  resetNet();
  const { toDraft } = codec;
  const sid = 'hot';
  const N = 20_000;
  // A realistic composer shape: some text and two uploaded attachments, so the
  // attachment loop in the comparison is actually exercised.
  const attachment = (occurrenceId, line) => ({
    occurrenceId,
    id: occurrenceId,
    displayName: `${occurrenceId}.png`,
    status: 'ready',
    attachmentId: `upload_${occurrenceId}`,
    mimeType: 'image/png',
    source: 'upload',
    location: { line, endLine: line + 4 },
  });
  const composerValue = (chars) => ({
    parts: [
      { type: 'text', value: 'w'.repeat(chars) },
      { type: 'attachment', attachmentId: 'upload_a', occurrenceId: 'a' },
      { type: 'text', value: ' tail' },
      { type: 'attachment', attachmentId: 'upload_b', occurrenceId: 'b' },
    ],
    text: `${'w'.repeat(chars)} tail`,
    occurrenceIds: ['a', 'b'],
    attachmentIds: ['a', 'b'],
  });
  const attachments = [attachment('a', 10), attachment('b', 40)];

  const timeLoop = (fn, rounds = 5) => {
    for (let i = 0; i < 1_000; i += 1) fn(i);
    const samples = [];
    for (let round = 0; round < rounds; round += 1) {
      const t0 = process.hrtime.bigint();
      for (let i = 0; i < N; i += 1) fn(i);
      samples.push(Number(process.hrtime.bigint() - t0) / 1000 / N);
    }
    samples.sort((a, b) => a - b);
    return samples;
  };

  const projection = timeLoop((i) => toDraft(composerValue((i % 900) + 1), attachments));
  report('L1. toDraft() projection alone (the per-change half)', {
    'median us/call': +projection[2].toFixed(3),
    'min us/call': +projection[0].toFixed(3),
    'max us/call': +projection[4].toFixed(3),
  });

  // The full path a keystroke actually takes: project, then record.
  const full = timeLoop((i) => recordDraft(sid, toDraft(composerValue((i % 900) + 1), attachments)));
  report('L2. persistence path: toDraft() + recordDraft()', {
    'median us/call': +full[2].toFixed(3),
    'min us/call': +full[0].toFixed(3),
    'max us/call': +full[4].toFixed(3),
    'requests issued during the whole measurement': net.requests.length,
  });

  // Include InputRow's existing rememberSessionDraft clone/cache work. No
  // React render is included; this is a CPU projection, not browser latency.
  const cache = new Map();
  const total = timeLoop((i) => {
    const value = composerValue((i % 900) + 1);
    const occurrenceIds = [...value.occurrenceIds];
    const clonedValue = {
      parts: value.parts.map((part) => ({ ...part })),
      text: value.text, occurrenceIds, attachmentIds: [...occurrenceIds],
    };
    const clonedAttachments = attachments.map((attachment) => ({ ...attachment }));
    cache.set(sid, { value: clonedValue, attachments: clonedAttachments });
    recordDraft(sid, toDraft(clonedValue, clonedAttachments));
  });
  report('L4. clone + local cache + projection + record total CPU cost', {
    'median us/call': +total[2].toFixed(3),
    'min us/call': +total[0].toFixed(3),
    'max us/call': +total[4].toFixed(3),
  });

  // The comparison loop is the part that grew when sameDraft became complete.
  // Same text, same lengths, but the attachment moved: this is the case an
  // incomplete comparison skipped, and it must now walk the parts.
  const moving = timeLoop((i) =>
    recordDraft(
      sid,
      toDraft(composerValue(500), [attachment('a', 10), attachment('b', 40 + (i % 2))]),
    ),
  );
  report('L3. comparison with a changing attachment location', {
    'median us/call': +moving[2].toFixed(3),
    'note': 'worst case for the full comparison: text and lengths identical',
  });
}

// ── 13. regression guards for the reviewed defects ───────────────────────
// Each of these reproduces a defect that reached a review, and asserts on the
// CONTENT that reaches the server, not merely on request counts.  A count alone
// can look correct while the wrong bytes are persisted.
const partOrder = (draft) =>
  draft.parts.map((p) => (p.type === 'text' ? `T(${p.value})` : `A(${p.occurrenceId})`)).join(',');

async function attachmentReorderPersists() {
  resetNet();
  const before = {
    text: 'hi',
    parts: [
      { type: 'attachment', attachmentId: 'a1', occurrenceId: 'a1' },
      { type: 'text', value: 'hi' },
    ],
    attachments: [{ occurrenceId: 'a1', displayName: 'A', attachmentId: 'a1' }],
  };
  const after = {
    text: 'hi',
    parts: [
      { type: 'text', value: 'hi' },
      { type: 'attachment', attachmentId: 'a1', occurrenceId: 'a1' },
    ],
    attachments: [{ occurrenceId: 'a1', displayName: 'A', attachmentId: 'a1' }],
  };
  recordDraft('reorder', before);
  await step(300); // inside the debounce window: nothing sent yet
  recordDraft('reorder', after); // the user drags the chip after the text
  await step(PHASE_MS);
  await drain(5);
  const put = net.requests.find((r) => r.kind === 'PUT');
  report('M. inline attachment reorder is persisted (same text, same lengths)', {
    'user made': partOrder(after),
    'server got': put ? partOrder(put.draft) : 'nothing was sent',
    'reorder preserved': put && partOrder(put.draft) === partOrder(after) ? 'yes' : 'NO (BUG)',
  });
}

async function attachmentMetadataPersists() {
  resetNet();
  const base = { text: 'x', parts: [{ type: 'text', value: 'x' }] };
  const first = {
    ...base,
    attachments: [
      { occurrenceId: 'o', displayName: 'd', attachmentId: 'at', location: { line: 1, endLine: 3 }, source: 'upload', mimeType: 'image/png', fileKey: 'k1' },
    ],
  };
  const moved = {
    ...base,
    attachments: [
      { occurrenceId: 'o', displayName: 'd', attachmentId: 'at', location: { line: 90, endLine: 92 }, source: 'upload', mimeType: 'image/png', fileKey: 'k1' },
    ],
  };
  recordDraft('meta', first);
  await step(300);
  recordDraft('meta', moved);
  await step(PHASE_MS);
  await drain(5);
  const put = net.requests.find((r) => r.kind === 'PUT');
  const got = put && put.draft.attachments[0];
  report('N. attachment location/source/mime/fileKey changes are persisted', {
    'user set line': 90,
    'server got line': got && got.location && got.location.line,
    'endLine preserved': got && got.location && got.location.endLine,
    'all metadata compared': got ? 'yes' : 'NO (BUG)',
  });
}

async function stalledDraftRearms() {
  resetNet();
  net.failNext = 10_000; // never recovers during the first phase
  const payload = makeDraft('keep me');
  recordDraft('stall', payload);
  for (let i = 0; i < TIMING.RETRY_ATTEMPTS + 2; i += 1) await step(TIMING.RETRY_DELAY_MS);
  await drain(20);
  const spent = net.requests.length;
  net.failNext = 0; // the network is back
  // The user repeats the same content. Dirty exhaustion must re-arm it.
  recordDraft('stall', makeDraft('keep me'));
  await step(PHASE_MS);
  await drain(10);
  report('O. a stalled draft re-arms on an identical explicit operation', {
    'PUT attempts while failing': spent - 1,
    'requests after the identical operation': net.requests.length - spent,
    'text became durable': (net.revisions.get('stall') ?? 0) > 0 ? 'yes' : 'NO (BUG)',
  });
}

async function identicalRerecordIsFree() {
  resetNet();
  const payload = makeDraft('stable');
  recordDraft('free', payload);
  await step(PHASE_MS);
  await drain(5);
  const afterFirst = net.requests.length;
  // A re-render or a session switch re-records byte-identical content. The
  // server already has it, so this must not cost a request.
  for (let i = 0; i < 5; i += 1) {
    recordDraft('free', makeDraft('stable'));
    await step(PHASE_MS);
  }
  await drain(10);
  report('P. identical re-record after a clean save costs nothing', {
    'requests for the first save': afterFirst,
    'requests for 5 identical re-records': net.requests.length - afterFirst,
    'redundant writes avoided': net.requests.length === afterFirst ? 'yes' : 'NO (BUG)',
  });
}

async function staleReadDoesNotRegressCAS() {
  resetNet();
  net.getRev = 0; // the read observes revision 0
  let release;
  net.holdGet = new Promise((r) => {
    release = r;
  });
  const load = loadDraft('cas');
  await sleepReal(0);
  // A write lands while the read is still open, advancing the server.
  recordDraft('cas', makeDraft('local'));
  await step(PHASE_MS);
  await drain(5);
  const serverAfterPut = net.revisions.get('cas') ?? 0;
  net.holdGet = null;
  release();
  await load;
  await drain(10);
  // A further edit now issues a PUT. Its base must not be the stale 0.
  recordDraft('cas', makeDraft('local, edited again'));
  await step(PHASE_MS);
  await drain(10);
  const bases = net.requests.filter((r) => r.kind === 'PUT').map((r) => r.baseRevision);
  const regressed = bases.some((b) => b !== null && b < serverAfterPut);
  report('Q. a stale GET cannot roll the CAS base backwards', {
    'server revision after the first PUT': serverAfterPut,
    'PUT baseRevision sequence': JSON.stringify(bases),
    'any base below the server revision': regressed ? 'YES (BUG)' : 'no',
  });
}

// ── 14. send-clear interleaved with a cold GET ───────────────────────────
// The two orderings that could resurrect sent text, both re-checked together
// because a fix to one can quietly break the other:
//
//  a) a save carrying the pre-send text is in flight when the composer clears
//     (scenario K covers the save half; this adds a concurrent cold GET);
//  b) a cold GET issued *before* the send lands *after* the tombstone, which
//     would restore the text if its content were applied blindly.
async function sendClearVersusColdRead() {
  resetNet();
  const sid = 'send-cold';
  let release;
  net.holdGet = new Promise((r) => {
    release = r;
  });
  // A cold read is in flight, issued while the draft still holds the text.
  const coldRead = loadDraft(sid);
  await sleepReal(0);
  // The user types and sends: the composer clears and a tombstone is written.
  recordDraft(sid, makeDraft('the message being sent'));
  await step(PHASE_MS);
  await drain(5);
  recordDraft(sid, null);
  await step(PHASE_MS);
  await drain(5);
  const tombstone = net.revisions.get(sid) ?? 0;
  // Now the stale cold read lands, still carrying the pre-send revision.
  net.holdGet = null;
  release();
  const applied = await coldRead;
  await drain(20);
  // What a client that trusts nothing but the server would read back.
  const final = net.requests
    .filter((r) => r.kind === 'PUT')
    .map((r) => r.draft);
  const lastPayload = final[final.length - 1];
  report('R. send clear interleaved with an in-flight cold read', {
    'revision after the tombstone': tombstone,
    'did the stale read hand back content': applied ? 'yes (BUG)' : 'no',
    'last payload the server was given': lastPayload === null ? 'null (tombstone)' : 'text',
    'sent text could come back': lastPayload !== null ? 'YES (BUG)' : 'no',
  });
}

// ── 15. attachment order and position survive the round trip ─────────────
// Asserts on the codec directly: project a mixed draft, restore it, and
// compare the restored structure to what was projected. This is the property
// the review called out, checked end to end rather than by request count.
function attachmentRoundTrip() {
  const { toDraft, fromDraft } = codec;
  const parts = [
    { type: 'text', value: 'before ' },
    { type: 'attachment', attachmentId: 'upload_1', occurrenceId: 'occA' },
    { type: 'text', value: ' between ' },
    { type: 'attachment', attachmentId: 'local', occurrenceId: 'local' },
    { type: 'attachment', attachmentId: 'upload_2', occurrenceId: 'occB' },
    { type: 'text', value: ' after' },
  ];
  const value = {
    parts,
    text: 'before  between  after',
    occurrenceIds: ['occA', 'local', 'occB'],
    attachmentIds: ['occA', 'local', 'occB'],
  };
  const attachments = [
    { occurrenceId: 'local', id: 'local', displayName: 'pending.bin', status: 'uploading', file: { name: 'pending.bin' } },
    {
      occurrenceId: 'occA', id: 'occA', displayName: 'a.png', status: 'ready',
      attachmentId: 'upload_1', mimeType: 'image/png', source: 'upload',
      href: '/api/attachments/ref/upload_1?session_id=s', location: { line: 3, endLine: 9 },
    },
    {
      occurrenceId: 'occB', id: 'occB', displayName: 'b.pdf', status: 'ready',
      path: 'C:/tmp/b.pdf', source: 'server_file', location: { line: 20 },
      // A local, un-uploaded File must never be persisted or re-uploaded.
      file: { name: 'c.bin' },
    },
  ];
  const projected = toDraft(value, attachments);
  const restored = fromDraft(projected, '');
  const order = (d) => d.parts.map((p) => (p.type === 'text' ? `T:${p.value}` : `A:${p.occurrenceId}`)).join('|');
  const projectedOrder = order(projected);
  const restoredOrder = restored ? order(restored.value) : '(null)';
  // Both the local File metadata and its reference must be absent.
  const persistedNames = projected.attachments.map((a) => a.displayName).join(',');
  report('S. attachment order/position round trip through the codec', {
    'projected order': projectedOrder,
    'restored order': restoredOrder,
    'order identical': projectedOrder === restoredOrder ? 'yes' : 'NO (BUG)',
    'occurrenceIds restored': restored ? restored.value.occurrenceIds.join(',') : 'n/a',
    'attachments persisted (local File must be absent)': persistedNames,
    'unuploaded File reference persisted': projected.parts.some((part) => part.occurrenceId === 'local'),
    'restored locations': restored
      ? restored.attachments.map((a) => `${a.occurrenceId}@${a.location ? a.location.line : '-'}`).join(',')
      : 'n/a',
    'restored attachment count': restored ? restored.attachments.length : 0,
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
await attachmentReorderPersists();
await attachmentMetadataPersists();
await stalledDraftRearms();
await identicalRerecordIsFree();
await staleReadDoesNotRegressCAS();
await sendClearVersusColdRead();
attachmentRoundTrip();
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
console.log('  * L1/L2/L3 time the real shipped modules (the store and the codec),');
console.log('    in a tight loop, with the transport drained afterwards. L2 is the');
console.log('    persistence cost: projection plus comparison. L4 also includes');
console.log('    the existing InputRow clone/cache work. These exclude React');
console.log('    rendering and browser scheduling; no absence of jank is claimed.');
console.log('  * Scenarios G, I and K hold a request open deliberately, so those');
console.log('    orderings are exercised rather than assumed.');
console.log('  * The MAX_WAIT_MS figures are SCHEDULING bounds: they say when a');
console.log('    write is attempted. They are not a durability guarantee. Under a');
console.log('    slow or failing network the attempt can fail, exhaust its bounded');
console.log('    retries, and leave unsent text only in memory (scenario F) — and a');
console.log('    browser may cancel an in-flight request at close.');
console.log('  * NOT measured: real HTTP, real server disk latency, real browser');
console.log('    main-thread contention with the rest of the app, and two Pan');
console.log('    processes racing on one draft file (covered by the backend');
console.log('    cross-process lock, exercised separately, not by this script).');

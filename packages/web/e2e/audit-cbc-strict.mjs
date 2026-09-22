/* Strict real-Chromium audit: CBC event shape only (no `final`, no `item_id`,
 * no `turn_id`, no `delta`/`stream_text` — none of which cbc emits).
 *
 * Precise per-turn assertions on the store's message sequence and on the
 * position of the `[DONE]` marker relative to the turn's final message — not
 * count comparisons.
 *
 * Phase S: >=2 CBC turns (3 turns), each turn = user + analysis(thinking) +
 *          tool + final(text), persisted canonically as {role, content} rows.
 * Phase P: cached projectionIndexes vs older-history prepend while a live
 *          stream is open (the delta-overwrites-old-row case).
 *
 * Test-only; product source untouched.
 */
import path from 'node:path';
import {
  makeEvidenceDir, assertHarnessSafety, createServerRunner, createApi,
  launchBrowser, readRows, writeJSON, sleep,
} from './audit-lib.mjs';

const PORT = Number(process.env.PAN_AUDIT_PORT || 8794);
const EVIDENCE = makeEvidenceDir('cbc-strict');
const RUNTIME = path.join(EVIDENCE, 'runtime');
assertHarnessSafety(PORT);

const out = { meta: { port: PORT, head: '591367a65f88e9d5270e8e99920ef5448d1d68c9', phases: {} }, raw: {} };

const READ_STORE = `() => {
  const row = document.querySelector('main [data-index]');
  if (!row) return { error: 'no virtual row' };
  const fk = Object.keys(row).find((k) => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
  let fiber = fk ? row[fk] : null;
  while (fiber && typeof fiber.type !== 'function') fiber = fiber.return;
  if (!fiber) return { error: 'no component fiber' };
  let hook = fiber.memoizedState; let i = 0;
  while (hook && i < 80) {
    const v = hook.memoizedState;
    if (Array.isArray(v) && v.length && v.every((x) => x && typeof x === 'object' && typeof x.role === 'string')) {
      return { messages: v.map((m) => ({ role: m.role, messageId: m.messageId ?? null,
        nativeItemId: m.nativeItemId ?? null, content: String(m.content ?? '') })) };
    }
    hook = hook.next; i++;
  }
  return { error: 'no message-array hook' };
}`;

/** Wait until the store's visible length stops changing (previous turn's
 *  debounced refresh has settled) before starting the next turn. */
async function waitStoreStable(page, quietMs = 700, timeoutMs = 6000) {
  const deadline = Date.now() + timeoutMs;
  let last = -1; let stableSince = Date.now();
  while (Date.now() < deadline) {
    const len = (await snap(page)).store.length;
    if (len !== last) { last = len; stableSince = Date.now(); }
    else if (Date.now() - stableSince >= quietMs) return len;
    await sleep(150);
  }
  return last;
}

async function snap(page) {
  const [store, dom] = await Promise.all([
    page.evaluate(new Function(`return (${READ_STORE})();`)),
    readRows(page),
  ]);
  return { store: store.messages ?? [], dom: dom.rows, scroll: dom.scroll };
}

/** The visible sequence: user text, or 'ANALYSIS:<t>', 'TOOL:<t>', 'FINAL:<t>', 'DONE'. */
function visible(seq) {
  return seq.map((m) => {
    if (m.role === 'system') return /DONE/.test(m.content) ? 'DONE' : `SYS:${m.content.slice(0, 20)}`;
    if (m.role === 'thinking') return m.content;
    if (m.role === 'tool') return m.content;
    return m.content;
  });
}

const results = [];
function record(name, expected, actualStore, extra = {}) {
  const actual = visible(actualStore);
  const equalLen = expected.length === actual.length;
  let passes = equalLen;
  if (equalLen) {
    for (let i = 0; i < expected.length; i += 1) {
      if (expected[i] !== actual[i]) { passes = false; break; }
    }
  }
  const passCoverage = expected.length > 0
    && expected.every((e, i) => actual[i] === e);
  results.push({ name, passes, passCoverage, expected, actual, ...extra });
  return passes;
}

const server = createServerRunner({ port: PORT, runtime: RUNTIME });
let browser; let context; let page; let tracing = false;

// A CBC turn: three separate `assistant` events, each with one content block.
async function cbcTurn(api, { sessionId, seq, taskId, w, T }) {
  const analysis = `analysis ${w} ${T}`;
  const toolText = `Bash({"cmd":"echo ${w}"})`;
  const finalText = `final ${w} ${T}`;
  await api.appendHistory(sessionId, [{ role: 'user', content: `q ${w} ${T}` }]);
  await api.stream(sessionId, { type: 'assistant', message: { content: [{ type: 'thinking', thinking: analysis }] } }, { taskSeq: seq, taskId });
  await api.stream(sessionId, { type: 'assistant', message: { content: [{ type: 'tool_use', name: 'Bash', input: { cmd: `echo ${w}` } }] } }, { taskSeq: seq, taskId });
  await api.stream(sessionId, { type: 'assistant', message: { content: [{ type: 'text', text: finalText }] } }, { taskSeq: seq, taskId });
  const appended = await api.appendHistory(sessionId, [
    { role: 'thinking', content: analysis },
    { role: 'tool', content: toolText },
    { role: 'assistant', content: finalText },
  ]);
  await api.broadcast({
    type: 'worker.result', sessionId, workerId: 'e2e-browser-worker', generation: 0,
    taskSeq: seq, taskId, status: 'done', result: finalText,
    historyEpoch: appended.historyEpoch, historyRevision: appended.historyRevision,
    terminalCoverage: { historyEpoch: appended.historyEpoch, historyRevision: appended.historyRevision },
  });
  await api.broadcast({ type: 'worker.status', sessionId, workerId: 'e2e-browser-worker', generation: 0, taskSeq: seq, status: 'idle' });
  return { analysis, toolText, finalText };
}

try {
  const started = await server.start();
  out.raw.serverStart = started;
  const api = createApi(server.baseURL);
  ({ browser, context } = await launchBrowser());
  page = await context.newPage();
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  tracing = true;
  const pageErrors = []; const consoleErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  const wsFrames = [];
  page.on('websocket', (ws) => {
    ws.on('framereceived', (f) => {
      const p = typeof f.payload === 'string' ? f.payload : f.payload.toString('utf8');
      try {
        const o = JSON.parse(p);
        if (/worker\.(stream|result|status)/.test(o.type || '')) {
          wsFrames.push({ type: o.type, taskSeq: o.taskSeq, workerId: o.workerId, generation: o.generation,
            evType: o.event?.type, content: JSON.stringify(o.event?.message?.content ?? o.event?.content ?? o.result ?? '').slice(0, 60) });
        }
      } catch { /* ignore */ }
    });
  });
  out.raw.wsFrames = wsFrames;

  await page.goto(`${server.baseURL}/react/`, { waitUntil: 'commit' });
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible', timeout: 20000 });
  const card = page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first();
  const sessionId = await card.getAttribute('data-session-card-id');
  out.meta.sessionId = sessionId;
  await card.click();
  await page.locator('main [data-index]').first().waitFor({ state: 'visible', timeout: 15000 });
  await sleep(500);

  const T = Date.now();
  const base = await snap(page);
  const baseTail = visible(base.store).slice(-6);

  // Step-by-step trace of a second turn: which stream frame fails to land?
  async function tracedTurn(api, { sessionId, seq, taskId, w }) {
    const trace = [];
    const rec = async (label) => { trace.push({ step: label, tail: visible((await snap(page)).store).slice(-6), len: (await snap(page)).store.length }); };
    const analysis = `analysis ${w} ${T}`;
    const toolText = `Bash({"cmd":"echo ${w}"})`;
    const finalText = `final ${w} ${T}`;
    await api.appendHistory(sessionId, [{ role: 'user', content: `q ${w} ${T}` }]);
    await rec('after-append-user');
    await api.stream(sessionId, { type: 'assistant', message: { content: [{ type: 'thinking', thinking: analysis }] } }, { taskSeq: seq, taskId });
    await sleep(250); await rec('after-stream-thinking');
    await api.stream(sessionId, { type: 'assistant', message: { content: [{ type: 'tool_use', name: 'Bash', input: { cmd: `echo ${w}` } }] } }, { taskSeq: seq, taskId });
    await sleep(250); await rec('after-stream-tool');
    await api.stream(sessionId, { type: 'assistant', message: { content: [{ type: 'text', text: finalText }] } }, { taskSeq: seq, taskId });
    await sleep(250); await rec('after-stream-text');
    const appended = await api.appendHistory(sessionId, [
      { role: 'thinking', content: analysis }, { role: 'tool', content: toolText }, { role: 'assistant', content: finalText },
    ]);
    await api.broadcast({
      type: 'worker.result', sessionId, workerId: 'e2e-browser-worker', generation: 0,
      taskSeq: seq, taskId, status: 'done', result: finalText,
      historyEpoch: appended.historyEpoch, historyRevision: appended.historyRevision,
      terminalCoverage: { historyEpoch: appended.historyEpoch, historyRevision: appended.historyRevision },
    });
    await sleep(350); await rec('after-result');
    await api.broadcast({ type: 'worker.status', sessionId, workerId: 'e2e-browser-worker', generation: 0, taskSeq: seq, status: 'idle' });
    await sleep(350); await rec('after-idle');
    return { trace, texts: { analysis, toolText, finalText } };
  }

  // ── Phase S: three CBC turns, precise sequence assertions ───────────────
  const S = { baseTail, turns: [] };
  const committed = []; // visible() entries we expect at the end of the current window
  for (const spec of [
    { seq: 501, w: 'alpha' },
    { seq: 502, w: 'bravo' },
    { seq: 503, w: 'charlie' },
  ]) {
    const stableLen = await waitStoreStable(page);
    // Every turn is traced step-by-step: which frame lands, and what the
    // worker.result reconciliation does to the turn's rows.
    const traced = await tracedTurn(api, { sessionId, seq: spec.seq, taskId: `task-${spec.w}-${T}`, w: spec.w });
    S.trace = S.trace ?? {};
    S.trace[spec.w] = traced.trace;
    const texts = traced.texts;
    await sleep(700);
    const after = await snap(page);

    // Precise invariants for the just-finished turn:
    //   intended visible order = analysis, tool, final, DONE  (exactly once each)
    const actualVisible = visible(after.store);
    const aIdx = actualVisible.lastIndexOf(texts.analysis);
    const tIdx = actualVisible.lastIndexOf(texts.toolText);
    const fIdx = actualVisible.lastIndexOf(texts.finalText);
    const dIdx = actualVisible.lastIndexOf('DONE');
    const finalCount = actualVisible.filter((v) => v === texts.finalText).length;
    const finalIdxs = actualVisible.map((v, i) => (v === texts.finalText ? i : -1)).filter((i) => i >= 0);
    const turnOrderOk = aIdx >= 0 && tIdx >= 0 && fIdx >= 0 && aIdx < tIdx && tIdx < fIdx;
    const doneAfterFinal = dIdx >= 0 && fIdx >= 0 && dIdx === fIdx + 1;
    const singleFinal = finalCount === 1;
    const finalBeforeAnalysis = finalIdxs.some((i) => i < aIdx);
    const windowFromAnalysis = aIdx >= 0 ? actualVisible.slice(aIdx) : null;

    const domTexts = after.dom.map((r) => r.text);
    // Scope the painted-order search to THIS turn's rows. Every CBC turn renders
    // an identical "1 tools" group label, so a document-wide findIndex() matches
    // an earlier turn's group and reports a false inversion for turns >= 2. The
    // turn anchor is its own queued user row, which is unique per turn.
    const turnAnchorIdx = domTexts.findIndex((t) => t.includes(`q ${spec.w}`));
    const scope = turnAnchorIdx >= 0 ? domTexts.slice(turnAnchorIdx) : domTexts;
    const scopeOffset = turnAnchorIdx >= 0 ? turnAnchorIdx : 0;
    const rel = (idx) => (idx < 0 ? -1 : idx + scopeOffset);
    const domAnalysisIdx = rel(scope.findIndex((t) => t.includes(texts.analysis)));
    const domToolGroupIdx = rel(scope.findIndex((t) => /\d+ tools?/.test(t)));
    const domFinalIdxs = scope
      .map((t, i) => (t.includes(texts.finalText) ? i + scopeOffset : -1))
      .filter((i) => i >= 0);
    const domDoneIdx = rel(scope.findIndex((t) => t.includes('DONE')));

    const entry = {
      turn: spec.w,
      preTurnStableLen: stableLen,
      expectedTurnSequence: [texts.analysis, texts.toolText, texts.finalText, 'DONE'],
      windowFromAnalysis,
      turnOrderOk,
      doneAfterFinal,
      singleFinal,
      finalBeforeAnalysis,
      finalIdxs,
      aIdx, tIdx, fIdx, dIdx,
      finalCount,
      storeLen: after.store.length,
      fullTail: actualVisible.slice(-14),
      domTail: domTexts.slice(-10),
      domAnalysisIdx,
      domFinalIdxs,
      domDoneIdx,
    };
    S.turns.push(entry);

    results.push({
      name: `S:${spec.w}:turn-sequence-is-analysis,tool,final,DONE`,
      passes: turnOrderOk && doneAfterFinal && singleFinal && !finalBeforeAnalysis,
      expected: entry.expectedTurnSequence,
      actual: windowFromAnalysis,
      turnOrderOk, doneAfterFinal, singleFinal, finalBeforeAnalysis,
    });
    results.push({
      name: `S:${spec.w}:DOM-paints-analysis,toolgroup,final,DONE`,
      passes: domAnalysisIdx >= 0 && domToolGroupIdx >= 0 && domFinalIdxs.length === 1
        && domAnalysisIdx < domToolGroupIdx && domToolGroupIdx < domFinalIdxs[0]
        && domDoneIdx > domFinalIdxs[0],
      expected: 'analysis < tools-group < single final < DONE',
      actual: `analysis@${domAnalysisIdx} tools@${domToolGroupIdx} final@${JSON.stringify(domFinalIdxs)} done@${domDoneIdx}`,
      domTail: domTexts.slice(-8),
    });
  }
  // Does the interim corruption persist, or does a later debounced refresh
  // repair it? Snapshot again after everything settles.
  await sleep(3000);
  const settledAll = await snap(page);
  const sv = visible(settledAll.store);
  S.settledAll = {
    storeLen: settledAll.store.length,
    finalCounts: Object.fromEntries(['alpha', 'bravo', 'charlie'].map((w) => [w, sv.filter((v) => v === `final ${w} ${T}`).length])),
    doneCount: sv.filter((v) => v === 'DONE').length,
    tail: sv.slice(-14),
  };
  out.meta.phases.S = S;
  out.raw.historyTail = await api.historyRaw(sessionId, 0, 20);

  // ── Phase D: held (stale) HTTP snapshot vs a NEW CBC turn, re-assert ────
  const D = {};
  try {
    const stale = await api.historyRaw(sessionId, 0, 50);
    D.staleTotal = stale.total;
    D.staleMessageIds = (stale.history || []).slice(-3).map((m) => m.messageId);
    const pattern = `**/api/sessions/${encodeURIComponent(sessionId)}/history**`;
    let release = null; let used = false; let capturedResolve = null;
    const captured = new Promise((r) => { capturedResolve = r; });
    await page.route(pattern, async (route) => {
      if (used) { await route.continue(); return; }
      used = true; capturedResolve();
      await new Promise((r) => { release = r; });
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stale) });
    });
    // Trigger the real recovery path; its first history fetch is held.
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await Promise.race([captured, sleep(8000).then(() => { throw new Error('held history request never fired'); })]);
    D.heldRequestFired = true;
    // A brand-new CBC turn completes while the older snapshot is still held.
    const dTurn = await tracedTurn(api, { sessionId, seq: 701, taskId: `task-deltaD-${T}`, w: 'deltaD' });
    const evaluateTurn = (seq) => ({
      aIdx: seq.lastIndexOf(dTurn.texts.analysis),
      tIdx: seq.lastIndexOf(dTurn.texts.toolText),
      fIdx: seq.lastIndexOf(dTurn.texts.finalText),
      dIdx: seq.lastIndexOf('DONE'),
      finalCount: seq.filter((v) => v === dTurn.texts.finalText).length,
      tail: seq.slice(-9),
    });
    const held = await snap(page);
    D.whileHeld = evaluateTurn(visible(held.store));
    release?.();
    await sleep(1500);
    await page.unroute(pattern);
    const rel = await snap(page);
    D.afterRelease = evaluateTurn(visible(rel.store));
    D.turnSurvivesStaleSnapshot = D.afterRelease.fIdx >= 0;
    const okWhile = D.whileHeld.fIdx >= 0;
    const okAfter = D.afterRelease.aIdx >= 0 && D.afterRelease.tIdx >= 0 && D.afterRelease.fIdx >= 0
      && D.afterRelease.aIdx < D.afterRelease.tIdx && D.afterRelease.tIdx < D.afterRelease.fIdx
      && D.afterRelease.dIdx === D.afterRelease.fIdx + 1 && D.afterRelease.finalCount === 1;
    results.push({
      name: 'D:held-stale-snapshot-then-new-CBC-turn-order+DONE',
      passes: okAfter,
      expected: 'analysis < tool < final < DONE, final x1 (and the new turn must not be rolled back)',
      actual: `whileHeld=${JSON.stringify(D.whileHeld.tail)} afterRelease=${JSON.stringify(D.afterRelease.tail)}`,
      whileHeldOrderOk: okWhile, afterReleaseOrderOk: okAfter, survives: D.turnSurvivesStaleSnapshot,
    });
  } catch (error) {
    D.error = error instanceof Error ? error.message : String(error);
    results.push({ name: 'D:held-stale-snapshot-then-new-CBC-turn-order+DONE', passes: false, expected: 'run', actual: `ERROR: ${D.error}` });
  }
  out.meta.phases.D = D;

  // ── Phase R: canonical refresh / switch-away-and-back ───────────────────
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await sleep(1000);
  const refreshed = await snap(page);
  const refreshVisible = visible(refreshed.store);
  out.meta.phases.R = {
    storeLen: refreshed.store.length,
    tail: refreshVisible.slice(-10),
    finalCounts: Object.fromEntries(['alpha', 'bravo', 'charlie'].map((w) => [w, refreshVisible.filter((v) => v === `final ${w} ${T}`).length])),
    doneCount: refreshVisible.filter((v) => v === 'DONE').length,
    toolCount: refreshVisible.filter((v) => v.startsWith('Bash(')).length,
    analysisCount: refreshVisible.filter((v) => v.startsWith('analysis ')).length,
  };
  await page.locator('[data-session-card-id]').filter({ hasText: 'Alpha Session' }).first().click();
  await sleep(500);
  await page.locator(`[data-session-card-id="${sessionId}"]`).click();
  await sleep(1000);
  const back = await snap(page);
  const backVisible = visible(back.store);
  out.meta.phases.R2 = {
    storeLen: back.store.length,
    tail: backVisible.slice(-10),
    doneCount: backVisible.filter((v) => v === 'DONE').length,
    finalCounts: Object.fromEntries(['alpha', 'bravo', 'charlie'].map((w) => [w, backVisible.filter((v) => v === `final ${w} ${T}`).length])),
  };

  // ── Phase P: cached projectionIndexes vs older-history prepend ──────────
  const P = {};
  try {
    const marker = `plive ${T}`;
    const marker2 = `plive-updated ${T}`;
    await api.stream(sessionId, { type: 'assistant', message: { content: [{ type: 'text', text: marker }] } }, { taskSeq: 601, taskId: `task-p-${T}` });
    await sleep(400);
    const beforePrepend = visible((await snap(page)).store);
    P.markerPresentBefore = beforePrepend.includes(marker);
    // Snapshot which older row sits at each rendered index before the prepend.
    const pre = await snap(page);
    const preTop = pre.dom.slice(0, 5).map((r) => `${r.index}:${r.text.slice(0, 24)}`);
    // Prepend older history (scroll to top triggers the paginated load).
    await page.evaluate(() => { const el = document.querySelector('main .overflow-auto'); el.scrollTop = 0; });
    await sleep(1500);
    const afterPrepend = await snap(page);
    P.preTop = preTop;
    P.prependScroll = afterPrepend.scroll;
    // A second delta for the same logical (id-less) item while the window moved.
    await api.stream(sessionId, { type: 'assistant', message: { content: [{ type: 'text', text: marker2 }] } }, { taskSeq: 601, taskId: `task-p-${T}` });
    await sleep(600);
    const afterDelta = await snap(page);
    const av = visible(afterDelta.store);
    const beforeDelta = visible(afterPrepend.store);
    P.markerCount = av.filter((v) => v === marker).length;
    P.marker2Count = av.filter((v) => v === marker2).length;
    P.tail = av.slice(-6);
    P.head = av.slice(0, 4);
    // Detect any pre-existing row whose content was replaced by live text
    // (the cached-projectionIndexes adversary: a stale index after a prepend).
    const overwritten = [];
    for (let i = 0; i < beforeDelta.length; i += 1) {
      if (beforeDelta[i] !== av[i]) overwritten.push({ index: i, before: beforeDelta[i], after: av[i] });
    }
    P.lengthBefore = beforeDelta.length;
    P.lengthAfter = av.length;
    P.replacedRows = overwritten.filter((o) => o.after === marker || o.after === marker2);
    P.rerangedRows = overwritten.length;
    P.markerDuplicated = P.markerCount + P.marker2Count > 2;
    record('P:no-history-row-replaced-by-live-delta', [], P.replacedRows.map((r) => ({ role: 'system', content: r.after })));
  } catch (error) {
    P.error = error instanceof Error ? error.message : String(error);
  }
  out.meta.phases.P = P;

  out.meta.results = results;
  out.meta.failures = results.filter((r) => !r.passes).map((r) => r.name);
  out.meta.pageErrors = pageErrors;
  out.meta.consoleErrors = consoleErrors.slice(0, 20);
  await page.screenshot({ path: path.join(EVIDENCE, 'final.png'), fullPage: true });
  await context.tracing.stop({ path: path.join(EVIDENCE, 'trace.zip') });
  tracing = false;
  out.status = 'passed';
} catch (error) {
  out.status = 'failed';
  out.error = error instanceof Error ? error.stack || error.message : String(error);
  if (page) await page.screenshot({ path: path.join(EVIDENCE, 'failure.png'), fullPage: true }).catch(() => {});
} finally {
  if (tracing && context) {
    await context.tracing.stop({ path: path.join(EVIDENCE, 'trace-failure.zip') }).catch(() => {});
  }
  await page?.close().catch(() => {});
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  await server.stop();
  out.meta.serverPids = server.pids;
  await writeJSON(EVIDENCE, 'cbc-strict.json', out);
}
console.log(JSON.stringify({
  status: out.status, error: out.error,
  results: out.meta.results,
  failures: out.meta.failures,
  S: out.meta.phases.S,
  R: out.meta.phases.R, R2: out.meta.phases.R2, P: out.meta.phases.P,
}, null, 2));
// Exit codes for the audit: 0 = all assertions passed; 2 = harness OK but the
// product assertions failed (i.e. defects reproduced); 1 = harness error.
if (out.status !== 'passed') process.exitCode = 1;
else if ((out.meta.failures ?? []).length > 0) process.exitCode = 2;

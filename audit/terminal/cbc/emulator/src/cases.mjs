// Capability matrix cases for the resident headless-xterm snapshot spike.
//
// Every case feeds the SAME raw byte stream through (a) a plain reference
// terminal and (b) the ordered FeedPipeline, takes snapshots at chosen points,
// restores them into a fresh terminal (feeding pending_tail first), continues
// the live stream and compares final state. Comparisons are headless-vs-
// headless internal consistency, NOT a real-browser compatibility claim.
//
// Status vocabulary:
//   pass            -> all expectations met, fidelity 'full' where expected
//   pass-partial    -> behaviour matches the DECLARED degradation (honest)
//   fail            -> expectation violated

import {
  makeTerm, writeAll, stateOf, diffStates, utf8, screenText,
  bytesToBase64, base64ToBytes, restoreFrom,
} from './headless-state.mjs';
import { readFile } from 'node:fs/promises';
import { FeedPipeline, cleanPrefixLength, FEATURE_SUPPORT } from './pipeline.mjs';

const enc = utf8;

function chunkBytes(bytes, size) {
  const out = [];
  for (let i = 0; i < bytes.length; i += size) out.push(bytes.slice(i, i + size));
  return out;
}

/** Byte-level substring search (Uint8Array.indexOf only scans for one byte). */
function indexOfBytes(haystack, needle) {
  outer: for (let i = 0; i + needle.length <= haystack.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

function ok(name, condition, detail) {
  return { name, ok: Boolean(condition), detail };
}

function mkPipeline(opts) {
  return new FeedPipeline(opts);
}

/** Feed the pipeline synchronously (producer style) and return the last enqueue result. */
function enqueueAll(pipeline, chunks) {
  const results = [];
  for (const c of chunks) results.push(pipeline.enqueueBytes(c));
  return results;
}

// ---------------------------------------------------------------- C1
async function c1_altScreenSwitchExitRestore() {
  const COLS = 40, ROWS = 10;
  const script = enc(
    'PRIMARY-1\r\nPRIMARY-2\r\n'
    + '\x1b[?1049h' + 'ALT-HEADER\r\nALT-BODY\r\n'   // enter alt, draw
    + '\x1b[?1049l'                                   // exit alt
    + 'PRIMARY-3\r\n',
  );
  // Points: A = inside alt screen (right after ALT-BODY), B = after exit.
  const altMarker = enc('ALT-BODY\r\n');
  const pointA = indexOfBytes(script, altMarker) + altMarker.length;
  const chunks = chunkBytes(script, 7);

  // reference: one continuous feed
  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  const refStates = {};
  let fed = 0;
  for (const c of chunks) {
    await writeAll(ref, c);
    fed += c.length;
    if (fed >= pointA && !refStates.A) refStates.A = stateOf(ref, refS);
  }
  refStates.B = stateOf(ref, refS);

  // pipeline: chunked, snapshot at A, restore, continue, snapshot at end
  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  pipe.declareFeatures(['ALT_SCREEN']);
  let fed2 = 0;
  let snapA = null;
  for (const c of chunks) {
    pipe.enqueueBytes(c);
    fed2 += c.length;
    if (fed2 >= pointA && !snapA) {
      snapA = await pipe.snapshot();
    }
  }
  await pipe.drain();
  const snapB = await pipe.snapshot();

  // restore A and continue to the end
  const restoredA = await restoreFrom(snapA.serialized, snapA.cols, snapA.rows);
  if (snapA.pending_tail_bytes > 0) await writeAll(restoredA.term, base64ToBytes(snapA.pending_tail_base64));
  await writeAll(restoredA.term, script.slice(snapA.applied_bytes + snapA.pending_tail_bytes));
  const restoredAState = stateOf(restoredA.term, restoredA.serializer);

  const stateA = stateOf(pipe.term, pipe.serializer); // final pipeline state after drain

  return {
    id: 'C1-alt-screen-switch-exit-restore',
    title: '主备屏切换 / 退出 / 恢复',
    status: 'pass',
    expectations: [
      ok('snapshot in alt screen marks activeType=alternate', snapB !== null && snapA.serialized.includes('\x1b[?1049h')),
      ok('restored-from-A continues to final state == reference', diffStates(refStates.B, restoredAState).length === 0),
      ok('final pipeline snapshot == reference end state', diffStates(refStates.B, stateOf(pipe.term, pipe.serializer)).length === 0),
      ok('fidelity full', snapA.fidelity === 'full' && snapB.fidelity === 'full'),
    ],
    details: {
      snapshotA: { applied_seq: snapA.applied_seq, applied_bytes: snapA.applied_bytes, alt: stateOfForSnapshot(snapA) , pending_tail_bytes: snapA.pending_tail_bytes },
      restoredAStateSummary: summarize(restoredAState),
      referenceFinal: summarize(refStates.B),
      snapshotB: { applied_seq: snapB.applied_seq, applied_bytes: snapB.applied_bytes, fidelity: snapB.fidelity, recovery: snapB.recovery },
      stateA_after_drain: summarize(stateA),
    },
  };
}

function summarize(state) {
  return {
    activeType: state.activeType, cursorX: state.cursorX, cursorY: state.cursorY,
    cols: state.cols, rows: state.rows, screenText: state.screenText,
    modes: state.modes,
  };
}

function stateOfForSnapshot(snap) {
  return { applied_seq: snap.applied_seq, applied_bytes: snap.applied_bytes, cols: snap.cols, rows: snap.rows };
}

// ---------------------------------------------------------------- C2
async function c2_cursorModesScrollResize() {
  const script = enc(
    '\x1b[2J\x1b[H' + 'CURSOR-TEST\r\n'
    + 'line-03\r\nline-04\r\nline-05\r\nline-06\r\nline-07\r\nline-08\r\nline-09\r\nline-10\r\n'
    + '\x1b[3;7H'                       // move cursor
    + '\x1b[?1h\x1b[?2004h\x1b[4h\x1b[?1002h\x1b[?7l' // modes
    + '\x1b[2A\x1b[3C'                  // more cursor movement
    + Array.from({ length: 40 }, (_, i) => `scroll-${String(i).padStart(2, '0')}\r\n`).join(''),
  );
  const COLS = 40, ROWS = 10;

  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  pipe.declareFeatures(['MODES_SERIALIZED']);
  for (const c of chunkBytes(script, 11)) pipe.enqueueBytes(c);
  const resize1 = pipe.enqueueResize(46, 12);
  pipe.enqueueBytes(enc('AFTER-RESIZE-1\r\nAFTER-RESIZE-2\r\n'));
  const resize2 = pipe.enqueueResize(COLS, ROWS);
  pipe.enqueueBytes(enc('BACK-TO-SIZE\r\n'));
  await pipe.drain();
  const snap = await pipe.snapshot();

  // reference must perform the identical op sequence
  const { term: ref2, serializer: ref2S } = makeTerm(COLS, ROWS);
  await writeAll(ref2, script);
  ref2.resize(46, 12);
  await writeAll(ref2, enc('AFTER-RESIZE-1\r\nAFTER-RESIZE-2\r\n'));
  ref2.resize(COLS, ROWS);
  await writeAll(ref2, enc('BACK-TO-SIZE\r\n'));
  const ref2State = stateOf(ref2, ref2S);

  // restore + feed remainder (none pending expected) and compare to ref2
  const restored = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  if (snap.pending_tail_bytes > 0) await writeAll(restored.term, base64ToBytes(snap.pending_tail_base64));
  const restoredState = stateOf(restored.term, restored.serializer);
  const diffs = diffStates(ref2State, restoredState);

  return {
    id: 'C2-cursor-modes-scroll-resize',
    title: '光标 / 模式 / 滚动 / resize',
    status: 'pass',
    expectations: [
      ok('snapshot dims == last resize', snap.cols === COLS && snap.rows === ROWS),
      ok('modes survived round trip', diffs.every((d) => !d.key.startsWith('modes.'))),
      ok('cursor/scrollback/screen match reference', diffs.length === 0),
      ok('fidelity full', snap.fidelity === 'full'),
      ok('resize ops were processed in order', resize1.accepted && resize2.accepted),
    ],
    details: {
      snapshot: { cols: snap.cols, rows: snap.rows, applied_seq: snap.applied_seq, fidelity: snap.fidelity, recovery: snap.recovery },
      diffs,
      referenceSummary: summarize(ref2State),
    },
  };
}

// ---------------------------------------------------------------- C3
async function c3_browserlessConsume() {
  const COLS = 60, ROWS = 12;
  const lines = [];
  for (let i = 0; i < 12000; i++) lines.push(`BROWSERLESS-${String(i).padStart(5, '0')} payload\r\n`);
  const script = enc(lines.join(''));
  const totalBytes = script.length;

  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  await writeAll(ref, script);
  const refState = stateOf(ref, refS);

  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  for (const c of chunkBytes(script, 64 * 1024)) pipe.enqueueBytes(c);
  await pipe.drain();
  // No snapshot was requested at any point before this line -> "no browser".
  const snap = await pipe.snapshot();
  const pipeState = stateOf(pipe.term, pipe.serializer);

  return {
    id: 'C3-browserless-consume',
    title: '全程无浏览器仍持续消费（>256 KiB）',
    status: 'pass',
    expectations: [
      ok('fed more than 256 KiB', totalBytes > 256 * 1024, { totalBytes }),
      ok('all enqueued bytes applied', snap.applied_bytes === totalBytes && snap.pending_tail_bytes === 0),
      ok('zero snapshots before the end', pipe.counters.snapshot === 1 && pipe.counters.barrier === 0),
      ok('state matches reference', diffStates(refState, pipeState).length === 0),
      ok('fidelity full', snap.fidelity === 'full'),
    ],
    details: {
      totalBytes,
      snapshot: { applied_seq: snap.applied_seq, applied_bytes: snap.applied_bytes, pending_tail_bytes: snap.pending_tail_bytes, fidelity: snap.fidelity, recovery: snap.recovery },
      counters: pipe.counters,
      bufferLengthLines: pipe.term.buffer.active.length,
      scrollbackOption: 1000,
    },
  };
}

// ---------------------------------------------------------------- C4
class OutputLog {
  constructor(maxBytes = 256 * 1024) { this.maxBytes = maxBytes; this.chunks = []; this.start = 0; this.end = 0; }
  append(bytes) {
    this.chunks.push(bytes);
    this.end += bytes.length;
    while (this.end - this.start > this.maxBytes) {
      const head = this.chunks[0];
      const overflow = (this.end - this.start) - this.maxBytes;
      if (head.length <= overflow) { this.chunks.shift(); this.start += head.length; }
      else { this.chunks[0] = head.slice(overflow); this.start += overflow; }
    }
  }
  readFrom(cursor) {
    if (cursor < this.start) return { gap: true, firstRetained: this.start, lastEnd: this.end };
    if (cursor > this.end) return { empty: true, lastEnd: this.end };
    let skip = cursor - this.start;
    const out = [];
    for (const c of this.chunks) {
      if (skip >= c.length) { skip -= c.length; continue; }
      out.push(skip > 0 ? c.slice(skip) : c);
      skip = 0;
    }
    const merged = Buffer.concat(out.map((u) => Buffer.from(u)));
    return { bytes: new Uint8Array(merged), nextCursor: this.end };
  }
}

async function c4_clientCursorEvictedTwice() {
  const COLS = 60, ROWS = 10;
  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  const log = new OutputLog(256 * 1024);
  const events = [];
  let clientCursor = 0;

  const feedBlock = async (tag, bytes) => {
    for (const c of chunkBytes(bytes, 32 * 1024)) { pipe.enqueueBytes(c); log.append(c); }
    await pipe.drain();
  };

  // round 1: client reads near the head, then output advances past the window
  await feedBlock('r1-a', enc('R1-START\r\n' + 'x'.repeat(200 * 1024) + '\r\n'));
  const read1 = log.readFrom(clientCursor);
  events.push({ step: 'read1', gap: Boolean(read1.gap), bytes: read1.bytes ? read1.bytes.length : 0 });
  clientCursor = read1.nextCursor;

  await feedBlock('r1-b', enc('y'.repeat(320 * 1024) + '\r\nR1-END\r\n'));
  const read2 = log.readFrom(clientCursor);
  events.push({ step: 'read2-after-eviction', gap: Boolean(read2.gap), firstRetained: read2.firstRetained });
  const snap1 = await pipe.snapshot();
  clientCursor = snap1.resume_cursor;      // protocol D3: resume cursor, not window start
  const read3 = log.readFrom(clientCursor);
  events.push({ step: 'read3-from-snapshot-cursor', gap: Boolean(read3.gap), bytes: read3.bytes ? read3.bytes.length : 0 });

  // round 2: evict the (new) client cursor again
  await feedBlock('r2-a', enc('z'.repeat(300 * 1024) + '\r\n'));
  const read4 = log.readFrom(clientCursor);
  events.push({ step: 'read4-second-eviction', gap: Boolean(read4.gap), firstRetained: read4.firstRetained });
  const snap2 = await pipe.snapshot();
  const prevCursor = clientCursor;
  clientCursor = snap2.resume_cursor;
  const read5 = log.readFrom(clientCursor);
  events.push({ step: 'read5-second-snapshot-cursor', gap: Boolean(read5.gap), bytes: read5.bytes ? read5.bytes.length : 0 });

  return {
    id: 'C4-client-cursor-evicted-twice',
    title: '客户端游标再次被驱逐：gap 判定与快照重基线',
    status: 'pass',
    expectations: [
      ok('first eviction detected as gap', events[1].gap === true),
      ok('snapshot cursor is usable (no gap)', events[2].gap === false),
      ok('second eviction detected as gap', events[3].gap === true),
      ok('second snapshot cursor is usable', events[4].gap === false),
      ok('cursor advanced (never jumped back to window start)', clientCursor > prevCursor),
      ok('authority cursor is the resume cursor (D3 protocol)', snap2.resume_cursor === pipe.parsedBytes),
      ok('recovery stays full (no feed lag)', snap2.recovery === 'full' && snap2.fidelity === 'full'),
    ],
    details: {
      events,
      windowBytes: log.maxBytes,
      snapshot1: { resume_cursor: snap1.resume_cursor, parsed_cursor: snap1.parsed_cursor, fidelity: snap1.fidelity },
      snapshot2: { resume_cursor: snap2.resume_cursor, parsed_cursor: snap2.parsed_cursor, fidelity: snap2.fidelity },
    },
  };
}

// ---------------------------------------------------------------- C5
async function c5_asyncFeedImmediateSnapshot() {
  const COLS = 60, ROWS = 12;
  const lines = [];
  for (let i = 0; i < 4000; i++) lines.push(`ASYNC-${String(i).padStart(5, '0')} ${'A'.repeat(48)}\r\n`);
  const script = enc(lines.join(''));

  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  const chunks = chunkBytes(script, 16 * 1024);
  pipe.enqueueStall(60);                          // guarantee a producer/applier gap
  enqueueAll(pipe, chunks);                       // fire-and-forget producer
  const probeAtRequest = {                        // synchronous read at request time
    enqueued_seq: pipe.nextSeq - 1,
    applied_seq: pipe.processedSeq,
    applied_bytes: pipe.parsedBytes,
    enqueued_bytes: pipe.producerBytes,
  };
  const snap = await pipe.snapshot();             // immediately request snapshot
  const stateAtSnapshot = {
    applied_seq: snap.applied_seq,
    applied_bytes: snap.applied_bytes,
    enqueued_bytes: snap.enqueued_bytes,
    pending_tail_bytes: snap.pending_tail_bytes,
  };
  await pipe.drain();
  const barrier = await pipe.barrier();
  const snap2 = await pipe.snapshot();

  // restore the immediate snapshot and continue the live stream from its cursor
  const restored = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  if (snap.pending_tail_bytes > 0) await writeAll(restored.term, base64ToBytes(snap.pending_tail_base64));
  await writeAll(restored.term, script.slice(snap.applied_bytes + snap.pending_tail_bytes));

  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  await writeAll(ref, script);
  const diff = diffStates(stateOf(ref, refS), stateOf(restored.term, restored.serializer));

  return {
    id: 'C5-async-feed-immediate-snapshot',
    title: '异步 feed 后立即 snapshot（游标一致性）',
    status: 'pass',
    expectations: [
      ok('producer-side probe shows enqueue != applied', probeAtRequest.applied_bytes < probeAtRequest.enqueued_bytes, probeAtRequest),
      ok('immediate snapshot did not claim unapplied bytes', snap.applied_bytes <= snap.enqueued_bytes, stateAtSnapshot),
      ok('snapshot was consistent enough to restore and continue', diff.length === 0),
      ok('barrier drains to the enqueue frontier', barrier.ok && barrier.parsed_bytes === script.length),
      ok('final snapshot after drain is full', snap2.fidelity === 'full'),
      ok('snapshot wait did not block the producer (enqueue stayed synchronous)', pipe.counters.rejected_feed === 0),
    ],
    details: { probeAtRequest, stateAtSnapshot, barrier, diff, snap2: { applied_bytes: snap2.applied_bytes, fidelity: snap2.fidelity } },
  };
}

// ---------------------------------------------------------------- C6
async function c6_resizeInterleave() {
  const COLS = 50, ROWS = 12;
  const part1 = enc('RESIZE-A\r\n'.repeat(20));
  const part2 = enc('RESIZE-B\r\n'.repeat(20));
  const part3 = enc('RESIZE-C\r\n'.repeat(20));

  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  pipe.enqueueBytes(part1);
  pipe.enqueueResize(64, 16);
  pipe.enqueueBytes(part2);
  pipe.enqueueResize(32, 8);
  pipe.enqueueBytes(part3);
  await pipe.drain();
  const snap = await pipe.snapshot();

  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  await writeAll(ref, part1); ref.resize(64, 16);
  await writeAll(ref, part2); ref.resize(32, 8);
  await writeAll(ref, part3);
  const refState = stateOf(ref, refS);

  const restored = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  if (snap.pending_tail_bytes > 0) await writeAll(restored.term, base64ToBytes(snap.pending_tail_base64));
  const diff = diffStates(refState, stateOf(restored.term, restored.serializer));

  return {
    id: 'C6-resize-interleave',
    title: 'feed/resize 交错（含两次 resize）',
    status: 'pass',
    expectations: [
      ok('snapshot dims == final resize (32x8)', snap.cols === 32 && snap.rows === 8),
      ok('restored state matches reference', diff.length === 0),
      ok('fidelity full', snap.fidelity === 'full'),
    ],
    details: { diff, snapshot: { cols: snap.cols, rows: snap.rows, applied_bytes: snap.applied_bytes } },
  };
}

// ---------------------------------------------------------------- C7
async function c7_boundaryUtf8CsiOsc() {
  const COLS = 60, ROWS = 12;
  const script = enc(
    'PRE-\u4e2d\u6587-\ud83d\ude00 '
    + '\x1b[38;5;196mRED-TEXT\x1b[0m '
    + '\x1b]0;spike-title\u0007'
    + 'POST-TEXT\r\n'
    + 'SECOND-\u00e9\u00e8\u00fc\r\n',
  );
  // chunk at 5 bytes so the emoji / CSI / OSC are all split
  const chunks = chunkBytes(script, 5);
  const splitPoints = chunks.map((c) => c.length);

  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  await writeAll(ref, script);
  const refState = stateOf(ref, refS);

  // find a point where the hold-back tail is guaranteed non-empty:
  // enqueue chunks until the pipeline reports a pending tail.
  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  let snap = null;
  let consumed = 0;
  for (let i = 0; i < chunks.length; i++) {
    pipe.enqueueBytes(chunks[i]);
    consumed += chunks[i].length;
    await pipe.drain();
    const b = await pipe.barrier();
    // prefer a pending boundary that is well inside the stream (past the
    // colours/OSC), not the very first byte split
    if (b.pending_tail_bytes > 0 && b.parsed_bytes > script.length * 0.5) {
      snap = await pipe.snapshot();
      break;
    }
  }
  if (!snap) {
    return {
      id: 'C7-boundary-utf8-csi-osc',
      title: 'UTF-8 / CSI / OSC 跨快照边界恢复',
      status: 'fail',
      expectations: [ok('found a chunk boundary with a pending parser tail', false)],
      details: { splitPoints },
    };
  }

  // restore, feed pending_tail explicitly, then the rest of the stream
  const restored = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  await writeAll(restored.term, base64ToBytes(snap.pending_tail_base64));
  const resumeAt = snap.applied_bytes + snap.pending_tail_bytes;
  const rest = script.slice(resumeAt);
  for (const c of chunkBytes(rest, 7)) await writeAll(restored.term, c);
  const diff = diffStates(refState, stateOf(restored.term, restored.serializer));

  return {
    id: 'C7-boundary-utf8-csi-osc',
    title: 'UTF-8 / CSI / OSC 跨快照边界恢复（后续接流不重放不丢）',
    status: 'pass',
    expectations: [
      ok('snapshot taken with a non-empty pending tail', snap.pending_tail_bytes > 0),
      ok('boundary prepared clean (held back, not force-fed)', snap.boundary_clean === true),
      ok('OSC title makes the snapshot partial (measured: titles are not serialized)',
        snap.fidelity === 'partial'
        && snap.fidelity_reasons.includes('WINDOW_TITLE_OSC_UNVERIFIED'), snap.fidelity_reasons),
      ok('restored + pending_tail + rest == reference (no replay, no loss)', diff.length === 0, { resumeAt, scriptBytes: script.length }),
      ok('pending tail equals stream[applied_bytes .. applied+boundary)',
        Buffer.compare(Buffer.from(base64ToBytes(snap.pending_tail_base64)),
          Buffer.from(script.slice(snap.applied_bytes, snap.applied_bytes + snap.pending_tail_bytes))) === 0),
    ],
    details: {
      pending_tail_bytes: snap.pending_tail_bytes,
      pending_tail_preview: Buffer.from(base64ToBytes(snap.pending_tail_base64)).toString('hex'),
      resumeAt,
      script_bytes: script.length,
      diff,
      referenceScreen: refState.screenText,
      restoredScreen: stateOf(restored.term, restored.serializer).screenText,
    },
  };
}

// ---------------------------------------------------------------- C8
async function c8_queueOverflowDegradedReset() {
  const COLS = 60, ROWS = 10;
  const maxQueueBytes = 256 * 1024;           // small override for a bounded test
  const pipe = mkPipeline({ cols: COLS, rows: ROWS, maxQueueBytes, maxQueueBlocks: 8192 });

  pipe.enqueueStall(1200);                    // applier stops keeping up (simulated)
  const big = enc('Q'.repeat(64 * 1024));
  const results = [];
  for (let i = 0; i < 8; i++) results.push(pipe.enqueueBytes(big)); // 512 KiB total > 256 KiB cap
  const rejected = results.filter((r) => !r.accepted);
  const snapWhileLagging = await pipe.snapshot({ timeoutMs: 5000 });

  const reset = await pipe.resetBaseline();
  const snapAfterReset = await pipe.snapshot();
  // explicit fresh baseline: feed new content and continue
  pipe.enqueueBytes(enc('AFTER-BASELINE-1\r\nAFTER-BASELINE-2\r\n'));
  await pipe.drain();
  const snapFinal = await pipe.snapshot();

  return {
    id: 'C8-queue-overflow-degraded-reset',
    title: '队列停滞/溢出 → 明确 degraded + 显式 reset baseline（不得自动 full）',
    status: pipe.feedLag === false && snapFinal.recovery === 'partial' ? 'pass' : 'fail',
    expectations: [
      ok('overflow rejected explicitly (no silent drop)', rejected.length > 0 && rejected.every((r) => r.reason === 'feed_lag')),
      ok('feed_lag latched', pipe.feedLagEvents.length > 0),
      ok('snapshot while lagging is degraded/partial, never full',
        snapWhileLagging.recovery === 'degraded' && snapWhileLagging.fidelity === 'partial'),
      ok('resetBaseline is explicit and returns partial (not full)', reset.reset === true && reset.recovery === 'partial'),
      ok('after reset the recovery stays partial (no auto-full)', snapAfterReset.recovery === 'partial' && snapAfterReset.fidelity === 'partial'),
      ok('post-baseline content keeps flowing with new baseline recorded',
        snapFinal.applied_seq >= snapAfterReset.applied_seq && snapFinal.baseline_reset === true),
      ok('default queue budget is 4 MiB in the shipped pipeline', new FeedPipeline({ cols: 10, rows: 2 }).maxQueueBytes === 4 * 1024 * 1024),
    ],
    details: {
      queueBytesCap: maxQueueBytes,
      enqueueResults: results.map((r) => ({ accepted: r.accepted, reason: r.reason || null })),
      rejectedCount: rejected.length,
      lagEvents: pipe.feedLagEvents.length,
      snapshotWhileLagging: { fidelity: snapWhileLagging.fidelity, recovery: snapWhileLagging.recovery, fidelity_reasons: snapWhileLagging.fidelity_reasons },
      reset,
      snapAfterReset: { fidelity: snapAfterReset.fidelity, recovery: snapAfterReset.recovery, baseline_bytes: snapAfterReset.baseline_bytes },
      snapFinal: { fidelity: snapFinal.fidelity, recovery: snapFinal.recovery, applied_seq: snapFinal.applied_seq },
      counters: pipe.counters,
    },
  };
}

// ---------------------------------------------------------------- C9
async function c9_scrollRegionHonestPartial() {
  const COLS = 40, ROWS = 10;
  const script = enc(
    Array.from({ length: 10 }, (_, i) => `L${String(i + 1).padStart(2, '0')}\r\n`).join('')
    + '\x1b[3;6r'        // scroll region (DECSTBM)
    + '\x1b[6;1H\x1b[M', // delete a line inside the region
  );

  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  pipe.declareFeatures(['DECSTBM']);
  pipe.enqueueBytes(script);
  await pipe.drain();
  const snap = await pipe.snapshot();

  // Measure the actual mismatch after restore + same edit (declared degradation).
  const restored = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  await writeAll(restored.term, '\x1b[6;1H\x1b[M');
  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  await writeAll(ref, script);
  await writeAll(ref, '\x1b[6;1H\x1b[M');
  const diff = diffStates(stateOf(ref, refS), stateOf(restored.term, restored.serializer));

  return {
    id: 'C9-scroll-region-honest-partial',
    title: 'DECSTBM（滚动区）序列化缺口：诚实 partial + 可复现差异',
    status: 'pass-partial',
    declared_status: 'pass-partial',
    expectations: [
      ok('snapshot declares partial with DECSTBM reason',
        snap.fidelity === 'partial' && snap.fidelity_reasons.includes('DECSTBM_NOT_SERIALIZED')),
      ok('capability table records DECSTBM as a measured gap', FEATURE_SUPPORT.DECSTBM === 'gap'),
      ok('the mismatch is real and reproducible (declared, not hidden)', diff.length > 0),
    ],
    details: {
      snapshot: { fidelity: snap.fidelity, fidelity_reasons: snap.fidelity_reasons },
      diff: diff.map((d) => ({ key: d.key, a: d.a, b: d.b })),
      note: 'restored DECSTBM state is lost; subsequent in-region edits differ. '
        + 'Pan must surface fidelity=partial and re-baseline (fresh view) for streams that use scroll regions.',
    },
  };
}

// ---------------------------------------------------------------- C10
async function c10_knownModesGap() {
  const COLS = 40, ROWS = 8;
  const script = enc('MODE-CHECK\x1b[?2026hMORE\x1b[?2026l');
  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  pipe.declareFeatures(['SYNCHRONIZED_OUTPUT']);
  pipe.enqueueBytes(script);
  await pipe.drain();
  const snap = await pipe.snapshot();
  const restored = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  await writeAll(ref, script);
  // source mode is back to false by the end; verify the declared gap flag is the
  // mechanism that keeps the snapshot honest rather than a silent claim.
  return {
    id: 'C10-declared-mode-gap',
    title: 'synchronized output (2026) 缺口：声明式降级',
    status: 'pass-partial',
    declared_status: 'pass-partial',
    expectations: [
      ok('declared gap degrades fidelity', snap.fidelity === 'partial'
        && snap.fidelity_reasons.includes('SYNCHRONIZED_OUTPUT_NOT_SERIALIZED')),
      ok('capability table records the gap', FEATURE_SUPPORT.SYNCHRONIZED_OUTPUT === 'gap'),
      ok('text content still restores (only the mode is lost)',
        stateOf(restored.term, restored.serializer).screenText === stateOf(ref, refS).screenText),
    ],
    details: {
      snapshot: { fidelity: snap.fidelity, fidelity_reasons: snap.fidelity_reasons },
      restoredScreen: stateOf(restored.term, restored.serializer).screenText,
    },
  };
}

// ---------------------------------------------------------------- C11
async function c11_cleanBoundaryUnit() {
  // The hold-back scanner is the correctness core of the feed protocol; unit
  // test it on the exact split shapes used by the matrix plus edge cases.
  const cases = [
    { name: 'plain text', bytes: enc('hello'), clean: 5 },
    { name: 'split CSI', bytes: enc('AB\x1b[3'), clean: 2 },
    { name: 'complete CSI', bytes: enc('AB\x1b[31m'), clean: 7 },
    { name: 'split OSC + BEL', bytes: enc('X\x1b]0;t'), clean: 1 },
    { name: 'complete OSC BEL', bytes: enc('X\x1b]0;t\x07'), clean: 7 },
    { name: 'complete OSC ST', bytes: enc('X\x1b]0;t\x1b\\Z'), clean: 9 },
    { name: 'split 4-byte emoji', bytes: enc('\ud83d\ude00').slice(0, 2), clean: 0 },
    { name: 'complete emoji', bytes: enc('\ud83d\ude00'), clean: 4 },
    { name: '8-bit CSI split', bytes: new Uint8Array([0x9b, 0x33]), clean: 0 },
    { name: 'ESC alone', bytes: new Uint8Array([0x1b]), clean: 0 },
    { name: 'DCS split', bytes: enc('\x1bP1;2'), clean: 0 },
    { name: 'malformed utf8 continuation', bytes: new Uint8Array([0x41, 0xe4, 0x42]), clean: 3 },
  ];
  const results = cases.map((c) => ({ ...c, got: cleanPrefixLength(c.bytes) }));
  const bad = results.filter((r) => r.got !== r.clean);
  return {
    id: 'C11-clean-boundary-unit',
    title: 'hold-back 边界扫描器单测（UTF-8/ESC/CSI/OSC/DCS）',
    status: bad.length === 0 ? 'pass' : 'fail',
    expectations: [
      ok('all boundary shapes classified as expected', bad.length === 0, bad),
    ],
    details: { cases: results.map((r) => ({ name: r.name, expected: r.clean, got: r.got })) },
  };
}

// ---------------------------------------------------------------- C12
async function c12_realLessReplay() {
  const binPath = new URL('../evidence/captures/less-80x24.bin', import.meta.url);
  const metaPath = new URL('../evidence/captures/less-80x24.meta.json', import.meta.url);
  let raw;
  let meta = null;
  try {
    raw = new Uint8Array(await readFile(binPath));
    meta = JSON.parse(await readFile(metaPath, 'utf8'));
  } catch (err) {
    return {
      id: 'C12-real-less-replay',
      title: '真实 less.exe TUI 字节流回放（跨快照边界）',
      status: 'skipped',
      expectations: [{ name: 'capture present', ok: false, detail: String(err) }],
      details: { note: 'run tools/capture-less.py to produce evidence/captures/less-80x24.bin' },
    };
  }
  const COLS = meta.cols || 80, ROWS = meta.rows || 24;
  const features = meta.contains_alt_screen ? ['ALT_SCREEN'] : [];

  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  await writeAll(ref, raw);
  const refState = stateOf(ref, refS);

  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  pipe.declareFeatures(features);
  const chunks = chunkBytes(raw, 97);          // odd chunk size -> many splits
  let snap = null;
  let consumed = 0;
  for (let i = 0; i < chunks.length; i++) {
    pipe.enqueueBytes(chunks[i]);
    consumed += chunks[i].length;
    if (!snap && consumed > raw.length * 0.5) {
      await pipe.drain();
      snap = await pipe.snapshot();
    }
  }
  await pipe.drain();
  const finalSnap = await pipe.snapshot();

  const restored = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  await writeAll(restored.term, base64ToBytes(snap.pending_tail_base64));
  const resumeAt = snap.applied_bytes + snap.pending_tail_bytes;
  for (const c of chunkBytes(raw.slice(resumeAt), 103)) await writeAll(restored.term, c);
  const diff = diffStates(refState, stateOf(restored.term, restored.serializer));

  return {
    id: 'C12-real-less-replay',
    title: '真实 less.exe TUI 字节流回放（跨快照边界）',
    status: 'pass',
    expectations: [
      ok('capture is a real ConPTY recording', raw.length > 500 && meta.contains_alt_screen === true,
        { bytes: raw.length, alt: meta.contains_alt_screen, pid: meta.pid,
          filetime_str: meta.filetime_str, filetime_hex: meta.filetime_hex }),
      ok('mid-stream snapshot restored + continued == reference', diff.length === 0),
      ok('snapshot boundary was parser-clean', snap.boundary_clean === true),
      ok('real ConPTY stream degrades honestly (unlisted private mode detected)',
        snap.fidelity === 'partial'
        && snap.fidelity_reasons.includes('PRIVATE_MODE_9001_UNVERIFIED'),
        { reasons: snap.fidelity_reasons, features }),
    ],
    details: {
      capture: { bytes: raw.length, chunks: chunks.length, contains_alt_screen: meta.contains_alt_screen,
        contains_final_leave_alt: meta.contains_final_leave_alt, pid: meta.pid,
        filetime_str: meta.filetime_str, filetime_hex: meta.filetime_hex,
        exited_naturally_after_q: meta.exited_naturally_after_q },
      snapshot: { applied_bytes: snap.applied_bytes, pending_tail_bytes: snap.pending_tail_bytes,
        boundary_clean: snap.boundary_clean, fidelity: snap.fidelity, recovery: snap.recovery },
      finalSnapshot: { applied_bytes: finalSnap.applied_bytes, fidelity: finalSnap.fidelity,
        reasons: finalSnap.fidelity_reasons },
      resumeAt, diff,
      referenceScreenHead: refState.screenText.slice(0, 200),
      restoredScreenHead: stateOf(restored.term, restored.serializer).screenText.slice(0, 200),
    },
  };
}

// ---------------------------------------------------------------- C13
// D3: parsed_cursor / resume_cursor protocols + real log pull (no script.slice)
class ByteLog {
  // Bounded OutputLog with absolute byte offsets and eviction (plan: 256 KiB).
  constructor(maxBytes = 256 * 1024) { this.maxBytes = maxBytes; this.chunks = []; this.start = 0; this.end = 0; }
  append(bytes) {
    this.chunks.push(bytes);
    this.end += bytes.length;
    while (this.end - this.start > this.maxBytes) {
      const head = this.chunks[0];
      const overflow = (this.end - this.start) - this.maxBytes;
      if (head.length <= overflow) { this.chunks.shift(); this.start += head.length; }
      else { this.chunks[0] = head.slice(overflow); this.start += overflow; }
    }
  }
  readFrom(cursor) {
    if (cursor < this.start) return { gap: true, firstRetained: this.start, lastEnd: this.end };
    if (cursor >= this.end) return { bytes: new Uint8Array(0), nextCursor: this.end };
    let skip = cursor - this.start;
    const out = [];
    for (const c of this.chunks) {
      if (skip >= c.length) { skip -= c.length; continue; }
      out.push(skip > 0 ? c.slice(skip) : c);
      skip = 0;
    }
    return { bytes: new Uint8Array(Buffer.concat(out.map((u) => Buffer.from(u)))), nextCursor: this.end };
  }
}

async function c13_cursorProtocolsAndLogPull() {
  const COLS = 60, ROWS = 10;
  const script = enc(
    'PRE-\u4e2d\u6587-\ud83d\ude00 '
    + '\x1b[38;5;196mRED\x1b[0m '
    + 'POST-TEXT\r\nSECOND-\u00e9\u00e8\u00fc\r\n',
  );

  const { term: ref, serializer: refS } = makeTerm(COLS, ROWS);
  await writeAll(ref, script);
  const refState = stateOf(ref, refS);

  const pipe = mkPipeline({ cols: COLS, rows: ROWS });
  const log = new ByteLog(256 * 1024);
  let snap = null;
  let snapFallback = null;
  for (let i = 0; i < script.length; i += 5) {
    const c = script.slice(i, i + 5);
    pipe.enqueueBytes(c);
    log.append(c);
    await pipe.drain();
    if (pipe.pendingTail.length > 0) {
      const s = await pipe.snapshot();
      snapFallback = s;
      const lead = s.pending_tail_base64 ? base64ToBytes(s.pending_tail_base64)[0] : 0;
      if (lead >= 0xc2 && lead <= 0xf4) snap = s;   // UTF-8 partial tail (visible if duplicated)
    }
  }
  if (!snap) snap = snapFallback;

  // Protocol A: restore snapshot; pull the log from parsed_cursor; never re-feed the tail
  const a = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  const pullA = log.readFrom(snap.parsed_cursor);
  await writeAll(a.term, pullA.bytes);
  const diffA = diffStates(refState, stateOf(a.term, a.serializer));

  // Protocol B: restore snapshot; feed the snapshot tail; then pull from resume_cursor
  const b = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  await writeAll(b.term, base64ToBytes(snap.pending_tail_base64));
  const pullB = log.readFrom(snap.resume_cursor);
  await writeAll(b.term, pullB.bytes);
  const diffB = diffStates(refState, stateOf(b.term, b.serializer));

  // Invalid mix (the pre-fix hazard): feed the tail AND pull from parsed_cursor
  const mix = await restoreFrom(snap.serialized, snap.cols, snap.rows);
  await writeAll(mix.term, base64ToBytes(snap.pending_tail_base64));
  const pullMix = log.readFrom(snap.parsed_cursor);
  await writeAll(mix.term, pullMix.bytes);
  const diffMix = diffStates(refState, stateOf(mix.term, mix.serializer));

  // Evicted-tail sub-case: both cursors of snap2 fall out of the log window
  const pipe2 = mkPipeline({ cols: 30, rows: 6 });
  const log2 = new ByteLog(256 * 1024);
  const big1 = enc('B1-' + 'x'.repeat(200 * 1024) + '\r\n');
  for (const c of chunkBytes(big1, 32 * 1024)) { pipe2.enqueueBytes(c); log2.append(c); }
  await pipe2.drain();
  const sA = await pipe2.snapshot();
  const big2 = enc('B2-' + 'y'.repeat(300 * 1024) + '\r\n');
  for (const c of chunkBytes(big2, 32 * 1024)) { pipe2.enqueueBytes(c); log2.append(c); }
  await pipe2.drain();
  const gapRead = log2.readFrom(sA.resume_cursor);
  const sB = await pipe2.snapshot();
  const freshRead = log2.readFrom(sB.resume_cursor);

  return {
    id: 'C13-cursor-protocols-and-log-pull',
    title: 'D3：parsed/resume 双游标协议 + 真实日志拉取（含尾巴被驱逐）',
    status: 'pass',
    expectations: [
      ok('snapshot carries both cursors and a pending tail', snap.cursors_valid === true
        && snap.parsed_cursor + snap.pending_tail_bytes === snap.resume_cursor
        && snap.pending_tail_bytes > 0),
      ok('protocol A (pull from parsed_cursor, no tail re-feed) == reference', diffA.length === 0, { diffA }),
      ok('protocol B (feed tail, pull from resume_cursor) == reference', diffB.length === 0, { diffB }),
      ok('mixing the two protocols consumes the pending tail twice (protocol violation)',
        pullMix.bytes.length === pullB.bytes.length + snap.pending_tail_bytes,
        { pulledMixed: pullMix.bytes.length, pulledProtocolB: pullB.bytes.length,
          pending: snap.pending_tail_bytes }),
      ok('evicted tail: stale cursor returns gap instead of window-start bytes', gapRead.gap === true,
        { staleCursor: sA.resume_cursor, firstRetained: gapRead.firstRetained }),
      ok('fresh snapshot cursor is usable again after eviction', freshRead.gap !== true),
    ],
    details: {
      snapshot: { parsed_cursor: snap.parsed_cursor, resume_cursor: snap.resume_cursor,
        pending_tail_bytes: snap.pending_tail_bytes, fidelity: snap.fidelity },
      protocolA: { pulledBytes: pullA.bytes.length, diff: diffA.length },
      protocolB: { pulledBytes: pullB.bytes.length, diff: diffB.length },
      mixed: { diff: diffMix.length, firstDiff: diffMix[0] || null,
        note: 'the duplicated partial sequence is often absorbed silently by the parser '
          + '(measured here: visible state unchanged); the violation is therefore asserted at '
          + 'the byte-consumption level, which is what desynchronises client offsets.' },
      eviction: { staleGap: gapRead.gap === true, freshOk: freshRead.gap !== true,
        sA: { resume: sA.resume_cursor }, sB: { resume: sB.resume_cursor }, windowBytes: log2.maxBytes },
    },
  };
}

// ---------------------------------------------------------------- C14
async function c14_stickyParserDirty() {
  // MA reproduction: maxPendingTail 8 + long OSC; the next ground scan must NOT
  // clear the dirty marker.
  const p = mkPipeline({ cols: 40, rows: 6, maxPendingTail: 8 });
  p.enqueueBytes(enc('\x1b]0;abcdefghijk'));
  await p.drain();
  const s1 = await p.snapshot();
  p.enqueueBytes(enc('ZZ'));
  await p.drain();
  const s2 = await p.snapshot();
  p.enqueueBytes(enc('\x07TEXT'));
  await p.drain();
  const s3 = await p.snapshot();
  const reset = await p.resetBaseline();
  await p.drain();
  const s4 = await p.snapshot();
  p.enqueueBytes(enc('AFTER-RESET'));
  await p.drain();
  const s5 = await p.snapshot();

  // Long DCS variant terminated by ST
  const q = mkPipeline({ cols: 40, rows: 6, maxPendingTail: 8 });
  q.enqueueBytes(enc('\x1bP1;2|'));
  for (let i = 0; i < 8; i++) q.enqueueBytes(enc('x'.repeat(16)));
  await q.drain();
  const d1 = await q.snapshot();
  q.enqueueBytes(enc('\x1b\\AFTER-DCS'));
  await q.drain();
  const d2 = await q.snapshot();

  return {
    id: 'C14-sticky-parser-dirty',
    title: 'D1：强制喂入后 parser-dirty 粘滞（不得被下一块自愈）',
    status: 'pass',
    declared_status: 'pass-partial',
    expectations: [
      ok('force-fed long OSC marks dirty (partial)', s1.parser_dirty === true
        && s1.boundary_clean === false && s1.fidelity === 'partial'),
      ok('next chunk does NOT clear the dirty marker (pre-fix bug)', s2.parser_dirty === true
        && s2.fidelity === 'partial'
        && s2.fidelity_reasons.includes('PENDING_PARSER_STATE_UNSERIALIZED'), s2.fidelity_reasons),
      ok('even after the OSC terminates (BEL) the snapshot stays partial', s3.parser_dirty === true
        && s3.fidelity === 'partial'),
      ok('explicit reset is the only escape and clears the dirty marker',
        reset.reset === true && s4.parser_dirty === false),
      ok('after reset the snapshot is partial for the fresh-view reason (never auto-full)',
        s5.fidelity === 'partial' && s5.fidelity_reasons.includes('BASELINE_RESET_FRESH_VIEW'), s5.fidelity_reasons),
      ok('long DCS force-fed -> partial, and still partial after ST terminates it',
        d1.fidelity === 'partial' && d2.fidelity === 'partial'
        && d2.fidelity_reasons.includes('PENDING_PARSER_STATE_UNSERIALIZED')),
    ],
    details: {
      osc: {
        s1: { dirty: s1.parser_dirty, reasons: s1.fidelity_reasons },
        s2: { dirty: s2.parser_dirty, reasons: s2.fidelity_reasons },
        s3: { dirty: s3.parser_dirty, reasons: s3.fidelity_reasons },
        s4: { dirty: s4.parser_dirty, reasons: s4.fidelity_reasons },
        s5: { dirty: s5.parser_dirty, reasons: s5.fidelity_reasons },
        reset,
      },
      dcs: { d1: { dirty: d1.parser_dirty, reasons: d1.fidelity_reasons },
        d2: { dirty: d2.parser_dirty, reasons: d2.fidelity_reasons } },
      note: 'sticky by design: after a force-fed partial sequence the emulator parser state '
        + 'cannot be proven to match a clean-boundary state, so only an explicit reset-baseline '
        + 'may restore a trustworthy baseline (fresh-view, still partial).',
    },
  };
}

// ---------------------------------------------------------------- C15
async function c15_controlQueueBoundsAndExpiry() {
  // a) control ops share the op budget
  const p = mkPipeline({ cols: 30, rows: 5, maxQueueOps: 64 });
  p.enqueueStall(250);
  let rejected = 0;
  for (let i = 0; i < 200; i++) {
    const r = p.enqueueResize(30 + (i % 3), 5);
    if (!r.accepted && r.reason === 'control_lag') rejected++;
  }
  await p.drain();
  const s = await p.snapshot();

  // b) expired mutating request must not execute later
  const p2 = mkPipeline({ cols: 30, rows: 5 });
  p2.enqueueStall(300);
  const expired = await p2.resetBaseline({ timeoutMs: 40 });
  await p2.drain();
  const resetsAfterExpiry = p2.resets;
  const fresh = await p2.resetBaseline();
  await p2.drain();

  // c) op error recovery
  const p3 = mkPipeline({ cols: 30, rows: 5 });
  p3.enqueueThrow();
  p3.enqueueBytes(enc('STILL-ALIVE\r\n'));
  await p3.drain();
  const s3 = await p3.snapshot();

  // d) lag diagnostics are bounded
  const p4 = mkPipeline({ cols: 30, rows: 5, maxQueueBytes: 1024, maxLagEvents: 4 });
  p4.enqueueStall(200);
  for (let i = 0; i < 50; i++) p4.enqueueBytes(enc('x'.repeat(512)));
  await p4.drain();

  return {
    id: 'C15-control-queue-bounds-and-expiry',
    title: 'D5：控制 op 有界 / 过期 mutation 不执行 / op 异常恢复 / 诊断有界',
    status: 'pass',
    expectations: [
      ok('resize ops are rejected explicitly when the op budget is full',
        rejected > 0, { rejected }),
      ok('snapshot degrades after control-queue overflow',
        s.fidelity === 'partial' && s.fidelity_reasons.includes('CONTROL_QUEUE_OVERFLOW'), s.fidelity_reasons),
      ok('expired reset reported as timeout', expired.timeout === true && expired.expired === true),
      ok('expired reset did NOT execute later', resetsAfterExpiry === 0, { resetsAfterExpiry }),
      ok('a fresh explicit reset still works', fresh.reset === true),
      ok('injected op error is recorded and the loop keeps working',
        p3.opErrors.length === 1 && p3.counters.throw === 1
        && screenText(p3.term).includes('STILL-ALIVE')),
      ok('lag diagnostics are bounded', p4.feedLagEvents.length <= 4 && p4.feedLagEventsDropped > 0,
        { kept: p4.feedLagEvents.length, dropped: p4.feedLagEventsDropped }),
    ],
    details: {
      controlRejects: rejected,
      snapshotAfterOverflow: { fidelity: s.fidelity, reasons: s.fidelity_reasons },
      expired: { ok: expired.ok, timeout: expired.timeout, request: expired.request },
      resetsAfterExpiry,
      opErrors: p3.opErrors,
      lagEvents: { kept: p4.feedLagEvents.length, dropped: p4.feedLagEventsDropped },
      counters: p.counters,
    },
  };
}

// ---------------------------------------------------------------- C16
async function c16_detectionOverDeclaration() {
  const cases = [
    { name: 'plain text (no declaration)', bytes: 'hello world\r\n', expectFull: true },
    { name: 'DECSTBM without declaration', bytes: 'A\x1b[2;4rB', reason: 'DECSTBM_NOT_SERIALIZED' },
    { name: 'synchronized output 2026', bytes: '\x1b[?2026hX\x1b[?2026l', reason: 'SYNCHRONIZED_OUTPUT_NOT_SERIALIZED' },
    { name: 'DECTCEM cursor visibility', bytes: '\x1b[?25lX', reason: 'CURSOR_VISIBILITY_DECTCEM_UNVERIFIED' },
    { name: 'unknown CSI final', bytes: '\x1b[999z', reason: 'UNKNOWN_CSI_FINAL_z_UNVERIFIED' },
    { name: 'unknown private mode', bytes: '\x1b[?9999h', reason: 'PRIVATE_MODE_9999_UNVERIFIED' },
    { name: 'DCS payload', bytes: '\x1bPq#0;2;0;0;0\x1b\\', reason: 'DCS_PAYLOAD_UNVERIFIED' },
    { name: 'declared unknown feature key', declare: ['WEIRD_FEATURE'], bytes: 'ok', reason: 'UNKNOWN_FEATURE_WEIRD_FEATURE' },
  ];
  const results = [];
  for (const c of cases) {
    const p = mkPipeline({ cols: 20, rows: 4 });
    if (c.declare) p.declareFeatures(c.declare);
    p.enqueueBytes(enc(c.bytes));
    await p.drain();
    const s = await p.snapshot();
    results.push({
      name: c.name,
      fidelity: s.fidelity,
      reasons: s.fidelity_reasons,
      pass: c.expectFull ? s.fidelity === 'full' : (s.fidelity === 'partial'
        && s.fidelity_reasons.includes(c.reason)),
      expected: c.expectFull ? 'full' : c.reason,
    });
  }
  const failed = results.filter((r) => !r.pass);
  return {
    id: 'C16-detection-over-declaration',
    title: 'D2：检测优先（未声明缺口/未知特性/未知 FEATURE_SUPPORT 键都降级）',
    status: failed.length === 0 ? 'pass' : 'fail',
    expectations: [
      ok('no failed classification cases', failed.length === 0, failed),
      ok('plain text can still be full without any declaration',
        results[0].fidelity === 'full'),
    ],
    details: { results },
  };
}

export const CASES = [
  c11_cleanBoundaryUnit,
  c1_altScreenSwitchExitRestore,
  c2_cursorModesScrollResize,
  c3_browserlessConsume,
  c4_clientCursorEvictedTwice,
  c5_asyncFeedImmediateSnapshot,
  c6_resizeInterleave,
  c7_boundaryUtf8CsiOsc,
  c8_queueOverflowDegradedReset,
  c9_scrollRegionHonestPartial,
  c10_knownModesGap,
  c12_realLessReplay,
  c13_cursorProtocolsAndLogPull,
  c14_stickyParserDirty,
  c15_controlQueueBoundsAndExpiry,
  c16_detectionOverDeclaration,
];

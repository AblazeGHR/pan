// Targeted reproduction of the five MA review findings against the CURRENT
// pipeline implementation (run BEFORE the fix; output kept as evidence).
//
//   node tools/repro-review-r1.mjs
//   -> ../evidence/reproduction-r1/repro.json
//
// Each demo prints/records the observed (wrong) behaviour so the fix can be
// judged against a frozen pre-fix snapshot.

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FeedPipeline } from '../src/pipeline.mjs';
import { utf8, makeTerm, writeAll } from '../src/headless-state.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, '..', 'evidence', 'reproduction-r1');
// pre-fix evidence is frozen as repro.json; after the fix run with
//   REPRO_OUT=repro-after-fix.json node tools/repro-review-r1.mjs
const OUT_NAME = process.env.REPRO_OUT || 'repro.json';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const out = {};

// --- D1: dirty parser boundary clears itself on the next ground scan
{
  const p = new FeedPipeline({ cols: 40, rows: 6, maxPendingTail: 8 });
  p.enqueueBytes(utf8('\x1b]0;abcdefghijk'));   // OSC never terminated
  await p.drain();
  const s1 = await p.snapshot();
  p.enqueueBytes(utf8('ZZ'));                    // still inside the OSC
  await p.drain();
  const s2 = await p.snapshot();
  out.D1_sticky_dirty = {
    first: { boundary_clean: s1.boundary_clean, fidelity: s1.fidelity, reasons: s1.fidelity_reasons },
    after_next_chunk: { boundary_clean: s2.boundary_clean, fidelity: s2.fidelity, reasons: s2.fidelity_reasons },
    expected_after_fix: 'second snapshot must stay partial until an explicit reset '
      + '(the emulator is still inside the OSC parser state)',
  };
}

// --- D2: undeclared gap sequences still report full with no reasons
{
  const p = new FeedPipeline({ cols: 20, rows: 4 });
  p.enqueueBytes(utf8('A\x1b[2;4rB'));           // DECSTBM, never declared
  await p.drain();
  const s = await p.snapshot();
  const p2 = new FeedPipeline({ cols: 20, rows: 4 });
  p2.enqueueBytes(utf8('\x1b[?2026hX\x1b[?2026l'));
  await p2.drain();
  const s2 = await p2.snapshot();
  out.D2_undeclared_gaps = {
    decstbm: { fidelity: s.fidelity, reasons: s.fidelity_reasons },
    sync2026: { fidelity: s2.fidelity, reasons: s2.fidelity_reasons },
    expected_after_fix: 'both must degrade without any declareFeatures() call',
  };
}

// --- D3: pending_tail + applied cursor double-feeds under the plan protocol
{
  const script = utf8('PRE-\u4e2d\u6587-\ud83d\ude00 ' + '\x1b[38;5;196mRED\x1b[0m'
    + '\x1b]0;spike-title\u0007' + 'POST\r\n');
  const p = new FeedPipeline({ cols: 40, rows: 6 });
  let snap = null;
  for (let i = 0; i < script.length; i += 5) {
    p.enqueueBytes(script.slice(i, i + 5));
    await p.drain();
    if (p.pendingTail.length > 0) { snap = await p.snapshot(); break; }
  }
  const { term: ref, serializer: refS } = makeTerm(40, 6);
  await writeAll(ref, script);
  const refText = ref.buffer.active.getLine(0).translateToString(true);

  // protocol the plan's cursor wording implies: feed pending from the snapshot
  // AND continue pulling the log from parsed_cursor (= applied_bytes)
  const { term: t } = makeTerm(40, 6);
  await writeAll(t, snap.serialized);
  await writeAll(t, Buffer.from(snap.pending_tail_base64, 'base64'));
  const mixedFrom = snap.parsed_cursor ?? snap.applied_bytes;   // <- starts with the same pending bytes
  const mixedPull = script.slice(mixedFrom);
  await writeAll(t, mixedPull);
  const doubleText = t.buffer.active.getLine(0).translateToString(true);
  // protocol B: feed the snapshot tail, then pull from resume_cursor
  const { term: tb } = makeTerm(40, 6);
  await writeAll(tb, snap.serialized);
  await writeAll(tb, Buffer.from(snap.pending_tail_base64, 'base64'));
  const resumeAt = snap.resume_cursor ?? (snap.applied_bytes + snap.pending_tail_bytes);
  const pulledB = script.slice(resumeAt);
  await writeAll(tb, pulledB);
  const bText = tb.buffer.active.getLine(0).translateToString(true);
  out.D3_cursor_double_feed = {
    applied_bytes: snap.applied_bytes,
    pending_tail_bytes: snap.pending_tail_bytes,
    parsed_cursor: snap.parsed_cursor ?? null,
    resume_cursor: snap.resume_cursor ?? resumeAt,
    reference_line0: refText,
    mixed_line0: doubleText,
    protocolB_line0: bText,
    mixed_visible_diff: doubleText !== refText,
    // the real violation: the client that already took the tail from the snapshot
    // and then reads from parsed_cursor consumes those bytes twice
    mixed_extra_consumed_bytes: (script.length - mixedFrom) - (script.length - resumeAt),
    mixed_pull_bytes: script.length - mixedFrom,
    protocolB_pull_bytes: script.length - resumeAt,
    expected_after_fix: 'explicit parsed_cursor / resume_cursor semantics; protocol A and B '
      + 'match the reference; mixing double-consumes the pending tail bytes',
  };
}

// --- D4: rejected chunks do not advance the producer frontier
{
  const p = new FeedPipeline({ cols: 30, rows: 5, maxQueueBytes: 64 * 1024 });
  p.enqueueStall(600);
  const results = [];
  for (let i = 0; i < 4; i++) results.push(p.enqueueBytes(utf8('x'.repeat(32 * 1024))));
  const rejected = results.filter((r) => !r.accepted).length;
  await p.drain();
  const s = await p.snapshot();
  out.D4_offsets = {
    rejected_chunks: rejected,
    enqueued_bytes_field: s.enqueued_bytes,
    applied_bytes_field: s.applied_bytes,
    producer_handed_bytes: results.length * 32 * 1024,
    expected_after_fix: 'producer frontier must count rejected bytes too; rejected ranges and '
      + 'absolute cursors (parsed/resume) must be reported; reset must define a new absolute frontier',
  };
}

// --- D5: control ops are unbounded; an expired reset still executes later
{
  const p = new FeedPipeline({ cols: 30, rows: 5 });
  const beforeResets = p.resets;
  p.enqueueStall(500);
  // NOTE: the public resetBaseline() does not even accept a timeout; use the
  // internal request path to demonstrate the late-mutation hazard.
  const timedOut = await p._request({ type: 'reset-baseline', timeoutMs: 50 });
  const processedAtTimeout = p.processedSeq;
  await p.drain();
  const afterResets = p.resets;

  const p2 = new FeedPipeline({ cols: 30, rows: 5 });
  p2.enqueueStall(300);                 // keep the applier busy
  let opRejected = 0;
  for (let i = 0; i < 20000; i++) {
    const r = p2.enqueueResize(40 + (i % 5), 10);
    if (!r.accepted) opRejected++;
  }
  const queuedResizeOps = p2.queue.length;
  await p2.drain();

  out.D5_queue_and_expiry = {
    expired_reset_timeout_result: { ok: timedOut.ok, timeout: timedOut.timeout },
    resets_executed_after_expiry: afterResets - beforeResets,
    processed_seq_at_timeout: processedAtTimeout,
    resize_ops_queued_behind_stall: queuedResizeOps,
    resize_ops_rejected: opRejected,
    expected_after_fix: 'expired mutating requests must NOT execute later (resets delta 0) and '
      + 'resize/request ops must be bounded with an explicit rejection reason',
  };
}

// --- D5b: an exception inside the applier must not hang the loop
{
  const p = new FeedPipeline({ cols: 20, rows: 3 });
  // No API to inject an error pre-fix; record the structural gap instead.
  out.D5b_op_error_recovery = {
    inject_api_present: typeof p.enqueueThrow === 'function',
    expected_after_fix: 'test-only throw op + op_errors counter; loop keeps processing afterwards',
  };
}

mkdirSync(OUT, { recursive: true });
writeFileSync(join(OUT, OUT_NAME), JSON.stringify(out, null, 2));
for (const [k, v] of Object.entries(out)) console.log(k, JSON.stringify(v).slice(0, 220));
console.log('written', join(OUT, OUT_NAME));
await sleep(10);

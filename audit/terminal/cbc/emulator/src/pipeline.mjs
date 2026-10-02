// Ordered feed pipeline for the resident headless-xterm snapshot candidate.
//
// MA review r2 fixes (all reproduced in tools/repro-review-r1.mjs first):
//   D1 sticky parser-dirty: a force-fed partial sequence can NOT be declared
//      clean again by a later ground-state scan; it stays partial until an
//      explicit resetBaseline().
//   D2 detection over declaration: fidelity is driven by a conservative
//      byte-stream classifier (measured gap patterns + allowlists); undeclared
//      streams with gaps degrade, unknown FEATURE_SUPPORT keys degrade, and
//      declareFeatures() is only a test-input restriction, never a guarantee.
//   D3 explicit cursor protocol: parsed_cursor (state == serialized) and
//      resume_cursor (= parsed + pending tail). Protocol A: restore snapshot
//      and pull the log from parsed_cursor (no re-feeding of the tail).
//      Protocol B: feed the snapshot tail, then pull the log from
//      resume_cursor. Mixing them double-applies (proved in C13).
//   D4 absolute producer accounting: every handed byte advances the producer
//      frontier (accepted or rejected); rejected ranges are recorded; cursors
//      are absolute source offsets and are reported as null while a gap is
//      sticky; resetBaseline() defines a new absolute frontier.
//   D5 bounded control queue / no late mutations: all op kinds share the op
//      budget; expired requests are skipped (never mutate later); feedLag
//      diagnostics are bounded; the applier loop is exception-safe.
//
// BOUNDARY: this is a spike. No IPC/security/process ownership; it gates the
// engine choice with measured behaviour. Headless-vs-headless only.

import { makeTerm, bytesToBase64 } from './headless-state.mjs';

const DEFAULT_MAX_QUEUE_BYTES = 4 * 1024 * 1024; // plan sec.13: 4 MiB
const DEFAULT_MAX_QUEUE_BLOCKS = 8192;           // plan sec.13: 8192 blocks (feed)
const DEFAULT_MAX_QUEUE_OPS = 8192;              // all op kinds share this budget
const DEFAULT_MAX_PENDING_TAIL = 8 * 1024;
const DEFAULT_MAX_LAG_EVENTS = 64;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Measured engine capability table (see evidence/capabilities.json):
//   supported   -> serialize() round-trips it (measured)
//   gap         -> serialize() does NOT carry it (measured)
//   unverified  -> not measured; MUST degrade fidelity when detected/declared
export const FEATURE_SUPPORT = {
  ALT_SCREEN: 'supported',
  MODES_SERIALIZED: 'supported',
  BLANK_STYLE: 'supported',
  DECSTBM: 'gap',
  SYNCHRONIZED_OUTPUT: 'gap',
  PENDING_PARSER_STATE: 'gap',
  CURSOR_VISIBILITY_DECTCEM: 'unverified',
  CURSOR_STYLE_DECSCUSR: 'unverified',
  WINDOW_TITLE_OSC: 'unverified',
  OSC_HYPERLINK: 'unverified',
  TAB_STOPS: 'unverified',
  CHARSET_G0G1: 'unverified',
  DCS_PAYLOAD: 'unverified',
  SELECTION: 'unverified',
  MOUSE_ENCODINGS_SGR: 'unverified',
};

// Conservative allowlists for the stream classifier: anything not listed and
// not explicitly measured-supported degrades to unverified.
// m = SGR (measured supported: the addon serializes fg/bg/attrs); e = VPR cursor motion
const ALLOWED_CSI_FINALS = new Set([...'ABCDEFGHJKLMPSTX@defmnsuct']);
const ALLOWED_ESC_FINALS = new Set([...'78=>MDEc']);
const ALLOWED_PRIVATE_MODES = new Set([1, 6, 7, 9, 45, 47, 66, 1000, 1002, 1003, 1004, 1047, 1048, 1049, 2004]);
const ALLOWED_ANSI_MODES = new Set([4]); // IRM insert mode (measured via IModes)

/**
 * Stateless scanner: returns the length of the parser-clean prefix plus the
 * sequences that completed inside it.
 *
 * Conservative by construction: an unfinished UTF-8 / ESC / CSI / OSC / DCS
 * sequence is never part of the clean prefix. `events` are completed sequences
 * {kind, body, final, absFrom, absTo} used by the feature classifier.
 */
export function scanStream(bytes, baseAbs = 0) {
  let i = 0;
  let state = 'text';
  let seqStart = 0;
  let bodyStart = 0;
  let utf8Remaining = 0;
  const events = [];

  const pushEvent = (kind, from, to, final = '', body = '') => {
    if (events.length < 4096) {
      events.push({ kind, absFrom: baseAbs + from, absTo: baseAbs + to, final, body });
    }
  };

  while (i < bytes.length) {
    const b = bytes[i];
    if (utf8Remaining > 0) {
      if ((b & 0xc0) === 0x80) { utf8Remaining--; i++; continue; }
      utf8Remaining = 0;
      continue; // re-examine b in ground state
    }
    if (state === 'text') {
      if (b === 0x1b) { state = 'esc'; seqStart = i; i++; continue; }
      if (b === 0x9b) { state = 'csi'; seqStart = i; bodyStart = i + 1; i++; continue; }
      if (b === 0x9d) { state = 'osc'; seqStart = i; bodyStart = i + 1; i++; continue; }
      if (b === 0x90) { state = 'dcs'; seqStart = i; bodyStart = i + 1; i++; continue; }
      if (b === 0x98 || b === 0x9e || b === 0x9f) {
        state = 'str'; seqStart = i; bodyStart = i + 1; i++; continue;
      }
      // a partial UTF-8 char starts here: the clean prefix must end at this byte
      if (b >= 0xc2 && b <= 0xdf) { seqStart = i; utf8Remaining = 1; i++; continue; }
      if (b >= 0xe0 && b <= 0xef) { seqStart = i; utf8Remaining = 2; i++; continue; }
      if (b >= 0xf0 && b <= 0xf4) { seqStart = i; utf8Remaining = 3; i++; continue; }
      i++; continue;
    }
    if (state === 'esc') {
      if (b === 0x5b) { state = 'csi'; bodyStart = i + 1; i++; continue; }   // [
      if (b === 0x5d) { state = 'osc'; bodyStart = i + 1; i++; continue; }   // ]
      if (b === 0x50) { state = 'dcs'; bodyStart = i + 1; i++; continue; }   // P
      if (b === 0x58 || b === 0x5e || b === 0x5f) { state = 'str'; i++; continue; }
      if (b === 0x1b) { seqStart = i; i++; continue; }         // ESC ESC
      if (b >= 0x20 && b <= 0x2f) { i++; continue; }           // intermediates
      pushEvent('esc', seqStart, i + 1, String.fromCharCode(b));
      state = 'text'; i++; continue;
    }
    if (state === 'csi') {
      if (b >= 0x40 && b <= 0x7e) {
        const body = Buffer.from(bytes.slice(bodyStart, i)).toString('latin1');
        pushEvent('csi', seqStart, i + 1, String.fromCharCode(b), body);
        state = 'text'; i++; continue;
      }
      i++; continue;
    }
    if (state === 'osc') {
      if (b === 0x07 || b === 0x9c) {
        const body = Buffer.from(bytes.slice(bodyStart, i)).toString('latin1');
        pushEvent('osc', seqStart, i + 1, '', body);
        state = 'text'; i++; continue;
      }
      if (b === 0x1b && bytes[i + 1] === 0x5c) {
        const body = Buffer.from(bytes.slice(bodyStart, i)).toString('latin1');
        pushEvent('osc', seqStart, i + 2, '', body);
        state = 'text'; i += 2; continue;
      }
      i++; continue;
    }
    // dcs / str: terminated by ST only
    if (b === 0x9c) {
      pushEvent(state === 'dcs' ? 'dcs' : 'str', seqStart, i + 1);
      state = 'text'; i++; continue;
    }
    if (b === 0x1b && bytes[i + 1] === 0x5c) {
      pushEvent(state === 'dcs' ? 'dcs' : 'str', seqStart, i + 2);
      state = 'text'; i += 2; continue;
    }
    i++; continue;
  }
  const clean = state === 'text' && utf8Remaining === 0 ? bytes.length : seqStart;
  return { cleanLen: clean, events, endState: state, utf8Remaining };
}

/** Back-compat wrapper used by the C11 unit test. */
export function cleanPrefixLength(bytes) {
  return scanStream(bytes).cleanLen;
}

/** Classify one completed sequence into a fidelity reason (or null = safe). */
export function classifySequence(ev) {
  if (ev.kind === 'csi') {
    const { final, body } = ev;
    const isPrivate = body.startsWith('?');
    const hasSpaceIntermediate = body.includes(' ');
    if (final === 'r' && !isPrivate && !hasSpaceIntermediate) return 'DECSTBM_NOT_SERIALIZED';
    if (hasSpaceIntermediate && final === 'q') return 'CURSOR_STYLE_DECSCUSR_UNVERIFIED';
    if (final === 'h' || final === 'l') {
      const raw = body.replace(/^[?>]/, '').replace(/ .*$/, '');
      const params = raw.split(';').filter((s) => s.length > 0).map((s) => Number.parseInt(s, 10));
      for (const n of params) {
        if (!Number.isFinite(n)) return `CSI_PARAM_UNVERIFIED_${body}`;
        if (isPrivate) {
          if (n === 2026) return 'SYNCHRONIZED_OUTPUT_NOT_SERIALIZED';
          if (n === 25) return 'CURSOR_VISIBILITY_DECTCEM_UNVERIFIED';
          if (!ALLOWED_PRIVATE_MODES.has(n)) return `PRIVATE_MODE_${n}_UNVERIFIED`;
        } else if (!ALLOWED_ANSI_MODES.has(n)) {
          return `ANSI_MODE_${n}_UNVERIFIED`;
        }
      }
      return null;
    }
    if (final === 'g') {
      const n = Number.parseInt(body || '0', 10);
      if (n === 3) return 'TAB_STOPS_UNVERIFIED';
      return null;
    }
    if (ALLOWED_CSI_FINALS.has(final)) return null;
    return `UNKNOWN_CSI_FINAL_${final}_UNVERIFIED`;
  }
  if (ev.kind === 'esc') {
    if (ALLOWED_ESC_FINALS.has(ev.final)) return null;
    return `UNKNOWN_ESC_${ev.final.charCodeAt(0)}_UNVERIFIED`;
  }
  if (ev.kind === 'osc') {
    const code = (ev.body || '').split(';')[0];
    if (code === '0' || code === '1' || code === '2') return 'WINDOW_TITLE_OSC_UNVERIFIED';
    if (code === '8') return 'OSC_HYPERLINK_UNVERIFIED';
    if (['4', '10', '11', '12'].includes(code)) return null; // palette: colours are resolved per-cell
    return `OSC_${code || 'EMPTY'}_UNVERIFIED`;
  }
  if (ev.kind === 'dcs') return 'DCS_PAYLOAD_UNVERIFIED';
  if (ev.kind === 'str') return 'UNKNOWN_STRING_SEQUENCE_UNVERIFIED';
  return null;
}

export class FeedPipeline {
  constructor({
    cols = 80, rows = 24, scrollback = 1000,
    maxQueueBytes = DEFAULT_MAX_QUEUE_BYTES,
    maxQueueBlocks = DEFAULT_MAX_QUEUE_BLOCKS,
    maxQueueOps = DEFAULT_MAX_QUEUE_OPS,
    maxPendingTail = DEFAULT_MAX_PENDING_TAIL,
    maxLagEvents = DEFAULT_MAX_LAG_EVENTS,
    serializerOptions = {},
  } = {}) {
    const { term, serializer } = makeTerm(cols, rows, { scrollback });
    this.term = term;
    this.serializer = serializer;
    this.serializerOptions = serializerOptions;
    this.maxQueueBytes = maxQueueBytes;
    this.maxQueueBlocks = maxQueueBlocks;
    this.maxQueueOps = maxQueueOps;
    this.maxPendingTail = maxPendingTail;
    this.maxLagEvents = maxLagEvents;

    this.queue = [];
    this.queueBytes = 0;
    this.nextSeq = 1;          // seq for every op (feed and control)
    this.processedSeq = 0;
    this.producerBytes = 0;    // absolute frontier: every byte handed over (accepted or rejected)
    this.acceptedBytes = 0;    // bytes accepted into the feed stream
    this.parsedBytes = 0;      // absolute end offset of the last byte fed to the emulator
    this.pendingTail = new Uint8Array(0);
    this.pendingAbsStart = null;
    this.rejectedRanges = [];  // [{seq, from, to}] absolute
    this.resets = 0;
    this.baselineFrontier = 0;
    this.parserDirty = false;  // D1: sticky until explicit reset
    this.feedLag = false;
    this.feedLagEvents = [];
    this.feedLagEventsDropped = 0;
    this.detectedReasons = new Set();
    this.declaredReasons = new Set();
    this.features = new Set();
    this.opErrors = [];
    this.counters = {
      feed: 0, resize: 0, snapshot: 0, barrier: 0, reset: 0, stall: 0, throw: 0,
      rejected_feed: 0, rejected_control: 0, expired_skipped: 0,
    };
    this._processing = false;
    this._processLoop();
  }

  /** Declare expected features (TEST INPUT RESTRICTION ONLY — never a
   *  production guarantee; fidelity still comes from detection). */
  declareFeatures(features) {
    for (const f of features) {
      this.features.add(f);
      const support = FEATURE_SUPPORT[f];
      if (support === 'gap') this.declaredReasons.add(`${f}_NOT_SERIALIZED`);
      else if (support === 'unverified') this.declaredReasons.add(`${f}_UNVERIFIED`);
      else if (support === undefined) this.declaredReasons.add(`UNKNOWN_FEATURE_${f}`);
    }
  }

  get gapSticky() { return this.rejectedRanges.length > 0; }

  _recordLag(entry) {
    if (this.feedLagEvents.length < this.maxLagEvents) this.feedLagEvents.push(entry);
    else this.feedLagEventsDropped++;
  }

  /** Synchronous producer API. Never blocks; overflow is explicit and still
   *  advances the producer frontier (D4). */
  enqueueBytes(bytes) {
    const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    const absStart = this.producerBytes;
    const absEnd = absStart + b.length;
    const overBytes = this.queueBytes + b.length > this.maxQueueBytes;
    const overBlocks = this.queue.length + 1 > this.maxQueueBlocks;
    const overOps = this.queue.length + 1 > this.maxQueueOps;
    if (overBytes || overBlocks || overOps) {
      this.feedLag = true;
      this.counters.rejected_feed++;
      this.rejectedRanges.push({ seq: this.nextSeq++, from: absStart, to: absEnd });
      this.producerBytes = absEnd;             // frontier advances even when rejected
      this._recordLag({ at: Date.now(), kind: 'feed', reason: overOps ? 'queue_ops' : 'queue_bytes',
        queueBytes: this.queueBytes, queueBlocks: this.queue.length, rejected: [absStart, absEnd] });
      return { accepted: false, seq: null, reason: 'feed_lag', absStart, absEnd,
        queueBytes: this.queueBytes, queueBlocks: this.queue.length };
    }
    const seq = this.nextSeq++;
    this.queue.push({ seq, type: 'feed', bytes: b, absStart, absEnd });
    this.queueBytes += b.length;
    this.acceptedBytes += b.length;
    this.producerBytes = absEnd;
    this._processLoop();
    return { accepted: true, seq, absStart, absEnd, queueBytes: this.queueBytes, queueBlocks: this.queue.length };
  }

  _enqueueControl(op) {
    if (this.queue.length + 1 > this.maxQueueOps) {
      this.feedLag = true;
      this.counters.rejected_control++;
      this._recordLag({ at: Date.now(), kind: op.type, reason: 'queue_ops',
        queueBytes: this.queueBytes, queueBlocks: this.queue.length });
      return { accepted: false, reason: 'control_lag' };
    }
    const seq = this.nextSeq++;
    this.queue.push({ ...op, seq });
    this._processLoop();
    return { accepted: true, seq };
  }

  enqueueResize(cols, rows) {
    return this._enqueueControl({ type: 'resize', cols, rows });
  }

  /** Test-only: stall the applier (simulates an emulator that stops keeping up). */
  enqueueStall(ms) {
    return this._enqueueControl({ type: 'stall', ms });
  }

  /** Test-only: force an op error to prove the loop recovers (D5). */
  enqueueThrow() {
    return this._enqueueControl({ type: 'throw' });
  }

  snapshot({ timeoutMs = 2000 } = {}) {
    return this._request({ type: 'snapshot', timeoutMs });
  }

  barrier({ timeoutMs = 2000, targetSeq = null } = {}) {
    return this._request({ type: 'barrier', timeoutMs, targetSeq });
  }

  /** Explicit baseline reset. Bounded; never executes if the request expired. */
  resetBaseline({ timeoutMs = 2000 } = {}) {
    return this._request({ type: 'reset-baseline', timeoutMs });
  }

  _request(op) {
    if (this.queue.length + 1 > this.maxQueueOps) {
      this.feedLag = true;
      this.counters.rejected_control++;
      this._recordLag({ at: Date.now(), kind: op.type, reason: 'queue_ops',
        queueBytes: this.queueBytes, queueBlocks: this.queue.length });
      return Promise.resolve({ ok: false, rejected: true, reason: 'control_lag',
        request: op.type, producer_frontier: this.producerBytes });
    }
    const entry = { ...op, seq: this.nextSeq++ };
    let resolveFn;
    const promise = new Promise((r) => { resolveFn = r; });
    const timeoutMs = op.timeoutMs ?? 2000;
    const timer = setTimeout(() => {
      entry.expired = true;                    // D5: late mutating ops must not run
      this.counters.expired_skipped++;
      resolveFn({
        ok: false,
        timeout: true,
        expired: true,
        request: op.type,
        enqueued_seq: entry.seq,
        processed_seq: this.processedSeq,
        parsed_bytes: this.parsedBytes,
        producer_frontier: this.producerBytes,
        pending_tail_bytes: this.pendingTail.length,
        queue_blocks: this.queue.length,
        feed_lag: this.feedLag,
        recovery: this.recovery,
      });
    }, timeoutMs);
    entry.resolve = (value) => { clearTimeout(timer); resolveFn(value); };
    this.queue.push(entry);
    this._processLoop();                        // resolve is attached before processing starts
    return promise;
  }

  async _processLoop() {
    if (this._processing) return;
    this._processing = true;
    try {
      while (this.queue.length > 0) {
        const op = this.queue.shift();
        if (op.expired) {                        // resolved as timeout already
          this.processedSeq = op.seq;
          continue;
        }
        try {
          if (op.type === 'feed') {
            this.queueBytes -= op.bytes.length;
            this.counters.feed++;
            await this._applyFeed(op);
          } else if (op.type === 'resize') {
            this.counters.resize++;
            this.term.resize(op.cols, op.rows);
          } else if (op.type === 'stall') {
            this.counters.stall++;
            await sleep(op.ms);
          } else if (op.type === 'throw') {
            this.counters.throw++;
            throw new Error('injected op error (test)');
          } else if (op.type === 'snapshot') {
            this.counters.snapshot++;
            op.resolve(this._buildSnapshot());
          } else if (op.type === 'barrier') {
            this.counters.barrier++;
            op.resolve(this._buildBarrier());
          } else if (op.type === 'reset-baseline') {
            this.counters.reset++;
            op.resolve(this._doResetBaseline());
          }
        } catch (err) {
          this.opErrors.push({ seq: op.seq, type: op.type, error: String(err && err.message || err) });
          if (this.opErrors.length > this.maxLagEvents) this.opErrors.shift();
          if (op.resolve) op.resolve({ ok: false, error: String(err && err.message || err), seq: op.seq });
        }
        this.processedSeq = op.seq;
      }
    } finally {
      this._processing = false;
      if (this.queue.length > 0) this._processLoop();
    }
  }

  async _applyFeed(op) {
    const merged = new Uint8Array(this.pendingTail.length + op.bytes.length);
    merged.set(this.pendingTail, 0);
    merged.set(op.bytes, this.pendingTail.length);
    const mergedAbsStart = this.pendingAbsStart ?? op.absStart;
    const scan = scanStream(merged, mergedAbsStart);
    for (const ev of scan.events) {
      const reason = classifySequence(ev);
      if (reason) this.detectedReasons.add(reason);
    }
    if (this.parserDirty) {
      // D1: already unrecoverable — keep feeding, stay partial.
      this.pendingTail = new Uint8Array(0);
      this.pendingAbsStart = null;
      await this._write(merged);
      this.parsedBytes = mergedAbsStart + merged.length;
      return;
    }
    if (scan.cleanLen < merged.length) {
      if (merged.length - scan.cleanLen > this.maxPendingTail) {
        this.parserDirty = true;               // sticky until explicit reset
        this.pendingTail = new Uint8Array(0);
        this.pendingAbsStart = null;
        await this._write(merged);
        this.parsedBytes = mergedAbsStart + merged.length;
        return;
      }
      this.pendingTail = merged.slice(scan.cleanLen);
      this.pendingAbsStart = mergedAbsStart + scan.cleanLen;
      if (scan.cleanLen > 0) await this._write(merged.slice(0, scan.cleanLen));
      this.parsedBytes = mergedAbsStart + scan.cleanLen;
      return;
    }
    this.pendingTail = new Uint8Array(0);
    this.pendingAbsStart = null;
    if (merged.length > 0) await this._write(merged);
    this.parsedBytes = mergedAbsStart + merged.length;
  }

  _write(bytes) {
    return new Promise((resolve) => {
      this.term.write(bytes, () => resolve());
    });
  }

  get recovery() {
    if (this.feedLag) return 'degraded';
    if (this.parserDirty || this.resets > 0 || this.gapSticky) return 'partial';
    return 'full';
  }

  _reasons() {
    const reasons = new Set();
    if (this.feedLag) reasons.add('FEED_LAG_QUEUE_OVERFLOW');
    if (this.counters.rejected_control > 0) reasons.add('CONTROL_QUEUE_OVERFLOW');
    if (this.parserDirty) reasons.add('PENDING_PARSER_STATE_UNSERIALIZED');
    if (this.resets > 0) reasons.add('BASELINE_RESET_FRESH_VIEW');
    if (this.gapSticky) reasons.add('SOURCE_GAP_STICKY');
    for (const r of this.detectedReasons) reasons.add(r);
    for (const r of this.declaredReasons) reasons.add(r);
    return [...reasons];
  }

  _cursors() {
    // Absolute source offsets are only meaningful without a sticky gap; after a
    // rejection the client must re-baseline instead of trusting an alignment.
    if (this.gapSticky) {
      return { parsed_cursor: null, resume_cursor: null, cursors_valid: false };
    }
    const parsed = this.pendingTail.length > 0 ? this.pendingAbsStart : this.parsedBytes;
    const resume = this.pendingTail.length > 0 ? this.pendingAbsStart + this.pendingTail.length : this.parsedBytes;
    return { parsed_cursor: parsed, resume_cursor: resume, cursors_valid: true };
  }

  _buildBarrier() {
    const cur = this._cursors();
    return {
      ok: true,
      processed_seq: this.processedSeq,
      parsed_bytes: this.parsedBytes,
      producer_frontier: this.producerBytes,
      accepted_bytes: this.acceptedBytes,
      applied_bytes: this.parsedBytes,     // legacy alias
      enqueued_bytes: this.producerBytes,  // legacy alias
      ...cur,
      pending_tail_bytes: this.pendingTail.length,
      boundary_clean: !this.parserDirty,
      queue_blocks: this.queue.length,
      feed_lag: this.feedLag,
      recovery: this.recovery,
    };
  }

  _buildSnapshot() {
    const serialized = this.serializer.serialize(this.serializerOptions);
    const reasons = this._reasons();
    const cur = this._cursors();
    return {
      ok: true,
      serialized,
      // D3 protocol fields
      parsed_cursor: cur.parsed_cursor,
      resume_cursor: cur.resume_cursor,
      cursors_valid: cur.cursors_valid,
      cursor_semantics: 'parsed_cursor: serialized state corresponds to this source offset. '
        + 'resume_cursor: parsed + pending tail; use Protocol A (pull log from parsed_cursor, '
        + 'do NOT re-feed the tail) or Protocol B (feed the tail, then pull from resume_cursor). '
        + 'Mixing double-applies. Null cursors mean a sticky source gap: re-baseline instead.',
      // legacy aliases kept for earlier evidence comparability
      applied_seq: this.processedSeq,
      applied_bytes: this.parsedBytes,
      enqueued_seq: this.nextSeq - 1,
      enqueued_bytes: this.producerBytes,
      // D4 accounting
      producer_frontier: this.producerBytes,
      accepted_bytes: this.acceptedBytes,
      rejected_ranges: this.rejectedRanges,
      gap_sticky: this.gapSticky,
      baseline_reset: this.resets > 0,
      baseline_frontier: this.baselineFrontier,
      // D1/D2 state
      pending_tail_base64: bytesToBase64(this.pendingTail),
      pending_tail_bytes: this.pendingTail.length,
      pending_tail_abs_start: this.pendingAbsStart,
      boundary_clean: !this.parserDirty,
      parser_dirty: this.parserDirty,
      declared_features: [...this.features],
      detected_reasons: [...this.detectedReasons],
      fidelity_reasons: reasons,
      fidelity: reasons.length === 0 ? 'full' : 'partial',
      recovery: this.recovery,
      feed_lag: this.feedLag,
      ...this._dims(),
    };
  }

  _doResetBaseline() {
    this.term.reset();
    this.pendingTail = new Uint8Array(0);
    this.pendingAbsStart = null;
    this.parsedBytes = this.producerBytes;      // align to the new absolute frontier
    this.parserDirty = false;
    this.feedLag = false;
    this.detectedReasons.clear();
    this.declaredReasons.clear();
    this.resets++;
    this.baselineFrontier = this.producerBytes;
    return {
      ok: true,
      reset: true,
      baseline_frontier: this.baselineFrontier,
      producer_frontier: this.producerBytes,
      recovery: this.recovery,
      note: 'explicit reset-baseline: screen cleared, a new absolute frontier is defined; '
        + 'history before it (including rejected ranges) is not recoverable',
    };
  }

  _dims() {
    return { cols: this.term.cols, rows: this.term.rows };
  }

  async drain() {
    while (this.queue.length > 0 || this._processing) {
      await sleep(2);
    }
  }
}

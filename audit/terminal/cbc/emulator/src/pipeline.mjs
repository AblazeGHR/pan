// Ordered feed pipeline for the resident headless-xterm snapshot candidate.
//
// Design goals (plan sec.8 / sec.8.5, MA freeze):
//   * reader enqueue != applied: enqueue() is synchronous and returns a seq;
//     the emulator acknowledges via the xterm write callback (applied_seq).
//   * feed / resize / snapshot / barrier / reset-baseline share ONE ordered
//     channel; barrier timeouts are bounded and never block the producer.
//   * raw bytes only: chunks are fed as Uint8Array so UTF-8 continuation state
//     survives chunk boundaries (splitting JS strings mid-surrogate does not).
//   * parser-clean boundaries: the applier holds back a trailing incomplete
//     UTF-8 / ESC / CSI / OSC / DCS sequence and reports it as `pending_tail`
//     in every snapshot, so a restored emulator can continue the live stream
//     with no replay and no loss. (Measured: @xterm/addon-serialize 0.14.0 does
//     NOT serialize pending parser state — see evidence/capabilities.json.)
//   * bounded queue (default 4 MiB / 8192 blocks): overflow latches `feed_lag`
//     and `recovery=degraded`; snapshots must never claim full afterwards.
//     Only an explicit resetBaseline() clears the latch, and even then the
//     recovery stays `partial` (fresh baseline, never auto-full).
//   * if the held-back tail exceeds maxPendingTail (unterminated OSC/DCS), the
//     boundary is force-fed and marked dirty: snapshots then report
//     `boundary_clean=false` and degrade fidelity, because serialize() cannot
//     carry the partial parser state.
//
// Scope note: this is a spike, not production code. No process ownership,
// no IPC, no security. It exists to gate the engine choice with measured data.

import { makeTerm, bytesToBase64 } from './headless-state.mjs';

const DEFAULT_MAX_QUEUE_BYTES = 4 * 1024 * 1024; // plan sec.13: 4 MiB
const DEFAULT_MAX_QUEUE_BLOCKS = 8192;           // plan sec.13: 8192 blocks
const DEFAULT_MAX_PENDING_TAIL = 8 * 1024;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Measured engine capability table (see evidence/capabilities.json):
//   supported   -> serialize() round-trips it (measured)
//   gap         -> serialize() does NOT carry it (measured)
//   unverified  -> not measured in this spike; must degrade fidelity if used
export const FEATURE_SUPPORT = {
  ALT_SCREEN: 'supported',
  MODES_SERIALIZED: 'supported',        // the ten IModes flags except SYNCHRONIZED_OUTPUT
  BLANK_STYLE: 'supported',
  DECSTBM: 'gap',                       // scroll region not serialized (P4)
  SYNCHRONIZED_OUTPUT: 'gap',           // mode 2026 not serialized (P5)
  PENDING_PARSER_STATE: 'gap',          // serialize() drops pending CSI/OSC (P2a/P2b);
                                        // the pipeline covers this with hold-back tails
  CURSOR_STYLE_DECSCUSR: 'unverified',
  WINDOW_TITLE_OSC: 'unverified',
  TAB_STOPS: 'unverified',
  CHARSET_G0G1: 'unverified',
  SELECTION: 'unverified',
  MOUSE_ENCODINGS_SGR: 'unverified',
};

/**
 * Return the length of the parser-clean prefix of `bytes`.
 *
 * Clean prefix = bytes that end in the ground state: no incomplete UTF-8
 * sequence, no ESC leader, no unfinished CSI/OSC/DCS/ST-terminated string.
 * Conservative by construction: anything not proven complete is held back.
 */
export function cleanPrefixLength(bytes) {
  let i = 0;
  let state = 'text';
  let seqStart = 0;
  let utf8Remaining = 0;
  while (i < bytes.length) {
    const b = bytes[i];
    if (utf8Remaining > 0) {
      if ((b & 0xc0) === 0x80) { utf8Remaining--; i++; continue; }
      // malformed continuation: let the emulator deal with it as ground text
      utf8Remaining = 0;
      continue; // re-examine b in ground state
    }
    if (state === 'text') {
      if (b === 0x1b) { state = 'esc'; seqStart = i; i++; continue; }
      if (b === 0x9b) { state = 'csi'; seqStart = i; i++; continue; }
      if (b === 0x9d) { state = 'osc'; seqStart = i; i++; continue; }
      if (b === 0x90) { state = 'dcs'; seqStart = i; i++; continue; }
      if (b === 0x98 || b === 0x9e || b === 0x9f) { state = 'str'; seqStart = i; i++; continue; }
      if (b >= 0xc2 && b <= 0xdf) { utf8Remaining = 1; i++; continue; }
      if (b >= 0xe0 && b <= 0xef) { utf8Remaining = 2; i++; continue; }
      if (b >= 0xf0 && b <= 0xf4) { utf8Remaining = 3; i++; continue; }
      i++; continue;
    }
    if (state === 'esc') {
      if (b === 0x5b) { state = 'csi'; i++; continue; }        // [
      if (b === 0x5d) { state = 'osc'; i++; continue; }        // ]
      if (b === 0x50) { state = 'dcs'; i++; continue; }        // P
      if (b === 0x58 || b === 0x5e || b === 0x5f) { state = 'str'; i++; continue; }
      if (b === 0x1b) { seqStart = i; i++; continue; }         // ESC ESC
      if (b >= 0x20 && b <= 0x2f) { i++; continue; }           // intermediates
      state = 'text'; i++; continue;                            // 2-byte final
    }
    if (state === 'csi') {
      if (b >= 0x40 && b <= 0x7e) { state = 'text'; i++; continue; }  // final
      i++; continue;                                            // params/intermediates
    }
    if (state === 'osc') {
      if (b === 0x07 || b === 0x9c) { state = 'text'; i++; continue; } // BEL / C1 ST
      if (b === 0x1b && bytes[i + 1] === 0x5c) { state = 'text'; i += 2; continue; } // ESC \
      i++; continue;
    }
    // dcs / str: terminated by ST only
    if (b === 0x9c) { state = 'text'; i++; continue; }
    if (b === 0x1b && bytes[i + 1] === 0x5c) { state = 'text'; i += 2; continue; }
    i++; continue;
  }
  return state === 'text' && utf8Remaining === 0 ? bytes.length : seqStart;
}

export class FeedPipeline {
  constructor({
    cols = 80, rows = 24, scrollback = 1000,
    maxQueueBytes = DEFAULT_MAX_QUEUE_BYTES,
    maxQueueBlocks = DEFAULT_MAX_QUEUE_BLOCKS,
    maxPendingTail = DEFAULT_MAX_PENDING_TAIL,
    serializerOptions = {},
  } = {}) {
    const { term, serializer } = makeTerm(cols, rows, { scrollback });
    this.term = term;
    this.serializer = serializer;
    this.serializerOptions = serializerOptions;
    this.maxQueueBytes = maxQueueBytes;
    this.maxQueueBlocks = maxQueueBlocks;
    this.maxPendingTail = maxPendingTail;

    this.queue = [];
    this.queueBytes = 0;
    this.nextSeq = 1;        // seq assigned at enqueue
    this.processedSeq = 0;   // last op taken by the applier
    this.appliedBytes = 0;   // bytes actually fed to the emulator
    this.enqueuedBytes = 0;
    this.pendingTail = new Uint8Array(0); // parser-dirty hold-back
    this.boundaryClean = true;
    this.feedLag = false;
    this.feedLagEvents = [];
    this.recovery = 'full';  // full | partial | degraded
    this.baselineSeq = 0;
    this.baselineBytes = 0;
    this.resets = 0;
    this.counters = { feed: 0, resize: 0, snapshot: 0, barrier: 0, reset: 0, stall: 0, rejected: 0 };
    this.features = new Set();
    this._processing = false;
    this._processLoop();
  }

  /** Declare which terminal features this stream may use (drives fidelity). */
  declareFeatures(features) {
    for (const f of features) this.features.add(f);
  }

  /** Synchronous producer API. Never blocks; overflow is explicit. */
  enqueueBytes(bytes) {
    const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    if (this.queueBytes + b.length > this.maxQueueBytes
      || this.queue.length + 1 > this.maxQueueBlocks) {
      this.feedLag = true;
      this.recovery = 'degraded';
      this.counters.rejected++;
      this.feedLagEvents.push({ at: Date.now(), queueBytes: this.queueBytes, queueBlocks: this.queue.length, rejectedBytes: b.length });
      return { accepted: false, seq: null, reason: 'feed_lag', queueBytes: this.queueBytes, queueBlocks: this.queue.length };
    }
    const seq = this.nextSeq++;
    this.queue.push({ seq, type: 'feed', bytes: b });
    this.queueBytes += b.length;
    this.enqueuedBytes += b.length;
    this._processLoop();
    return { accepted: true, seq, queueBytes: this.queueBytes, queueBlocks: this.queue.length };
  }

  enqueueResize(cols, rows) {
    const seq = this.nextSeq++;
    this.queue.push({ seq, type: 'resize', cols, rows });
    this._processLoop();
    return { accepted: true, seq };
  }

  /** Test-only: stall the applier (simulates an emulator that stops keeping up). */
  enqueueStall(ms) {
    const seq = this.nextSeq++;
    this.queue.push({ seq, type: 'stall', ms });
    this._processLoop();
    return { accepted: true, seq };
  }

  /** Ordered snapshot: waits (bounded) for all earlier ops, then serializes. */
  snapshot({ timeoutMs = 2000 } = {}) {
    return this._request({ type: 'snapshot', timeoutMs }).then((r) => r);
  }

  /** Ordered barrier: waits (bounded) for all earlier ops to be processed. */
  barrier({ timeoutMs = 2000, targetSeq = null } = {}) {
    return this._request({ type: 'barrier', timeoutMs, targetSeq });
  }

  /** Explicit client/operator baseline reset after feed_lag. Never auto-full. */
  resetBaseline() {
    return this._request({ type: 'reset-baseline', timeoutMs: 2000 });
  }

  _request(op) {
    const seq = this.nextSeq++;
    let resolveFn;
    const promise = new Promise((r) => { resolveFn = r; });
    const timeoutMs = op.timeoutMs ?? 2000;
    const timer = setTimeout(() => {
      // Bounded wait: report the timeout explicitly instead of blocking the
      // caller (and therefore the producer) forever.
      resolveFn({
        ok: false,
        timeout: true,
        request: op.type,
        enqueued_seq: seq,
        processed_seq: this.processedSeq,
        applied_bytes: this.appliedBytes,
        pending_tail_bytes: this.pendingTail.length,
        queue_blocks: this.queue.length,
        feed_lag: this.feedLag,
        recovery: this.recovery,
      });
    }, timeoutMs);
    const wrapped = (value) => { clearTimeout(timer); resolveFn(value); };
    this.queue.push({ ...op, seq, resolve: wrapped });
    this._processLoop();
    return promise;
  }

  async _processLoop() {
    if (this._processing) return;
    this._processing = true;
    while (this.queue.length > 0) {
      const op = this.queue.shift();
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
      this.processedSeq = op.seq;
    }
    this._processing = false;
    // Re-check in case an enqueue raced with the shutdown of this loop.
    if (this.queue.length > 0) this._processLoop();
  }

  async _applyFeed(op) {
    // Append to the hold-back buffer and feed the parser-clean prefix.
    const merged = new Uint8Array(this.pendingTail.length + op.bytes.length);
    merged.set(this.pendingTail, 0);
    merged.set(op.bytes, this.pendingTail.length);
    const cleanLen = cleanPrefixLength(merged);
    if (cleanLen < merged.length) {
      if (merged.length - cleanLen > this.maxPendingTail) {
        // Unterminated sequence (e.g. a title stream) exceeds the hold-back
        // budget: feed everything, remember the boundary is no longer clean.
        this.boundaryClean = false;
        this.pendingTail = new Uint8Array(0);
        await this._write(merged);
        return;
      }
      this.boundaryClean = true;
      this.pendingTail = merged.slice(cleanLen);
      if (cleanLen > 0) await this._write(merged.slice(0, cleanLen));
      return;
    }
    this.boundaryClean = true;
    this.pendingTail = new Uint8Array(0);
    if (merged.length > 0) await this._write(merged);
  }

  _write(bytes) {
    return new Promise((resolve) => {
      this.term.write(bytes, () => {
        this.appliedBytes += bytes.length;
        resolve();
      });
    });
  }

  _dims() {
    return { cols: this.term.cols, rows: this.term.rows };
  }

  _buildBarrier() {
    return {
      ok: true,
      processed_seq: this.processedSeq,
      applied_bytes: this.appliedBytes,
      enqueued_bytes: this.enqueuedBytes,
      pending_tail_bytes: this.pendingTail.length,
      boundary_clean: this.boundaryClean,
      queue_blocks: this.queue.length,
      feed_lag: this.feedLag,
      recovery: this.recovery,
    };
  }

  _buildSnapshot() {
    const serialized = this.serializer.serialize(this.serializerOptions);
    const dims = this._dims();
    const reasons = [];
    if (this.feedLag) reasons.push('FEED_LAG_QUEUE_OVERFLOW');
    if (!this.boundaryClean) reasons.push('PENDING_PARSER_STATE_UNSERIALIZED');
    if (this.resets > 0) reasons.push('BASELINE_RESET_FRESH_VIEW');
    for (const f of this.features) {
      const support = FEATURE_SUPPORT[f];
      if (support === 'gap') reasons.push(`${f}_NOT_SERIALIZED`);
      else if (support === 'unverified') reasons.push(`${f}_UNVERIFIED`);
    }
    const fidelity = reasons.length === 0 ? 'full' : 'partial';
    return {
      ok: true,
      // cursor = position of the authority state that was serialized
      applied_seq: this.processedSeq,
      applied_bytes: this.appliedBytes,
      cursor_semantics: 'applied_seq/applied_bytes identify the serialized state; '
        + 'pending_tail must be fed before continuing the live stream',
      enqueued_seq: this.nextSeq - 1,
      enqueued_bytes: this.enqueuedBytes,
      pending_tail_base64: bytesToBase64(this.pendingTail),
      pending_tail_bytes: this.pendingTail.length,
      boundary_clean: this.boundaryClean,
      serialized,
      ...dims,
      feed_lag: this.feedLag,
      baseline_reset: this.resets > 0,
      baseline_seq: this.baselineSeq,
      baseline_bytes: this.baselineBytes,
      declared_features: [...this.features],
      fidelity_reasons: reasons,
      fidelity,
      recovery: this.recovery,
    };
  }

  _doResetBaseline() {
    this.term.reset();
    this.pendingTail = new Uint8Array(0);
    this.boundaryClean = true;
    this.feedLag = false;
    this.resets++;
    this.baselineSeq = this.processedSeq;
    this.baselineBytes = this.appliedBytes;
    // Explicit fresh baseline: never auto-full, the client must re-baseline.
    this.recovery = 'partial';
    return {
      ok: true,
      reset: true,
      baseline_seq: this.baselineSeq,
      baseline_bytes: this.baselineBytes,
      recovery: this.recovery,
      note: 'explicit reset-baseline: screen cleared, only output from this cursor on is guaranteed',
    };
  }

  async drain() {
    while (this.queue.length > 0 || this._processing) {
      await sleep(2);
    }
  }
}

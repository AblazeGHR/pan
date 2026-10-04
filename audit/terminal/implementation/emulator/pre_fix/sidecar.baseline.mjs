// Pan Terminal resident headless-xterm authoritative emulator sidecar.
//
// BASELINE VERSION (pre-fix): this revision intentionally mirrors the
// exploration pipeline semantics of
// `audit/terminal/cbc/emulator/src/pipeline.mjs` so that the productionization
// gaps listed by MA can be reproduced deterministically before they are fixed
// (see docs/design/PAN_TERMINAL_EMULATOR_INTERFACES_20261003.md). The known
// baseline gaps are:
//   B1. scanStream stops recording completed sequences after 4096 events in a
//       single call (later unknown sequences can be masked).
//   B2. rejectedRanges / opErrors / reasons are unbounded.
//   B3. an op execution error is recorded but does not sticky-degrade fidelity.
//   B4. resetBaseline() defines the new frontier from the producer frontier
//       instead of the command's actual execution position.
//   B5. resetBaseline() does not clear old gap diagnostics.
// The fixed revision removes all five (see "FIXED" comments in the final
// committed file and the pre/post evidence in
// `audit/terminal/implementation/emulator/`).
//
// PROTOCOL (stdin/stdout, binary framing, both directions):
//   [u32le total_len][u32le header_len][header UTF-8 JSON][payload bytes]
//   total_len == header_len + payload_len; frames are bounded (see
//   MAX_FRAME_BYTES / MAX_HEADER_BYTES). All 64-bit quantities (absolute byte
//   offsets) travel as decimal strings; never as JS numbers.
//
// BOUNDARY: this sidecar never sees tokens or other secrets. It is a pure
// state engine owned by the runner through a kill-on-close job guard.

import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

const PROTOCOL_VERSION = 1;
const MAX_FRAME_BYTES = 8 * 1024 * 1024;
const MAX_HEADER_BYTES = 64 * 1024;
const REQUIRED_XTERM = '6.0.0';
const REQUIRED_SERIALIZE = '0.14.0';

const DEFAULT_MAX_PENDING_TAIL = 8 * 1024;
const DEFAULT_MAX_LAG_EVENTS = 64;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// Frame I/O
// ---------------------------------------------------------------------------

function encodeFrame(header, payload = Buffer.alloc(0)) {
  const head = Buffer.from(JSON.stringify(header), 'utf8');
  if (head.length > MAX_HEADER_BYTES) {
    throw new Error(`frame header too large: ${head.length}`);
  }
  const total = head.length + payload.length;
  if (total > MAX_FRAME_BYTES) {
    throw new Error(`frame too large: ${total}`);
  }
  const buf = Buffer.alloc(8 + total);
  buf.writeUInt32LE(total, 0);
  buf.writeUInt32LE(head.length, 4);
  head.copy(buf, 8);
  payload.copy(buf, 8 + head.length);
  return buf;
}

async function writeFrame(header, payload) {
  const buf = encodeFrame(header, payload);
  await new Promise((resolve, reject) => {
    try {
      if (process.stdout.write(buf)) resolve();
      else process.stdout.once('drain', resolve);
    } catch (err) {
      reject(err);
    }
  });
}

function sendApplied(op, extra = {}) {
  return writeFrame({
    v: PROTOCOL_VERSION,
    type: 'applied',
    op,
    applied_bytes: state.appliedFrontier.toString(),
    processed_frontier: state.processedFrontier.toString(),
    pending_tail_bytes: state.pendingTail.length,
    parser_dirty: state.parserDirty,
    ...extra,
  });
}

function sendError(op, code, detail) {
  return writeFrame({
    v: PROTOCOL_VERSION,
    type: 'error',
    op,
    code,
    detail: String(detail ?? '').slice(0, 512),
  });
}

async function sendSnapshot(op) {
  const serialized = state.serializer.serialize({});
  await writeFrame(
    {
      v: PROTOCOL_VERSION,
      type: 'snapshot',
      op,
      applied_bytes: state.appliedFrontier.toString(),
      processed_frontier: state.processedFrontier.toString(),
      parsed_cursor: state.parsedCursor().toString(),
      cursors_valid: state.cursorsValid(),
      pending_tail_bytes: state.pendingTail.length,
      pending_tail_abs_start: state.pendingAbsStart === null ? null : state.pendingAbsStart.toString(),
      boundary_clean: !state.parserDirty,
      parser_dirty: state.parserDirty,
      gap_sticky: state.gapSticky,
      baseline_reset: state.resets > 0,
      baseline_frontier: state.baselineFrontier.toString(),
      fidelity: state.fidelity(),
      recovery: state.recovery(),
      feed_lag: false,
      engine_error: state.engineError,
      reasons: state.reasons(),
      reason_overflow: state.reasonOverflow,
      engine: state.engineDesc,
      rows: state.term.rows,
      cols: state.term.cols,
    },
    Buffer.from(serialized, 'utf8'),
  );
}

async function sendBarrier(op) {
  await writeFrame({
    v: PROTOCOL_VERSION,
    type: 'barrier',
    op,
    applied_bytes: state.appliedFrontier.toString(),
    processed_frontier: state.processedFrontier.toString(),
    parsed_cursor: state.parsedCursor().toString(),
    cursors_valid: state.cursorsValid(),
    pending_tail_bytes: state.pendingTail.length,
    gap_sticky: state.gapSticky,
    parser_dirty: state.parserDirty,
    fidelity: state.fidelity(),
    recovery: state.recovery(),
  });
}

// ---------------------------------------------------------------------------
// Stream scanner + conservative classifier (measured capability allowlists).
// BASELINE B1: completed events are buffered with a 4096 cap.
// ---------------------------------------------------------------------------

const ALLOWED_CSI_FINALS = new Set([...'ABCDEFGHJKLMPSTX@defmnsuct']);
const ALLOWED_ESC_FINALS = new Set([...'78=>MDEc']);
const ALLOWED_PRIVATE_MODES = new Set([1, 6, 7, 9, 45, 47, 66, 1000, 1002, 1003, 1004, 1047, 1048, 1049, 2004]);
const ALLOWED_ANSI_MODES = new Set([4]);

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
    if (['4', '10', '11', '12'].includes(code)) return null;
    return `OSC_${code || 'EMPTY'}_UNVERIFIED`;
  }
  if (ev.kind === 'dcs') return 'DCS_PAYLOAD_UNVERIFIED';
  if (ev.kind === 'str') return 'UNKNOWN_STRING_SEQUENCE_UNVERIFIED';
  return null;
}

export function scanStream(bytes, baseAbs, onSequence = null) {
  let i = 0;
  let state_ = 'text';
  let seqStart = 0;
  let bodyStart = 0;
  let utf8Remaining = 0;
  const events = []; // BASELINE B1: capped at 4096

  const pushEvent = (kind, from, to, final = '', body = '') => {
    if (events.length < 4096) {
      events.push({ kind, absFrom: baseAbs + BigInt(from), absTo: baseAbs + BigInt(to), final, body });
    }
  };

  while (i < bytes.length) {
    const b = bytes[i];
    if (utf8Remaining > 0) {
      if ((b & 0xc0) === 0x80) { utf8Remaining--; i++; continue; }
      utf8Remaining = 0;
      continue;
    }
    if (state_ === 'text') {
      if (b === 0x1b) { state_ = 'esc'; seqStart = i; i++; continue; }
      if (b === 0x9b) { state_ = 'csi'; seqStart = i; bodyStart = i + 1; i++; continue; }
      if (b === 0x9d) { state_ = 'osc'; seqStart = i; bodyStart = i + 1; i++; continue; }
      if (b === 0x90) { state_ = 'dcs'; seqStart = i; bodyStart = i + 1; i++; continue; }
      if (b === 0x98 || b === 0x9e || b === 0x9f) {
        state_ = 'str'; seqStart = i; bodyStart = i + 1; i++; continue;
      }
      if (b >= 0xc2 && b <= 0xdf) { seqStart = i; utf8Remaining = 1; i++; continue; }
      if (b >= 0xe0 && b <= 0xef) { seqStart = i; utf8Remaining = 2; i++; continue; }
      if (b >= 0xf0 && b <= 0xf4) { seqStart = i; utf8Remaining = 3; i++; continue; }
      i++; continue;
    }
    if (state_ === 'esc') {
      if (b === 0x5b) { state_ = 'csi'; bodyStart = i + 1; i++; continue; }
      if (b === 0x5d) { state_ = 'osc'; bodyStart = i + 1; i++; continue; }
      if (b === 0x50) { state_ = 'dcs'; bodyStart = i + 1; i++; continue; }
      if (b === 0x58 || b === 0x5e || b === 0x5f) { state_ = 'str'; i++; continue; }
      if (b === 0x1b) { seqStart = i; i++; continue; }
      if (b >= 0x20 && b <= 0x2f) { i++; continue; }
      pushEvent('esc', seqStart, i + 1, String.fromCharCode(b));
      state_ = 'text'; i++; continue;
    }
    if (state_ === 'csi') {
      if (b >= 0x40 && b <= 0x7e) {
        const body = Buffer.from(bytes.slice(bodyStart, i)).toString('latin1');
        pushEvent('csi', seqStart, i + 1, String.fromCharCode(b), body);
        state_ = 'text'; i++; continue;
      }
      i++; continue;
    }
    if (state_ === 'osc') {
      if (b === 0x07 || b === 0x9c) {
        const body = Buffer.from(bytes.slice(bodyStart, i)).toString('latin1');
        pushEvent('osc', seqStart, i + 1, '', body);
        state_ = 'text'; i++; continue;
      }
      if (b === 0x1b && bytes[i + 1] === 0x5c) {
        const body = Buffer.from(bytes.slice(bodyStart, i)).toString('latin1');
        pushEvent('osc', seqStart, i + 2, '', body);
        state_ = 'text'; i += 2; continue;
      }
      i++; continue;
    }
    if (b === 0x9c) {
      pushEvent(state_ === 'dcs' ? 'dcs' : 'str', seqStart, i + 1);
      state_ = 'text'; i++; continue;
    }
    if (b === 0x1b && bytes[i + 1] === 0x5c) {
      pushEvent(state_ === 'dcs' ? 'dcs' : 'str', seqStart, i + 2);
      state_ = 'text'; i += 2; continue;
    }
    i++; continue;
  }

  const clean = state_ === 'text' && utf8Remaining === 0 ? bytes.length : seqStart;
  for (const ev of events) {
    if (onSequence) {
      const reason = onSequence(ev);
      if (reason) recordReason(reason);
    }
  }
  return { cleanLen: clean, endState: state_, utf8Remaining };
}

// ---------------------------------------------------------------------------
// Engine state
// ---------------------------------------------------------------------------

const state = {
  term: null,
  serializer: null,
  engineDesc: '',
  scrollback: 1000,
  maxPendingTail: DEFAULT_MAX_PENDING_TAIL,
  maxLagEvents: DEFAULT_MAX_LAG_EVENTS,

  processedFrontier: 0n, // end offset of every processed feed (incl. held back)
  appliedFrontier: 0n,   // end offset actually handed to the parser
  pendingTail: Buffer.alloc(0),
  pendingAbsStart: null,
  parserDirty: false,
  engineError: null,
  gapSticky: false,

  resets: 0,
  baselineFrontier: 0n,
  detectedReasons: new Set(),
  reasonOverflow: 0,
  rejectedRanges: [], // BASELINE B2: unbounded
  opErrors: [],       // BASELINE B2: unbounded
  opErrorOverflow: 0,

  counters: {
    feed: 0, resize: 0, reset: 0, snapshot: 0, barrier: 0, stall: 0,
    injected_error: 0, rejected: 0, late: 0,
  },
};

function recordReason(reason) {
  if (reason) state.detectedReasons.add(reason); // BASELINE B2: unbounded set
}

state.parsedCursor = function parsedCursor() {
  if (state.gapSticky) return null; // D4 semantics: no trustworthy alignment while a gap is sticky
  if (state.pendingTail.length > 0) return state.pendingAbsStart;
  return state.appliedFrontier;
};

state.cursorsValid = function cursorsValid() {
  return !state.gapSticky;
};

state.fidelity = function fidelity() {
  return state.reasons().length === 0 ? 'full' : 'partial';
};

state.recovery = function recovery() {
  if (state.parserDirty || state.resets > 0 || state.gapSticky) return 'partial';
  return 'full';
};

state.reasons = function reasons() {
  const out = [];
  if (state.parserDirty) out.push('PENDING_PARSER_STATE_UNSERIALIZED');
  if (state.resets > 0) out.push('BASELINE_RESET_FRESH_VIEW');
  if (state.gapSticky) out.push('SOURCE_GAP_STICKY');
  for (const r of state.detectedReasons) out.push(r);
  return out;
};

// ---------------------------------------------------------------------------
// Feed application (hold-back protocol)
// ---------------------------------------------------------------------------

function writeToTerm(bytes) {
  return new Promise((resolve, reject) => {
    try {
      state.term.write(bytes, () => resolve());
    } catch (err) {
      reject(err);
    }
  });
}

async function flushPendingForce() {
  if (state.pendingTail.length === 0) return;
  const tail = state.pendingTail;
  const tailEnd = state.pendingAbsStart + BigInt(tail.length);
  state.pendingTail = Buffer.alloc(0);
  state.pendingAbsStart = null;
  await writeToTerm(tail);
  state.appliedFrontier = tailEnd;
}

async function applyFeed(absStart, absEnd, bytes) {
  state.counters.feed++;
  const joinEnd = state.pendingAbsStart === null
    ? state.processedFrontier
    : state.pendingAbsStart + BigInt(state.pendingTail.length);
  let merged;
  let mergedAbsStart;
  if (state.pendingTail.length > 0 && absStart === joinEnd && !state.gapSticky) {
    merged = Buffer.concat([state.pendingTail, bytes]);
    mergedAbsStart = state.pendingAbsStart;
  } else {
    if (state.pendingTail.length > 0) {
      // Absolute discontinuity: we cannot splice across a hole safely.
      state.gapSticky = true;
      await flushPendingForce();
    }
    if (absStart !== state.processedFrontier) {
      state.gapSticky = true; // defensive: source offsets are not contiguous
    }
    merged = bytes;
    mergedAbsStart = absStart;
  }
  if (state.parserDirty) {
    state.pendingTail = Buffer.alloc(0);
    state.pendingAbsStart = null;
    await writeToTerm(merged);
    state.appliedFrontier = mergedAbsStart + BigInt(merged.length);
  } else {
    const scan = scanStream(merged, mergedAbsStart, classifySequence);
    if (scan.cleanLen < merged.length) {
      if (merged.length - scan.cleanLen > state.maxPendingTail) {
        state.parserDirty = true; // sticky until explicit reset
        state.pendingTail = Buffer.alloc(0);
        state.pendingAbsStart = null;
        await writeToTerm(merged);
        state.appliedFrontier = mergedAbsStart + BigInt(merged.length);
      } else {
        state.pendingTail = Buffer.from(merged.subarray(scan.cleanLen));
        state.pendingAbsStart = mergedAbsStart + BigInt(scan.cleanLen);
        if (scan.cleanLen > 0) {
          await writeToTerm(merged.subarray(0, scan.cleanLen));
        }
        state.appliedFrontier = mergedAbsStart + BigInt(scan.cleanLen);
      }
    } else {
      state.pendingTail = Buffer.alloc(0);
      state.pendingAbsStart = null;
      if (merged.length > 0) await writeToTerm(merged);
      state.appliedFrontier = mergedAbsStart + BigInt(merged.length);
    }
  }
  if (absEnd > state.processedFrontier) state.processedFrontier = absEnd;
}

function doReset() {
  state.term.reset();
  state.pendingTail = Buffer.alloc(0);
  state.pendingAbsStart = null;
  state.parserDirty = false;
  // BASELINE B4: uses the producer frontier, not the command execution position.
  state.appliedFrontier = state.processedFrontier;
  state.baselineFrontier = state.processedFrontier;
  state.resets++;
  return state.baselineFrontier;
}

// ---------------------------------------------------------------------------
// Command loop
// ---------------------------------------------------------------------------

let initialized = false;
let injectErrorTarget = null;
let shuttingDown = false;

async function handleFrame(header, payload) {
  const type = header.type;
  if (type === 'hello') {
    if (initialized) return sendError(null, 'already-initialized', 'duplicate hello');
    const cols = Number(header.cols) || 80;
    const rows = Number(header.rows) || 24;
    state.scrollback = Number(header.scrollback) || 1000;
    state.maxPendingTail = Number(header.max_pending_tail) || DEFAULT_MAX_PENDING_TAIL;
    state.maxLagEvents = Number(header.max_lag_events) || DEFAULT_MAX_LAG_EVENTS;
    const startCursor = BigInt(header.start_cursor || '0');
    state.processedFrontier = startCursor;
    state.appliedFrontier = startCursor;
    state.baselineFrontier = startCursor;
    // allowProposedApi is required by @xterm/addon-serialize 0.14.0 (measured:
    // serialize() throws "You must set the allowProposedApi option" otherwise).
    state.term = new Terminal({ cols, rows, scrollback: state.scrollback, allowProposedApi: true });
    state.serializer = new SerializeAddon();
    state.term.loadAddon(state.serializer);
    state.engineDesc = `xterm-headless/${REQUIRED_XTERM}+serialize/${REQUIRED_SERIALIZE}`;
    initialized = true;
    await writeFrame({
      v: PROTOCOL_VERSION,
      type: 'ready',
      pid: process.pid,
      engine: state.engineDesc,
      xterm_version: REQUIRED_XTERM,
      serialize_version: REQUIRED_SERIALIZE,
      node_version: process.version,
      cols,
      rows,
      scrollback: state.scrollback,
    });
    return;
  }
  if (!initialized) {
    return writeFrame({ v: PROTOCOL_VERSION, type: 'fatal', code: 'not-initialized', detail: `first frame must be hello, got ${type}` });
  }
  if (header.v !== PROTOCOL_VERSION) {
    return writeFrame({ v: PROTOCOL_VERSION, type: 'fatal', code: 'bad-version', detail: `v=${header.v}` });
  }
  const op = typeof header.op === 'number' ? header.op : null;
  try {
    switch (type) {
      case 'feed': {
        if (injectErrorTarget === 'next-feed') {
          injectErrorTarget = null;
          state.counters.injected_error++;
          throw new Error('injected feed error (test hook)');
        }
        await applyFeed(BigInt(header.abs_start), BigInt(header.abs_end), payload);
        await sendApplied(op, { abs_start: header.abs_start, abs_end: header.abs_end });
        break;
      }
      case 'resize': {
        state.term.resize(Number(header.cols), Number(header.rows));
        await sendApplied(op);
        break;
      }
      case 'reset': {
        const baseline = doReset();
        await sendApplied(op, { baseline_frontier: baseline.toString(), resets: state.resets });
        break;
      }
      case 'snapshot': {
        state.counters.snapshot++;
        await sendSnapshot(op);
        break;
      }
      case 'barrier': {
        state.counters.barrier++;
        await sendBarrier(op);
        break;
      }
      case 'test_stall': {
        state.counters.stall++;
        await sleep(Math.max(0, Math.min(Number(header.ms) | 0, 10000)));
        await sendApplied(op);
        break;
      }
      case 'test_inject_error': {
        injectErrorTarget = String(header.target || 'next-feed');
        await sendApplied(op);
        break;
      }
      case 'shutdown': {
        await sendApplied(op);
        state.term.dispose();
        shuttingDown = true;
        process.stdin.destroy();
        break;
      }
      default:
        await sendError(op, 'unknown-op', `unknown frame type ${type}`);
    }
  } catch (err) {
    // BASELINE B3: recorded but does NOT sticky-degrade fidelity.
    state.opErrors.push({ op, type, error: String(err?.message || err).slice(0, 256) });
    if (state.opErrors.length > 64) {
      state.opErrors.shift();
      state.opErrorOverflow++;
    }
    state.counters.late++;
    await sendError(op, 'op-error', String(err?.message || err));
  }
}

async function main() {
  let buffer = Buffer.alloc(0);
  for await (const chunk of process.stdin) {
    buffer = Buffer.concat([buffer, chunk]);
    while (buffer.length >= 8) {
      const total = buffer.readUInt32LE(0);
      const headerLen = buffer.readUInt32LE(4);
      if (total > MAX_FRAME_BYTES || headerLen > total || headerLen > MAX_HEADER_BYTES) {
        await writeFrame({ v: PROTOCOL_VERSION, type: 'fatal', code: 'frame-too-large', detail: `total=${total} header=${headerLen}` });
        process.exitCode = 4;
        return;
      }
      if (buffer.length < 8 + total) break;
      const header = JSON.parse(buffer.subarray(8, 8 + headerLen).toString('utf8'));
      const payload = Buffer.from(buffer.subarray(8 + headerLen, 8 + total));
      buffer = buffer.subarray(8 + total);
      await handleFrame(header, payload);
      if (shuttingDown) return;
    }
  }
  // stdin EOF: parent went away -> exit without touching the guard (the job
  // guard is owned by the runner and kills us on crash anyway).
  process.exitCode = 0;
}

// ---------------------------------------------------------------------------
// Engine bootstrap: delay imports so missing dependencies are reported as a
// bounded fatal frame (instead of an unparsed module-loader stack trace).
// ---------------------------------------------------------------------------

function readPkgVersion(specifier) {
  const url = new URL(`./node_modules/${specifier}/package.json`, import.meta.url);
  return JSON.parse(fs.readFileSync(url, 'utf8')).version;
}

let Terminal;
let SerializeAddon;
try {
  const headlessMod = await import('@xterm/headless');
  Terminal = headlessMod.Terminal ?? headlessMod.default?.Terminal;
  const serializeMod = await import('@xterm/addon-serialize');
  SerializeAddon = serializeMod.SerializeAddon ?? serializeMod.default?.SerializeAddon;
  if (typeof Terminal !== 'function' || typeof SerializeAddon !== 'function') {
    throw new Error('xterm modules loaded but expected exports are missing');
  }
} catch (err) {
  await writeFrame({ v: PROTOCOL_VERSION, type: 'fatal', code: 'dependency-missing', detail: String(err?.message || err) });
  process.exit(3);
}
try {
  const xtermVersion = readPkgVersion('@xterm/headless');
  const serializeVersion = readPkgVersion('@xterm/addon-serialize');
  if (xtermVersion !== REQUIRED_XTERM || serializeVersion !== REQUIRED_SERIALIZE) {
    await writeFrame({
      v: PROTOCOL_VERSION,
      type: 'fatal',
      code: 'dependency-version-mismatch',
      detail: `headless=${xtermVersion} serialize=${serializeVersion} required=${REQUIRED_XTERM}/${REQUIRED_SERIALIZE}`,
    });
    process.exit(3);
  }
} catch (err) {
  await writeFrame({ v: PROTOCOL_VERSION, type: 'fatal', code: 'dependency-unreadable', detail: String(err?.message || err) });
  process.exit(3);
}

try {
  await main();
} catch (err) {
  try {
    await writeFrame({ v: PROTOCOL_VERSION, type: 'fatal', code: 'sidecar-exception', detail: String(err?.message || err) });
  } catch {
    /* stdout already broken */
  }
  process.exitCode = 5;
}

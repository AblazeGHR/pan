// Shared headless-xterm helpers for the snapshot spike.
// Pinned: @xterm/headless 6.0.0 + @xterm/addon-serialize 0.14.0 (see package.json).
//
// NOTE (honesty): comparisons in this spike are headless-vs-headless internal
// consistency. They say nothing about real-browser rendering compatibility;
// the browser is out of scope for this spike.

import headlessPkg from '@xterm/headless';
import serializePkg from '@xterm/addon-serialize';

export const { Terminal } = headlessPkg;
export const { SerializeAddon } = serializePkg;

const encoder = new TextEncoder();
export const utf8 = (s) => encoder.encode(s);

export function makeTerm(cols = 80, rows = 24, { scrollback = 1000 } = {}) {
  const term = new Terminal({ cols, rows, scrollback, allowProposedApi: true });
  const serializer = new SerializeAddon();
  term.loadAddon(serializer);
  return { term, serializer };
}

/** Write one chunk and wait until xterm confirms it was applied. */
export function writeApplied(term, bytes) {
  const data = typeof bytes === 'string' ? utf8(bytes) : bytes;
  return new Promise((resolve) => term.write(data, () => resolve()));
}

export async function writeAll(term, bytes) {
  await writeApplied(term, bytes);
}

export function screenText(term) {
  const buf = term.buffer.active;
  const lines = [];
  for (let y = 0; y < term.rows; y++) {
    const line = buf.getLine(buf.baseY + y);
    lines.push(line ? line.translateToString(true) : '');
  }
  return lines.join('\n');
}

export function viewportAndScrollbackText(term, maxScrollback = 200) {
  const buf = term.buffer.active;
  const start = Math.max(0, buf.baseY - maxScrollback);
  const lines = [];
  for (let y = start; y < buf.baseY + term.rows; y++) {
    const line = buf.getLine(y);
    lines.push(line ? line.translateToString(true) : '');
  }
  return lines.join('\n');
}

export function modesOf(term) {
  const m = term.modes;
  return {
    applicationCursorKeysMode: m.applicationCursorKeysMode,
    applicationKeypadMode: m.applicationKeypadMode,
    bracketedPasteMode: m.bracketedPasteMode,
    insertMode: m.insertMode,
    originMode: m.originMode,
    reverseWraparoundMode: m.reverseWraparoundMode,
    sendFocusMode: m.sendFocusMode,
    synchronizedOutputMode: m.synchronizedOutputMode,
    wraparoundMode: m.wraparoundMode,
    mouseTrackingMode: m.mouseTrackingMode,
  };
}

export function stateOf(term, serializer) {
  const buf = term.buffer.active;
  return {
    activeType: buf.type,
    cursorX: buf.cursorX,
    cursorY: buf.cursorY,
    baseY: buf.baseY,
    viewportY: buf.viewportY,
    length: buf.length,
    cols: term.cols,
    rows: term.rows,
    modes: modesOf(term),
    screenText: screenText(term),
    scrollbackText: viewportAndScrollbackText(term),
    serialized: serializer.serialize(),
  };
}

/**
 * Restore a snapshot string into a fresh terminal of the same dimensions.
 * The caller is responsible for feeding the snapshot's `pending_tail` (if any)
 * before continuing the live stream — that is what keeps parser state lossless
 * across the snapshot boundary.
 */
export async function restoreFrom(serialized, cols, rows, { scrollback = 1000 } = {}) {
  const { term, serializer } = makeTerm(cols, rows, { scrollback });
  await writeAll(term, serialized);
  return { term, serializer };
}

export function diffStates(a, b) {
  const diffs = [];
  for (const key of ['activeType', 'cursorX', 'cursorY', 'baseY', 'length', 'cols', 'rows']) {
    if (a[key] !== b[key]) diffs.push({ key, a: a[key], b: b[key] });
  }
  for (const key of Object.keys(a.modes)) {
    if (a.modes[key] !== b.modes[key]) diffs.push({ key: `modes.${key}`, a: a.modes[key], b: b.modes[key] });
  }
  if (a.screenText !== b.screenText) diffs.push({ key: 'screenText', a: a.screenText, b: b.screenText });
  if (a.serialized !== b.serialized) diffs.push({ key: 'serialized', a: a.serialized, b: b.serialized });
  return diffs;
}

export function bytesToBase64(bytes) {
  return Buffer.from(bytes).toString('base64');
}

export function base64ToBytes(b64) {
  return new Uint8Array(Buffer.from(b64, 'base64'));
}

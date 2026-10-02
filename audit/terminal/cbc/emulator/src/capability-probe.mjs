// Raw capability probe for @xterm/headless 6.0.0 + @xterm/addon-serialize 0.14.0.
//
// Ground-truth questions (answered empirically, no pipeline involved):
//   P1  does serialize() restore the ALTERNATE buffer switch itself?
//   P2  are pending (partial) CSI / OSC parser states captured by serialize()?
//   P3  are UTF-8 bytes split across write() calls decoded correctly?
//   P4  is the scroll region (DECSTBM) captured?
//   P5  which modes survive a serialize/restore round trip?
//   P6  does write(data, cb) fire the callback after the data was applied?
//
// Run: node src/capability-probe.mjs   (writes ../evidence/capabilities.json)

// @xterm/headless 与 addon-serialize 是 CommonJS 包：ESM 下用默认导入再解构。
import headlessPkg from '@xterm/headless';
import serializePkg from '@xterm/addon-serialize';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const { Terminal } = headlessPkg;
const { SerializeAddon } = serializePkg;

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE = join(HERE, '..', 'evidence');

const enc = new TextEncoder();

function makeTerm(cols = 40, rows = 10, opts = {}) {
  const t = new Terminal({ cols, rows, scrollback: 200, allowProposedApi: true, ...opts });
  const s = new SerializeAddon();
  t.loadAddon(s);
  return { t, s };
}

function writeSync(t, data) {
  return new Promise((resolve) => {
    const bytes = typeof data === 'string' ? enc.encode(data) : data;
    t.write(bytes, () => resolve());
  });
}

function screenText(t) {
  const b = t.buffer.active;
  const lines = [];
  for (let y = 0; y < t.rows; y++) {
    const line = b.getLine(b.baseY + y);
    lines.push(line ? line.translateToString(true) : '');
  }
  return lines.join('\n');
}

function stateOf(t, s) {
  const b = t.buffer.active;
  return {
    activeType: b.type,
    cursorX: b.cursorX,
    cursorY: b.cursorY,
    baseY: b.baseY,
    viewportY: b.viewportY,
    length: b.length,
    cols: t.cols,
    rows: t.rows,
    modes: {
      applicationCursorKeysMode: t.modes.applicationCursorKeysMode,
      applicationKeypadMode: t.modes.applicationKeypadMode,
      bracketedPasteMode: t.modes.bracketedPasteMode,
      insertMode: t.modes.insertMode,
      originMode: t.modes.originMode,
      reverseWraparoundMode: t.modes.reverseWraparoundMode,
      sendFocusMode: t.modes.sendFocusMode,
      synchronizedOutputMode: t.modes.synchronizedOutputMode,
      wraparoundMode: t.modes.wraparoundMode,
      mouseTrackingMode: t.modes.mouseTrackingMode,
    },
    screenText: screenText(t),
    serialized: s.serialize(),
  };
}

// Restore a serialized snapshot into a fresh terminal of the same size.
async function restore(serialized, cols, rows) {
  const { t, s } = makeTerm(cols, rows);
  await writeSync(t, serialized);
  return { t, s };
}

const results = {};

// --- P1: alternate buffer switch
{
  const { t, s } = makeTerm();
  await writeSync(t, 'PRIMARY-LINE-1\r\nPRIMARY-LINE-2');
  await writeSync(t, '\x1b[?1049h');            // enter alt screen
  await writeSync(t, 'ALT-CONTENT');
  const snap = stateOf(t, s);
  const { t: r, s: rs } = await restore(snap.serialized, t.cols, t.rows);
  results.P1_alt_buffer = {
    sourceActiveType: snap.activeType,
    snapshotHas1049h: snap.serialized.includes('\x1b[?1049h'),
    restoredActiveType: r.buffer.active.type,
    restoredScreen: screenText(r),
    restoredSerializedHead: rs.serialize().slice(0, 160),
  };
}

// --- P2a: partial CSI state captured?
{
  const { t, s } = makeTerm();
  await writeSync(t, 'AB\x1b[3');               // pending "ESC [ 3"
  const snapPending = s.serialize();
  const { t: r } = await restore(snapPending, t.cols, t.rows);
  await writeSync(r, '1mRED');                  // completes "ESC[31m"
  const { t: ref } = makeTerm();
  await writeSync(ref, 'AB\x1b[31mRED');
  results.P2a_partial_csi = {
    pendingSnapshotTail: JSON.stringify(snapPending.slice(-12)),
    restoredAfterContinuation: screenText(r),
    reference: screenText(ref),
    matchesReference: screenText(r) === screenText(ref),
  };
}

// --- P2b: partial OSC state captured?
{
  const { t, s } = makeTerm();
  await writeSync(t, 'X\x1b]0;title-');         // pending OSC
  const snap = s.serialize();
  const { t: r } = await restore(snap, t.cols, t.rows);
  await writeSync(r, 'done\x07Y');              // completes OSC, then text
  const { t: ref } = makeTerm();
  await writeSync(ref, 'X\x1b]0;title-done\x07Y');
  results.P2b_partial_osc = {
    restoredAfterContinuation: screenText(r),
    reference: screenText(ref),
    matchesReference: screenText(r) === screenText(ref),
  };
}

// --- P3: UTF-8 split across raw-byte writes
{
  const { t, s } = makeTerm();
  const bytes = enc.encode('中A😀B');           // 3-byte + 4-byte sequences
  await writeSync(t, bytes.slice(0, 1));        // partial 中
  await writeSync(t, bytes.slice(1, 5));        // rest of 中 + A + partial 😀
  await writeSync(t, bytes.slice(5));
  const { t: ref } = makeTerm();
  await writeSync(ref, '中A😀B');
  results.P3_utf8_split = {
    splitScreen: screenText(t),
    reference: screenText(ref),
    matchesReference: screenText(t) === screenText(ref),
    serialized: s.serialize(),
  };
}

// --- P3b: same content via JS string split (does string write keep state?)
{
  const { t } = makeTerm();
  const s1 = '中A😀B';
  await writeSync(t, s1.slice(0, 1));
  await writeSync(t, s1.slice(1, 3));
  await writeSync(t, s1.slice(3));
  const { t: ref } = makeTerm();
  await writeSync(ref, '中A😀B');
  results.P3b_utf8_string_split = {
    splitScreen: screenText(t),
    matchesReference: screenText(t) === screenText(ref),
  };
}

// --- P4: scroll region captured?
{
  const { t, s } = makeTerm(40, 10);
  for (let i = 1; i <= 10; i++) await writeSync(t, `L${String(i).padStart(2, '0')}\r\n`);
  await writeSync(t, '\x1b[3;6r');              // scroll region rows 3..6
  await writeSync(t, '\x1b[6;1H\x1b[M');        // delete one line inside region
  const snap = s.serialize();
  const { t: r } = await restore(snap, t.cols, t.rows);
  // Apply the same edit to the restored terminal; if DECSTBM survived, the same
  // line should be deleted; otherwise the cursor position behaves differently.
  await writeSync(r, '\x1b[6;1H\x1b[M');
  const { t: ref } = makeTerm(40, 10);
  for (let i = 1; i <= 10; i++) await writeSync(ref, `L${String(i).padStart(2, '0')}\r\n`);
  await writeSync(ref, '\x1b[3;6r');
  await writeSync(ref, '\x1b[6;1H\x1b[M');
  await writeSync(ref, '\x1b[6;1H\x1b[M');
  results.P4_scroll_region = {
    sourceScreenAfterEdit: screenText(t),
    restoredAfterSameEdit: screenText(r),
    referenceAfterSameEdits: screenText(ref),
    matchesReference: screenText(r) === screenText(ref),
    snapshotContainsDECSTBM: /\x1b\[[0-9;]*r/.test(snap),
  };
}

// --- P5: modes round trip
{
  const { t, s } = makeTerm();
  await writeSync(t,
    '\x1b[?1h\x1b[?66h\x1b[?2004h\x1b[4h\x1b[?6h\x1b[?45h\x1b[?1004h\x1b[?1002h\x1b[?2026h\x1b[?7l');
  const before = { ...t.modes };
  const snap = s.serialize();
  const { t: r } = await restore(snap, t.cols, t.rows);
  const after = { ...r.modes };
  const flags = ['applicationCursorKeysMode', 'applicationKeypadMode', 'bracketedPasteMode',
    'insertMode', 'originMode', 'reverseWraparoundMode', 'sendFocusMode',
    'synchronizedOutputMode', 'wraparoundMode', 'mouseTrackingMode'];
  const diff = {};
  for (const f of flags) diff[f] = { source: before[f], restored: after[f], ok: before[f] === after[f] };
  results.P5_modes_roundtrip = { diff, snapshotModesTail: JSON.stringify(snap.slice(-80)) };
}

// --- P6: write callback timing vs onWriteParsed
{
  const { t } = makeTerm();
  const events = [];
  t.onWriteParsed(() => events.push('parsed'));
  const order = [];
  order.push('before-write');
  await new Promise((resolve) => {
    t.write('HELLO', () => { order.push('write-cb'); resolve(); });
    order.push('after-write-call');
  });
  // give the parsed event a tick to flush
  await new Promise((r) => setTimeout(r, 10));
  results.P6_write_callback = { order, parsedEvents: events.length };
}

mkdirSync(EVIDENCE, { recursive: true });
writeFileSync(join(EVIDENCE, 'capabilities.json'),
  JSON.stringify({ xtermHeadless: '6.0.0', addonSerialize: '0.14.0', results }, null, 2));
console.log(JSON.stringify({ ok: true, out: join(EVIDENCE, 'capabilities.json') }));

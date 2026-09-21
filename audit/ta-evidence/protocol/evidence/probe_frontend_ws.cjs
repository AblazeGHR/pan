/**
 * Read-only deterministic probe for the frontend WS + stream reducer contract.
 *
 * It bundles the *real* worktree TypeScript (ws.ts + useWebSocket.ts) with the
 * sibling worktree's esbuild (read-only) and executes it in Node with stubs for
 * React and the zustand stores.  `appendEventToMessages` / `extractBlocks` are
 * internal to useWebSocket.ts, so a build-time plugin appends an export
 * statement to that one file in memory only; no product file is modified.
 */
const path = require('path');
const fs = require('fs');

const WEB = path.resolve(__dirname, '..', 'packages', 'web');
const ESBUILD = require(path.resolve(
  'D:/project/pan-worktrees/frontend-reaudit-history-ds-20260921',
  'packages/web/node_modules/.pnpm/esbuild@0.21.5/node_modules/esbuild',
));

const STUBS = {
  '@/types': '__stub_types',
  '@/stores/sessionStore': '__stub_sessionStore',
  '@/stores/workerStore': '__stub_workerStore',
  '@/stores/uiStore': '__stub_uiStore',
  '@/stores/queueStore': '__stub_queueStore',
  '@/stores/appSettingsStore': '__stub_appSettingsStore',
  '@/stores/adapterStore': '__stub_adapterStore',
  '@/demo/mockBackend': '__stub_mockBackend',
  react: '__stub_react',
};

const stubSources = {
  __stub_types: 'export {};',
  __stub_react: 'export const useEffect = () => {}; export default {};',
  __stub_mockBackend: 'export const isMockMode = () => false;',
  __stub_sessionStore: 'export const useSessionStore = { getState: () => ({}) };',
  __stub_workerStore: 'export const useWorkerStore = { getState: () => ({}) };',
  __stub_uiStore: 'export const useUIStore = { getState: () => ({}) };',
  __stub_queueStore: 'export const useQueueStore = { getState: () => ({}) };',
  __stub_appSettingsStore: 'export const useAppSettingsStore = { getState: () => ({}) };',
  __stub_adapterStore: 'export const useAdapterStore = { getState: () => ({}) };',
};

const appendExportPlugin = {
  name: 'append-reexport',
  setup(build) {
    build.onLoad({ filter: /hooks[\\/]useWebSocket\.ts$/ }, (args) => {
      const src = fs.readFileSync(args.path, 'utf8')
        + '\nexport { appendEventToMessages, extractBlocks };\n';
      return { contents: src, loader: 'ts' };
    });
    build.onResolve({ filter: /^@\// }, (args) => {
      const key = args.path;
      if (STUBS[key]) return { path: STUBS[key], namespace: 'stub' };
      if (key === '@/services/ws') {
        return { path: path.join(WEB, 'src/services/ws.ts') };
      }
      if (key === '@/utils/messageIdentity') {
        return { path: path.join(WEB, 'src/utils/messageIdentity.ts') };
      }
      return { path: STUBS['@/types'], namespace: 'stub' };
    });
    build.onResolve({ filter: /^react$/ }, () => ({ path: '__stub_react', namespace: 'stub' }));
    build.onLoad({ filter: /.*/, namespace: 'stub' }, (args) => ({
      contents: stubSources[args.path] || 'export {};',
      loader: 'js',
    }));
  },
};

async function build() {
  const entry = [
    "import { appendEventToMessages, extractBlocks } from '" +
      path.join(WEB, 'src/hooks/useWebSocket.ts').replace(/\\/g, '/') + "';",
    "import { wsClient } from '" +
      path.join(WEB, 'src/services/ws.ts').replace(/\\/g, '/') + "';",
    'export { appendEventToMessages, extractBlocks, wsClient };',
  ].join('\n');
  const result = await ESBUILD.build({
    stdin: { contents: entry, resolveDir: WEB, loader: 'ts' },
    bundle: true,
    write: false,
    format: 'cjs',
    platform: 'node',
    target: 'node18',
    logLevel: 'silent',
    plugins: [appendExportPlugin],
  });
  return result.outputFiles[0].text;
}

function loadBundle(code) {
  const Module = require('module');
  const m = new Module('probe-bundle');
  m.filename = path.join(__dirname, 'probe-bundle.cjs');
  m.paths = Module._nodeModulePaths(__dirname);
  m._compile(code, m.filename);
  return m.exports;
}

const out = {};

function blocks(event) {
  return loadBundleAppend.extractBlocks(event);
}

function append(sessionId, event, initial, scope) {
  return loadBundleAppend.appendEventToMessages(sessionId, event, initial, scope);
}

let loadBundleAppend = null;

// ws.ts constructs its singleton at module load and reads `location`; install
// the browser-ish globals before the bundle is evaluated.
global.location = { protocol: 'http:', host: 'localhost' };
let currentSocket = null;
global.WebSocket = function FakeWebSocket() {
  currentSocket = {
    readyState: 1, onopen: null, onmessage: null, onclose: null, onerror: null,
    send() {}, close() {},
  };
  return currentSocket;
};
global.WebSocket.OPEN = 1;
global.WebSocket.CONNECTING = 0;

// ── C: frontend JSON projection vs backend canonical projection ─────────────
function projectionProbe() {
  const adapterShapedFloat = {
    type: 'assistant',
    final: true,
    item_id: 'x1',
    message: { content: [{ type: 'tool_use', name: 'Sleep', input: { seconds: 30.0 } }] },
  };
  const toolBlock = blocks(adapterShapedFloat).find((b) => b.role === 'tool');
  return {
    frontend_tool_block: toolBlock,
    backend_canonical: 'Sleep({"seconds":30.0})',
    mismatch: toolBlock && toolBlock.content !== 'Sleep({"seconds":30.0})',
  };
}

// ── B: reducer sequences ────────────────────────────────────────────────────
function reducerProbe() {
  const sid = 's1';
  const results = {};

  const delta = (itemId, text, cumulative, turnId) => ({
    type: 'content.part', role: 'assistant', delta: true, item_id: itemId,
    turn_id: turnId, part: { type: 'text', text }, stream_text: cumulative,
    message: { content: [{ type: 'text', text }] },
  });
  const finalMsg = (itemId, text, turnId) => ({
    type: 'assistant', final: true, item_id: itemId, turn_id: turnId,
    message: { content: [{ type: 'text', text }] },
  });
  const scope = { workerId: 'w1', generation: 0, taskSeq: 1 };

  // B1: single item delta then final prefix -> one row
  let msgs = append(sid, delta('a1', 'Hel', 'Hel', 't1'), [], scope);
  msgs = append(sid, finalMsg('a1', 'Hello', 't1'), msgs, scope);
  results.b1_single_item_prefix = msgs.map((m) => [m.role, m.content, m.nativeItemId]);

  // B2: codex aggregate result vs per-item rows (frontend reconcile substitute)
  //   live: two assistant rows, then worker.result carries the aggregate.
  let live = append(sid + '-agg', finalMsg('a1', 'first half ', 't1'), [], scope);
  live = append(sid + '-agg', finalMsg('a2', 'second half', 't1'), live, scope);
  results.b2_live_before_result = live.map((m) => [m.role, m.content, m.nativeItemId]);

  // B3: cbc-style two identical non-final assistant messages
  let cbc = append(sid + '-cbc', {
    type: 'assistant', message: { content: [{ type: 'text', text: 'same' }] },
  }, [], scope);
  cbc = append(sid + '-cbc', {
    type: 'assistant', message: { content: [{ type: 'text', text: 'same' }] },
  }, cbc, scope);
  results.b3_cbc_identical_non_final = cbc.map((m) => [m.role, m.content, m.nativeItemId]);

  // B4: codex unknown native item rendered as tool
  results.b4_unknown_native_item = blocks({
    type: 'codex.item.completed',
    item: { id: 'u1', type: 'futureNativeItem', summary: 'kept' },
  });

  // B5: compound item (thinking + text) then a bare assistant final replace
  const compound = {
    type: 'assistant', final: true, item_id: 'c1', turn_id: 't1',
    message: { content: [
      { type: 'thinking', thinking: 'think' },
      { type: 'text', text: 'answer' },
    ] },
  };
  let comp = append(sid + '-comp', compound, [], scope);
  comp = append(sid + '-comp', finalMsg('c1', 'answer2', 't1'), comp, scope);
  results.b5_compound_then_replace = comp.map((m) => [m.role, m.content, m.nativeItemId]);

  // B6: same turn, different item ids, prefix text (alias risk)
  let alias = append(sid + '-alias', finalMsg('x1', 'Hello', 't1'), [], scope);
  alias = append(sid + '-alias', finalMsg('x2', 'Hello world', 't1'), alias, scope);
  results.b6_same_turn_prefix_items = alias.map((m) => [m.role, m.content, m.nativeItemId]);

  // B7: same turn, different items, second is NOT a prefix of the first
  let alias2 = append(sid + '-alias2', finalMsg('y1', 'Hello world', 't1'), [], scope);
  alias2 = append(sid + '-alias2', finalMsg('y2', 'Different', 't1'), alias2, scope);
  results.b7_same_turn_nonprefix_items = alias2.map((m) => [m.role, m.content, m.nativeItemId]);

  return results;
}

// ── D: ws.ts cursor behaviour ───────────────────────────────────────────────
function wsProbe() {
  const wsClient = loadBundleAppend.wsClient;
  const seen = [];
  const emitted = [];

  // `emit()` (control events) bypasses the '*' wildcard handler, so register the
  // concrete event names explicitly.
  for (const name of ['resync_required', 'server_epoch_changed', 'open']) {
    wsClient.on(name, (e) => emitted.push({ type: name, reason: e.reason || null }));
  }
  wsClient.onAll((e) => seen.push({ type: e.type, reason: e.reason || null }));

  const feed = (frame) => currentSocket.onmessage({ data: JSON.stringify(frame) });

  wsClient.connect();
  currentSocket.onopen && currentSocket.onopen();

  const results = {};

  // contiguous delivery + coalesced source range must not resync
  feed({ type: 'worker.stream', eventEpoch: 'E', eventSeq: 1, deliveryEpoch: 'E', deliverySeq: 1, sourceCursorStart: 1, sourceCursorEnd: 1 });
  feed({ type: 'worker.stream', eventEpoch: 'E', eventSeq: 3, deliveryEpoch: 'E', deliverySeq: 2, sourceCursorStart: 2, sourceCursorEnd: 3 });
  results.contiguous_then_coalesced_resync = emitted.filter((e) => e.type === 'resync_required').length;

  // duplicate delivery seq is dropped by the transport
  const before = seen.length;
  feed({ type: 'worker.stream', eventEpoch: 'E', eventSeq: 3, deliveryEpoch: 'E', deliverySeq: 2, sourceCursorStart: 2, sourceCursorEnd: 3 });
  results.duplicate_delivery_dispatched = seen.length - before;

  // real delivery gap -> resync
  feed({ type: 'worker.stream', eventEpoch: 'E', eventSeq: 9, deliveryEpoch: 'E', deliverySeq: 5, sourceCursorStart: 9, sourceCursorEnd: 9 });
  results.after_gap_resync = emitted.filter((e) => e.type === 'resync_required');

  // reset with a snapshot
  const snap = (eventSeq, deliverySeq) => ({
    type: 'resync.snapshot', eventEpoch: 'E', eventSeq, deliveryEpoch: 'E',
    deliverySeq, sourceCursorStart: eventSeq, sourceCursorEnd: eventSeq,
  });
  feed(snap(10, 6));
  results.resyncPendingAfterSnapshot = wsClient.resyncPending;
  results.gapFlagsCleared = emitted.filter((e) => e.type === 'resync_required').length;

  // server builds the snapshot at seq 10, then a broadcast at 11 is enqueued
  // first and the snapshot (still stamped 10) lands afterwards.
  feed({ type: 'worker.stream', eventEpoch: 'E', eventSeq: 11, deliveryEpoch: 'E', deliverySeq: 7, sourceCursorStart: 11, sourceCursorEnd: 11 });
  feed(snap(10, 8));
  const cursorAfterStaleSnap = wsClient.getEventCursor();
  const emittedBefore = emitted.length;
  feed({ type: 'worker.stream', eventEpoch: 'E', eventSeq: 12, deliveryEpoch: 'E', deliverySeq: 9, sourceCursorStart: 12, sourceCursorEnd: 12 });
  results.stale_snapshot = {
    cursorAfterStaleSnap,
    emittedAfter: emitted.slice(emittedBefore),
  };

  // epoch change
  feed({ type: 'worker.stream', eventEpoch: 'F', eventSeq: 1, deliveryEpoch: 'F', deliverySeq: 1, sourceCursorStart: 1, sourceCursorEnd: 1 });
  results.epoch_changes = emitted.filter((e) => e.type === 'server_epoch_changed');

  return results;
}

(async () => {
  const code = await build();
  fs.writeFileSync(path.join(__dirname, 'probe_frontend_ws.bundle.cjs'), code);
  loadBundleAppend = loadBundle(code);
  out.projection = projectionProbe();
  out.reducer = reducerProbe();
  out.ws = wsProbe();
  console.log(JSON.stringify(out, null, 2));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});

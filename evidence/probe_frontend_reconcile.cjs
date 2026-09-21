/**
 * Read-only probe: run the *real* sessionStore reducer path for a worker result.
 *
 * Bundles packages/web/src/stores/sessionStore.ts with esbuild from the sibling
 * worktree (read-only) and stubs only the HTTP/UI collaborators.  Nothing in the
 * product source is modified.
 */
const path = require('path');
const fs = require('fs');

const WEB = path.resolve(__dirname, '..', 'packages', 'web');
const SIBLING = 'D:/project/pan-worktrees/frontend-reaudit-history-ds-20260921/packages/web';
const ESBUILD = require(path.resolve(
  SIBLING, 'node_modules/.pnpm/esbuild@0.21.5/node_modules/esbuild'));

const API_EXPORTS = [
  'fetchSessions', 'fetchSessionHistory', 'createSession', 'deleteSession',
  'batchDeleteSessions', 'renameSession', 'branchSession', 'reimportSession',
  'sendSessionMessage', 'steerSessionWorker', 'fetchSessionQueue',
  'updateSessionSettings', 'fetchSessionSummary',
];

const STUBS = {
  '@/types': 'export {};',
  '@/demo/mockBackend': 'export const isMockMode = () => false;',
  '@/stores/uiStore': 'export const useUIStore = { getState: () => ({}) };',
  '@/services/api': API_EXPORTS.map((n) => `export const ${n} = async () => ({});`).join('\n'),
};

const plugin = {
  name: 'stub-externals',
  setup(build) {
    build.onResolve({ filter: /^@\// }, (args) => {
      if (STUBS[args.path] !== undefined) {
        return { path: args.path, namespace: 'stub' };
      }
      if (args.path === '@/utils/messageIdentity') {
        return { path: path.join(WEB, 'src/utils/messageIdentity.ts') };
      }
      // The repaired store factors its ordering/window helpers into this module;
      // bundle the real one so the probe exercises the shipped behaviour.
      if (args.path === '@/stores/messageOrdering') {
        return { path: path.join(WEB, 'src/stores/messageOrdering.ts') };
      }
      return { path: args.path, namespace: 'stub' };
    });
    build.onLoad({ filter: /.*/, namespace: 'stub' }, (args) => ({
      contents: STUBS[args.path] || 'export {};',
      loader: 'js',
    }));
  },
};

async function build() {
  return (await ESBUILD.build({
    stdin: {
      contents: "export { useSessionStore } from '" +
        path.join(WEB, 'src/stores/sessionStore.ts').replace(/\\/g, '/') + "';",
      resolveDir: WEB,
      loader: 'ts',
    },
    bundle: true,
    write: false,
    format: 'cjs',
    platform: 'node',
    target: 'node18',
    logLevel: 'silent',
    nodePaths: [path.resolve(SIBLING, 'node_modules')],
    plugins: [plugin],
  })).outputFiles[0].text;
}

const code = build ? null : null;

(async () => {
  const bundled = await build();
  fs.writeFileSync(path.join(__dirname, 'probe_frontend_reconcile.bundle.cjs'), bundled);
  const Module = require('module');
  const m = new Module('r');
  m.filename = path.join(__dirname, 'probe_frontend_reconcile.bundle.cjs');
  m.paths = Module._nodeModulePaths(__dirname);
  m._compile(bundled, m.filename);
  const { useSessionStore } = m.exports;

  const sid = 'ses-reconcile';
  const scope = { workerId: 'w1', generation: 0, taskSeq: 1, taskId: 'task-1' };
  const out = {};

  const runCase = (label, liveMessages, history, result, opts = {}) => {
    useSessionStore.setState({
      serverEpoch: 'E',
      currentSessionId: sid,
      sessions: [{
        id: sid, name: sid, adapter: opts.adapter || 'codex', workdir: '',
        history: history.map((r) => ({ ...r })),
        historyTotal: history.length,
        lastMessage: '',
      }],
      // Each case must start from an empty transcript or the previous case's
      // window/runtime leaks into this one.
      sessionTranscripts: {},
      terminalWatermarks: {},
      liveStreamBuffers: {},
      currentMessages: (opts.currentMessages ?? []).map((r) => ({ ...r })),
    });
    // Produce the live buffer the way the real pipeline does — through
    // applyLiveStream — so the display, the buffer's projection refs and the
    // transcript's runtime region are the mutually consistent state that
    // reconcileWorkerResult actually sees. Hand-seeding them as disjoint clones
    // models a state the pipeline can never produce.
    if (liveMessages.length > 0) {
      useSessionStore.getState().applyLiveStream(sid, liveMessages.map((r) => ({ ...r })), {
        ...scope, serverEpoch: 'E',
      });
    }
    useSessionStore.getState().reconcileWorkerResult(sid, {
      type: 'worker.result', sessionId: sid, status: 'done',
      result, taskSeq: 1, taskId: 'task-1', generation: 0, workerId: 'w1',
    }, scope);
    const after = useSessionStore.getState();
    const session = after.sessions.find((s) => s.id === sid);
    out[label] = {
      sessionHistory: (session.history || []).map((r) => [r.role, r.content, r.nativeItemId ?? null]),
      historyTotal: session.historyTotal,
      currentMessages: after.currentMessages.map((r) => [r.role, r.content, r.nativeItemId ?? null]),
      lastMessage: session.lastMessage,
    };
  };

  // Aggregate result that already exists as two per-item rows in canonical
  // history (this is what the worker persists today).
  runCase(
    'aggregate_result_vs_two_canonical_rows',
    [
      { role: 'assistant', content: 'first half ', nativeItemId: 'a1' },
      { role: 'assistant', content: 'second half', nativeItemId: 'a2' },
    ],
    [
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'first half ', nativeItemId: 'a1' },
      { role: 'assistant', content: 'second half', nativeItemId: 'a2' },
      { role: 'assistant', content: 'first half second half' },
    ],
    'first half second half',
  );

  // Canonical history already ends with the identical result row (dedupe case).
  runCase(
    'exact_result_matches_canonical_tail',
    [{ role: 'assistant', content: 'Hello', nativeItemId: 'a1' }],
    [
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'Hello', nativeItemId: 'a1' },
    ],
    'Hello',
  );

  // No live assistant at all (provider result with no streamed message).
  runCase('result_without_live_assistant', [], [{ role: 'user', content: 'q' }], 'answer');

  // ── the reported reorder shape ─────────────────────────────────────────────
  // canonical history already holds the ordered turn, and the live buffer holds
  // the same turn.  Case A: every row carries an explicit native identity
  // (Codex).  Case B: no row carries any identity (real cbc, which has no
  // nativeItemId on live blocks and no messageId on the wire).
  const orderedTurn = [
    { role: 'user', content: 'q' },
    { role: 'assistant', content: 'analysis' },
    { role: 'tool', content: 'Read({"file_path":"a.txt"})' },
    { role: 'assistant', content: 'final' },
  ];
  const withIds = orderedTurn.map((r, i) => (
    r.role === 'assistant' || r.role === 'tool'
      ? { ...r, nativeItemId: `n${i}` }
      : { ...r, nativeItemId: `local:user:${sid}:${i}` }
  ));

  runCase(
    'ordered_turn_with_ids_result_equals_last',
    withIds.slice(1),
    withIds,
    'final',
  );
  runCase(
    'ordered_turn_with_ids_result_differs',
    withIds.slice(1),
    withIds,
    'analysis\nfinal',
  );
  runCase(
    'ordered_turn_idless_result_equals_last',
    orderedTurn.slice(1),
    orderedTurn,
    'final',
    { adapter: 'cbc' },
  );
  runCase(
    'ordered_turn_idless_result_differs',
    orderedTurn.slice(1),
    orderedTurn,
    'analysis\nfinal',
    { adapter: 'cbc' },
  );

  // Precondition that can actually produce the reported [user, final, analysis, tool]:
  // the client's cached session.history lags the live turn (typical mid-stream),
  // so history.push(result) inserts the result BEFORE the live-only rows.
  runCase(
    'partial_history_with_ids',
    withIds.slice(1),
    [withIds[0]],                 // only the user row is canonical yet
    'final',
    { currentMessages: withIds },
  );
  runCase(
    'partial_history_idless',
    orderedTurn.slice(1),
    [orderedTurn[0]],
    'final',
    { adapter: 'cbc', currentMessages: orderedTurn },
  );

  console.log(JSON.stringify(out, null, 2));
})().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});

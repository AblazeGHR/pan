/* eslint-disable react-refresh/only-export-components */
import { createRoot } from 'react-dom/client';
import { SendQueuePanel } from '@/components/chat/SendQueuePanel';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { AgentQueueItem } from '@/types';
import './index.css';

// One row per row-end lock-button state. Fixtures mirror the structured lock
// fields the backend queue API projects (queueLockSource / queueLockAutoReport),
// so the harness exercises the same meta shape the store normalization emits.
const items: AgentQueueItem[] = [
  {
    id: 'q-illegal', queueItemId: 'q-illegal', kind: 'task', source: 'user', createdAt: 0,
    text: 'Illegal 锁定（i + 小锁，既有行为）',
    meta: { dispatchState: 'queued', revision: 1, locked: true, lockSource: 'illegal' },
  },
  {
    id: 'q-report', queueItemId: 'q-report', kind: 'report', source: 'report', createdAt: 0,
    text: 'Pause reports 自动锁定（Re + 小锁）',
    meta: { dispatchState: 'queued', revision: 1, locked: true, lockAutoReport: true, lockManual: false },
  },
  {
    id: 'q-manual', queueItemId: 'q-manual', kind: 'task', source: 'user', createdAt: 0,
    text: '普通手动锁定（原锁图标）',
    meta: { dispatchState: 'queued', revision: 1, locked: true, lockSource: 'manual' },
  },
  {
    id: 'q-paused', queueItemId: 'q-paused', kind: 'task', source: 'user', createdAt: 0,
    text: '未锁定 + Pause everything（仅展示 P，不加锁）',
    meta: { dispatchState: 'queued', revision: 1 },
  },
  {
    id: 'q-open', queueItemId: 'q-open', kind: 'task', source: 'user', createdAt: 0,
    text: '未锁定未暂停（原 Unlock）',
    meta: { dispatchState: 'queued', revision: 1 },
  },
];

useSessionStore.setState({ currentSessionId: 's1', sessions: [], currentMessages: [] });
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
useQueueStore.setState({
  panelOpen: true,
  queues: { s1: items },
  agentQueues: {}, edits: {}, batchSend: {}, sendingId: null,
  agentQueueLoadSeq: {}, queueRevisions: {},
  queuePaused: { s1: true }, queuePauseLoaded: { s1: true },
  agentReportsPaused: { s1: true }, agentReportsPauseLoaded: { s1: true },
});

function Harness() {
  return (
    <div className="bg-bg-primary p-2 text-sm">
      <SendQueuePanel />
    </div>
  );
}

// The P glyph is session-scoped (Pause everything), so the narrow-screen
// verification toggles the session flag instead of splitting rows.
Object.assign(window, {
  __setQueuePaused: (paused: boolean) => useQueueStore.setState({ queuePaused: { s1: paused } }),
});

createRoot(document.getElementById('root')!).render(<Harness />);

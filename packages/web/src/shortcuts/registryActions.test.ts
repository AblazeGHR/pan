// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import { useUIStore } from '@/stores/uiStore';
import { buildShortcutActions } from './registry';
import { entries } from '@/components/layout/viewEntries';
import { registerLockedEnqueueHandler } from './composerBridge';
import { registerChatScrollToBottomHandler } from './chatScrollBridge';
import { runSessionToggleShortcut } from '@/utils/sessionQuickActions';

vi.mock('@/utils/sessionQuickActions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/sessionQuickActions')>();
  return { ...actual, runSessionToggleShortcut: vi.fn(async () => 'ok') };
});

function action(id: string) {
  const found = buildShortcutActions(entries).find((candidate) => candidate.id === id);
  if (!found) throw new Error(`missing action ${id}`);
  return found;
}

const session = (id: string) => ({
  id,
  name: id,
  adapter: 'cbc',
  model: null,
  permissionMode: null,
  alwaysThinkingEnabled: false,
  effort: '',
  workerStatus: 'idle',
  history: [],
});

beforeEach(() => {
  vi.clearAllMocks();
  useSessionStore.setState({ currentSessionId: 's1', sessions: [session('s1')] as never });
  useQueueStore.setState({
    queues: {},
    queueLockUpdating: {},
    agentReportsPauseUpdating: {},
    queuePauseLoaded: { s1: true },
    queuePauseUpdating: {},
    queuePaused: { s1: true },
    panelOpen: false,
  });
  useUIStore.setState({ sidebarCollapsed: false });
});

describe('shortcut action semantics', () => {
  it('Lock all locks queued rows only and never pauses or stops anything', () => {
    const locks = vi.fn(async () => undefined);
    const pauses = vi.fn(async () => undefined);
    useQueueStore.setState({ setQueueItemsLocked: locks, setQueuePaused: pauses });
    action('queue.lockAll').run({ navigate: vi.fn() });
    expect(locks).toHaveBeenCalledWith('s1', true);
    expect(pauses).not.toHaveBeenCalled();
  });

  it('Pause everything toggles the session queue pause gate only', () => {
    const locks = vi.fn(async () => undefined);
    const pauses = vi.fn(async () => undefined);
    useQueueStore.setState({ setQueueItemsLocked: locks, setQueuePaused: pauses });
    action('queue.pauseAll').run({ navigate: vi.fn() });
    expect(pauses).toHaveBeenCalledWith('s1', false);
    expect(locks).not.toHaveBeenCalled();
  });

  it('gates queue/session actions without a selected Session', () => {
    useSessionStore.setState({ currentSessionId: null, sessions: [] });
    const locks = vi.fn(async () => undefined);
    const pauses = vi.fn(async () => undefined);
    const togglePanel = vi.fn();
    useQueueStore.setState({ setQueueItemsLocked: locks, setQueuePaused: pauses, togglePanel });
    const handler = vi.fn(() => true);
    const unregister = registerLockedEnqueueHandler(handler);
    try {
      for (const id of [
        'queue.togglePanel', 'queue.enqueueLocked', 'queue.lockAll', 'queue.pauseAll',
        'session.toggleStopReport', 'session.toggleNotification',
      ]) {
        action(id).run({ navigate: vi.fn() });
      }
      expect(togglePanel).not.toHaveBeenCalled();
      expect(handler).not.toHaveBeenCalled();
      expect(locks).not.toHaveBeenCalled();
      expect(pauses).not.toHaveBeenCalled();
      expect(runSessionToggleShortcut).not.toHaveBeenCalled();
    } finally {
      unregister();
    }
  });

  it('routes the locked-enqueue action through the mounted composer for the selected Session', () => {
    const handler = vi.fn(() => true);
    const unregister = registerLockedEnqueueHandler(handler);
    try {
      action('queue.enqueueLocked').run({ navigate: vi.fn() });
      expect(handler).toHaveBeenCalledWith('s1');
    } finally {
      unregister();
    }
  });

  it('routes the chat scroll action only through a list mounted for the selected Session', () => {
    const scroll = vi.fn(() => true);
    // No visible chat list: the action stays disabled and never runs.
    expect(action('chat.scrollToBottom').isEnabled?.()).toBe(false);
    action('chat.scrollToBottom').run({ navigate: vi.fn() });
    expect(scroll).not.toHaveBeenCalled();

    const unregister = registerChatScrollToBottomHandler('s1', scroll);
    try {
      expect(action('chat.scrollToBottom').isEnabled?.()).toBe(true);
      action('chat.scrollToBottom').run({ navigate: vi.fn() });
      expect(scroll).toHaveBeenCalledTimes(1);

      // A switched selection must neither dispatch to the old list nor look
      // enabled before the successor list has registered.
      useSessionStore.setState({
        currentSessionId: 's2',
        sessions: [session('s1'), session('s2')] as never,
      });
      expect(action('chat.scrollToBottom').isEnabled?.()).toBe(false);
      action('chat.scrollToBottom').run({ navigate: vi.fn() });
      expect(scroll).toHaveBeenCalledTimes(1);
    } finally {
      unregister();
    }
    expect(action('chat.scrollToBottom').isEnabled?.()).toBe(false);
  });

  it('keeps panel navigation and session toggle targeting stable', () => {
    const navigate = vi.fn();
    action('panel:builtin:editor').run({ navigate });
    expect(navigate).toHaveBeenCalledWith('/editor');

    action('session.toggleStopReport').run({ navigate: vi.fn() });
    expect(runSessionToggleShortcut).toHaveBeenCalledWith('s1', 'stopReport');
    action('session.toggleNotification').run({ navigate: vi.fn() });
    expect(runSessionToggleShortcut).toHaveBeenCalledWith('s1', 'notification');
  });
});

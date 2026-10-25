// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { entries } from '@/components/layout/viewEntries';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import type { AgentQueueItem } from '@/types';
import { buildShortcutActions } from './registry';
import { registerQueueSelectedEditHandler } from './queueBridge';

function action(id: string) {
  const found = buildShortcutActions(entries).find((candidate) => candidate.id === id);
  if (!found) throw new Error(`missing action ${id}`);
  return found;
}

function row(id: string, locked = false): AgentQueueItem {
  return {
    id,
    queueItemId: id,
    text: id,
    source: 'user',
    kind: 'task',
    createdAt: 1,
    meta: { dispatchState: 'queued', revision: 1, locked },
  };
}

const navigate = () => vi.fn();

function selection(): string | null {
  return useQueueStore.getState().queueSelection.s1 ?? null;
}

let unregisterEdit: (() => void) | null = null;

beforeEach(() => {
  useSessionStore.setState({
    currentSessionId: 's1',
    sessions: [{ id: 's1', name: 's1', readonlySession: false }] as never,
  });
  useQueueStore.setState({
    queues: { s1: [row('q1'), row('q2'), row('q3')] },
    queueSelection: {},
    panelOpen: true,
    edits: {},
    queueLockUpdating: {},
    agentReportsPauseUpdating: {},
  });
});

afterEach(() => {
  unregisterEdit?.();
  unregisterEdit = null;
});

describe('queue selection shortcut defaults', () => {
  it('ships the four panel-navigation chords bound and composer-scoped', () => {
    const expected: Record<string, string> = {
      'queue.selectPrevious': 'Alt+ArrowUp',
      'queue.selectNext': 'Alt+ArrowDown',
      'queue.editSelected': 'Alt+Enter',
      'queue.toggleSelectedLock': 'Alt+L',
    };
    for (const [id, binding] of Object.entries(expected)) {
      const found = action(id);
      expect(found.group).toBe('Queue');
      expect(found.defaultBinding).toBe(binding);
      expect(found.allowInComposer).toBe(true);
      expect(found.isEnabled).toBeTypeOf('function');
    }
  });
});

describe('queue.selectPrevious / queue.selectNext', () => {
  it('starts at the first/last row and clamps without wrapping', () => {
    action('queue.selectNext').run({ navigate: navigate() });
    expect(selection()).toBe('q1');

    action('queue.selectNext').run({ navigate: navigate() });
    action('queue.selectNext').run({ navigate: navigate() });
    expect(selection()).toBe('q3');

    // Boundary: stays on the last row, never wraps to the first.
    action('queue.selectNext').run({ navigate: navigate() });
    expect(selection()).toBe('q3');

    action('queue.selectPrevious').run({ navigate: navigate() });
    expect(selection()).toBe('q2');

    action('queue.selectPrevious').run({ navigate: navigate() });
    action('queue.selectPrevious').run({ navigate: navigate() });
    expect(selection()).toBe('q1');

    // Boundary: stays on the first row, never wraps to the last.
    action('queue.selectPrevious').run({ navigate: navigate() });
    expect(selection()).toBe('q1');
  });

  it('continues from the row the pointer selected', () => {
    useQueueStore.getState().setQueueSelection('s1', 'q2');
    action('queue.selectNext').run({ navigate: navigate() });
    expect(selection()).toBe('q3');
    action('queue.selectPrevious').run({ navigate: navigate() });
    expect(selection()).toBe('q2');
  });

  it('treats a selection pointing at a dispatched row as no selection', () => {
    useQueueStore.setState({
      queues: {
        s1: [
          { ...row('q1'), meta: { dispatchState: 'sent_to_cli', revision: 1 } },
          row('q2'),
        ],
      },
      queueSelection: { s1: 'q1' },
    });
    action('queue.selectNext').run({ navigate: navigate() });
    expect(selection()).toBe('q2');
  });

  it('is disabled (and therefore consumed by nobody) when the panel is closed', () => {
    useQueueStore.setState({ panelOpen: false });
    expect(action('queue.selectNext').isEnabled?.()).toBe(false);
    expect(action('queue.selectPrevious').isEnabled?.()).toBe(false);
    action('queue.selectNext').run({ navigate: navigate() });
    expect(selection()).toBeNull();
  });

  it('is disabled without queued rows or a selected Session', () => {
    useQueueStore.setState({ queues: { s1: [] } });
    expect(action('queue.selectNext').isEnabled?.()).toBe(false);

    useQueueStore.setState({ queues: { s1: [row('q1')] } });
    useSessionStore.setState({ currentSessionId: null, sessions: [] });
    expect(action('queue.selectNext').isEnabled?.()).toBe(false);
    action('queue.selectNext').run({ navigate: navigate() });
    expect(useQueueStore.getState().queueSelection.s1 ?? null).toBeNull();
  });
});

describe('queue.editSelected', () => {
  it('routes the selected row through the mounted panel edit transaction', () => {
    const handler = vi.fn(() => true);
    unregisterEdit = registerQueueSelectedEditHandler(handler);
    useQueueStore.getState().setQueueSelection('s1', 'q2');

    expect(action('queue.editSelected').isEnabled?.()).toBe(true);
    action('queue.editSelected').run({ navigate: navigate() });
    expect(handler).toHaveBeenCalledWith('s1', 'q2');
  });

  it('no-ops without a selection, without a panel handler, or during an edit', () => {
    const handler = vi.fn(() => true);
    unregisterEdit = registerQueueSelectedEditHandler(handler);

    expect(action('queue.editSelected').isEnabled?.()).toBe(false);
    action('queue.editSelected').run({ navigate: navigate() });
    expect(handler).not.toHaveBeenCalled();

    useQueueStore.getState().setQueueSelection('s1', 'q1');
    useQueueStore.setState({ edits: { s1: { id: 'q1' } as never } });
    expect(action('queue.editSelected').isEnabled?.()).toBe(false);
    action('queue.editSelected').run({ navigate: navigate() });
    expect(handler).not.toHaveBeenCalled();

    unregisterEdit();
    unregisterEdit = null;
    useQueueStore.setState({ edits: {} });
    expect(action('queue.editSelected').isEnabled?.()).toBe(false);
    action('queue.editSelected').run({ navigate: navigate() });
    expect(handler).not.toHaveBeenCalled();
  });

  it('is disabled for a readonly Session', () => {
    unregisterEdit = registerQueueSelectedEditHandler(() => true);
    useQueueStore.getState().setQueueSelection('s1', 'q1');
    useSessionStore.setState({
      sessions: [{ id: 's1', name: 's1', readonlySession: true }] as never,
    });
    expect(action('queue.editSelected').isEnabled?.()).toBe(false);
  });
});

describe('queue.toggleSelectedLock', () => {
  it('locks an unlocked row and unlocks a locked one', () => {
    const locked = vi.fn(async () => undefined);
    useQueueStore.setState({ setQueueItemLocked: locked });
    useQueueStore.getState().setQueueSelection('s1', 'q2');

    expect(action('queue.toggleSelectedLock').isEnabled?.()).toBe(true);
    action('queue.toggleSelectedLock').run({ navigate: navigate() });
    expect(locked).toHaveBeenCalledWith('s1', 'q2', true);

    useQueueStore.setState({ queues: { s1: [row('q1'), row('q2', true), row('q3')] } });
    action('queue.toggleSelectedLock').run({ navigate: navigate() });
    expect(locked).toHaveBeenLastCalledWith('s1', 'q2', false);
  });

  it('no-ops without a selection or while a lock update is running', () => {
    const locked = vi.fn(async () => undefined);
    useQueueStore.setState({ setQueueItemLocked: locked });

    expect(action('queue.toggleSelectedLock').isEnabled?.()).toBe(false);
    action('queue.toggleSelectedLock').run({ navigate: navigate() });
    expect(locked).not.toHaveBeenCalled();

    useQueueStore.getState().setQueueSelection('s1', 'q1');
    useQueueStore.setState({ queueLockUpdating: { s1: true } });
    expect(action('queue.toggleSelectedLock').isEnabled?.()).toBe(false);
    action('queue.toggleSelectedLock').run({ navigate: navigate() });
    expect(locked).not.toHaveBeenCalled();

    useQueueStore.setState({ queueLockUpdating: {}, agentReportsPauseUpdating: { s1: true } });
    expect(action('queue.toggleSelectedLock').isEnabled?.()).toBe(false);
    action('queue.toggleSelectedLock').run({ navigate: navigate() });
    expect(locked).not.toHaveBeenCalled();
  });
});

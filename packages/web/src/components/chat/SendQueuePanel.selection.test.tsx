// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  acquireSessionQueueItemEdit: vi.fn(),
  deleteSessionQueueItem: vi.fn(),
  enqueueSessionMessage: vi.fn(),
  fetchSessionQueue: vi.fn(),
  releaseSessionQueueItemEdit: vi.fn(),
  reorderSessionQueue: vi.fn(),
  setSessionAgentReportsPaused: vi.fn(),
  setSessionQueuePaused: vi.fn(),
  setSessionQueueItemLocked: vi.fn(),
  setSessionQueueItemsLocked: vi.fn(),
  steerSessionQueueItem: vi.fn(),
  updateSessionQueueItem: vi.fn(),
}));

vi.mock('@/services/api', () => api);

import { SendQueuePanel } from './SendQueuePanel';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import {
  hasQueueSelectedEditHandler,
  requestQueueSelectedEdit,
} from '@/shortcuts/queueBridge';
import type { AgentQueueItem } from '@/types';

function queued(id: string, text = id): AgentQueueItem {
  return {
    id,
    queueItemId: id,
    text,
    source: 'user',
    kind: 'task',
    createdAt: 1,
    meta: { dispatchState: 'queued', revision: 1 },
  };
}

function selection(): string | null {
  return useQueueStore.getState().queueSelection.s1 ?? null;
}

function rowFor(container: HTMLElement, id: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`[data-queue-row="${id}"]`);
  if (!element) throw new Error(`missing queue row ${id}`);
  return element;
}

beforeEach(() => {
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
  useSessionStore.setState({
    currentSessionId: 's1',
    currentMessages: [],
    sessions: [{ id: 's1', name: 'Test', adapter: 'cbc' }] as never,
  });
  useQueueStore.setState({
    queues: { s1: [queued('q1', 'first'), queued('q2', 'second'), queued('q3', 'third')] },
    queueSelection: {},
    edits: {},
    panelOpen: true,
    queueLockUpdating: {},
    agentReportsPauseUpdating: {},
  });
  api.fetchSessionQueue.mockResolvedValue([queued('q1'), queued('q2'), queued('q3')]);
  api.acquireSessionQueueItemEdit.mockResolvedValue({
    expiresAt: Math.floor(Date.now() / 1000) + 300,
    text: 'second',
    revision: 1,
    bodyFormat: 'text',
  });
  api.releaseSessionQueueItemEdit.mockResolvedValue(undefined);
  vi.clearAllMocks();
});

afterEach(() => cleanup());

describe('SendQueuePanel selection', () => {
  it('moves the highlight to the hovered row and keeps it after the pointer leaves', () => {
    const { container } = render(<SendQueuePanel />);
    const second = rowFor(container, 'q2');
    const first = rowFor(container, 'q1');

    fireEvent.mouseEnter(second);
    expect(selection()).toBe('q2');
    expect(second.getAttribute('data-queue-selected')).toBe('true');
    expect(first.hasAttribute('data-queue-selected')).toBe(false);

    // Selected and hovered rows share one background token.
    expect(second.className).toContain('bg-bg-hover');
    expect(second.className).toContain('hover:bg-bg-hover');

    fireEvent.mouseLeave(second);
    expect(selection()).toBe('q2');
    expect(second.getAttribute('data-queue-selected')).toBe('true');
  });

  it('renders keyboard selection from the shared store exactly like hover', () => {
    const { container } = render(<SendQueuePanel />);
    act(() => useQueueStore.getState().setQueueSelection('s1', 'q3'));
    const third = rowFor(container, 'q3');
    const first = rowFor(container, 'q1');
    expect(third.getAttribute('data-queue-selected')).toBe('true');
    expect(third.className).toContain('bg-bg-hover');
    expect(first.getAttribute('data-queue-selected')).toBeNull();
  });

  it('drops the highlight when the row disappears or the panel closes', async () => {
    render(<SendQueuePanel />);
    act(() => useQueueStore.getState().setQueueSelection('s1', 'q2'));

    act(() => {
      useQueueStore.setState({ queues: { s1: [queued('q1'), queued('q3')] } });
    });
    await waitFor(() => expect(selection()).toBeNull());

    act(() => useQueueStore.getState().setQueueSelection('s1', 'q1'));
    act(() => useQueueStore.setState({ panelOpen: false }));
    await waitFor(() => expect(selection()).toBeNull());
  });

  it('exposes the existing edit transaction to the shortcut bridge', async () => {
    render(<SendQueuePanel />);
    expect(hasQueueSelectedEditHandler()).toBe(true);

    act(() => useQueueStore.getState().setQueueSelection('s1', 'q2'));
    expect(requestQueueSelectedEdit('s1', 'q2')).toBe(true);

    await waitFor(() => expect(useQueueStore.getState().edits.s1?.id).toBe('q2'));
    expect(api.acquireSessionQueueItemEdit.mock.calls[0]?.slice(0, 2)).toEqual(['s1', 'q2']);
  });

  it('refuses a bridge request for another Session or an unknown row', () => {
    render(<SendQueuePanel />);
    expect(requestQueueSelectedEdit('s2', 'q1')).toBe(false);
    expect(requestQueueSelectedEdit('s1', 'missing')).toBe(false);
    expect(useQueueStore.getState().edits.s1 ?? null).toBeNull();
  });
});

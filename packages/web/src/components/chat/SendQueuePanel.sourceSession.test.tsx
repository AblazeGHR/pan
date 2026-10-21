// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { AgentQueueItem } from '@/types';

const api = vi.hoisted(() => ({
  acquireSessionQueueItemEdit: vi.fn(),
  fetchSessionQueue: vi.fn(),
  enqueueSessionMessage: vi.fn(),
  releaseSessionQueueItemEdit: vi.fn(),
  deleteSessionQueueItem: vi.fn(),
  updateSessionQueueItem: vi.fn(),
  reorderSessionQueue: vi.fn(),
}));

vi.mock('@/services/api', () => api);

const clipboard = vi.hoisted(() => ({ copyText: vi.fn() }));
vi.mock('@/utils/clipboard', () => clipboard);

const navigation = vi.hoisted(() => ({ openSessionFromNotification: vi.fn() }));
vi.mock('@/utils/session-deeplink', () => ({
  isValidSessionRef: (value: string | null | undefined) =>
    typeof value === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(value),
  openSessionFromNotification: navigation.openSessionFromNotification,
}));

import { SendQueuePanel } from './SendQueuePanel';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { useUIStore } from '@/stores/uiStore';

const SOURCE_ID = 'ses_0123456789abcdef';

function agentItem(overrides: Partial<AgentQueueItem> = {}): AgentQueueItem {
  return {
    id: 'q-agent',
    queueItemId: 'q-agent',
    kind: 'task',
    text: 'agent queued message',
    createdAt: '2026-10-21T00:00:00Z',
    source: 'agent',
    meta: { dispatchState: 'queued', revision: 1, sourceSessionId: SOURCE_ID },
    ...overrides,
  };
}

function snapshot(items: AgentQueueItem[]) {
  Object.defineProperty(items, 'queueRevision', { value: 2, enumerable: false });
  return items;
}

beforeEach(() => {
  clipboard.copyText.mockReset();
  clipboard.copyText.mockResolvedValue(undefined);
  navigation.openSessionFromNotification.mockReset();
  navigation.openSessionFromNotification.mockResolvedValue(true);
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
  useUIStore.setState({ toastQueue: [] });
  useSessionStore.setState({
    currentSessionId: 's1',
    sessions: [
      { id: 's1', name: 'Worker', alwaysThinkingEnabled: false, effort: '', history: [] },
      { id: SOURCE_ID, name: 'Source Orchestrator', alwaysThinkingEnabled: false, effort: '', history: [] },
    ],
    currentMessages: [],
  });
  useQueueStore.setState({
    queues: { s1: [agentItem()] },
    agentQueues: {}, edits: {}, batchSend: {}, sendingId: null,
    panelOpen: true, agentQueueLoadSeq: {}, queueRevisions: {},
  });
  api.fetchSessionQueue.mockResolvedValue(snapshot([agentItem()]));
  api.releaseSessionQueueItemEdit.mockResolvedValue(undefined);
});

afterEach(() => cleanup());

describe('SendQueuePanel preview source session', () => {
  it('shows the structured agent origin inside the existing full-text preview', async () => {
    render(<SendQueuePanel />);
    await waitFor(() => expect(screen.getByText('agent queued message')).toBeTruthy());

    fireEvent.click(screen.getByText('agent queued message'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('Source session');
    expect(dialog.textContent).toContain('Source Orchestrator');
    expect(dialog.textContent).toContain(SOURCE_ID);
    // Existing preview behavior is untouched.
    expect(dialog.textContent).toContain('复制');
    expect(dialog.textContent).toContain('关闭');
  });

  it('closes the preview and jumps to the source session', async () => {
    render(<SendQueuePanel />);
    await waitFor(() => expect(screen.getByText('agent queued message')).toBeTruthy());

    fireEvent.click(screen.getByText('agent queued message'));
    fireEvent.click(await screen.findByText('Open source chat'));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(navigation.openSessionFromNotification).toHaveBeenCalledWith(
      SOURCE_ID,
      expect.objectContaining({ missing: expect.any(String) }),
    );
  });

  it('does not show a source session for user-sourced queued messages', async () => {
    const userItem = agentItem({
      id: 'q-user', queueItemId: 'q-user', text: 'user queued message', source: 'user', meta: { dispatchState: 'queued', revision: 1 },
    });
    useQueueStore.setState({ queues: { s1: [userItem] } });
    api.fetchSessionQueue.mockResolvedValue(snapshot([userItem]));

    render(<SendQueuePanel />);
    await waitFor(() => expect(screen.getByText('user queued message')).toBeTruthy());
    fireEvent.click(screen.getByText('user queued message'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).not.toContain('Source session');
    expect(screen.queryByText('Open source chat')).toBeNull();
  });
});

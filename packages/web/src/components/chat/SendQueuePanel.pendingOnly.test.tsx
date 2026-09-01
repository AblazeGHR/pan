// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  fetchSessionQueue: vi.fn(),
  enqueueSessionMessage: vi.fn(),
  deleteSessionQueueItem: vi.fn(),
  updateSessionQueueItem: vi.fn(),
  reorderSessionQueue: vi.fn(),
}));

vi.mock('@/services/api', () => api);

import { SendQueuePanel } from './SendQueuePanel';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';

function item(id: string, text: string, dispatchState: 'queued' | 'sent_to_cli' | 'write_failed' | 'unknown_after_crash') {
  return {
    id,
    queueItemId: id,
    text,
    source: 'user' as const,
    kind: 'task' as const,
    createdAt: '2026-09-01T00:00:00Z',
    meta: { dispatchState, revision: 1 },
  };
}

function snapshot(items: ReturnType<typeof item>[]) {
  Object.defineProperty(items, 'queueRevision', { value: 2, enumerable: false });
  return items;
}

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: 's1', sessions: [], currentMessages: [] });
  useQueueStore.setState({
    queues: {
      s1: [
        item('q-pending', '仍待发送', 'queued'),
        item('q-sent', '已写入 CLI', 'sent_to_cli'),
        item('q-failed', '写入失败', 'write_failed'),
        item('q-unknown', '崩溃未知', 'unknown_after_crash'),
      ],
    },
    agentQueues: {}, edits: {}, batchSend: {}, sendingId: null,
    panelOpen: true, agentQueueLoadSeq: {}, queueRevisions: {},
  });
  api.fetchSessionQueue.mockResolvedValue(snapshot([item('q-pending', '仍待发送', 'queued')]));
  vi.clearAllMocks();
});

describe('SendQueuePanel pending-only view', () => {
  it('does not render delivery-ledger terminal or uncertain states', async () => {
    render(<SendQueuePanel />);

    await waitFor(() => expect(api.fetchSessionQueue).toHaveBeenCalledWith('s1'));
    expect(screen.getByText('仍待发送')).toBeTruthy();
    expect(screen.queryByText('已写入 CLI')).toBeNull();
    expect(screen.queryByText('写入失败')).toBeNull();
    expect(screen.queryByText('崩溃未知')).toBeNull();
  });
});

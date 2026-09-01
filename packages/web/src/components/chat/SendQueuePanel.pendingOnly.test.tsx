// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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

function item(
  id: string,
  text: string,
  dispatchState: 'queued' | 'sent_to_cli' | 'write_failed' | 'unknown_after_crash',
  source: 'user' | 'agent' | 'report' | 'qq' = 'user',
  kind: 'task' | 'report' | 'qq' = 'task',
) {
  return {
    id,
    queueItemId: id,
    text,
    source,
    kind,
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

afterEach(() => cleanup());

describe('SendQueuePanel pending-only view', () => {
  it('does not render delivery-ledger terminal or uncertain states', async () => {
    render(<SendQueuePanel />);

    await waitFor(() => expect(api.fetchSessionQueue).toHaveBeenCalledWith('s1'));
    expect(screen.getByText('仍待发送')).toBeTruthy();
    expect(screen.queryByText('已写入 CLI')).toBeNull();
    expect(screen.queryByText('写入失败')).toBeNull();
    expect(screen.queryByText('崩溃未知')).toBeNull();
  });

  it('shows reorder controls for non-user sources and sends their native ids', async () => {
    const user = item('q-user', '用户', 'queued');
    const agent = item('q-agent', 'Agent', 'queued', 'agent');
    const report = item('q-report', '报告', 'queued', 'report', 'report');
    const qq = item('q-qq', 'QQ', 'queued', 'qq', 'qq');
    api.fetchSessionQueue.mockResolvedValue(snapshot([user, agent, report, qq]));
    api.reorderSessionQueue.mockResolvedValue(snapshot([user, report, agent, qq]));
    render(<SendQueuePanel />);

    await waitFor(() => expect(screen.getByText('Agent task')).toBeTruthy());
    const agentRow = screen.getByText('Agent task').closest('div');
    expect(agentRow).toBeTruthy();
    fireEvent.click(within(agentRow!).getByTitle('下移'));
    await waitFor(() => expect(api.reorderSessionQueue).toHaveBeenCalledWith(
      's1', ['q-user', 'q-report', 'q-agent', 'q-qq'], 2,
    ));
  });

  it('edits a queued user task and keeps its queue identity', async () => {
    const original = item('q-user-edit', '原始用户任务', 'queued');
    const updated = {
      ...original,
      text: '修改后的用户任务',
      meta: { ...original.meta, revision: 2 },
    };
    api.fetchSessionQueue
      .mockResolvedValueOnce(snapshot([original]))
      .mockResolvedValueOnce(snapshot([updated]));
    api.updateSessionQueueItem.mockResolvedValue({
      ok: true,
      item: updated,
      queueRevision: 3,
    });

    render(<SendQueuePanel />);
    await waitFor(() => expect(screen.getByText('原始用户任务')).toBeTruthy());

    const row = screen.getByText('原始用户任务').closest('div');
    expect(row).toBeTruthy();
    fireEvent.click(within(row!).getByTitle('编辑'));
    fireEvent.change(screen.getByDisplayValue('原始用户任务'), {
      target: { value: '修改后的用户任务' },
    });
    fireEvent.click(screen.getByTitle('保存'));

    await waitFor(() => expect(api.updateSessionQueueItem).toHaveBeenCalledWith(
      's1', 'q-user-edit', '修改后的用户任务', 1,
    ));
    await waitFor(() => expect(screen.getByText('修改后的用户任务')).toBeTruthy());
    expect(screen.queryByDisplayValue('修改后的用户任务')).toBeNull();
  });
});

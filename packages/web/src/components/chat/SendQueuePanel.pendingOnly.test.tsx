// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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

import { SendQueuePanel } from './SendQueuePanel';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';

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
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
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
  api.acquireSessionQueueItemEdit.mockImplementation(async (sessionId: string, id: string) => {
    const row = (useQueueStore.getState().queues[sessionId] ?? useQueueStore.getState().agentQueues[sessionId] ?? []).find(item => item.id === id);
    if (!row) throw new Error(`Missing queue lease fixture: ${sessionId}/${id}`);
    return { expiresAt: Math.floor(Date.now() / 1000) + 300, text: row.text, revision: row.meta?.revision ?? 1, bodyFormat: 'text' };
  });
  api.releaseSessionQueueItemEdit.mockResolvedValue(undefined);
  vi.clearAllMocks();
});

afterEach(() => cleanup());

it('shows the Illegal composite icon and explanation while preserving manual unlock behavior', () => {
  const illegal = { ...item('q-illegal', 'Illegal locked', 'queued'),
    meta: { dispatchState: 'queued' as const, revision: 1, locked: true, lockSource: 'illegal' as const } };
  const manual = { ...item('q-manual', 'Manual locked', 'queued'),
    meta: { dispatchState: 'queued' as const, revision: 1, locked: true, lockSource: 'manual' as const } };
  const unlock = vi.fn();
  const original = useQueueStore.getState().setQueueItemLocked;
  useQueueStore.setState({ queues: { s1: [illegal, manual] }, setQueueItemLocked: unlock });
  try {
    render(<SendQueuePanel />);
    const button = screen.getByRole('button', { name: 'Unlock queued message 1' });
    expect(button.title).toContain('Worker entered Illegal status');
    expect(button.title).toContain('unlock it manually');
    expect(button.title).toContain('safely offline');
    expect(within(button).getByText('i')).toBeTruthy();
    expect(button.className).toContain('rounded');
    expect(button.querySelector('svg')?.getAttribute('width')).toBe('8');
    const manualButton = screen.getByRole('button', { name: 'Unlock queued message 2' });
    expect(manualButton.title).toBe('Unlock');
    expect(within(manualButton).queryByText('i')).toBeNull();
    fireEvent.click(button);
    expect(unlock).toHaveBeenCalledWith('s1', 'q-illegal', false);
  } finally {
    useQueueStore.setState({ setQueueItemLocked: original });
  }
});

describe('SendQueuePanel row lock icon priority', () => {
  it('shows the Re report-lock composite icon and still unlocks on click', () => {
    const report = { ...item('q-report-lock', 'Report auto locked', 'queued', 'report', 'report'),
      meta: { dispatchState: 'queued' as const, revision: 1, locked: true, lockAutoReport: true, lockManual: false } };
    const unlock = vi.fn();
    const original = useQueueStore.getState().setQueueItemLocked;
    useQueueStore.setState({ queues: { s1: [report] }, setQueueItemLocked: unlock, queuePaused: {}, queuePauseLoaded: {} });
    try {
      render(<SendQueuePanel />);
      const button = screen.getByRole('button', { name: 'Unlock queued message 1' });
      expect(button.getAttribute('aria-pressed')).toBe('true');
      expect(button.title).toBe('Unlock');
      expect(within(button).getByText('Re')).toBeTruthy();
      expect(within(button).queryByText('i')).toBeNull();
      expect(within(button).queryByText('P')).toBeNull();
      expect(button.querySelector('svg')?.getAttribute('width')).toBe('8');
      fireEvent.click(button);
      expect(unlock).toHaveBeenCalledWith('s1', 'q-report-lock', false);
    } finally {
      useQueueStore.setState({ setQueueItemLocked: original, queuePaused: {}, queuePauseLoaded: {} });
    }
  });

  it('shows a display-only P glyph while Pause everything holds an unlocked row', () => {
    const plain = item('q-plain', '未锁定', 'queued');
    const unlock = vi.fn();
    const original = useQueueStore.getState().setQueueItemLocked;
    useQueueStore.setState({ queues: { s1: [plain] }, setQueueItemLocked: unlock, queuePaused: { s1: true }, queuePauseLoaded: { s1: true } });
    try {
      render(<SendQueuePanel />);
      const button = screen.getByRole('button', { name: 'Lock queued message 1' });
      expect(button.getAttribute('aria-pressed')).toBe('false');
      expect(button.title).toBe('Lock');
      expect(within(button).getByText('P')).toBeTruthy();
      expect(within(button).queryByText('Re')).toBeNull();
      expect(button.querySelector('svg')).toBeNull();
      fireEvent.click(button);
      expect(unlock).toHaveBeenCalledWith('s1', 'q-plain', true);
    } finally {
      useQueueStore.setState({ setQueueItemLocked: original, queuePaused: {}, queuePauseLoaded: {} });
    }
  });

  it('prefers real lock icons over the pause P glyph', () => {
    const illegal = { ...item('q-illegal-p', 'Illegal', 'queued'),
      meta: { dispatchState: 'queued' as const, revision: 1, locked: true, lockSource: 'illegal' as const } };
    const report = { ...item('q-report-p', 'Report', 'queued', 'report', 'report'),
      meta: { dispatchState: 'queued' as const, revision: 1, locked: true, lockAutoReport: true, lockManual: false } };
    const manual = { ...item('q-manual-p', 'Manual', 'queued'),
      meta: { dispatchState: 'queued' as const, revision: 1, locked: true, lockSource: 'manual' as const } };
    useQueueStore.setState({ queues: { s1: [illegal, report, manual] }, queuePaused: { s1: true }, queuePauseLoaded: { s1: true } });
    try {
      render(<SendQueuePanel />);
      const illegalButton = screen.getByRole('button', { name: 'Unlock queued message 1' });
      expect(within(illegalButton).getByText('i')).toBeTruthy();
      expect(within(illegalButton).queryByText('P')).toBeNull();
      const reportButton = screen.getByRole('button', { name: 'Unlock queued message 2' });
      expect(within(reportButton).getByText('Re')).toBeTruthy();
      expect(within(reportButton).queryByText('P')).toBeNull();
      const manualButton = screen.getByRole('button', { name: 'Unlock queued message 3' });
      expect(within(manualButton).queryByText('P')).toBeNull();
      expect(within(manualButton).queryByText('Re')).toBeNull();
      expect(manualButton.querySelector('svg')?.getAttribute('width')).toBe('12');
    } finally {
      useQueueStore.setState({ queuePaused: {}, queuePauseLoaded: {} });
    }
  });

  it('keeps the plain Unlock icon for unlocked rows while the queue runs', () => {
    const plain = item('q-open', '未锁定', 'queued');
    useQueueStore.setState({ queues: { s1: [plain] }, queuePaused: {}, queuePauseLoaded: {} });
    render(<SendQueuePanel />);
    const button = screen.getByRole('button', { name: 'Lock queued message 1' });
    expect(button.getAttribute('aria-pressed')).toBe('false');
    expect(within(button).queryByText('P')).toBeNull();
    expect(within(button).queryByText('Re')).toBeNull();
    expect(button.querySelector('svg')?.getAttribute('width')).toBe('12');
  });
});

describe('SendQueuePanel pending-only view', () => {
  it('keeps the native queue open without changing the saved Pan preference', async () => {
    useQueueStore.setState({ panelOpen: false });
    const { container, rerender } = render(<SendQueuePanel nativeMode />);
    await waitFor(() => expect(screen.getByText('仍待发送')).toBeTruthy());
    expect(container.firstElementChild?.className).toContain('grid-rows-[1fr]');
    expect(useQueueStore.getState().panelOpen).toBe(false);
    rerender(<SendQueuePanel />);
    expect(container.firstElementChild?.className).toContain('grid-rows-[0fr]');
  });

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
    // Editing now lives in InputRow; the panel hands the full body and native
    // identity to the shared composer state (covered by InputRow.test.tsx).
    await waitFor(() => expect(useQueueStore.getState().edits.s1).toMatchObject({
      id: original.id, text: original.text, acquiring: false,
    }));
    useQueueStore.getState().updateEditDraft('修改后的用户任务');
    await useQueueStore.getState().saveEdit();

    await waitFor(() => expect(api.updateSessionQueueItem).toHaveBeenCalledWith(
      's1', 'q-user-edit', '修改后的用户任务', 1, expect.any(String), undefined, [{ type: 'text', text: '修改后的用户任务' }],
    ));
    await waitFor(() => expect(screen.getByText('修改后的用户任务')).toBeTruthy());
    expect(screen.queryByDisplayValue('修改后的用户任务')).toBeNull();
  });
});

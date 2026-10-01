// @vitest-environment jsdom
// Bug 3 regression: the FIRST confirm click must show live progress, even
// when every WS progress event is dropped (polling fallback).
// Background-run: the progress view offers "后台运行", which closes the popup
// without cancelling the job, and progress keeps being applied afterwards.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, act, fireEvent, cleanup, screen } from '@testing-library/react';
import { RewindConfirmModal } from './RewindConfirmModal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Message } from '@/types';

const rewindSessionHistoryMock = vi.fn();
const fetchRewindJobStatusMock = vi.fn();

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    rewindSessionHistory: (...args: unknown[]) => rewindSessionHistoryMock(...args),
    fetchRewindJobStatus: (...args: unknown[]) => fetchRewindJobStatusMock(...args),
  };
});

const ANCHOR: Message = { role: 'user', content: 'anchor text', messageId: 'msg_1' };

function resetStores() {
  useSessionStore.setState({
    activeRewinds: [],
    rewindTarget: null,
    currentSessionId: 's1',
    currentMessages: [ANCHOR],
    setInputDraft: vi.fn(),
    loadSessions: vi.fn(() => Promise.resolve()),
    selectSession: vi.fn(() => Promise.resolve()),
  });
  useUIStore.setState({ toastQueue: [], composerFocusToken: 0 });
}

function emit(stage: string, patch: Record<string, unknown> = {}) {
  act(() => {
    useSessionStore.getState().applyRewindProgress({
      type: 'session.rewind.progress',
      jobId: 'j1',
      sessionId: 's1',
      stage,
      ...patch,
    } as never);
  });
}

describe('RewindConfirmModal', () => {
  beforeEach(() => {
    rewindSessionHistoryMock.mockReset();
    fetchRewindJobStatusMock.mockReset();
    resetStores();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('shows progress from the FIRST confirm click, before the POST resolves', async () => {
    let resolvePost!: (value: unknown) => void;
    rewindSessionHistoryMock.mockReturnValue(new Promise((resolve) => {
      resolvePost = resolve;
    }));
    const onClose = vi.fn();
    render(<RewindConfirmModal message={ANCHOR} onClose={onClose} />);

    fireEvent.click(screen.getByText('确认撤回'));

    // The pending rewind is registered synchronously: the progress view is
    // visible immediately, no waiting for the POST.
    expect(await screen.findByText('正在启动回滚…')).toBeTruthy();

    // An early WS event (arriving before the POST resolves) advances it.
    emit('resuming');
    expect(await screen.findByText('正在恢复会话副本…')).toBeTruthy();

    await act(async () => {
      resolvePost({ ok: true, jobId: 'j1', stage: 'starting' });
    });
    expect(useSessionStore.getState().activeRewinds[0]?.jobId).toBe('j1');

    emit('rewinding-files');
    expect(await screen.findByText('正在回滚代码…')).toBeTruthy();
  });

  it('keeps advancing progress via the polling fallback when WS events never arrive', async () => {
    vi.useFakeTimers();
    rewindSessionHistoryMock.mockResolvedValue({ ok: true, jobId: 'j1', stage: 'starting' });
    fetchRewindJobStatusMock.mockResolvedValue({
      ok: true, jobId: 'j1', sessionId: 's1', stage: 'rewinding-files', status: 'running',
    });
    render(<RewindConfirmModal message={ANCHOR} onClose={vi.fn()} />);

    fireEvent.click(screen.getByText('确认撤回'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText('正在启动回滚…')).toBeTruthy();

    // No WS events at all — the 2s polling tick must drive the stage.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(fetchRewindJobStatusMock).toHaveBeenCalledWith('s1', 'j1');
    expect(useSessionStore.getState().activeRewinds[0]?.stage).toBe('rewinding-files');
  });

  it('closing via 后台运行 does NOT cancel the job; progress keeps applying', async () => {
    rewindSessionHistoryMock.mockResolvedValue({ ok: true, jobId: 'j1', stage: 'starting' });
    const onClose = vi.fn();
    render(<RewindConfirmModal message={ANCHOR} onClose={onClose} />);
    fireEvent.click(screen.getByText('确认撤回'));
    await act(async () => {});
    expect(useSessionStore.getState().activeRewinds).toHaveLength(1);

    fireEvent.click(screen.getByText('后台运行'));
    expect(onClose).toHaveBeenCalled();
    // The popup anchor is cleared, but the job entry must survive.
    expect(useSessionStore.getState().rewindTarget).toBeNull();
    expect(useSessionStore.getState().activeRewinds).toHaveLength(1);
    expect(useSessionStore.getState().activeRewinds[0]?.status).toBe('running');

    // Events arriving after the popup closed still advance the job.
    emit('truncating');
    expect(useSessionStore.getState().activeRewinds[0]?.stage).toBe('truncating');
  });

  it('does not auto-close while the job runs (progress stays visible)', async () => {
    rewindSessionHistoryMock.mockResolvedValue({ ok: true, jobId: 'j1', stage: 'starting' });
    const onClose = vi.fn();
    render(<RewindConfirmModal message={ANCHOR} onClose={onClose} />);
    fireEvent.click(screen.getByText('确认撤回'));
    await act(async () => {});
    emit('resuming');
    emit('truncating');
    expect(onClose).not.toHaveBeenCalled();
    expect(useSessionStore.getState().activeRewinds[0]?.stage).toBe('truncating');
  });

  it('store keeps the modal target independent of bubble lifecycle', () => {
    useSessionStore.getState().openRewind(ANCHOR);
    expect(useSessionStore.getState().rewindTarget).toBe(ANCHOR);
    useSessionStore.getState().closeRewind();
    expect(useSessionStore.getState().rewindTarget).toBeNull();
  });
});

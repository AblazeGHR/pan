// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SessionDetailsModal } from './SessionDetailsModal';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import type { Session } from '@/types';

const baseSession: Session = {
  id: 'ses_full_session_id',
  name: 'Details test',
  adapter: 'cbc',
  cliSessionId: 'cli_full_session_id',
  workdir: 'D:\\projects\\pan\\a-very-long-working-directory',
  totalUsage: { credit: 12.3456, prompt_tokens: 100 },
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useUIStore.setState({ toastQueue: [] });
  useWorkerStore.setState({ workers: {}, currentWorkerId: null, currentWorker: null });
});

describe('SessionDetailsModal', () => {
  it('shows authoritative cumulative credit and all session identifiers', () => {
    render(<SessionDetailsModal session={baseSession} onClose={() => {}} />);

    expect(screen.getByText('额度（累计消费 credit）')).toBeTruthy();
    expect(screen.getByText('12.35')).toBeTruthy();
    expect(screen.getByText(baseSession.name)).toBeTruthy();
    expect(screen.getByText(baseSession.workdir!)).toBeTruthy();
    expect(screen.getByText(baseSession.id)).toBeTruthy();
    expect(screen.getByText(baseSession.cliSessionId!)).toBeTruthy();
  });

  it('copies identifiers without invoking the modal close or card handlers', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const onClose = vi.fn();
    render(<SessionDetailsModal session={baseSession} onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: '复制工作目录' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(baseSession.workdir));
    expect(onClose).not.toHaveBeenCalled();
    expect(useUIStore.getState().toastQueue.at(-1)?.message).toBe('工作目录 已复制');
  });

  it('copies Session name through the shared clipboard helper without closing', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const onClose = vi.fn();
    render(<SessionDetailsModal session={baseSession} onClose={onClose} />);

    const button = screen.getByRole('button', { name: '复制Session name' });
    expect(button.getAttribute('title')).toBe('复制Session name');
    fireEvent.click(button);

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(baseSession.name));
    expect(onClose).not.toHaveBeenCalled();
    expect(useUIStore.getState().toastQueue.at(-1)?.message).toBe('Session name 已复制');
  });

  it('reports a name copy failure and keeps the modal open', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const onClose = vi.fn();
    render(<SessionDetailsModal session={baseSession} onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: '复制Session name' }));
    await waitFor(() => expect(useUIStore.getState().toastQueue.at(-1)?.message).toBe('复制失败'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('uses the empty-value semantics for an unnamed session', () => {
    const unnamed: Session = { ...baseSession, name: '' };
    render(<SessionDetailsModal session={unnamed} onClose={() => {}} />);

    expect(screen.getByText('Session name')).toBeTruthy();
    expect(screen.getAllByText('暂无 / 未建立')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '复制Session name' }));
    expect(useUIStore.getState().toastQueue.at(-1)?.message).toBe('Session name 暂无可复制内容');
  });

  it('shows only provider-proven weekly and monthly codex windows', () => {
    const codex: Session = {
      ...baseSession,
      id: 'ses_codex_quota',
      adapter: 'codex',
      totalUsage: { credit: 999.99 },
    };
    useWorkerStore.setState({
      workers: {
        [codex.id]: {
          id: 'worker-codex-quota',
          sessionId: codex.id,
          status: 'running',
          nativeRateLimits: {
            primary: { windowDurationMins: 300, usedPercent: 5 },
            secondary: { windowDurationMins: 10080, usedPercent: 25, remainingPercent: 75 },
            monthly: { name: 'monthly', usedPercent: 40, remainingCredits: 12.5 },
          },
        },
      },
    });

    render(<SessionDetailsModal session={codex} onClose={() => {}} />);

    expect(screen.getByText('周额度')).toBeTruthy();
    expect(screen.getByText('月额度')).toBeTruthy();
    expect(screen.getByText(/已使用 25%.*剩余 75%/)).toBeTruthy();
    expect(screen.getByText(/已使用 40%.*剩余 12\.5 credit/)).toBeTruthy();
    expect(screen.queryByText('999.99')).toBeNull();
    expect(screen.queryByText('已使用 5%')).toBeNull();
  });

  it('does not borrow quota windows from another session and shows missing windows clearly', () => {
    const codex: Session = { ...baseSession, id: 'ses_codex_target', adapter: 'codex' };
    useWorkerStore.setState({
      workers: {
        [codex.id]: {
          id: 'worker-codex-target',
          sessionId: codex.id,
          status: 'running',
          nativeRateLimits: { secondary: { windowDurationMins: 10080, usedPercent: 12 } },
        },
        other: {
          id: 'worker-other',
          sessionId: 'other',
          status: 'running',
          nativeRateLimits: { monthly: { windowDurationMins: 43200, usedPercent: 91 } },
        },
      },
    });

    render(<SessionDetailsModal session={codex} onClose={() => {}} />);

    expect(screen.getByText(/已使用 12%/)).toBeTruthy();
    expect(screen.getByText('月额度')).toBeTruthy();
    expect(screen.getAllByText('暂无数据')).toHaveLength(1);
    expect(screen.queryByText(/已使用 91%/)).toBeNull();
  });

  it('shows an explicit empty state when the current worker is offline or absent', () => {
    const codex: Session = { ...baseSession, id: 'ses_codex_offline', adapter: 'codex' };
    useWorkerStore.setState({
      workers: {
        [codex.id]: { id: 'worker-offline', sessionId: codex.id, status: 'offline' },
      },
    });

    render(<SessionDetailsModal session={codex} onClose={() => {}} />);

    expect(screen.getByText('当前 Worker 不可用，暂无额度数据')).toBeTruthy();
    expect(screen.queryByText('周额度')).toBeNull();
    expect(screen.queryByText('月额度')).toBeNull();
  });

  it('degrades safely when clipboard is unavailable and displays missing values', () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    const missing: Session = { ...baseSession, workdir: undefined, cliSessionId: null, totalUsage: null };
    render(<SessionDetailsModal session={missing} onClose={() => {}} />);

    expect(screen.getAllByText('暂无 / 未建立')).toHaveLength(2);
    expect(screen.getByText('暂无 usage 数据')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '复制工作目录' }));
    expect(useUIStore.getState().toastQueue.at(-1)?.type).toBe('error');
  });
});

// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SessionDetailsModal } from './SessionDetailsModal';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

const baseSession: Session = {
  id: 'ses_full_session_id',
  name: 'Details test',
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
});

describe('SessionDetailsModal', () => {
  it('shows authoritative cumulative credit and all session identifiers', () => {
    render(<SessionDetailsModal session={baseSession} onClose={() => {}} />);

    expect(screen.getByText('额度（累计消费 credit）')).toBeTruthy();
    expect(screen.getByText('12.35')).toBeTruthy();
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

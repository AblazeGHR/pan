// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SessionDetailsModal } from './SessionDetailsModal';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import * as api from '@/services/api';
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
});

beforeEach(() => {
  vi.spyOn(api, 'fetchSessionUsage').mockRejectedValue(new Error('usage unavailable'));
});

describe('SessionDetailsModal', () => {
  it('keeps hook order while opening and closing from a null session', () => {
    const onClose = vi.fn();
    const { rerender } = render(<SessionDetailsModal session={null} onClose={onClose} />);

    expect(screen.queryByRole('dialog')).toBeNull();

    expect(() => {
      rerender(<SessionDetailsModal session={baseSession} onClose={onClose} />);
    }).not.toThrow();
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText(baseSession.id)).toBeTruthy();

    expect(() => {
      rerender(<SessionDetailsModal session={null} onClose={onClose} />);
    }).not.toThrow();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('keeps Usage collapsed by default and shows all session identifiers', () => {
    render(<SessionDetailsModal session={baseSession} onClose={() => {}} />);

    expect(screen.getByRole('button', { name: /Usage/ }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('Credits（累计）')).toBeNull();
    expect(screen.getByText(baseSession.name)).toBeTruthy();
    expect(screen.getByText(baseSession.workdir!)).toBeTruthy();
    expect(screen.getByText(baseSession.id)).toBeTruthy();
    expect(screen.getByText(baseSession.cliSessionId!)).toBeTruthy();
    expect(screen.getByRole('button', { name: /System prompt/ }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('region', { name: 'System prompt content' })).toBeNull();
  });

  it('expands the current system prompt and preserves its line breaks', () => {
    const session = { ...baseSession, systemPrompt: 'You are a careful assistant.\nUse concise answers.' };
    render(<SessionDetailsModal session={session} onClose={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: /System prompt/ }));

    expect(screen.getByRole('region', { name: 'System prompt content' }).textContent).toBe(session.systemPrompt);
    expect(screen.getByRole('button', { name: /Usage/ })).toBeTruthy();
  });

  it('shows the missing system prompt state when the prompt is blank', () => {
    render(<SessionDetailsModal session={{ ...baseSession, systemPrompt: '   ' }} onClose={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: /System prompt/ }));

    expect(screen.getByRole('region', { name: 'System prompt content' }).textContent).toContain('暂无 / 未建立');
  });

  it('loads persisted usage on expand and renders credits plus token metrics', async () => {
    vi.mocked(api.fetchSessionUsage).mockResolvedValue({
      sessionId: baseSession.id, adapter: 'cbc', input: 101, output: 202,
      cache: { read: 30, write: 4, total: 34 }, total: { tokens: 303, credit: 12.3456 },
    });
    render(<SessionDetailsModal session={baseSession} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));
    expect(await screen.findByText('Credits（累计）')).toBeTruthy();
    expect(screen.getByText('12.35')).toBeTruthy();
    expect(screen.getByText('101')).toBeTruthy();
    expect(screen.getByText('202')).toBeTruthy();
    expect(screen.getByText(/总计 34.*读 30.*写 4/)).toBeTruthy();
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

  it('shows only provider-proven weekly and monthly codex windows', async () => {
    const codex: Session = {
      ...baseSession,
      id: 'ses_codex_quota',
      adapter: 'codex',
      totalUsage: { credit: 999.99 },
    };
    vi.spyOn(api, 'fetchSessionUsage').mockResolvedValue({
      sessionId: codex.id, adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
      codexQuota: {
        ok: true,
        stale: true,
        cacheMode: 'persisted',
        windows: {
          primary: { kind: 'five_hour', usage: { usedPercent: 5 } },
          secondary: { kind: 'weekly', usage: { usedPercent: 25, remainingPercent: 75 } },
          monthly: { kind: 'monthly', usage: { usedPercent: 40, remainingCredits: 12.5 } },
        },
      },
    });

    render(<SessionDetailsModal session={codex} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(await screen.findByText('周额度')).toBeTruthy();
    expect(screen.getByText('月额度')).toBeTruthy();
    expect(screen.getByText('Quota（最近缓存，可能已过期）')).toBeTruthy();
    expect(screen.getByText(/已使用 25%.*剩余 75%/)).toBeTruthy();
    expect(screen.getByText(/已使用 40%.*剩余 12\.5 credit/)).toBeTruthy();
    expect(screen.queryByText('999.99')).toBeNull();
    expect(screen.queryByText('已使用 5%')).toBeNull();
  });

  it('omits five-hour, unknown, and empty quota windows when no supported window exists', async () => {
    const codex: Session = { ...baseSession, id: 'ses_codex_no_supported_quota', adapter: 'codex' };
    useWorkerStore.setState({ workers: { [codex.id]: {
      id: 'worker-no-supported-quota',
      sessionId: codex.id,
      status: 'running',
      nativeRateLimits: {
        primary: { windowDurationMins: 300, usedPercent: 5 },
        unknown: { usedPercent: 77 },
        monthly: {},
      },
    } } });

    render(<SessionDetailsModal session={codex} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(await screen.findByText('输入 Token')).toBeTruthy();
    expect(screen.queryByLabelText('Codex quota')).toBeNull();
    expect(screen.queryByText('周额度')).toBeNull();
    expect(screen.queryByText('月额度')).toBeNull();
    expect(screen.queryByText('已使用 5%')).toBeNull();
    expect(screen.queryByText('已使用 77%')).toBeNull();
  });

  it('does not borrow quota windows from another session and shows missing windows clearly', async () => {
    const codex: Session = { ...baseSession, id: 'ses_codex_target', adapter: 'codex' };
    vi.spyOn(api, 'fetchSessionUsage').mockResolvedValue({
      sessionId: codex.id, adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
      codexQuota: {
        ok: true,
        windows: { secondary: { kind: 'weekly', usage: { usedPercent: 12 } } },
      },
    });

    render(<SessionDetailsModal session={codex} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(await screen.findByText('输入 Token')).toBeTruthy();
    expect(screen.getByText(/已使用 12%/)).toBeTruthy();
    expect(screen.queryByText('月额度')).toBeNull();
    expect(screen.queryByText(/已使用 91%/)).toBeNull();
  });

  it('shows an explicit empty state when the current worker is offline or absent', async () => {
    const codex: Session = { ...baseSession, id: 'ses_codex_offline', adapter: 'codex' };
    vi.spyOn(api, 'fetchSessionUsage').mockResolvedValue({
      sessionId: codex.id, adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
    });

    render(<SessionDetailsModal session={codex} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(await screen.findByText('输入 Token')).toBeTruthy();
    expect(await screen.findByText('当前没有可用的周/月 quota 缓存')).toBeTruthy();
    expect(screen.queryByText('周额度')).toBeNull();
    expect(screen.queryByText('月额度')).toBeNull();
  });

  it('degrades safely when clipboard is unavailable and displays missing values', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    const missing: Session = { ...baseSession, workdir: undefined, cliSessionId: null, totalUsage: null };
    render(<SessionDetailsModal session={missing} onClose={() => {}} />);

    expect(screen.getAllByText('暂无 / 未建立')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));
    expect(await screen.findByText('Credits（累计）')).toBeTruthy();
    expect(screen.getAllByText('暂无数据')).toHaveLength(4);
    fireEvent.click(screen.getByRole('button', { name: '复制工作目录' }));
    expect(useUIStore.getState().toastQueue.at(-1)?.type).toBe('error');
  });
});

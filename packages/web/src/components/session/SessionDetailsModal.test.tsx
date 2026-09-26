// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SessionDetailsModal } from './SessionDetailsModal';
import { clearSessionUsageCache } from './sessionUsageCache';
import { useUIStore } from '@/stores/uiStore';
import * as api from '@/services/api';
import type { Session, SessionUsageView } from '@/types';

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
  clearSessionUsageCache();
  useUIStore.setState({ toastQueue: [] });
});

beforeEach(() => {
  vi.spyOn(api, 'fetchSession').mockRejectedValue(new Error('session details unavailable'));
  vi.spyOn(api, 'fetchSessionUsage').mockRejectedValue(new Error('usage unavailable'));
});

function dialogCard(): HTMLElement {
  const card = document.body.querySelector<HTMLElement>('.modal-card');
  expect(card).toBeTruthy();
  return card!;
}

describe('SessionDetailsModal', () => {
  it('opts into the mobile fullscreen shell while keeping the desktop dialog unchanged', () => {
    render(<SessionDetailsModal session={baseSession} onClose={() => {}} />);

    const overlay = document.body.querySelector<HTMLElement>('.modal-overlay')!;
    const card = dialogCard();

    expect(overlay.className).toContain('modal-overlay--mobile-fullscreen');
    expect(overlay.className).toContain('md:p-4');
    expect(card.className).toContain('modal-card--mobile-fullscreen');
    expect(card.className).toContain('max-md:h-[100dvh]');
    expect(card.className).toContain('max-md:max-h-[100dvh]');
    expect(card.className).toContain('max-md:max-w-none');
    expect(card.className).toContain('max-md:rounded-none');
    expect(card.className).toContain('max-md:border-0');
    // Desktop (>= md) keeps the previous centered size="lg" window: every
    // viewport-filling override is max-md:-scoped and nothing resets the
    // desktop width/height/radius.
    expect(card.className).toContain('max-w-[42rem]');
    const viewportResets = card.className.split(/\s+/).filter((name) =>
      name.includes('100dvh') || name.endsWith('rounded-none') || name.endsWith('max-w-none'));
    expect(viewportResets.length).toBeGreaterThan(0);
    expect(viewportResets.every((name) => name.startsWith('max-md:'))).toBe(true);
  });

  it('keeps the title row and close button outside the scrollable content', () => {
    render(<SessionDetailsModal session={baseSession} onClose={() => {}} />);

    const card = dialogCard();
    const heading = screen.getByRole('heading', { name: 'Session Details' });
    const close = screen.getByRole('button', { name: 'Close' });
    const scroller = card.querySelector<HTMLElement>(':scope > div.overflow-y-auto');

    expect(scroller).toBeTruthy();
    expect(scroller!.contains(heading)).toBe(false);
    expect(scroller!.contains(close)).toBe(false);
  });

  it('keeps the usage loading indicator and summary fallbacks inside the fullscreen shell', async () => {
    let rejectUsage: (error: Error) => void = () => {};
    vi.mocked(api.fetchSessionUsage).mockImplementation(
      () => new Promise((_resolve, reject) => { rejectUsage = reject; }),
    );

    render(<SessionDetailsModal session={baseSession} onClose={() => {}} />);
    const usageButton = screen.getByRole('button', { name: /Usage/ });
    fireEvent.click(usageButton);

    expect(usageButton.textContent).toContain('加载中…');
    expect(usageButton.getAttribute('aria-expanded')).toBe('true');
    expect(dialogCard().className).toContain('max-md:h-[100dvh]');

    rejectUsage(new Error('usage unavailable'));

    expect(await screen.findByText('usage unavailable，当前显示已有数据')).toBeTruthy();
    expect(screen.getByText('Credits（累计）')).toBeTruthy();
    expect(screen.getByText('12.35')).toBeTruthy();
    expect(screen.getByText('100')).toBeTruthy();
    expect(screen.getAllByText('暂无数据')).toHaveLength(2);
    expect(usageButton.textContent).not.toContain('加载中…');
  });

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

    const usageButton = screen.getByRole('button', { name: /Usage/ });
    expect(usageButton.getAttribute('aria-expanded')).toBe('false');
    expect(usageButton.querySelector('svg.lucide-chevron-right')).not.toBeNull();
    expect(screen.queryByText('Credits（累计）')).toBeNull();
    expect(screen.getByText(baseSession.name)).toBeTruthy();
    expect(screen.getByText(baseSession.workdir!)).toBeTruthy();
    expect(screen.getByText(baseSession.id)).toBeTruthy();
    expect(screen.getByText(baseSession.cliSessionId!)).toBeTruthy();
    const systemPromptButton = screen.getByRole('button', { name: /System prompt/ });
    expect(systemPromptButton.getAttribute('aria-expanded')).toBe('false');
    expect(systemPromptButton.querySelector('svg.lucide-chevron-right')).not.toBeNull();
    expect(screen.queryByRole('region', { name: 'System prompt content' })).toBeNull();
  });

  it('expands the current system prompt and preserves its line breaks', () => {
    const session = { ...baseSession, systemPrompt: 'You are a careful assistant.\nUse concise answers.' };
    render(<SessionDetailsModal session={session} onClose={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: /System prompt/ }));

    expect(screen.getByRole('region', { name: 'System prompt content' }).textContent).toBe(session.systemPrompt);
    expect(screen.getByRole('button', { name: /System prompt/ }).querySelector('svg.lucide-chevron-down')).not.toBeNull();
    expect(screen.getByRole('button', { name: /Usage/ })).toBeTruthy();
  });

  it('loads the persisted system prompt after opening from a summary session', async () => {
    const summarySession: Session = { ...baseSession, systemPrompt: undefined };
    const fullSession: Session = {
      ...summarySession,
      systemPrompt: 'Persisted session instructions.\nKeep the response concise.',
    };
    vi.mocked(api.fetchSession).mockResolvedValue(fullSession);

    render(<SessionDetailsModal session={summarySession} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /System prompt/ }));

    expect(screen.getByText('暂无 / 未建立')).toBeTruthy();
    await waitFor(() => expect(
      screen.getByRole('region', { name: 'System prompt content' }).textContent,
    ).toBe(fullSession.systemPrompt));
    expect(api.fetchSession).toHaveBeenCalledWith(summarySession.id);
  });

  it('keeps the summary fallback when loading the full session fails', async () => {
    const summarySession: Session = { ...baseSession, systemPrompt: undefined };
    render(<SessionDetailsModal session={summarySession} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /System prompt/ }));

    await waitFor(() => expect(api.fetchSession).toHaveBeenCalledWith(summarySession.id));
    expect(screen.getByRole('region', { name: 'System prompt content' }).textContent)
      .toContain('暂无 / 未建立');
  });

  it('shows detail freshness and retries without replacing the summary fallback', async () => {
    const summarySession: Session = { ...baseSession, systemPrompt: undefined };
    vi.mocked(api.fetchSession)
      .mockRejectedValueOnce(new Error('metadata timeout'))
      .mockResolvedValueOnce({
        ...summarySession,
        systemPrompt: 'Fresh prompt',
        updatedAt: '2026-09-19T01:02:03+00:00',
      });

    render(<SessionDetailsModal session={summarySession} onClose={() => {}} />);
    expect(await screen.findByText('error')).toBeTruthy();
    expect(screen.getByText(/metadata timeout/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('updated')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /System prompt/ }));
    expect(screen.getByText('Fresh prompt')).toBeTruthy();
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

  it('reuses the Session-scoped usage cache after close and re-open', async () => {
    const refreshed = {
      sessionId: baseSession.id, adapter: 'cbc', input: 111, output: 222,
      cache: { read: null, write: null, total: null }, total: { tokens: 333, credit: 12.3 },
    };
    let resolveRefresh: (value: typeof refreshed) => void = () => {};
    vi.mocked(api.fetchSessionUsage)
      .mockResolvedValueOnce({ ...refreshed, input: 101, output: 202 })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRefresh = resolve; }));

    const { rerender } = render(<SessionDetailsModal session={baseSession} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));
    expect(await screen.findByText('101')).toBeTruthy();

    rerender(<SessionDetailsModal session={null} onClose={() => {}} />);
    rerender(<SessionDetailsModal session={baseSession} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(screen.getByText('101')).toBeTruthy();
    await waitFor(() => expect(api.fetchSessionUsage).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('button', { name: /Usage/ }).textContent).toContain('更新中…');

    resolveRefresh(refreshed);
    expect(await screen.findByText('111')).toBeTruthy();
  });

  it('shows cached Codex quota before a slow refresh and then applies the fresh result', async () => {
    const cached: SessionUsageView = {
      sessionId: 'ses_codex_slow_refresh', adapter: 'codex', input: 10, output: 20,
      cache: { read: null, write: null, total: null }, total: { tokens: 30, credit: null },
      codexQuota: {
        ok: true, stale: true, windows: { primary: { kind: 'five_hour', usage: { usedPercent: 10 } } },
      },
    };
    let resolveQuota: (value: NonNullable<typeof cached.codexQuota>) => void = () => {};
    vi.mocked(api.fetchSessionUsage).mockResolvedValue(cached);
    vi.spyOn(api, 'fetchCodexQuota').mockImplementation(
      () => new Promise((resolve) => { resolveQuota = resolve; }),
    );

    render(<SessionDetailsModal session={{ ...baseSession, id: cached.sessionId, adapter: 'codex' }} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(await screen.findByText(/已使用 10%/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Usage/ }).textContent).toContain('更新中…');

    resolveQuota({
      ok: true, stale: false,
      windows: { primary: { kind: 'five_hour', usage: { usedPercent: 20 } } },
    });
    expect(await screen.findByText(/已使用 20%/)).toBeTruthy();
  });

  it('keeps cached quota and marks stale/error when refresh fails', async () => {
    const cached: SessionUsageView = {
      sessionId: 'ses_codex_refresh_error', adapter: 'codex', input: 10, output: 20,
      cache: { read: null, write: null, total: null }, total: { tokens: 30, credit: null },
      codexQuota: {
        ok: true, stale: false, windows: { primary: { kind: 'five_hour', usage: { usedPercent: 10 } } },
      },
    };
    vi.mocked(api.fetchSessionUsage).mockResolvedValue(cached);
    vi.spyOn(api, 'fetchCodexQuota').mockRejectedValue(new Error('network timeout'));

    render(<SessionDetailsModal session={{ ...baseSession, id: cached.sessionId, adapter: 'codex' }} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(await screen.findByText('Quota（最近缓存，可能已过期）')).toBeTruthy();
    expect(await screen.findByText(/Quota 刷新失败：network timeout，当前显示缓存/)).toBeTruthy();
    expect(screen.getByText(/已使用 10%/)).toBeTruthy();
  });

  it('keeps the base Usage loading/error path when there is no quota cache', async () => {
    let rejectQuota: (error: Error) => void = () => {};
    const empty = {
      sessionId: 'ses_codex_no_cache', adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
    };
    vi.mocked(api.fetchSessionUsage).mockResolvedValue(empty);
    vi.spyOn(api, 'fetchCodexQuota').mockImplementation(
      () => new Promise((_resolve, reject) => { rejectQuota = reject; }),
    );

    render(<SessionDetailsModal session={{ ...baseSession, id: empty.sessionId, adapter: 'codex', totalUsage: null }} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(await screen.findByText('Quota 加载中…')).toBeTruthy();
    expect(screen.getByText('输入 Token')).toBeTruthy();
    rejectQuota(new Error('quota unavailable'));
    expect(await screen.findByText(/Quota 刷新失败：quota unavailable/)).toBeTruthy();
    expect(screen.getByText('当前没有可用的五小时/周/月 quota 缓存')).toBeTruthy();
  });

  it('ignores an old Session usage response after switching Sessions', async () => {
    const sessionA = { ...baseSession, id: 'ses-usage-a' };
    const sessionB = { ...baseSession, id: 'ses-usage-b', totalUsage: null };
    let resolveA: (value: SessionUsageView) => void = () => {};
    let resolveB: (value: SessionUsageView) => void = () => {};
    vi.mocked(api.fetchSessionUsage).mockImplementation((id) => new Promise((resolve) => {
      if (id === sessionA.id) resolveA = resolve;
      else resolveB = resolve;
    }));
    const usage = (sessionId: string, input: number): SessionUsageView => ({
      sessionId, adapter: 'cbc', input, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
    });

    const { rerender } = render(<SessionDetailsModal session={sessionA} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));
    rerender(<SessionDetailsModal session={sessionB} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    resolveA(usage(sessionA.id, 111));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText('111')).toBeNull();

    resolveB(usage(sessionB.id, 222));
    expect(await screen.findByText('222')).toBeTruthy();
    expect(screen.queryByText('111')).toBeNull();
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

  it('shows provider-proven five-hour, weekly and monthly codex windows', async () => {
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

    expect(await screen.findByText('五小时额度')).toBeTruthy();
    expect(await screen.findByText('周额度')).toBeTruthy();
    expect(screen.getByText('月额度')).toBeTruthy();
    expect(screen.getByText('Quota（最近缓存，可能已过期）')).toBeTruthy();
    expect(screen.getByText(/已使用 25%.*剩余 75%/)).toBeTruthy();
    expect(screen.getByText(/已使用 40%.*剩余 12\.5 credit/)).toBeTruthy();
    expect(screen.queryByText('999.99')).toBeNull();
    expect(screen.getByText(/已使用 5%/)).toBeTruthy();
  });

  it('omits unknown and empty quota windows when no provider data exists', async () => {
    const codex: Session = { ...baseSession, id: 'ses_codex_no_supported_quota', adapter: 'codex' };
    vi.spyOn(api, 'fetchSessionUsage').mockResolvedValue({
      sessionId: codex.id, adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
      codexQuota: {
        ok: true,
        windows: {
          first: { kind: 'unknown', usage: { usedPercent: 5 } },
          secondary: { kind: 'weekly', usage: {} },
          monthly: {},
        },
      },
    });

    render(<SessionDetailsModal session={codex} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Usage/ }));

    expect(await screen.findByText('输入 Token')).toBeTruthy();
    expect(screen.getByLabelText('Codex quota')).toBeTruthy();
    expect(screen.queryByText('周额度')).toBeNull();
    expect(screen.queryByText('月额度')).toBeNull();
    expect(screen.queryByText('已使用 5%')).toBeNull();
    expect(screen.getByText('当前没有可用的五小时/周/月 quota 缓存')).toBeTruthy();
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
    expect(await screen.findByText('当前没有可用的五小时/周/月 quota 缓存')).toBeTruthy();
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

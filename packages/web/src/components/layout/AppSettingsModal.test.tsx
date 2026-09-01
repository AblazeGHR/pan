// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import { AppSettingsModal } from './AppSettingsModal';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';

const {
  fetchCodexModelsMock,
  refreshCodexOfficialModelsMock,
  fetchMainRestartStatusMock,
  restartMainServiceMock,
  fetchHealthMock,
} = vi.hoisted(() => ({
  fetchCodexModelsMock: vi.fn(),
  refreshCodexOfficialModelsMock: vi.fn(),
  fetchMainRestartStatusMock: vi.fn(),
  restartMainServiceMock: vi.fn(),
  fetchHealthMock: vi.fn(),
}));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchRemoteStatus: vi.fn().mockResolvedValue({
      available: false,
      enabled: false,
      running: false,
    }),
    fetchMainRestartStatus: fetchMainRestartStatusMock,
    restartMainService: restartMainServiceMock,
    fetchHealth: fetchHealthMock,
    fetchCodexModels: fetchCodexModelsMock,
    refreshCodexOfficialModels: refreshCodexOfficialModelsMock,
  };
});

// AppSettingsModal renders through a portal to document.body — query there,
// not the render() container.
function overlayEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.app-settings-overlay');
  expect(el).toBeTruthy();
  return el!;
}

function cardEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.app-settings-card');
  expect(el).toBeTruthy();
  return el!;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('AppSettingsModal', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
    fetchCodexModelsMock.mockResolvedValue({
      models: ['gpt-5-codex', 'gpt-5-mini'],
      default: 'gpt-5-codex',
    });
    refreshCodexOfficialModelsMock.mockResolvedValue({
      ok: true,
      before: ['gpt-5-codex'],
      after: ['gpt-5.1-codex', 'gpt-5-mini'],
    });
    fetchMainRestartStatusMock.mockResolvedValue({
      available: true,
      pending: false,
      platform: 'nt',
    });
    restartMainServiceMock.mockResolvedValue({
      ok: true,
      status: 'scheduled',
      requestId: 'restart-1',
    });
    fetchHealthMock.mockResolvedValue({ status: 'ok', version: 'test' });
    fetchMainRestartStatusMock.mockClear();
    restartMainServiceMock.mockClear();
    fetchHealthMock.mockClear();
  });

  it('renders nothing when closed', () => {
    render(<AppSettingsModal open={false} onClose={() => {}} />);
    expect(document.body.querySelector('.app-settings-overlay')).toBeNull();
  });

  it('renders the 4 settings items plus Reset', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const card = cardEl();
    expect(card.textContent).toContain('Default group by');
    expect(card.querySelectorAll('[role="switch"]')).toHaveLength(3);
    expect(card.textContent).toContain('Reset to defaults');
    expect(card.textContent).toContain('Notification');
  });

  it('shows the Codex warning Toast option on the Notification tab', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const notificationTab = Array.from(
      document.body.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent?.includes('Notification'))!;
    fireEvent.click(notificationTab);
    expect(cardEl().textContent).toContain('Codex warnings via Toast');
    expect(cardEl().textContent).toContain('CBC warnings via Toast');
    expect(cardEl().querySelectorAll('[role="switch"]')).toHaveLength(1);
    fireEvent.click(cardEl().querySelector('[role="switch"]')!);
    expect(useAppSettingsStore.getState().notifications.codexWarningToast).toBe(false);
  });

  it('loads and replaces the Codex whitelist on the Adapter tab', async () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const adapterTab = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.includes('Adapter'),
    )!;
    fireEvent.click(adapterTab);

    await waitFor(() => expect(cardEl().textContent).toContain('gpt-5-codex, gpt-5-mini'));
    fireEvent.click(
      Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((button) =>
        button.textContent?.includes('替换为官方模型目录'),
      )!,
    );
    await waitFor(() => expect(cardEl().textContent).toContain('after: gpt-5.1-codex, gpt-5-mini'));
    expect(refreshCodexOfficialModelsMock).toHaveBeenCalledTimes(1);
  });

  it('toggles the Codex terminal input popup option and persists it', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(
      Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((button) =>
        button.textContent?.includes('Adapter'),
      )!,
    );
    const terminalSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) => element.textContent?.includes('Show Codex terminal input popup'))!;

    expect(terminalSwitch.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(terminalSwitch);
    expect(useAppSettingsStore.getState().showCodexTerminalInput).toBe(true);
  });

  it('is full-screen on mobile and ~75% of the viewport on desktop', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const card = cardEl();
    // Mobile (base): edge-to-edge, no rounded corners.
    expect(card.className).toContain('w-full');
    expect(card.className).toContain('h-full');
    // Desktop (md+): centered card covering ~3/4 of the window.
    expect(card.className).toContain('md:w-[75vw]');
    expect(card.className).toContain('md:h-[75vh]');
  });

  it('closes when the backdrop is clicked', () => {
    const onClose = vi.fn();
    render(<AppSettingsModal open onClose={onClose} />);
    fireEvent.click(overlayEl());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close when clicking inside the card', () => {
    const onClose = vi.fn();
    render(<AppSettingsModal open onClose={onClose} />);
    fireEvent.click(cardEl());
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<AppSettingsModal open onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('toggles a setting through a switch and writes the store', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const switches = Array.from(document.body.querySelectorAll<HTMLElement>('[role="switch"]'));
    expect(switches).toHaveLength(3);
    // meta-agent is on by default; toggle it off.
    expect(switches[0]!.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(switches[0]!);
    expect(useAppSettingsStore.getState().showMetaAgent).toBe(false);
    expect(switches[0]!.getAttribute('aria-checked')).toBe('false');
  });

  it('changes default group by via the select', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const select = document.body.querySelector<HTMLSelectElement>('select')!;
    fireEvent.change(select, { target: { value: 'workdir' } });
    expect(useAppSettingsStore.getState().defaultGroupBy).toBe('workdir');
  });

  it('resets all settings to defaults', () => {
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      defaultGroupBy: 'manager',
      showMetaAgent: false,
      showTaskAgent: false,
      showQQ: false,
      notifications: { codexWarningToast: false },
    });
    render(<AppSettingsModal open onClose={() => {}} />);
    const resetBtn = Array.from(document.body.querySelectorAll<HTMLElement>('button')).find((b) =>
      b.textContent?.includes('Reset to defaults'),
    )!;
    fireEvent.click(resetBtn);
    const s = useAppSettingsStore.getState();
    expect(s.defaultGroupBy).toBe(DEFAULT_SETTINGS.defaultGroupBy);
    expect(s.showMetaAgent).toBe(true);
    expect(s.showTaskAgent).toBe(true);
    expect(s.showQQ).toBe(true);
    expect(s.notifications.codexWarningToast).toBe(true);
  });

  it('requires confirmation and reports successful main-service recovery', async () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const restartButton = await waitFor(() => {
      const button = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
        (b) => b.textContent?.includes('Restart Pan main service'),
      );
      expect(button).toBeTruthy();
      return button!;
    });

    fireEvent.click(restartButton);
    expect(cardEl().textContent).toContain('Confirm restart');
    fireEvent.click(
      Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
        b.textContent?.includes('Confirm restart'),
      )!,
    );
    expect(restartMainServiceMock).toHaveBeenCalledTimes(1);
    expect(cardEl().textContent).toContain('Waiting for /api/health');

    // The first probe is delayed so a still-live old process cannot be
    // mistaken for the replacement.  Health resolves on the first probe.
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1050));
    });
    await waitFor(() => expect(cardEl().textContent).toContain('healthy again'));
    expect(fetchHealthMock).toHaveBeenCalledTimes(1);
  });

  it('allows stopping the bounded health check and does not call health forever', async () => {
    vi.useFakeTimers();
    try {
      render(<AppSettingsModal open onClose={() => {}} />);
      await act(async () => {
        await Promise.resolve();
      });
      fireEvent.click(
        Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
          b.textContent?.includes('Restart Pan main service'),
        )!,
      );
      fireEvent.click(
        Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
          b.textContent?.includes('Confirm restart'),
        )!,
      );
      fireEvent.click(
        Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
          b.textContent?.includes('Stop checking'),
        )!,
      );
      expect(cardEl().textContent).toContain('Health checking stopped');
      await act(async () => {
        await vi.runAllTimersAsync();
      });
      expect(fetchHealthMock).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('disables the main-service control when restart scripts are unavailable', async () => {
    fetchMainRestartStatusMock.mockResolvedValueOnce({
      available: false,
      pending: false,
      platform: 'posix',
      reason: 'main service restart is available only on Windows',
    });
    render(<AppSettingsModal open onClose={() => {}} />);
    const button = await waitFor(() => {
      const found = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
        (b) => b.textContent?.includes('Restart Pan main service'),
      );
      expect(found).toBeTruthy();
      expect(found!.disabled).toBe(true);
      return found!;
    });
    expect(button.disabled).toBe(true);
    await waitFor(() => expect(cardEl().textContent).toContain('available only on Windows'));
  });

  it('shows a finite timeout when the restarted service never becomes healthy', async () => {
    vi.useFakeTimers();
    try {
      fetchHealthMock.mockRejectedValue(new Error('connection refused'));
      render(<AppSettingsModal open onClose={() => {}} />);
      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });
      fireEvent.click(
        Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
          b.textContent?.includes('Restart Pan main service'),
        )!,
      );
      fireEvent.click(
        Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
          b.textContent?.includes('Confirm restart'),
        )!,
      );
      await act(async () => {
        await vi.runAllTimersAsync();
      });
      expect(cardEl().textContent).toContain('health check timed out');
      expect(fetchHealthMock).toHaveBeenCalledTimes(20);
    } finally {
      vi.useRealTimers();
    }
  });
});

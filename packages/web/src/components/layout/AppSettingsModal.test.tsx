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
  fetchMainExitStatusMock,
  exitMainServiceMock,
  updateUiSettingsMock,
} = vi.hoisted(() => ({
  fetchCodexModelsMock: vi.fn(),
  refreshCodexOfficialModelsMock: vi.fn(),
  fetchMainRestartStatusMock: vi.fn(),
  restartMainServiceMock: vi.fn(),
  fetchHealthMock: vi.fn(),
  fetchMainExitStatusMock: vi.fn(),
  exitMainServiceMock: vi.fn(),
  updateUiSettingsMock: vi.fn(),
}));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    updateUiSettings: updateUiSettingsMock,
    fetchRemoteStatus: vi.fn().mockResolvedValue({
      available: false,
      enabled: false,
      running: false,
    }),
    fetchMainRestartStatus: fetchMainRestartStatusMock,
    restartMainService: restartMainServiceMock,
    fetchMainExitStatus: fetchMainExitStatusMock,
    exitMainService: exitMainServiceMock,
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
    fetchMainExitStatusMock.mockResolvedValue({
      available: true,
      pending: false,
      platform: 'nt',
      stage: 'idle',
    });
    exitMainServiceMock.mockResolvedValue({
      ok: true,
      status: 'scheduled',
      requestId: 'exit-1',
    });
    fetchMainRestartStatusMock.mockClear();
    restartMainServiceMock.mockClear();
    fetchHealthMock.mockClear();
    fetchMainExitStatusMock.mockClear();
    exitMainServiceMock.mockClear();
    updateUiSettingsMock.mockReset();
    updateUiSettingsMock.mockResolvedValue({});
  });

  it('renders nothing when closed', () => {
    render(<AppSettingsModal open={false} onClose={() => {}} />);
    expect(document.body.querySelector('.app-settings-overlay')).toBeNull();
  });

  it('renders the 6 settings items plus Reset', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const card = cardEl();
    expect(card.textContent).toContain('Default group by');
    expect(document.getElementById('app-settings-tab-preferences')?.textContent)
      .toContain('Preferences');
    expect(card.textContent).toContain('Reset to defaults');
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);
    expect(card.querySelectorAll('[role="switch"]')).toHaveLength(7);
    expect(card.textContent).toContain('Notification');
  });

  it('keeps all settings tabs reachable in a horizontal-only scroller', () => {
    render(<AppSettingsModal open onClose={() => {}} />);

    const tabList = document.body.querySelector<HTMLElement>('[role="tablist"]')!;
    const tabs = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    const panel = document.getElementById('app-settings-tabpanel')!;
    expect(tabs.map((tab) => tab.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
      'General',
      'Preferences',
      'Appearance',
      'Notification',
      'Adapter',
    ]);
    expect(tabList.className).toContain('overflow-x-auto');
    expect(tabList.className).toContain('overscroll-x-contain');
    expect(tabList.className).toContain('touch-pan-x');
    expect(tabs.every((tab) => tab.className.includes('shrink-0'))).toBe(true);
    expect(tabs.every((tab) => tab.className.includes('whitespace-nowrap'))).toBe(true);
    expect(cardEl().className).toContain('overflow-hidden');
    expect(panel.className).toContain('overflow-y-auto');
    expect(panel.className).not.toContain('overflow-x-auto');

    fireEvent.keyDown(tabs[0]!, { key: 'End' });
    expect(tabs[4]!.getAttribute('aria-selected')).toBe('true');
    expect(tabs[4]!.tabIndex).toBe(0);
    expect(tabs.slice(0, 4).every((tab) => tab.tabIndex === -1)).toBe(true);
    fireEvent.keyDown(tabs[4]!, { key: 'ArrowLeft' });
    expect(tabs[3]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(tabs[3]!, { key: 'Home' });
    expect(tabs[0]!.getAttribute('aria-selected')).toBe('true');
    expect(panel.getAttribute('aria-labelledby')).toBe('app-settings-tab-general');
  });

  it('shows message visibility on the Appearance tab and keeps its settings and persistence', () => {
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      showMetaAgent: false,
      showTaskAgent: true,
      showQQ: false,
    });
    render(<AppSettingsModal open onClose={() => {}} />);

    const tabs = document.body.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    const generalTab = document.getElementById('app-settings-tab-general') as HTMLButtonElement;
    const appearanceTab = document.getElementById(
      'app-settings-tab-appearance',
    ) as HTMLButtonElement;

    expect(tabs).toHaveLength(5);
    expect(appearanceTab.getAttribute('aria-selected')).toBe('false');
    expect(cardEl().textContent).not.toContain('Message visibility');
    expect(cardEl().textContent).not.toContain('Show meta-agent info');

    fireEvent.keyDown(generalTab, { key: 'ArrowRight' });
    expect(document.getElementById('app-settings-tab-preferences')?.getAttribute('aria-selected'))
      .toBe('true');
    fireEvent.keyDown(document.getElementById('app-settings-tab-preferences')!, {
      key: 'ArrowRight',
    });

    expect(appearanceTab.getAttribute('aria-selected')).toBe('true');
    expect(document.getElementById('app-settings-tabpanel')?.getAttribute('aria-labelledby')).toBe(
      'app-settings-tab-appearance',
    );
    expect(cardEl().textContent).toContain('Message visibility');
    expect(cardEl().textContent).toContain('Message grouping');
    const metaSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) => element.textContent?.includes('Show meta-agent info'))!;
    const taskSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) => element.textContent?.includes('Show task-agent info'))!;
    const qqSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) => element.textContent?.includes('Show QQ messages'))!;
    expect(metaSwitch.getAttribute('aria-checked')).toBe('false');
    expect(taskSwitch.getAttribute('aria-checked')).toBe('true');
    expect(qqSwitch.getAttribute('aria-checked')).toBe('false');

    fireEvent.click(metaSwitch);
    expect(useAppSettingsStore.getState().showMetaAgent).toBe(true);
    expect(updateUiSettingsMock).toHaveBeenCalledWith({ showMetaAgent: true });

    fireEvent.click(generalTab);
    expect(cardEl().textContent).not.toContain('Message visibility');
    fireEvent.click(appearanceTab);
    expect(
      Array.from(document.body.querySelectorAll<HTMLElement>('[role="switch"]')).find(
        (element) => element.textContent?.includes('Show meta-agent info'),
      )?.getAttribute('aria-checked'),
    ).toBe('true');
  });

  it('switches Bubble view on and off from Appearance and persists the selected style', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);

    expect(cardEl().textContent).toContain('Chat view');
    const bubbleSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) => element.textContent?.includes('Use Bubble chat view'))!;
    expect(bubbleSwitch.getAttribute('aria-checked')).toBe('false');

    fireEvent.click(bubbleSwitch);
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('bubble');
    expect(updateUiSettingsMock).toHaveBeenCalledWith({ chatViewStyle: 'bubble' });
    expect(bubbleSwitch.getAttribute('aria-checked')).toBe('true');

    fireEvent.click(bubbleSwitch);
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('tui');
    expect(updateUiSettingsMock).toHaveBeenLastCalledWith({ chatViewStyle: 'tui' });
    expect(bubbleSwitch.getAttribute('aria-checked')).toBe('false');
  });

  it('toggles the default new Session Workspace preference and persists it', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-preferences')!);

    expect(cardEl().textContent).toContain('New Sessions');
    const workspaceSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) =>
      element.textContent?.includes('Place new Sessions in the current Workspace by default'),
    )!;
    expect(workspaceSwitch.getAttribute('aria-checked')).toBe('true');

    fireEvent.click(workspaceSwitch);

    expect(useAppSettingsStore.getState().defaultNewSessionToCurrentWorkspace).toBe(false);
    expect(updateUiSettingsMock).toHaveBeenCalledWith({
      defaultNewSessionToCurrentWorkspace: false,
    });
    expect(workspaceSwitch.getAttribute('aria-checked')).toBe('false');
  });

  it('toggles merged tool/thinking groups on the Appearance tab and persists the setting', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);

    const mergeSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) =>
      element.textContent?.includes('Group consecutive tool and thinking blocks'),
    )!;
    expect(mergeSwitch.getAttribute('aria-checked')).toBe('false');
    expect(mergeSwitch.textContent).toContain('disabled by default');

    fireEvent.click(mergeSwitch);

    expect(useAppSettingsStore.getState().mergeConsecutiveNonBodyBlocks).toBe(true);
    expect(updateUiSettingsMock).toHaveBeenCalledWith({ mergeConsecutiveNonBodyBlocks: true });
    expect(mergeSwitch.getAttribute('aria-checked')).toBe('true');
  });

  it('toggles the per-session scroll memory on the Appearance tab and persists it', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);

    expect(cardEl().textContent).toContain('Scroll position');
    const scrollSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) => element.textContent?.includes('Keep reading position per session'))!;
    expect(scrollSwitch).toBeDefined();
    // TUI sessions start at the newest message unless the reader opts in.
    expect(scrollSwitch.getAttribute('aria-checked')).toBe('false');

    fireEvent.click(scrollSwitch);

    expect(useAppSettingsStore.getState().keepScrollOnSessionSwitch).toBe(true);
    expect(updateUiSettingsMock).toHaveBeenCalledWith({ keepScrollOnSessionSwitch: true });
    expect(scrollSwitch.getAttribute('aria-checked')).toBe('true');
  });

  it('toggles the message navigation rail on the Appearance tab and persists it', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);

    expect(cardEl().textContent).toContain('Quick location');
    const railSwitch = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).find((element) => element.textContent?.includes('Show message navigation rail'))!;
    expect(railSwitch).toBeDefined();
    // Off by default: the rail is an opt-in strip.
    expect(railSwitch.getAttribute('aria-checked')).toBe('false');

    fireEvent.click(railSwitch);

    expect(useAppSettingsStore.getState().showMessageNavigationRail).toBe(true);
    expect(updateUiSettingsMock).toHaveBeenCalledWith({ showMessageNavigationRail: true });
    expect(railSwitch.getAttribute('aria-checked')).toBe('true');

    // It remains the master enable: turning it off persists false so ChatView
    // can unmount the rail and release its history index.
    fireEvent.click(railSwitch);
    expect(useAppSettingsStore.getState().showMessageNavigationRail).toBe(false);
    expect(updateUiSettingsMock).toHaveBeenLastCalledWith({ showMessageNavigationRail: false });
    expect(railSwitch.getAttribute('aria-checked')).toBe('false');
  });

  it('shows the Codex warning Toast option on the Notification tab', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const notificationTab = Array.from(
      document.body.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent?.includes('Notification'))!;
    fireEvent.click(notificationTab);
    expect(cardEl().textContent).toContain('Codex warnings via Toast');
    expect(cardEl().textContent).toContain('CBC warnings via Toast');
    expect(cardEl().querySelectorAll('[role="switch"]')).toHaveLength(2);
    const codexWarningSwitch = Array.from(cardEl().querySelectorAll<HTMLElement>('[role="switch"]'))
      .find((element) => element.textContent?.includes('Codex warnings via Toast'))!;
    fireEvent.click(codexWarningSwitch);
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

  it('shows a readable Codex refresh error in the Adapter tab', async () => {
    const message = 'HTTP 502: codex debug models failed: authentication required';
    refreshCodexOfficialModelsMock.mockRejectedValueOnce(new Error(message));
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(
      Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((button) =>
        button.textContent?.includes('Adapter'),
      )!,
    );

    await waitFor(() => expect(cardEl().textContent).toContain('替换为官方模型目录'));
    fireEvent.click(
      Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((button) =>
        button.textContent?.includes('替换为官方模型目录'),
      )!,
    );
    await waitFor(() => expect(cardEl().textContent).toContain(message));
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
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);
    const switches = Array.from(document.body.querySelectorAll<HTMLElement>('[role="switch"]'));
    expect(switches).toHaveLength(7);
    // meta-agent is on by default; toggle it off.
    const metaSwitch = switches.find((element) => element.textContent?.includes('Show meta-agent info'))!;
    expect(metaSwitch.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(metaSwitch);
    expect(useAppSettingsStore.getState().showMetaAgent).toBe(false);
    expect(metaSwitch.getAttribute('aria-checked')).toBe('false');
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
      notifications: { codexWarningToast: false, confirmCrossWorkspaceManagement: true },
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
    expect(s.notifications.confirmCrossWorkspaceManagement).toBe(true);
    expect(s.keepScrollOnSessionSwitch).toBe(DEFAULT_SETTINGS.keepScrollOnSessionSwitch);
    expect(s.showMessageNavigationRail).toBe(DEFAULT_SETTINGS.showMessageNavigationRail);
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

  it('requires confirmation and schedules a stop-only Pan exit without health polling', async () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const exitButton = await waitFor(() => {
      const button = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
        (b) => b.textContent?.includes('Exit Pan main service'),
      );
      expect(button).toBeTruthy();
      return button!;
    });

    fireEvent.click(exitButton);
    expect(cardEl().textContent).toContain('Confirm exit');
    fireEvent.click(
      Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
        b.textContent?.includes('Confirm exit'),
      )!,
    );
    await waitFor(() => expect(exitMainServiceMock).toHaveBeenCalledTimes(1));
    expect(cardEl().textContent).toContain('No health-recovery check will run');
    expect(fetchHealthMock).not.toHaveBeenCalled();
  });

  it('shows an unavailable exit control and API error state', async () => {
    fetchMainExitStatusMock.mockResolvedValueOnce({
      available: false,
      pending: false,
      platform: 'posix',
      stage: 'idle',
      reason: 'main service exit is available only on Windows',
    });
    render(<AppSettingsModal open onClose={() => {}} />);
    const button = await waitFor(() => {
      const found = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
        (b) => b.textContent?.includes('Exit Pan main service'),
      );
      expect(found).toBeTruthy();
      expect(found!.disabled).toBe(true);
      return found!;
    });
    expect(button.disabled).toBe(true);
    await waitFor(() =>
      expect(cardEl().textContent).toContain('main service exit is available only on Windows'),
    );
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

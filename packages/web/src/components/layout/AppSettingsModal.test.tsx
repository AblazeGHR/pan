// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor, act, screen } from '@testing-library/react';
import { AppSettingsModal } from './AppSettingsModal';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';

const {
  fetchCodexModelsMock,
  refreshCodexOfficialModelsMock,
  fetchMainRestartStatusMock,
  restartMainServiceMock,
  fetchHealthMock,
  fetchMainExitStatusMock,
  fetchSessionLifecyclePreferencesMock,
  updateSessionLifecyclePreferencesMock,
  exitMainServiceMock,
  updateUiSettingsMock,
  fetchDataCatalogMock,
  fetchDataRetentionMock,
  updateDataRetentionMock,
  fetchCompletedJobRetentionSettingsMock,
  updateCompletedJobRetentionSettingsMock,
} = vi.hoisted(() => ({
  fetchCodexModelsMock: vi.fn(),
  refreshCodexOfficialModelsMock: vi.fn(),
  fetchMainRestartStatusMock: vi.fn(),
  restartMainServiceMock: vi.fn(),
  fetchHealthMock: vi.fn(),
  fetchMainExitStatusMock: vi.fn(),
  fetchSessionLifecyclePreferencesMock: vi.fn(),
  updateSessionLifecyclePreferencesMock: vi.fn(),
  exitMainServiceMock: vi.fn(),
  updateUiSettingsMock: vi.fn(),
  fetchDataCatalogMock: vi.fn(),
  fetchDataRetentionMock: vi.fn(),
  updateDataRetentionMock: vi.fn(),
  fetchCompletedJobRetentionSettingsMock: vi.fn(),
  updateCompletedJobRetentionSettingsMock: vi.fn(),
}));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    updateUiSettings: updateUiSettingsMock,
    fetchDataCatalog: fetchDataCatalogMock,
    fetchDataRetention: fetchDataRetentionMock,
    updateDataRetention: updateDataRetentionMock,
    fetchCompletedJobRetentionSettings: fetchCompletedJobRetentionSettingsMock,
    updateCompletedJobRetentionSettings: updateCompletedJobRetentionSettingsMock,
    fetchRemoteStatus: vi.fn().mockResolvedValue({
      available: false,
      enabled: false,
      running: false,
    }),
    fetchMainRestartStatus: fetchMainRestartStatusMock,
    restartMainService: restartMainServiceMock,
    fetchMainExitStatus: fetchMainExitStatusMock,
    fetchSessionLifecyclePreferences: fetchSessionLifecyclePreferencesMock,
    updateSessionLifecyclePreferences: updateSessionLifecyclePreferencesMock,
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

const defaultJobRetentionRules = {
  completed: { enabled: false, days: null },
  failed: { enabled: false, days: null },
  timed_out: { enabled: false, days: null },
  cancelled: { enabled: false, days: null },
  logs: { enabled: false, days: null },
};

function jobRetentionResponse(rules = defaultJobRetentionRules) {
  const configValidity = {
    completed: true,
    failed: true,
    timed_out: true,
    cancelled: true,
    logs: true,
  };
  const lastRuns = {
    completed: null,
    failed: null,
    timed_out: null,
    cancelled: null,
    logs: null,
  };
  return {
    settings: rules.completed,
    rules,
    configValid: true,
    configValidity,
    lastRun: null,
    lastRuns,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('AppSettingsModal', () => {
  beforeEach(() => {
    // McpRemoteSettings uses fetch directly rather than the mocked API module.
    // Keep the real panel mounted without adding an unrelated network-error alert.
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (input !== '/api/remote/mcp' || init?.method !== 'GET') {
        throw new Error(`Unexpected settings fetch: ${init?.method} ${String(input)}`);
      }
      return new Response(JSON.stringify({
        config: {
          enabled: false,
          port: 8769,
          public_hostname: '',
          access_issuer: '',
          access_audience: '',
          config_path: '',
          binary_path: '',
        },
        publicUrl: '',
        gateway: { listening: false },
        tunnel: { running: false },
      }), { headers: { 'Content-Type': 'application/json' } });
    }));
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
    fetchSessionLifecyclePreferencesMock.mockReset();
    fetchSessionLifecyclePreferencesMock.mockResolvedValue({
      exitStrategy: 'ask',
      startupPreference: 'ask',
    });
    updateSessionLifecyclePreferencesMock.mockReset();
    updateSessionLifecyclePreferencesMock.mockImplementation(async (preferences) => preferences);
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
    fetchDataCatalogMock.mockReset();
    fetchDataCatalogMock.mockResolvedValue({
      categories: [
        {
          id: 'sessions-history',
          name: 'Sessions 元数据与 history',
          purpose: 'Session JSON、history JSONL 与队列。',
          policyStatus: 'data_retention_policy',
          paths: [
            {
              label: 'Sessions 与 history 目录',
              path: 'D:\\Pan\\data\\sessions',
              exists: false,
              source: 'default',
              overridden: false,
              external: false,
            },
          ],
          note: '仅按明确配置的保留期清理。',
        },
        {
          id: 'jobs-records',
          name: 'Jobs 记录',
          purpose: '统一 Jobs JSON 记录。',
          policyStatus: 'jobs_api_managed',
          paths: [
            {
              label: 'jobs 目录',
              path: 'E:\\PanData\\jobs',
              exists: true,
              source: 'environment: PAN_BACKGROUND_JOBS_DIR',
              overridden: true,
              external: true,
            },
          ],
        },
        {
          id: 'external-provider-auth',
          name: '外部 provider 与 auth 数据',
          purpose: 'CLI 用户目录和凭据。',
          policyStatus: 'not_auto_cleanable',
          paths: [
            {
              label: 'Codex CLI HOME',
              path: 'C:\\Users\\tester\\.codex',
              exists: true,
              source: 'platform default',
              overridden: false,
              external: true,
            },
          ],
        },
      ],
      notice: 'data/ 下其他用户自建目录未登记。',
      jobsRetention: {
        slot: 'jobs-retention-control',
        status: 'reserved',
        message: 'Data 标签复用 Jobs completed-retention 设置组件；规则经 canonical API 存于 config.jobs。',
      },
    });
    const policies = {
      sessions: { enabled: false, days: null },
      attachments: { enabled: false, days: null },
      qq_history: { enabled: false, days: null },
      qq_media: { enabled: false, days: null },
      pan_logs: { enabled: false, days: null },
    };
    const lastScans = Object.fromEntries(Object.keys(policies).map((key) => [key, {
      scanned: 0, deleted: 0, skipped: 0, skipReasons: {}, lastScanAt: null,
    }]));
    fetchDataRetentionMock.mockReset();
    fetchDataRetentionMock.mockResolvedValue({
      policies,
      configKey: 'data_retention',
      lastScans,
    });
    updateDataRetentionMock.mockReset();
    updateDataRetentionMock.mockResolvedValue({
      policies,
      configKey: 'data_retention',
      lastScans,
    });
    fetchCompletedJobRetentionSettingsMock.mockReset();
    fetchCompletedJobRetentionSettingsMock.mockResolvedValue(jobRetentionResponse());
    updateCompletedJobRetentionSettingsMock.mockReset();
    updateCompletedJobRetentionSettingsMock.mockImplementation(async (rules) => jobRetentionResponse(rules));
  });

  it('renders nothing when closed', () => {
    render(<AppSettingsModal open={false} onClose={() => {}} />);
    expect(document.body.querySelector('.app-settings-overlay')).toBeNull();
  });

  it('renders the settings sections, including Session history search, plus Reset', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const card = cardEl();
    expect(card.textContent).not.toContain('Default group by');
    expect(document.getElementById('app-settings-tab-preferences')?.textContent)
      .toContain('Preferences');
    expect(card.textContent).toContain('Reset to defaults');
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);
    expect(card.textContent).toContain('Default group by');
    expect(card.querySelectorAll('[role="switch"]')).toHaveLength(8);
    expect(card.textContent).toContain('Notification');
    expect(card.textContent).toContain('Enable Session and global history search');
  });

  it('loads lifecycle preferences from config with ask defaults and saves updates', async () => {
    fetchSessionLifecyclePreferencesMock.mockResolvedValueOnce({
      exitStrategy: 'ask',
      startupPreference: 'ask',
    });
    render(<AppSettingsModal open onClose={() => {}} />);

    const exitStrategy = await waitFor(() => {
      const select = document.body.querySelector<HTMLSelectElement>('#session-exit-strategy');
      expect(select).toBeTruthy();
      return select!;
    });
    const startupPreference = document.body.querySelector<HTMLSelectElement>(
      '#session-startup-preference',
    )!;
    expect(exitStrategy.value).toBe('ask');
    expect(startupPreference.value).toBe('ask');

    fireEvent.change(exitStrategy, { target: { value: 'offline' } });
    await waitFor(() => expect(updateSessionLifecyclePreferencesMock).toHaveBeenCalledWith({
      exitStrategy: 'offline',
      startupPreference: 'ask',
    }));
    expect(exitStrategy.value).toBe('offline');

    fireEvent.change(startupPreference, { target: { value: 'sync-actual' } });
    await waitFor(() => expect(updateSessionLifecyclePreferencesMock).toHaveBeenLastCalledWith({
      exitStrategy: 'offline',
      startupPreference: 'sync-actual',
    }));
  });

  it('shows lifecycle preference save errors and keeps the last saved value', async () => {
    updateSessionLifecyclePreferencesMock.mockRejectedValueOnce(new Error('config is read-only'));
    render(<AppSettingsModal open onClose={() => {}} />);

    const startupPreference = await waitFor(() => {
      const select = document.body.querySelector<HTMLSelectElement>(
        '#session-startup-preference',
      );
      expect(select).toBeTruthy();
      return select!;
    });
    fireEvent.change(startupPreference, { target: { value: 'wake-running' } });

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('config is read-only');
    expect(startupPreference.value).toBe('ask');
  });

  it('shows a preference load error and retries the server read', async () => {
    fetchSessionLifecyclePreferencesMock.mockRejectedValueOnce(new Error('settings unavailable'));
    render(<AppSettingsModal open onClose={() => {}} />);

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('settings unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(document.body.querySelector<HTMLSelectElement>('#session-exit-strategy')?.value)
        .toBe('ask');
    });
  });

  it.each([
    ['offline', 'stop all Workers and mark legal running Sessions offline.'],
    ['preserve-running', 'stop all Workers and preserve legal running state.'],
  ] as const)(
    'confirms Exit using the saved %s policy without asking again',
    async (exitStrategy, expectedSummary) => {
      fetchSessionLifecyclePreferencesMock.mockResolvedValueOnce({
        exitStrategy,
        startupPreference: 'ask',
      });
      render(<AppSettingsModal open onClose={() => {}} />);
      const exitButton = await waitFor(() => {
        const button = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
          .find((candidate) => candidate.textContent?.includes('Exit Pan main service'));
        expect(button).toBeTruthy();
        expect(button!.disabled).toBe(false);
        return button!;
      });

      fireEvent.click(exitButton);
      expect(cardEl().textContent).toContain(expectedSummary);
      expect(document.body.querySelectorAll('input[name="main-exit-running-session-state"]'))
        .toHaveLength(0);
      fireEvent.click(Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent?.includes('Confirm exit'))!);

      await waitFor(() => expect(exitMainServiceMock).toHaveBeenCalledTimes(1));
      expect(exitMainServiceMock).toHaveBeenCalledWith(undefined);
    },
  );

  it.each([
    ['offline', 'mark legal running Sessions offline'],
    ['preserve-running', 'preserve legal running state'],
  ] as const)(
    'confirms Restart using the saved %s policy without asking again',
    async (exitStrategy, expectedSummary) => {
      fetchSessionLifecyclePreferencesMock.mockResolvedValueOnce({
        exitStrategy,
        startupPreference: 'preserve-running',
      });
      render(<AppSettingsModal open onClose={() => {}} />);
      const restartButton = await waitFor(() => {
        const button = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
          .find((candidate) => candidate.textContent?.includes('Restart Pan main service'));
        expect(button).toBeTruthy();
        expect(button!.disabled).toBe(false);
        return button!;
      });

      fireEvent.click(restartButton);
      expect(cardEl().textContent).toContain(expectedSummary);
      expect(document.body.querySelectorAll(
        'input[name="main-restart-running-session-state"]',
      )).toHaveLength(0);
      fireEvent.click(Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent?.includes('Confirm restart'))!);

      await waitFor(() => expect(restartMainServiceMock).toHaveBeenCalledTimes(1));
      expect(restartMainServiceMock).toHaveBeenCalledWith(undefined);
    },
  );

  it('refreshes both lifecycle statuses on every modal open and clears stale Exit state', async () => {
    fetchMainExitStatusMock
      .mockResolvedValueOnce({
        available: true,
        pending: true,
        platform: 'nt',
        operation: 'exit',
        phase: 'stopping_workers',
        stage: 'stopping_workers',
      })
      .mockResolvedValue({
        available: true,
        pending: false,
        platform: 'nt',
        stage: 'offline',
        phase: 'offline',
      });
    const { rerender } = render(<AppSettingsModal open onClose={() => {}} />);
    await waitFor(() => expect(cardEl().textContent).toContain('Stopping Workers and Pan'));
    expect(fetchMainRestartStatusMock).toHaveBeenCalledTimes(1);
    expect(fetchMainExitStatusMock).toHaveBeenCalledTimes(1);

    rerender(<AppSettingsModal open={false} onClose={() => {}} />);
    rerender(<AppSettingsModal open onClose={() => {}} />);
    const exitButton = await waitFor(() => {
      const button = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((candidate) => candidate.textContent?.includes('Exit Pan main service'));
      expect(button).toBeTruthy();
      expect(button!.disabled).toBe(false);
      return button!;
    });

    expect(fetchMainRestartStatusMock).toHaveBeenCalledTimes(2);
    expect(fetchMainExitStatusMock).toHaveBeenCalledTimes(2);
    expect(exitButton.disabled).toBe(false);
    expect(cardEl().textContent).not.toContain('Exit scheduled; this service will go offline');
    expect(fetchHealthMock).not.toHaveBeenCalled();
    expect(restartMainServiceMock).not.toHaveBeenCalled();
    expect(exitMainServiceMock).not.toHaveBeenCalled();
  });

  it('keeps lifecycle actions disabled after a status fetch error until Retry succeeds', async () => {
    fetchMainRestartStatusMock.mockRejectedValueOnce(new Error('status API unavailable'));
    render(<AppSettingsModal open onClose={() => {}} />);

    const refreshButton = await screen.findByRole('button', { name: 'Refresh status' });
    expect(screen.getByRole('alert').textContent).toContain('status API unavailable');
    const restartButton = Array.from(
      document.body.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent?.includes('Restart Pan main service'))!;
    const exitButton = Array.from(
      document.body.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent?.includes('Exit Pan main service'))!;
    expect(restartButton.disabled).toBe(true);
    expect(exitButton.disabled).toBe(true);

    fireEvent.click(refreshButton);
    await waitFor(() => {
      expect(Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent?.includes('Restart Pan main service'))?.disabled)
        .toBe(false);
    });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(fetchMainRestartStatusMock).toHaveBeenCalledTimes(2);
    expect(fetchMainExitStatusMock).toHaveBeenCalledTimes(2);
    expect(fetchHealthMock).not.toHaveBeenCalled();
  });

  it('ignores status responses from an earlier modal-open cycle', async () => {
    let resolveOldRestart!: (value: {
      available: boolean;
      pending: boolean;
      platform: string;
    }) => void;
    let resolveOldExit!: (value: {
      available: boolean;
      pending: boolean;
      platform: string;
      stage: string;
    }) => void;
    fetchMainRestartStatusMock.mockReturnValueOnce(new Promise((resolve) => {
      resolveOldRestart = resolve;
    }));
    fetchMainExitStatusMock.mockReturnValueOnce(new Promise((resolve) => {
      resolveOldExit = resolve;
    }));
    const { rerender } = render(<AppSettingsModal open onClose={() => {}} />);
    rerender(<AppSettingsModal open={false} onClose={() => {}} />);
    rerender(<AppSettingsModal open onClose={() => {}} />);

    const exitButton = await waitFor(() => {
      const button = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((candidate) => candidate.textContent?.includes('Exit Pan main service'));
      expect(button).toBeTruthy();
      expect(button!.disabled).toBe(false);
      return button!;
    });
    resolveOldRestart({ available: true, pending: true, platform: 'nt' });
    resolveOldExit({
      available: true, pending: true, platform: 'nt', stage: 'stopping_workers',
    });
    await act(async () => { await Promise.resolve(); });

    expect(exitButton.disabled).toBe(false);
    expect(cardEl().textContent).not.toContain('Stopping Workers and Pan');
    expect(fetchMainRestartStatusMock).toHaveBeenCalledTimes(2);
    expect(fetchMainExitStatusMock).toHaveBeenCalledTimes(2);
  });

  it('does not let a lifecycle action response from an earlier open overwrite the new status', async () => {
    let resolveRestartAction!: (value: {
      ok: boolean;
      status: 'scheduled';
      requestId: string;
    }) => void;
    restartMainServiceMock.mockReturnValueOnce(new Promise((resolve) => {
      resolveRestartAction = resolve;
    }));
    fetchMainRestartStatusMock
      .mockResolvedValueOnce({ available: true, pending: false, platform: 'nt' })
      .mockResolvedValue({
        available: true,
        pending: true,
        platform: 'nt',
        operation: 'restart',
        phase: 'stopping_workers',
      });
    const { rerender } = render(<AppSettingsModal open onClose={() => {}} />);
    const restartButton = await waitFor(() => {
      const button = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((candidate) => candidate.textContent?.includes('Restart Pan main service'));
      expect(button).toBeTruthy();
      expect(button!.disabled).toBe(false);
      return button!;
    });
    fireEvent.click(restartButton);
    fireEvent.click(Array.from(
      document.body.querySelectorAll<HTMLInputElement>(
        'input[name="main-restart-running-session-state"]',
      ),
    ).find((input) => input.parentElement?.textContent?.includes('No, stop'))!);
    fireEvent.click(Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent?.includes('Confirm restart'))!);
    await waitFor(() => expect(restartMainServiceMock).toHaveBeenCalledTimes(1));

    rerender(<AppSettingsModal open={false} onClose={() => {}} />);
    rerender(<AppSettingsModal open onClose={() => {}} />);
    await waitFor(() => expect(cardEl().textContent).toContain('Pan restart is in progress'));

    resolveRestartAction({ ok: true, status: 'scheduled', requestId: 'restart-old-open' });
    await act(async () => { await Promise.resolve(); });
    await waitFor(() => expect(fetchMainRestartStatusMock).toHaveBeenCalledTimes(3));
    expect(cardEl().textContent).toContain('Pan restart is in progress');
    expect(fetchHealthMock).not.toHaveBeenCalled();
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
      'Plugin',
      'Data',
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
    expect(tabs[6]!.getAttribute('aria-selected')).toBe('true');
    expect(tabs[6]!.tabIndex).toBe(0);
    expect(tabs.slice(0, 6).every((tab) => tab.tabIndex === -1)).toBe(true);
    fireEvent.keyDown(tabs[6]!, { key: 'ArrowLeft' });
    expect(tabs[5]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(tabs[5]!, { key: 'Home' });
    expect(tabs[0]!.getAttribute('aria-selected')).toBe('true');
    expect(panel.getAttribute('aria-labelledby')).toBe('app-settings-tab-general');
  });

  it('loads the Data catalog on demand and keeps long paths usable in a narrow panel', async () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    expect(fetchDataCatalogMock).not.toHaveBeenCalled();

    fireEvent.click(document.getElementById('app-settings-tab-data')!);
    expect(fetchDataCatalogMock).toHaveBeenCalledTimes(1);
    expect(document.getElementById('app-settings-tabpanel')?.getAttribute('aria-labelledby'))
      .toBe('app-settings-tab-data');
    expect(cardEl().querySelector('[role="tabpanel"]')?.className).toContain('overflow-y-auto');
    expect(cardEl().querySelector('[role="tabpanel"]')?.className).not.toContain('overflow-x-auto');

    await waitFor(() => expect(cardEl().textContent).toContain('D:\\Pan\\data\\sessions'));
    const panel = document.querySelector<HTMLElement>('[data-testid="data-settings-panel"]')!;
    expect(panel.className).toContain('min-w-0');
    expect(panel.textContent).toContain('由 Jobs API 管理');
    expect(panel.textContent).toContain('每类默认关闭');
    expect(panel.textContent).toContain('不可自动清理');
    expect(panel.textContent).toContain('尚未创建');
    expect(panel.textContent).toContain('外部路径');
    expect(panel.textContent).toContain('用户自建目录未登记');
    expect(panel.querySelector('code')?.className).toContain('break-all');
    expect(panel.textContent).not.toContain('共享策略');
    await waitFor(() => expect(panel.querySelector(
      '[aria-label="Keep Completed Jobs for days"]',
    )).not.toBeNull());
    expect(document.querySelectorAll('[role="tab"]')).toHaveLength(7);
    expect(updateUiSettingsMock).not.toHaveBeenCalled();
  });

  it('shows Data catalog loading and failure states without blocking other tabs', async () => {
    let resolveCatalog!: (value: {
      categories: [];
      notice: string;
      jobsRetention: { slot: string; status: 'reserved'; message: string };
    }) => void;
    fetchDataCatalogMock.mockReturnValueOnce(new Promise((resolve) => {
      resolveCatalog = resolve;
    }));
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-data')!);
    expect(await document.querySelector('[role="status"]')?.textContent)
      .toContain('正在读取存储路径');

    await act(async () => {
      resolveCatalog({
        categories: [],
        notice: '登记路径',
        jobsRetention: {
          slot: 'jobs-retention-control',
          status: 'reserved',
          message: '保留期字段待定',
        },
      });
    });
    await waitFor(() => expect(cardEl().textContent).toContain('登记路径'));

    fetchDataCatalogMock.mockRejectedValueOnce(new Error('catalog unavailable'));
    fireEvent.click(document.getElementById('app-settings-tab-general')!);
    fireEvent.click(document.getElementById('app-settings-tab-data')!);
    await waitFor(() =>
      expect(document.querySelector('[role="alert"]')?.textContent)
        .toContain('catalog unavailable'),
    );
    fireEvent.click(document.getElementById('app-settings-tab-general')!);
    expect(cardEl().textContent).toContain('Worker configuration');
  });

  it('loads disabled retention policies, edits days, saves, and shows recent scan counts', async () => {
    const policies = {
      sessions: { enabled: false, days: null },
      attachments: { enabled: false, days: 45 },
      qq_history: { enabled: false, days: 60 },
      qq_media: { enabled: false, days: 90 },
      pan_logs: { enabled: false, days: null },
    };
    const lastScans = {
      sessions: { scanned: 3, deleted: 1, skipped: 2, skipReasons: { live_worker: 2 }, lastScanAt: '2026-09-27T00:00:00+08:00' },
      attachments: { scanned: 4, deleted: 2, skipped: 0, skipReasons: {}, lastScanAt: null },
      qq_history: { scanned: 5, deleted: 3, skipped: 1, skipReasons: { qq_history_format_or_timestamp_unclear: 1 }, lastScanAt: null },
      qq_media: { scanned: 6, deleted: 4, skipped: 0, skipReasons: {}, lastScanAt: null },
      pan_logs: { scanned: 2, deleted: 1, skipped: 1, skipReasons: { active_log_file: 1 }, lastScanAt: null },
    };
    fetchDataRetentionMock.mockResolvedValueOnce({
      policies, configKey: 'data_retention', lastScans,
    });
    updateDataRetentionMock.mockImplementationOnce(async ({ policies: submitted }) => ({
      policies: submitted,
      configKey: 'data_retention',
      lastScans,
    }));
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-data')!);

    await waitFor(() => expect(document.querySelector(
      '[aria-label="Sessions 与 history 自动清理"]',
    )).not.toBeNull());
    const sessionSwitch = document.querySelector<HTMLButtonElement>(
      '[aria-label="Sessions 与 history 自动清理"]',
    )!;
    expect(sessionSwitch?.getAttribute('aria-checked')).toBe('false');
    expect(document.querySelector<HTMLInputElement>(
      '[aria-label="Sessions 与 history 保留天数"]',
    )?.value).toBe('');
    expect(cardEl().textContent).toContain('扫描 3，删除 1，跳过 2');
    expect(cardEl().textContent).toContain('live_worker: 2');
    const saveButton = Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent?.includes('保存清理策略'))!;
    expect(saveButton.disabled).toBe(true);

    fireEvent.click(sessionSwitch!);
    fireEvent.change(document.querySelector<HTMLInputElement>(
      '[aria-label="Sessions 与 history 保留天数"]',
    )!, { target: { value: '14' } });
    fireEvent.click(document.getElementById('app-settings-tab-general')!);
    fireEvent.click(document.getElementById('app-settings-tab-data')!);
    await waitFor(() => expect(document.querySelector<HTMLButtonElement>(
      '[aria-label="Sessions 与 history 自动清理"]',
    )?.getAttribute('aria-checked')).toBe('true'));
    expect(document.querySelector<HTMLInputElement>(
      '[aria-label="Sessions 与 history 保留天数"]',
    )?.value).toBe('14');
    const currentSaveButton = Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent?.includes('保存清理策略'))!;
    expect(currentSaveButton.disabled).toBe(false);
    fireEvent.change(document.querySelector<HTMLInputElement>(
      '[aria-label="Sessions 与 history 保留天数"]',
    )!, { target: { value: '' } });
    expect(document.querySelector<HTMLInputElement>(
      '[aria-label="Sessions 与 history 保留天数"]',
    )?.value).toBe('');
    fireEvent.click(currentSaveButton);
    await waitFor(() => expect(updateDataRetentionMock).toHaveBeenCalledWith({
      policies: {
        ...policies,
        sessions: { enabled: true, days: null },
      },
    }));
  });

  it('projects the shared Jobs retention editor in Data and saves via its canonical API', async () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-data')!);

    const failedDays = await waitFor(() => {
      const input = document.querySelector<HTMLInputElement>(
        '[aria-label="Keep Failed Jobs for days"]',
      );
      expect(input).not.toBeNull();
      return input!;
    });
    expect(fetchCompletedJobRetentionSettingsMock).toHaveBeenCalledTimes(1);
    expect(failedDays.value).toBe('');

    fireEvent.change(failedDays, { target: { value: '21' } });
    fireEvent.click(Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent?.trim() === 'Save settings')!);

    await waitFor(() => expect(updateCompletedJobRetentionSettingsMock).toHaveBeenCalledWith({
      ...defaultJobRetentionRules,
      failed: { enabled: false, days: 21 },
    }));
    expect(updateDataRetentionMock).not.toHaveBeenCalled();
  });

  it('shows retention settings loading and failure while keeping the Data path panel usable', async () => {
    fetchDataRetentionMock.mockRejectedValueOnce(new Error('retention unavailable'));
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-data')!);
    await waitFor(() => expect(document.querySelector('[role="alert"]')?.textContent)
      .toContain('retention unavailable'));
    expect(document.querySelector('[data-testid="data-settings-panel"]')?.textContent)
      .toContain('用户自建目录未登记');
    expect(document.getElementById('app-settings-tabpanel')?.className).toContain('overflow-y-auto');
    expect(document.getElementById('app-settings-tabpanel')?.className).not.toContain('overflow-x-auto');
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

    expect(tabs).toHaveLength(7);
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

  it('selects TUI, Bubble or Raw chat view from Appearance and persists the chosen style', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);

    expect(cardEl().textContent).toContain('Chat view');
    const chatViewGroup = document.body.querySelector<HTMLElement>(
      '[role="radiogroup"][aria-label="Chat view"]',
    );
    expect(chatViewGroup).toBeTruthy();
    const option = (label: string) =>
      Array.from(chatViewGroup!.querySelectorAll<HTMLElement>('[role="radio"]')).find(
        (element) => element.textContent?.trim() === label,
      )!;

    expect(option('TUI').getAttribute('aria-checked')).toBe('true');
    expect(option('Bubble').getAttribute('aria-checked')).toBe('false');
    expect(option('Raw').getAttribute('aria-checked')).toBe('false');

    fireEvent.click(option('Bubble'));
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('bubble');
    expect(updateUiSettingsMock).toHaveBeenCalledWith({ chatViewStyle: 'bubble' });
    expect(option('Bubble').getAttribute('aria-checked')).toBe('true');
    expect(option('TUI').getAttribute('aria-checked')).toBe('false');

    fireEvent.click(option('Raw'));
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('raw');
    expect(updateUiSettingsMock).toHaveBeenLastCalledWith({ chatViewStyle: 'raw' });
    expect(option('Raw').getAttribute('aria-checked')).toBe('true');
    expect(option('Bubble').getAttribute('aria-checked')).toBe('false');

    fireEvent.click(option('TUI'));
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('tui');
    expect(updateUiSettingsMock).toHaveBeenLastCalledWith({ chatViewStyle: 'tui' });
    expect(option('TUI').getAttribute('aria-checked')).toBe('true');
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
    expect(switches).toHaveLength(8);
    // meta-agent is on by default; toggle it off.
    const metaSwitch = switches.find((element) => element.textContent?.includes('Show meta-agent info'))!;
    expect(metaSwitch.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(metaSwitch);
    expect(useAppSettingsStore.getState().showMetaAgent).toBe(false);
    expect(metaSwitch.getAttribute('aria-checked')).toBe('false');
  });

  it('persists the Session history search setting when its switch changes', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);
    const findSearchSwitch = () => Array.from(document.body.querySelectorAll<HTMLElement>('[role="switch"]'))
      .find((element) => element.textContent?.includes('Enable Session and global history search'))!;
    const searchSwitch = findSearchSwitch();

    expect(useAppSettingsStore.getState().showHistorySearch).toBe(false);
    expect(searchSwitch.getAttribute('aria-checked')).toBe('false');

    fireEvent.click(searchSwitch);
    expect(useAppSettingsStore.getState().showHistorySearch).toBe(true);
    expect(findSearchSwitch().getAttribute('aria-checked')).toBe('true');
    expect(updateUiSettingsMock).toHaveBeenLastCalledWith({ showHistorySearch: true });

    fireEvent.click(findSearchSwitch());
    expect(useAppSettingsStore.getState().showHistorySearch).toBe(false);
    expect(findSearchSwitch().getAttribute('aria-checked')).toBe('false');
    expect(updateUiSettingsMock).toHaveBeenLastCalledWith({ showHistorySearch: false });
  });

  it('exposes the group-by settings only on the Appearance tab without duplicates', () => {
    render(<AppSettingsModal open onClose={() => {}} />);

    // General tab: Default group by moved out.
    expect(cardEl().textContent).not.toContain('Default group by');
    expect(document.getElementById('app-settings-default-group-by')).toBeNull();

    // Preferences tab: Show Group by moved out.
    fireEvent.click(document.getElementById('app-settings-tab-preferences')!);
    expect(cardEl().textContent).not.toContain('Show Group by');
    expect(cardEl().textContent).not.toContain('Default group by');
    expect(document.getElementById('app-settings-default-group-by')).toBeNull();

    // Appearance tab: both controls present, each exactly once.
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);
    expect(cardEl().textContent).toContain('Show Group by');
    expect(cardEl().textContent).toContain('Default group by');
    const groupBySwitches = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    ).filter((element) => element.textContent?.includes('Show Group by'));
    expect(groupBySwitches).toHaveLength(1);
    expect(document.body.querySelectorAll('#app-settings-default-group-by')).toHaveLength(1);
  });

  it('toggles Show Group by on the Appearance tab and persists it', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);
    const groupSwitch = Array.from(document.body.querySelectorAll<HTMLElement>('[role="switch"]'))
      .find((element) => element.textContent?.includes('Show Group by'))!;
    expect(groupSwitch.getAttribute('aria-checked')).toBe('false');

    fireEvent.click(groupSwitch);
    expect(useAppSettingsStore.getState().showGroupBy).toBe(true);
    expect(updateUiSettingsMock).toHaveBeenCalledWith({ showGroupBy: true });
    expect(groupSwitch.getAttribute('aria-checked')).toBe('true');

    fireEvent.click(groupSwitch);
    expect(useAppSettingsStore.getState().showGroupBy).toBe(false);
    expect(updateUiSettingsMock).toHaveBeenLastCalledWith({ showGroupBy: false });
    expect(groupSwitch.getAttribute('aria-checked')).toBe('false');
  });

  it('changes Default group by on the Appearance tab and persists the selection', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    fireEvent.click(document.getElementById('app-settings-tab-appearance')!);
    const select = document.body.querySelector<HTMLSelectElement>(
      '#app-settings-default-group-by',
    )!;
    expect(select).toBeTruthy();
    fireEvent.change(select, { target: { value: 'workdir' } });
    expect(useAppSettingsStore.getState().defaultGroupBy).toBe('workdir');
    expect(updateUiSettingsMock).toHaveBeenCalledWith({ defaultGroupBy: 'workdir' });
    expect(select.value).toBe('workdir');
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
      expect(button!.disabled).toBe(false);
      return button!;
    });

    fireEvent.click(restartButton);
    expect(cardEl().textContent).toContain('Confirm restart');
    const restartConfirmButton = Array.from(
      document.body.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent?.includes('Confirm restart'))!;
    expect(restartConfirmButton.disabled).toBe(true);
    const preserveRunningChoice = Array.from(
      document.body.querySelectorAll<HTMLInputElement>('input[name="main-restart-running-session-state"]'),
    ).find((input) => input.parentElement?.textContent?.includes('No, stop'))!;
    fireEvent.click(preserveRunningChoice);
    fireEvent.click(
      restartConfirmButton,
    );
    await waitFor(() => expect(restartMainServiceMock).toHaveBeenCalledTimes(1));
    expect(restartMainServiceMock).toHaveBeenCalledWith({ markRunningSessionsOffline: false });
    await waitFor(() => expect(cardEl().textContent).toContain('Waiting for /api/health'));

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
      expect(button!.disabled).toBe(false);
      return button!;
    });

    fireEvent.click(exitButton);
    expect(cardEl().textContent).toContain('Confirm exit');
    const confirmButton = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
      b.textContent?.includes('Confirm exit'),
    )!;
    expect(confirmButton.disabled).toBe(true);
    const runningStateOptions = Array.from(
      document.body.querySelectorAll<HTMLInputElement>('input[type="radio"]'),
    ).slice(-2);
    fireEvent.click(runningStateOptions[1]!);
    fireEvent.click(
      confirmButton,
    );
    await waitFor(() => expect(exitMainServiceMock).toHaveBeenCalledTimes(1));
    expect(exitMainServiceMock).toHaveBeenCalledWith({ markRunningSessionsOffline: false });
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
      fireEvent.click(Array.from(
        document.body.querySelectorAll<HTMLInputElement>(
          'input[name="main-restart-running-session-state"]',
        ),
      ).find((input) => input.parentElement?.textContent?.includes('No, stop'))!);
      fireEvent.click(
        Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
          b.textContent?.includes('Confirm restart'),
        )!,
      );
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      const stopCheckingButton = Array.from(
        document.body.querySelectorAll<HTMLButtonElement>('button'),
      ).find((candidate) => candidate.textContent?.includes('Stop checking'));
      expect(stopCheckingButton).toBeTruthy();
      fireEvent.click(stopCheckingButton!);
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
      fireEvent.click(Array.from(
        document.body.querySelectorAll<HTMLInputElement>(
          'input[name="main-restart-running-session-state"]',
        ),
      ).find((input) => input.parentElement?.textContent?.includes('No, stop'))!);
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

  it('requires an explicit lifecycle status refresh when the final restart status read fails', async () => {
    vi.useFakeTimers();
    try {
      fetchHealthMock.mockRejectedValue(new Error('connection refused'));
      render(<AppSettingsModal open onClose={() => {}} />);
      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });
      fetchMainRestartStatusMock.mockRejectedValueOnce(new Error('status API unavailable'));
      fireEvent.click(
        Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
          b.textContent?.includes('Restart Pan main service'),
        )!,
      );
      fireEvent.click(Array.from(
        document.body.querySelectorAll<HTMLInputElement>(
          'input[name="main-restart-running-session-state"]',
        ),
      ).find((input) => input.parentElement?.textContent?.includes('No, stop'))!);
      fireEvent.click(
        Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
          b.textContent?.includes('Confirm restart'),
        )!,
      );
      await act(async () => {
        await vi.runAllTimersAsync();
      });

      expect(screen.getByRole('alert').textContent).toContain('status API unavailable');
      expect(screen.getByRole('button', { name: 'Refresh status' })).toBeTruthy();
      expect(Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent?.includes('Restart Pan main service'))?.disabled)
        .toBe(true);
      expect(Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent?.includes('Exit Pan main service'))?.disabled)
        .toBe(true);

      fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
      await act(async () => { await Promise.resolve(); });
      expect(screen.queryByRole('alert')).toBeNull();
      expect(Array.from(document.body.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent?.includes('Exit Pan main service'))?.disabled)
        .toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

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

  it('renders the settings sections plus Reset', () => {
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
    expect(tabs[5]!.getAttribute('aria-selected')).toBe('true');
    expect(tabs[5]!.tabIndex).toBe(0);
    expect(tabs.slice(0, 5).every((tab) => tab.tabIndex === -1)).toBe(true);
    fireEvent.keyDown(tabs[5]!, { key: 'ArrowLeft' });
    expect(tabs[4]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(tabs[4]!, { key: 'Home' });
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
    expect(document.querySelectorAll('[role="tab"]')).toHaveLength(6);
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
    expect(cardEl().textContent).toContain('Default group by');
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

    expect(tabs).toHaveLength(6);
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

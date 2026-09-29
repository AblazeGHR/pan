// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NewSessionModal } from './NewSessionModal';
import { useSessionStore } from '@/stores/sessionStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { CliDiagnostic } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchUiSettings: vi.fn(),
  updateUiSettings: vi.fn(),
  fetchSessionTemplates: vi.fn(),
  fetchNewSessionDefaults: vi.fn(),
  saveNewSessionDefaults: vi.fn(),
  fetchDirectories: vi.fn(),
  createDirectory: vi.fn(),
}));

vi.mock('@/services/api', () => apiMock);

const cliStatus = (): CliDiagnostic => ({
  name: 'cbc', label: 'cbc', available: true, command: ['cbc'], missing: [], hint: '',
});

const listing = (current: string, entries: Array<{ name: string; path: string; isDirectory?: boolean }>) => ({
  current,
  parent: null,
  entries: entries.map((entry) => ({ ...entry, isDirectory: entry.isDirectory ?? true })),
});

function setup() {
  const createNewSession = vi.fn(async () => {});
  const showToast = vi.fn();
  useAdapterStore.setState({
    adapters: [{ name: 'cbc', defaultModel: '', supportsResume: false, supportsFork: false }],
    cliStatus: { adapters: [cliStatus()], available: ['cbc'], hasAvailable: true },
    cliStatusLoading: false,
    cliStatusError: null,
    adapterConfigs: { cbc: { models: [], defaultModel: '', effortValues: [], permissionModes: [], defaultPermissionMode: '', supportedSettings: [], executionModes: ['stream'] } },
    loadAdapterList: vi.fn(async () => {}), loadCliStatus: vi.fn(async () => {}), loadConfig: vi.fn(async () => {}),
  });
  useSessionStore.setState({ sessions: [], createNewSession });
  useUIStore.setState({ showToast, activeWorkspaceId: 'all' });
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: false });
  useWorkspaceStore.setState({ workspaces: [], loaded: true, loading: false, error: null });
  return { createNewSession, showToast };
}

async function renderReady() {
  const view = render(<NewSessionModal open onClose={() => {}} />);
  await waitFor(() => expect(
    (screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled,
  ).toBe(false));
  return view;
}

describe('New Session directory input', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.fetchSessionTemplates.mockResolvedValue([]);
    apiMock.fetchNewSessionDefaults.mockResolvedValue(null);
    apiMock.saveNewSessionDefaults.mockImplementation(async (defaults) => defaults);
    apiMock.fetchUiSettings.mockResolvedValue({
      defaultNewSessionToCurrentWorkspace: true,
    });
    apiMock.updateUiSettings.mockResolvedValue({});
    apiMock.fetchDirectories.mockResolvedValue(listing('', []));
    apiMock.createDirectory.mockResolvedValue({ ok: true, path: 'D:\\workspace\\new' });
    setup();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('uses one workdir input and searches after the final backslash', async () => {
    apiMock.fetchDirectories.mockResolvedValue(listing('D:\\workspace', [
      { name: 'app', path: 'D:\\workspace\\app' },
      { name: 'archive', path: 'D:\\workspace\\archive' },
    ]));
    await renderReady();
    const input = screen.getByTestId('new-session-workdir-input');
    fireEvent.change(input, { target: { value: 'D:\\workspace\\app' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'app' })).toBeTruthy());
    expect(apiMock.fetchDirectories).toHaveBeenCalledWith('D:\\workspace', false);
    expect(screen.queryByTestId('directory-search')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'app' }));
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('D:\\workspace\\app'));
  });

  it('prefills persisted New Session defaults and leaves saving opt-in', async () => {
    apiMock.fetchNewSessionDefaults.mockResolvedValue({
      adapter: 'cbc', outputMode: '', sessionTemplate: '', workdir: 'D:\\saved\\work',
    });
    await renderReady();
    await waitFor(() => expect(
      (screen.getByTestId('new-session-workdir-input') as HTMLInputElement).value,
    ).toBe('D:\\saved\\work'));
    expect((screen.getByRole('checkbox', {
      name: '将本次配置设为默认（不含 Session Name）',
    }) as HTMLInputElement).checked).toBe(false);
  });

  it('prefills the adapter, output mode, and template while retaining the template adapter lock', async () => {
    const kimiStatus = { ...cliStatus(), name: 'kimi', label: 'kimi' };
    useAdapterStore.setState({
      cliStatus: { adapters: [cliStatus(), kimiStatus], available: ['cbc', 'kimi'], hasAvailable: true },
      adapterConfigs: {
        cbc: { models: [], defaultModel: '', effortValues: [], permissionModes: [], defaultPermissionMode: '', supportedSettings: [], executionModes: ['stream'] },
        kimi: { models: [], defaultModel: '', effortValues: [], permissionModes: [], defaultPermissionMode: '', supportedSettings: [], executionModes: ['stream', 'oneshot'] },
      },
    });
    apiMock.fetchSessionTemplates.mockResolvedValue([
      { name: 'kimi-template', adapter: 'kimi', model: 'model-x', mcpServers: [] },
    ]);
    apiMock.fetchNewSessionDefaults.mockResolvedValue({
      adapter: 'kimi', outputMode: 'oneshot', sessionTemplate: 'kimi-template', workdir: '',
    });

    await renderReady();

    expect((screen.getAllByRole('combobox')[0] as HTMLSelectElement).value).toBe('kimi');
    expect((screen.getByRole('combobox', { name: 'Output Mode' }) as HTMLSelectElement).value).toBe('oneshot');
    expect(screen.getByRole('combobox', { name: /Session Template/ }).textContent).toContain('kimi-template');
    expect((screen.getAllByRole('combobox')[0] as HTMLSelectElement).disabled).toBe(true);
  });

  it('filters templates by name, adapter, model, and manifest without changing selection', async () => {
    apiMock.fetchSessionTemplates.mockResolvedValue([
      { name: 'alpha', adapter: 'cbc', model: 'o3', sourceManifestLabel: 'plugins/first/manifest.json' },
      { name: 'beta', adapter: 'kimi', model: 'moonshot-v1', sourceManifestLabel: 'plugins/second/manifest.json' },
    ]);
    apiMock.fetchNewSessionDefaults.mockResolvedValue({
      adapter: 'cbc', outputMode: '', sessionTemplate: 'alpha', workdir: '',
    });
    await renderReady();
    const picker = screen.getByRole('combobox', { name: /Session Template/ });
    expect(picker.textContent).toContain('alpha');
    fireEvent.click(picker);
    const search = screen.getByRole('searchbox', { name: '搜索 Session Template' });

    for (const [query, expected, hidden] of [
      ['beta', 'beta', 'alpha'],
      ['kimi', 'beta', 'alpha'],
      ['moonshot-v1', 'beta', 'alpha'],
      ['plugins/first', 'alpha', 'beta'],
    ] as const) {
      fireEvent.change(search, { target: { value: query } });
      expect(screen.getByRole('option', { name: new RegExp(expected) })).toBeTruthy();
      expect(screen.queryByRole('option', { name: new RegExp(hidden) })).toBeNull();
      expect(picker.textContent).toContain('alpha');
    }
  });

  it('supports keyboard navigation and selection, Escape cancellation, and clearing with None', async () => {
    apiMock.fetchSessionTemplates.mockResolvedValue([
      { name: 'alpha', adapter: 'cbc', model: 'a-model' },
      { name: 'beta', adapter: 'kimi', model: 'b-model' },
    ]);
    await renderReady();
    const picker = screen.getByRole('combobox', { name: /Session Template/ });
    fireEvent.keyDown(picker, { key: 'ArrowDown' });
    const search = screen.getByRole('searchbox', { name: '搜索 Session Template' });
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(picker.textContent).toContain('beta');

    fireEvent.click(picker);
    expect(screen.getByRole('option', { name: 'None' })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('searchbox', { name: '搜索 Session Template' }), { key: 'Escape' });
    expect(picker.textContent).toContain('beta');

    fireEvent.click(picker);
    fireEvent.click(screen.getByRole('option', { name: 'None' }));
    expect(picker.textContent).toBe('None▾');
  });

  it('applies and releases the adapter lock only after explicitly selecting a template', async () => {
    const kimiStatus = { ...cliStatus(), name: 'kimi', label: 'kimi' };
    useAdapterStore.setState({
      cliStatus: { adapters: [cliStatus(), kimiStatus], available: ['cbc', 'kimi'], hasAvailable: true },
      adapterConfigs: {
        cbc: { models: [], defaultModel: '', effortValues: [], permissionModes: [], defaultPermissionMode: '', supportedSettings: [], executionModes: ['stream'] },
        kimi: { models: [], defaultModel: '', effortValues: [], permissionModes: [], defaultPermissionMode: '', supportedSettings: [], executionModes: ['stream'] },
      },
    });
    apiMock.fetchSessionTemplates.mockResolvedValue([
      { name: 'kimi-template', adapter: 'kimi', model: 'model-x' },
      { name: 'plain-template', model: 'model-y' },
    ]);
    await renderReady();
    const adapterSelect = screen.getAllByRole('combobox')[0] as HTMLSelectElement;
    const picker = screen.getByRole('combobox', { name: /Session Template/ });
    fireEvent.click(picker);
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索 Session Template' }), { target: { value: 'kimi' } });
    expect(picker.textContent).toBe('None▾');
    expect(adapterSelect.disabled).toBe(false);
    fireEvent.click(screen.getByRole('option', { name: /kimi-template/ }));
    expect(adapterSelect.value).toBe('kimi');
    expect(adapterSelect.disabled).toBe(true);

    fireEvent.click(picker);
    fireEvent.click(screen.getByRole('option', { name: 'None' }));
    expect(adapterSelect.disabled).toBe(false);
  });

  it('keeps CLI-unavailable pinned templates from enabling creation and clears missing defaults', async () => {
    const { showToast } = setup();
    apiMock.fetchSessionTemplates.mockResolvedValue([
      { name: 'needs-kimi', adapter: 'kimi', model: 'model-x' },
    ]);
    apiMock.fetchNewSessionDefaults.mockResolvedValue({
      adapter: 'cbc', outputMode: '', sessionTemplate: 'removed-template', workdir: '',
    });
    const view = render(<NewSessionModal open onClose={() => {}} />);
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      '已保存的 Session Template「removed-template」不可用，已清除该预填项', 'error',
    ));
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(false));
    const picker = screen.getByRole('combobox', { name: /Session Template/ });
    fireEvent.click(picker);
    fireEvent.click(screen.getByRole('option', { name: /needs-kimi/ }));
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getByText(/当前模板要求 adapter/).textContent).toContain('kimi');
    view.unmount();
  });

  it('keeps the template menu inside a narrow mobile viewport', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('max-width'), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })));
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 667 });
    await renderReady();
    const picker = screen.getByRole('combobox', { name: /Session Template/ });
    vi.spyOn(picker, 'getBoundingClientRect').mockReturnValue({
      x: 300, y: 80, left: 300, top: 80, right: 640, bottom: 120, width: 340, height: 40,
      toJSON: () => ({}),
    } as DOMRect);
    fireEvent.click(picker);
    const menu = document.querySelector('[data-session-template-menu]') as HTMLDivElement;
    expect(menu.style.left).toBe('27px');
    expect(menu.style.width).toBe('340px');
    expect(parseFloat(menu.style.left) + parseFloat(menu.style.width)).toBeLessThanOrEqual(367);
  });

  it('saves the non-name form fields only when explicitly checked', async () => {
    const { createNewSession } = setup();
    apiMock.fetchNewSessionDefaults.mockResolvedValue(null);
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), {
      target: { value: 'D:\\workspace\\app' },
    });
    fireEvent.click(screen.getByRole('checkbox', { name: '将本次配置设为默认（不含 Session Name）' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createNewSession).toHaveBeenCalled());
    await waitFor(() => expect(apiMock.saveNewSessionDefaults).toHaveBeenCalledWith({
      adapter: 'cbc', outputMode: '', sessionTemplate: '', workdir: 'D:\\workspace\\app',
    }));
  });

  it('reports that the Session exists when saving opted-in defaults fails', async () => {
    const { createNewSession, showToast } = setup();
    apiMock.saveNewSessionDefaults.mockRejectedValue(new Error('disk unavailable'));
    await renderReady();
    fireEvent.click(screen.getByRole('checkbox', { name: '将本次配置设为默认（不含 Session Name）' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createNewSession).toHaveBeenCalled());
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('Session 已创建，但默认配置未保存：disk unavailable'),
      'error',
    ));
  });

  it('enters a searched directory on double-click and refreshes the search base', async () => {
    apiMock.fetchDirectories
      .mockResolvedValueOnce(listing('D:\\workspace', [
        { name: 'dir', path: 'D:\\workspace\\dir' },
      ]))
      .mockResolvedValueOnce(listing('D:\\workspace\\dir', [
        { name: 'nested', path: 'D:\\workspace\\dir\\nested' },
      ]));
    await renderReady();
    const input = screen.getByTestId('new-session-workdir-input');
    fireEvent.change(input, { target: { value: 'D:\\workspace\\dir' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'dir' })).toBeTruthy());

    const directoryButton = screen.getByRole('button', { name: 'dir' });
    // Model the browser sequence: two clicks followed by dblclick.
    fireEvent.click(directoryButton);
    fireEvent.click(directoryButton);
    fireEvent.doubleClick(directoryButton);

    await waitFor(() => {
      expect((input as HTMLInputElement).value).toBe('D:\\workspace\\dir\\');
      expect(apiMock.fetchDirectories).toHaveBeenLastCalledWith('D:\\workspace\\dir', false);
    });
    expect(screen.queryByText(/检索“dir”/)).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: 'nested' })).toBeTruthy());
  });

  it('shows the exact invalid-directory message for a missing search base', async () => {
    apiMock.fetchDirectories.mockRejectedValue(new Error('HTTP 404: Not Found'));
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\missing\\app' } });
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('当前目录非法'));
  });

  it('revalidates an existing directory immediately before creating a session', async () => {
    const { createNewSession } = setup();
    apiMock.fetchDirectories
      .mockResolvedValueOnce(listing('D:\\workspace', []))
      .mockResolvedValueOnce(listing('D:\\workspace\\app', []));
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\workspace\\app' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith('session-1', 'D:\\workspace\\app', 'cbc', undefined, { outputMode: undefined, workspaceIds: [] }));
    expect(apiMock.fetchDirectories).toHaveBeenLastCalledWith('D:\\workspace\\app');
  });

  it('asks before creating a missing directory; cancel never submits', async () => {
    const { createNewSession } = setup();
    apiMock.fetchDirectories.mockRejectedValue(new Error('HTTP 404: Not Found'));
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\workspace\\new' } });
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('当前目录非法'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: '创建工作目录' }).textContent).toContain('目录不存在，是否创建？'));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(apiMock.createDirectory).not.toHaveBeenCalled();
    expect(createNewSession).not.toHaveBeenCalled();
  });

  it('creates only after confirmation and does not submit if creation fails', async () => {
    const { createNewSession, showToast } = setup();
    apiMock.fetchDirectories.mockRejectedValue(new Error('HTTP 404: Not Found'));
    apiMock.createDirectory.mockRejectedValue(new Error('HTTP 403: Forbidden'));
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\workspace\\new' } });
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('当前目录非法'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: '创建工作目录' }).textContent).toContain('目录不存在，是否创建？'));
    fireEvent.click(screen.getByRole('button', { name: '创建目录' }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('HTTP 403: Forbidden', 'error'));
    expect(createNewSession).not.toHaveBeenCalled();
  });

  it('submits only after confirmed directory creation succeeds', async () => {
    const { createNewSession } = setup();
    apiMock.fetchDirectories.mockRejectedValue(new Error('HTTP 404: Not Found'));
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\workspace\\new' } });
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('当前目录非法'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: '创建工作目录' }).textContent).toContain('目录不存在，是否创建？'));
    fireEvent.click(screen.getByRole('button', { name: '创建目录' }));
    await waitFor(() => expect(apiMock.createDirectory).toHaveBeenCalledWith('D:\\workspace\\new'));
    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith('session-1', 'D:\\workspace\\new', 'cbc', undefined, { outputMode: undefined, workspaceIds: [] }));
  });

  it('assigns a new Session to the selected Workspace at submit time', async () => {
    const { createNewSession } = setup();
    await renderReady();
    useWorkspaceStore.setState({
      workspaces: [{ id: 'ws-current', name: 'Current', order: null }],
      loaded: true,
    });
    useUIStore.setState({ activeWorkspaceId: 'ws-current' });

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined,
      { outputMode: undefined, workspaceIds: ['ws-current'] },
    ));
  });

  it('leaves a new Session ungrouped when the default Workspace preference is off', async () => {
    const { createNewSession } = setup();
    useUIStore.setState({ activeWorkspaceId: 'ws-current' });
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      loaded: true,
      defaultNewSessionToCurrentWorkspace: false,
    });
    await renderReady();

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined,
      { outputMode: undefined, workspaceIds: [] },
    ));
  });

  it('captures the active Workspace when submitted before async directory validation', async () => {
    const { createNewSession } = setup();
    let resolveValidation!: (value: ReturnType<typeof listing>) => void;
    apiMock.fetchDirectories.mockImplementation((path: string) => {
      if (path === 'D:\\workspace\\app') {
        return new Promise((resolve) => {
          resolveValidation = resolve;
        });
      }
      return Promise.resolve(listing('', []));
    });
    useUIStore.setState({ activeWorkspaceId: 'ws-at-submit' });
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), {
      target: { value: 'D:\\workspace\\app' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(apiMock.fetchDirectories)
      .toHaveBeenCalledWith('D:\\workspace\\app'));

    useUIStore.setState({ activeWorkspaceId: 'ws-after-submit' });
    resolveValidation(listing('D:\\workspace\\app', []));

    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', 'D:\\workspace\\app', 'cbc', undefined,
      { outputMode: undefined, workspaceIds: ['ws-at-submit'] },
    ));
  });

  it('waits for persisted false before submitting a Session and keeps the submit-time scope', async () => {
    const { createNewSession } = setup();
    let resolveSettings!: (value: Record<string, unknown>) => void;
    apiMock.fetchUiSettings.mockReturnValue(new Promise((resolve) => {
      resolveSettings = resolve;
    }));
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: false });
    useUIStore.setState({ activeWorkspaceId: 'ws-at-submit' });
    await renderReady();

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(apiMock.fetchUiSettings).toHaveBeenCalledTimes(1));
    expect(createNewSession).not.toHaveBeenCalled();

    useUIStore.setState({ activeWorkspaceId: 'ws-after-submit' });
    resolveSettings({ defaultNewSessionToCurrentWorkspace: false });

    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined,
      { outputMode: undefined, workspaceIds: [] },
    ));
  });

  it.each(['all', 'ungrouped'])('keeps new Sessions ungrouped in the %s scope', async (activeWorkspaceId) => {
    const { createNewSession } = setup();
    useUIStore.setState({ activeWorkspaceId });
    await renderReady();

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined,
      { outputMode: undefined, workspaceIds: [] },
    ));
  });

  it('keeps a stale selected Workspace id for authoritative server validation', async () => {
    const { createNewSession } = setup();
    useUIStore.setState({ activeWorkspaceId: 'ws-deleted' });
    await renderReady();

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined,
      { outputMode: undefined, workspaceIds: ['ws-deleted'] },
    ));
  });

  it('keeps adapter availability and mobile dialog guards intact', async () => {
    useAdapterStore.setState({
      cliStatus: { adapters: [cliStatus(), { ...cliStatus(), name: 'kimi', label: 'kimi', available: false }], available: ['cbc'], hasAvailable: true },
    });
    await renderReady();
    expect(screen.getAllByRole('combobox')[0]!.textContent).toContain('cbc');
    expect(screen.getAllByRole('combobox')[0]!.textContent).not.toContain('kimi');
  });

  it('renders the mobile full-screen form and does not render when closed', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('max-width'), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })));
    const { rerender } = await renderReady();
    expect(screen.getByTestId('new-session-fullscreen')).toBeTruthy();
    expect(document.querySelector('.modal-overlay')).toBeNull();
    rerender(<NewSessionModal open={false} onClose={() => {}} />);
    expect(screen.queryByTestId('new-session-fullscreen')).toBeNull();
  });
});

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
  fetchSessionTemplateTargets: vi.fn(),
  fetchMcpServers: vi.fn(),
  saveSessionTemplate: vi.fn(),
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
    apiMock.fetchSessionTemplateTargets.mockResolvedValue([]);
    apiMock.fetchMcpServers.mockResolvedValue([]);
    apiMock.saveSessionTemplate.mockResolvedValue({ ok: true });
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

  it('shows the complete shared form on Basic and Advanced, with advanced controls only on Advanced', async () => {
    useAdapterStore.setState({
      adapterConfigs: {
        cbc: {
          models: ['model-x'], defaultModel: '', effortValues: ['high'],
          permissionModes: [{ value: 'safe', label: 'Safe' }], defaultPermissionMode: '',
          supportedSettings: ['model', 'permissionMode', 'effort', 'thinking', 'modelContextWindow', 'modelAutoCompactTokenLimit'],
          executionModes: ['stream', 'oneshot'],
        },
      },
    });
    await renderReady();

    expect(screen.getByRole('tab', { name: 'Basic' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByLabelText('Adapter')).toBeTruthy();
    expect(screen.getByLabelText('Output Mode')).toBeTruthy();
    expect(screen.getByRole('combobox', { name: /Session Template/ })).toBeTruthy();
    expect(screen.getByLabelText('Session Name')).toBeTruthy();
    expect(screen.getByTestId('new-session-workdir-input')).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: 'Save these settings as the default (Session Name is excluded)' })).toBeTruthy();
    expect(screen.queryByLabelText('Model')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save as Template' })).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: 'Advanced' }));
    expect(screen.getByLabelText('Adapter')).toBeTruthy();
    expect(screen.getByLabelText('Output Mode')).toBeTruthy();
    expect(screen.getByRole('combobox', { name: /Session Template/ })).toBeTruthy();
    expect(screen.getByLabelText('Session Name')).toBeTruthy();
    expect(screen.getByTestId('new-session-workdir-input')).toBeTruthy();
    expect(screen.getByLabelText('Model')).toBeTruthy();
    expect(screen.getByLabelText('Permission Mode')).toBeTruthy();
    expect(screen.getByLabelText('Effort / Thinking Level')).toBeTruthy();
    expect(screen.getByLabelText('Always Thinking')).toBeTruthy();
    expect(screen.getByLabelText('Model Context Window')).toBeTruthy();
    expect(screen.getByLabelText('Auto Compact Token Limit')).toBeTruthy();
    expect(screen.getByLabelText('MCP Servers')).toBeTruthy();
    expect(screen.getByText('Pan access')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save as Template' })).toBeTruthy();
  });

  it('does not submit advanced edits from Basic, but Advanced submits explicit overrides and false/empty values', async () => {
    const { createNewSession } = setup();
    useAdapterStore.setState({
      adapterConfigs: {
        cbc: {
          models: ['model-x'], defaultModel: '', effortValues: ['high'],
          permissionModes: [{ value: 'safe', label: 'Safe' }], defaultPermissionMode: '',
          supportedSettings: ['model', 'permissionMode', 'effort', 'thinking', 'modelContextWindow', 'modelAutoCompactTokenLimit'],
          executionModes: ['stream'],
        },
      },
    });
    await renderReady();

    fireEvent.click(screen.getByRole('tab', { name: 'Advanced' }));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'model-x' } });
    fireEvent.change(screen.getByLabelText('Permission Mode'), { target: { value: 'safe' } });
    fireEvent.change(screen.getByLabelText('Effort / Thinking Level'), { target: { value: 'high' } });
    fireEvent.change(screen.getByLabelText('Always Thinking'), { target: { value: 'false' } });
    fireEvent.change(screen.getByLabelText('Model Context Window'), { target: { value: '64000' } });
    fireEvent.change(screen.getByLabelText('Auto Compact Token Limit'), { target: { value: '50000' } });
    fireEvent.click(screen.getByLabelText('Override System Prompt (an empty value is submitted as an empty string)'));
    fireEvent.change(screen.getByLabelText('System Prompt'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('MCP Servers'), { target: { value: 'custom' } });
    const claimPermission = screen.getByText('Allow claiming unmanaged Sessions').parentElement!.querySelector('select')!;
    fireEvent.change(claimPermission, { target: { value: 'false' } });

    fireEvent.click(screen.getByRole('tab', { name: 'Basic' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined, { outputMode: undefined, workspaceIds: [] },
    ));

    createNewSession.mockClear();
    fireEvent.click(screen.getByRole('tab', { name: 'Advanced' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined,
      {
        outputMode: undefined,
        workspaceIds: [],
        model: 'model-x',
        permissionMode: 'safe',
        effort: 'high',
        modelContextWindow: 64000,
        modelAutoCompactTokenLimit: 50000,
        alwaysThinkingEnabled: false,
        systemPrompt: '',
        mcpServers: [],
        panAccess: { canClaimUnmanaged: false },
      },
    ));
  });

  it('saves an Advanced template to the selected writable manifest and refreshes the picker', async () => {
    const { showToast } = setup();
    apiMock.fetchSessionTemplates
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ name: 'saved-template', adapter: 'cbc' }]);
    apiMock.fetchSessionTemplateTargets.mockResolvedValue([
      { id: 'readonly', label: 'Built-in', writable: false, reason: 'Read-only' },
      { id: 'user', label: 'User manifest', writable: true },
    ]);
    await renderReady();

    fireEvent.click(screen.getByRole('tab', { name: 'Advanced' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save as Template' }));
    expect(screen.getByRole('dialog', { name: 'Save as Template' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Template Name/), { target: { value: 'saved-template' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Template' }));

    await waitFor(() => expect(apiMock.saveSessionTemplate).toHaveBeenCalledWith({
      manifestId: 'user', name: 'saved-template', adapter: 'cbc', mcp_mode: 'optional',
    }));
    await waitFor(() => expect(apiMock.fetchSessionTemplates).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('combobox', { name: /Session Template/ }));
    expect(screen.getByRole('option', { name: /saved-template/ })).toBeTruthy();
    expect(apiMock.fetchSessionTemplates).toHaveBeenCalledTimes(2);
    expect(showToast).toHaveBeenCalledWith('Session Template “saved-template” saved.', 'info');
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
      name: 'Save these settings as the default (Session Name is excluded)',
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
    const search = screen.getByRole('searchbox', { name: 'Search Session Template' });

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
    const search = screen.getByRole('searchbox', { name: 'Search Session Template' });
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(picker.textContent).toContain('beta');

    fireEvent.click(picker);
    expect(screen.getByRole('option', { name: 'None' })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('searchbox', { name: 'Search Session Template' }), { key: 'Escape' });
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
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search Session Template' }), { target: { value: 'kimi' } });
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
      'Saved Session Template “removed-template” is unavailable. The prefilled value has been cleared.', 'error',
    ));
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(false));
    const picker = screen.getByRole('combobox', { name: /Session Template/ });
    fireEvent.click(picker);
    fireEvent.click(screen.getByRole('option', { name: /needs-kimi/ }));
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getByText(/selected template requires adapter/).textContent).toContain('kimi');
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
    fireEvent.click(screen.getByRole('checkbox', { name: 'Save these settings as the default (Session Name is excluded)' }));
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
    fireEvent.click(screen.getByRole('checkbox', { name: 'Save these settings as the default (Session Name is excluded)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createNewSession).toHaveBeenCalled());
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('Session created, but defaults were not saved: disk unavailable'),
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
    expect(screen.queryByText(/Searching for “dir”/)).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: 'nested' })).toBeTruthy());
  });

  it('shows the exact invalid-directory message for a missing search base', async () => {
    apiMock.fetchDirectories.mockRejectedValue(new Error('HTTP 404: Not Found'));
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\missing\\app' } });
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('Invalid directory.'));
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
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('Invalid directory.'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Create Working Directory' }).textContent).toContain('This directory does not exist. Create it?'));
    fireEvent.click(screen.getByRole('dialog', { name: 'Create Working Directory' }).querySelector('button')!);
    expect(apiMock.createDirectory).not.toHaveBeenCalled();
    expect(createNewSession).not.toHaveBeenCalled();
  });

  it('creates only after confirmation and does not submit if creation fails', async () => {
    const { createNewSession, showToast } = setup();
    apiMock.fetchDirectories.mockRejectedValue(new Error('HTTP 404: Not Found'));
    apiMock.createDirectory.mockRejectedValue(new Error('HTTP 403: Forbidden'));
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\workspace\\new' } });
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('Invalid directory.'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Create Working Directory' }).textContent).toContain('This directory does not exist. Create it?'));
    fireEvent.click(screen.getByRole('button', { name: 'Create Directory' }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('HTTP 403: Forbidden', 'error'));
    expect(createNewSession).not.toHaveBeenCalled();
  });

  it('submits only after confirmed directory creation succeeds', async () => {
    const { createNewSession } = setup();
    apiMock.fetchDirectories.mockRejectedValue(new Error('HTTP 404: Not Found'));
    await renderReady();
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\workspace\\new' } });
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('Invalid directory.'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Create Working Directory' }).textContent).toContain('This directory does not exist. Create it?'));
    fireEvent.click(screen.getByRole('button', { name: 'Create Directory' }));
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

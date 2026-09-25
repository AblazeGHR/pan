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
  fetchSessionTemplates: vi.fn(),
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
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  useWorkspaceStore.setState({ workspaces: [], loaded: true, loading: false, error: null });
  return { createNewSession, showToast };
}

describe('New Session directory input', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.fetchSessionTemplates.mockResolvedValue([]);
    apiMock.fetchDirectories.mockResolvedValue(listing('', []));
    apiMock.createDirectory.mockResolvedValue({ ok: true, path: 'D:\\workspace\\new' });
    setup();
  });

  afterEach(cleanup);

  it('uses one workdir input and searches after the final backslash', async () => {
    apiMock.fetchDirectories.mockResolvedValue(listing('D:\\workspace', [
      { name: 'app', path: 'D:\\workspace\\app' },
      { name: 'archive', path: 'D:\\workspace\\archive' },
    ]));
    render(<NewSessionModal open onClose={() => {}} />);
    const input = screen.getByTestId('new-session-workdir-input');
    fireEvent.change(input, { target: { value: 'D:\\workspace\\app' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'app' })).toBeTruthy());
    expect(apiMock.fetchDirectories).toHaveBeenCalledWith('D:\\workspace', false);
    expect(screen.queryByTestId('directory-search')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'app' }));
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('D:\\workspace\\app'));
  });

  it('enters a searched directory on double-click and refreshes the search base', async () => {
    apiMock.fetchDirectories
      .mockResolvedValueOnce(listing('D:\\workspace', [
        { name: 'dir', path: 'D:\\workspace\\dir' },
      ]))
      .mockResolvedValueOnce(listing('D:\\workspace\\dir', [
        { name: 'nested', path: 'D:\\workspace\\dir\\nested' },
      ]));
    render(<NewSessionModal open onClose={() => {}} />);
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
    render(<NewSessionModal open onClose={() => {}} />);
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\missing\\app' } });
    await waitFor(() => expect(screen.getByTestId('directory-error').textContent).toBe('当前目录非法'));
  });

  it('revalidates an existing directory immediately before creating a session', async () => {
    const { createNewSession } = setup();
    apiMock.fetchDirectories
      .mockResolvedValueOnce(listing('D:\\workspace', []))
      .mockResolvedValueOnce(listing('D:\\workspace\\app', []));
    render(<NewSessionModal open onClose={() => {}} />);
    fireEvent.change(screen.getByTestId('new-session-workdir-input'), { target: { value: 'D:\\workspace\\app' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith('session-1', 'D:\\workspace\\app', 'cbc', undefined, { outputMode: undefined, workspaceIds: [] }));
    expect(apiMock.fetchDirectories).toHaveBeenLastCalledWith('D:\\workspace\\app');
  });

  it('asks before creating a missing directory; cancel never submits', async () => {
    const { createNewSession } = setup();
    apiMock.fetchDirectories.mockRejectedValue(new Error('HTTP 404: Not Found'));
    render(<NewSessionModal open onClose={() => {}} />);
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
    render(<NewSessionModal open onClose={() => {}} />);
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
    render(<NewSessionModal open onClose={() => {}} />);
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
    render(<NewSessionModal open onClose={() => {}} />);
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
      defaultNewSessionToCurrentWorkspace: false,
    });
    render(<NewSessionModal open onClose={() => {}} />);

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
    render(<NewSessionModal open onClose={() => {}} />);
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

  it.each(['all', 'ungrouped'])('keeps new Sessions ungrouped in the %s scope', async (activeWorkspaceId) => {
    const { createNewSession } = setup();
    useUIStore.setState({ activeWorkspaceId });
    render(<NewSessionModal open onClose={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined,
      { outputMode: undefined, workspaceIds: [] },
    ));
  });

  it('keeps a stale selected Workspace id for authoritative server validation', async () => {
    const { createNewSession } = setup();
    useUIStore.setState({ activeWorkspaceId: 'ws-deleted' });
    render(<NewSessionModal open onClose={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createNewSession).toHaveBeenCalledWith(
      'session-1', null, 'cbc', undefined,
      { outputMode: undefined, workspaceIds: ['ws-deleted'] },
    ));
  });

  it('keeps adapter availability and mobile dialog guards intact', () => {
    useAdapterStore.setState({
      cliStatus: { adapters: [cliStatus(), { ...cliStatus(), name: 'kimi', label: 'kimi', available: false }], available: ['cbc'], hasAvailable: true },
    });
    render(<NewSessionModal open onClose={() => {}} />);
    expect(screen.getAllByRole('combobox')[0]!.textContent).toContain('cbc');
    expect(screen.getAllByRole('combobox')[0]!.textContent).not.toContain('kimi');
  });

  it('renders the mobile full-screen form and does not render when closed', () => {
    vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('max-width'), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })));
    const { rerender } = render(<NewSessionModal open onClose={() => {}} />);
    expect(screen.getByTestId('new-session-fullscreen')).toBeTruthy();
    expect(document.querySelector('.modal-overlay')).toBeNull();
    rerender(<NewSessionModal open={false} onClose={() => {}} />);
    expect(screen.queryByTestId('new-session-fullscreen')).toBeNull();
  });
});

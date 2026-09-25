// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ImportModal } from './ImportModal';
import { useAdapterStore } from '@/stores/adapterStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import type {
  ApiCliStatusResponse,
  CbcProject,
  CbcSessionItem,
  KimiWorkspace,
  KimiSessionItem,
  OpencodeSessionItem,
  CodexSessionItem,
} from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchCbcProjects: vi.fn(async (): Promise<CbcProject[]> => []),
  fetchCbcSessions: vi.fn(async (): Promise<CbcSessionItem[]> => []),
  importCbcSession: vi.fn(),
  fetchKimiWorkspaces: vi.fn(async (): Promise<KimiWorkspace[]> => []),
  fetchKimiSessions: vi.fn(async (): Promise<KimiSessionItem[]> => []),
  importKimiSession: vi.fn(),
  fetchOpencodeSessions: vi.fn(async (): Promise<OpencodeSessionItem[]> => []),
  importOpencodeSession: vi.fn(),
  fetchCodexSessions: vi.fn(async (): Promise<CodexSessionItem[]> => []),
  importCodexSession: vi.fn(),
}));

vi.mock('@/services/api', () => apiMock);

const cliStatus = (...entries: Array<[string, boolean]>): ApiCliStatusResponse => ({
  adapters: entries.map(([name, available]) => ({
    name,
    label: name,
    available,
    command: [name],
    missing: available ? [] : [name],
    hint: '',
  })),
  available: entries.filter(([, available]) => available).map(([name]) => name),
  hasAvailable: entries.some(([, available]) => available),
});

describe('ImportModal adapter availability', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.fetchCbcProjects.mockResolvedValue([]);
    apiMock.fetchCbcSessions.mockResolvedValue([]);
    apiMock.fetchKimiWorkspaces.mockResolvedValue([]);
    apiMock.fetchKimiSessions.mockResolvedValue([]);
    apiMock.fetchOpencodeSessions.mockResolvedValue([]);
    apiMock.fetchCodexSessions.mockResolvedValue([]);
    apiMock.importCbcSession.mockResolvedValue({ id: 'pan-imported' });
    apiMock.importKimiSession.mockResolvedValue({ id: 'pan-imported' });
    apiMock.importOpencodeSession.mockResolvedValue({ id: 'pan-imported' });
    apiMock.importCodexSession.mockResolvedValue({ id: 'pan-imported' });
    useWorkspaceStore.setState({
      workspaces: [{ id: 'ws-active', name: 'Active', order: null }],
      loaded: true,
      loading: false,
      error: null,
    });
    useUIStore.setState({ activeWorkspaceId: 'ws-active' });
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
    useSessionStore.setState({
      loadSessions: vi.fn(async () => {}),
      selectSession: vi.fn(async () => {}),
    });
    useAdapterStore.setState({
      cliStatus: cliStatus(
        ['cbc', true],
        ['kimi', false],
        ['opencode', false],
        ['codex', true],
      ),
      cliStatusLoading: false,
      cliStatusError: null,
      loadCliStatus: vi.fn(async () => {}),
    });
  });

  afterEach(cleanup);

  it('renders tabs only for available adapters', () => {
    render(<ImportModal open onClose={() => {}} />);

    expect(screen.getByRole('button', { name: 'cbc' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'codex' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'kimi' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'opencode' })).toBeNull();
  });

  it('switches the dynamically rendered tab and loads that adapter sessions', async () => {
    render(<ImportModal open onClose={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: 'codex' }));
    expect(screen.getByText('Working Directory')).toBeTruthy();
    await waitFor(() => expect(apiMock.fetchCodexSessions).toHaveBeenCalledWith(''));
  });

  it('shows the CLI request error instead of rendering import tabs', () => {
    useAdapterStore.setState({
      cliStatus: null,
      cliStatusLoading: false,
      cliStatusError: 'connection refused',
    });
    render(<ImportModal open onClose={() => {}} />);

    expect(screen.getByRole('alert').textContent).toContain('connection refused');
    expect(screen.queryByRole('button', { name: 'cbc' })).toBeNull();
  });

  it('shows a loading state before rendering adapter tabs', () => {
    useAdapterStore.setState({
      cliStatus: null,
      cliStatusLoading: true,
      cliStatusError: null,
    });
    render(<ImportModal open onClose={() => {}} />);

    expect(screen.getByText('正在检测 Agent CLI 可用性…')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'cbc' })).toBeNull();
  });

  it('explains an empty available adapter list', () => {
    useAdapterStore.setState({
      cliStatus: cliStatus(['cbc', false], ['kimi', false]),
      cliStatusLoading: false,
      cliStatusError: null,
    });
    render(<ImportModal open onClose={() => {}} />);

    expect(screen.getByRole('alert').textContent).toContain('没有可用的 Agent CLI');
    expect(screen.queryByRole('button', { name: 'cbc' })).toBeNull();
  });

  it('falls back from an unavailable initial adapter with an explanatory message', () => {
    render(<ImportModal open onClose={() => {}} initialAdapter="kimi" />);

    expect(screen.getByRole('button', { name: 'cbc' })).toBeTruthy();
    expect(screen.getByText(/请求的 adapter/).parentElement?.textContent)
      .toContain('kimi');
  });

  it.each(['cbc', 'kimi', 'opencode', 'codex'] as const)(
    'passes the active Workspace to the %s import adapter', async (adapter) => {
      useAdapterStore.setState({
        cliStatus: cliStatus(['cbc', true], ['kimi', true], ['opencode', true], ['codex', true]),
      });
      if (adapter === 'cbc') {
        apiMock.fetchCbcProjects.mockResolvedValue([{
          project_dir: 'C:\\project', session_count: 1, path_hint: '', drive: 'C:', short_label: 'project',
        }]);
        apiMock.fetchCbcSessions.mockResolvedValue([{
          session_id: 'cbc-native', project_dir: 'C:\\project', title: 'CBC import target',
          message_count: 1, first_timestamp: '', last_timestamp: '', model: '', forked_from: null,
        }]);
      } else if (adapter === 'kimi') {
        apiMock.fetchKimiWorkspaces.mockResolvedValue([{
          workspace_id: 'kimi-ws', name: 'Kimi workspace', root: 'C:\\project', session_count: 1,
        }]);
        apiMock.fetchKimiSessions.mockResolvedValue([{
          session_id: 'kimi-native', workspace_id: 'kimi-ws', title: 'Kimi import target',
          workDir: 'C:\\project', message_count: 1, model: '', updatedAt: '',
        }]);
      } else if (adapter === 'opencode') {
        apiMock.fetchOpencodeSessions.mockResolvedValue([{
          session_id: 'opencode-native', title: 'OpenCode import target', workDir: 'C:\\project',
          createdAt: '', updatedAt: '', message_count: 1, model: '',
        }]);
      } else {
        apiMock.fetchCodexSessions.mockResolvedValue([{
          session_id: 'codex-native', title: 'Codex import target', workDir: 'C:\\project',
          createdAt: '', updatedAt: '', message_count: 1, model: '',
        }]);
      }

      render(<ImportModal open initialAdapter={adapter} onClose={() => {}} />);
      if (adapter === 'cbc') {
        await waitFor(() => expect(screen.getByLabelText('Project')).toBeTruthy());
        fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'C:\\project' } });
      } else if (adapter === 'kimi') {
        await waitFor(() => expect(screen.getByLabelText('Workspace')).toBeTruthy());
        fireEvent.change(screen.getByLabelText('Workspace'), { target: { value: 'C:\\project' } });
      }

      const title = {
        cbc: 'CBC import target',
        kimi: 'Kimi import target',
        opencode: 'OpenCode import target',
        codex: 'Codex import target',
      }[adapter];
      await waitFor(() => expect(screen.getByText(title)).toBeTruthy());
      fireEvent.click(screen.getByText(title));

      const importedId = `${adapter}-native`;
      await waitFor(() => {
        const importMock = {
          cbc: apiMock.importCbcSession,
          kimi: apiMock.importKimiSession,
          opencode: apiMock.importOpencodeSession,
          codex: apiMock.importCodexSession,
        }[adapter];
        expect(importMock).toHaveBeenCalledWith(
          importedId,
          'C:\\project',
          ['ws-active'],
        );
      });
    },
  );

  it('imports a new Pan Session without Workspace membership when the preference is off', async () => {
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      defaultNewSessionToCurrentWorkspace: false,
    });
    apiMock.fetchCbcProjects.mockResolvedValue([{
      project_dir: 'C:\\project', session_count: 1, path_hint: '', drive: 'C:', short_label: 'project',
    }]);
    apiMock.fetchCbcSessions.mockResolvedValue([{
      session_id: 'cbc-native', project_dir: 'C:\\project', title: 'CBC import target',
      message_count: 1, first_timestamp: '', last_timestamp: '', model: '', forked_from: null,
    }]);

    render(<ImportModal open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText('Project')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'C:\\project' } });
    await waitFor(() => expect(screen.getByText('CBC import target')).toBeTruthy());
    fireEvent.click(screen.getByText('CBC import target'));

    await waitFor(() => expect(apiMock.importCbcSession).toHaveBeenCalledWith(
      'cbc-native', 'C:\\project', [],
    ));
  });
});

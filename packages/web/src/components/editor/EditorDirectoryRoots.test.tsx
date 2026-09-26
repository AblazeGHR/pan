// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { EditorDirectoryRoots } from './EditorDirectoryRoots';
import { resetEditorStoreOperationState, useEditorStore } from '@/stores/editorStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

vi.mock('@/services/api', () => ({
  listFiles: vi.fn(async () => []),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async () => undefined),
  renameFs: vi.fn(async () => undefined),
  deleteFs: vi.fn(async () => undefined),
  fetchDirectories: vi.fn(),
  fetchWorkspaces: vi.fn(async () => []),
  updateWorkspaceDirs: vi.fn(),
}));

import * as api from '@/services/api';

const CWD_ID = 'cwd:D:/project';

function confirmAdd() {
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '添加目录' }));
}

function session(overrides: Partial<Session> = {}): Session {
  return {
    id: 's1',
    name: 'Test',
    workdir: 'D:\\project',
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    ...overrides,
  };
}

function seedRoots(extraRoots: Array<{ id: string; kind: 'workspace' | 'temp'; path: string; label: string; workspaceId?: string }> = []) {
  const roots = [
    { id: CWD_ID, kind: 'cwd' as const, path: 'D:/project', label: 'CWD' },
    ...extraRoots,
  ];
  const rootTrees: Record<string, { nodes: []; loading: boolean }> = {};
  for (const root of roots) rootTrees[root.id] = { nodes: [], loading: false };
  useEditorStore.setState({
    sessionId: 's1',
    workdir: 'D:\\project',
    roots,
    rootTrees,
    rootTreeGenerations: {},
    expanded: new Set(),
    tempDirs: extraRoots.filter((r) => r.kind === 'temp').map((r) => r.path),
    workspaceDirs: [],
    workspaceId: null,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  resetEditorStoreOperationState();
  useSessionStore.setState({ sessions: [session()], currentSessionId: 's1' });
  useWorkspaceStore.setState({ workspaces: [], loaded: true, loading: false, error: null });
  useUIStore.setState({ toastQueue: [] });
  vi.mocked(api.fetchDirectories).mockImplementation(async (path?: string) => ({
    current: path ?? '',
    parent: null,
    entries: [],
  }));
  seedRoots();
});

afterEach(() => cleanup());

describe('EditorDirectoryRoots', () => {
  it('labels each root by kind and only offers removal for workspace/temp roots', () => {
    seedRoots([
      { id: 'workspace:D:/shared/lib', kind: 'workspace', path: 'D:/shared/lib', label: 'lib', workspaceId: 'ws1' },
      { id: 'temp:D:/tmp/scratch', kind: 'temp', path: 'D:/tmp/scratch', label: 'scratch' },
    ]);

    render(<EditorDirectoryRoots />);

    expect(screen.getByText('CWD')).toBeTruthy();
    expect(screen.getByText('workspace dir')).toBeTruthy();
    expect(screen.getByText('temp dir')).toBeTruthy();

    expect(screen.queryByLabelText('从列表移除 D:/project')).toBeNull();
    expect(screen.getByLabelText('从列表移除 D:/shared/lib')).toBeTruthy();
    expect(screen.getByLabelText('从列表移除 D:/tmp/scratch')).toBeTruthy();
  });

  it('collapses each root independently', () => {
    seedRoots([{ id: 'temp:D:/tmp/scratch', kind: 'temp', path: 'D:/tmp/scratch', label: 'scratch' }]);

    render(<EditorDirectoryRoots />);
    expect(screen.getAllByText('Empty directory')).toHaveLength(2);

    const [cwdHeader] = screen.getAllByTestId('editor-root-header');
    fireEvent.click(cwdHeader!);
    expect(screen.getAllByText('Empty directory')).toHaveLength(1);
  });

  it('disables the workspace option with a hint when the Session is ungrouped', () => {
    render(<EditorDirectoryRoots />);
    fireEvent.click(screen.getByLabelText('添加目录'));

    const workspaceItem = screen.getByRole('menuitem', { name: '为工作区添加目录' }) as HTMLButtonElement;
    expect(workspaceItem.disabled).toBe(true);
    expect(screen.getByText(/当前 Session 未归属工作区/)).toBeTruthy();
    expect((screen.getByRole('menuitem', { name: '添加临时目录' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('adds a temp directory to browser memory', async () => {
    localStorage.clear();
    render(<EditorDirectoryRoots />);
    fireEvent.click(screen.getByLabelText('添加目录'));
    fireEvent.click(screen.getByRole('menuitem', { name: '添加临时目录' }));

    const input = screen.getByTestId('add-directory-input');
    fireEvent.change(input, { target: { value: 'D:\\tmp\\scratch' } });
    await waitFor(() => expect(screen.getByTestId('add-directory-resolved')).toBeTruthy());

    confirmAdd();

    await waitFor(() => {
      expect(useEditorStore.getState().tempDirs).toEqual(['D:/tmp/scratch']);
    });
    // Never persisted to the backend or browser storage.
    expect(api.updateWorkspaceDirs).not.toHaveBeenCalled();
    const persisted = Object.keys(localStorage)
      .map((key) => `${key}=${localStorage.getItem(key) ?? ''}`)
      .join(';');
    expect(persisted).not.toContain('scratch');
  });

  it('removes a temp root from the list without calling the workspace API', () => {
    seedRoots([{ id: 'temp:D:/tmp/scratch', kind: 'temp', path: 'D:/tmp/scratch', label: 'scratch' }]);

    render(<EditorDirectoryRoots />);
    fireEvent.click(screen.getByLabelText('从列表移除 D:/tmp/scratch'));

    expect(useEditorStore.getState().tempDirs).toEqual([]);
    expect(useEditorStore.getState().roots.some((root) => root.kind === 'temp')).toBe(false);
    expect(api.updateWorkspaceDirs).not.toHaveBeenCalled();
  });

  it('persists a workspace directory through the workspace API', async () => {
    useSessionStore.setState({
      sessions: [session({ workspaceIds: ['ws1'] })],
      currentSessionId: 's1',
    });
    useWorkspaceStore.setState({
      workspaces: [{ id: 'ws1', name: 'Team', order: null, dirs: [] }],
      loaded: true,
      loading: false,
      error: null,
    });
    vi.mocked(api.updateWorkspaceDirs).mockResolvedValue({
      id: 'ws1', name: 'Team', order: null, dirs: ['D:/shared/lib'],
    });

    render(<EditorDirectoryRoots />);
    fireEvent.click(screen.getByLabelText('添加目录'));
    const workspaceItem = screen.getByRole('menuitem', { name: '为工作区添加目录' }) as HTMLButtonElement;
    expect(workspaceItem.disabled).toBe(false);
    fireEvent.click(workspaceItem);

    fireEvent.change(screen.getByTestId('add-directory-input'), {
      target: { value: 'D:/shared/lib' },
    });
    await waitFor(() => expect(screen.getByTestId('add-directory-resolved')).toBeTruthy());
    confirmAdd();

    await waitFor(() =>
      expect(api.updateWorkspaceDirs).toHaveBeenCalledWith('ws1', ['D:/shared/lib']),
    );
    expect(useWorkspaceStore.getState().workspaces[0]?.dirs).toEqual(['D:/shared/lib']);
  });

  it('removes a workspace directory through the workspace API (metadata only)', async () => {
    useSessionStore.setState({
      sessions: [session({ workspaceIds: ['ws1'] })],
      currentSessionId: 's1',
    });
    useWorkspaceStore.setState({
      // The server may retain its Windows form while the editor root uses
      // forward slashes; removal must still match the actual root.path.
      workspaces: [{ id: 'ws1', name: 'Team', order: null, dirs: ['D:\\Shared\\Lib'] }],
      loaded: true,
      loading: false,
      error: null,
    });
    seedRoots([
      { id: 'workspace:D:/shared/lib', kind: 'workspace', path: 'D:/shared/lib', label: 'lib', workspaceId: 'ws1' },
    ]);
    vi.mocked(api.updateWorkspaceDirs).mockResolvedValue({
      id: 'ws1', name: 'Team', order: null, dirs: [],
    });
    const removeWorkspaceDir = vi.spyOn(useWorkspaceStore.getState(), 'removeWorkspaceDir');

    render(<EditorDirectoryRoots />);
    fireEvent.click(screen.getByLabelText('从列表移除 D:/shared/lib'));

    expect(removeWorkspaceDir).toHaveBeenCalledWith('ws1', 'D:/shared/lib');
    await waitFor(() => expect(api.updateWorkspaceDirs).toHaveBeenLastCalledWith('ws1', []));
    expect(api.deleteFs).not.toHaveBeenCalled();
  });

  it('shows an empty state when the Session has no roots', () => {
    useEditorStore.setState({ roots: [], rootTrees: {}, workdir: null });
    render(<EditorDirectoryRoots />);
    expect(screen.getByTestId('editor-roots-empty')).toBeTruthy();
  });
});

describe('AddDirectoryModal validation', () => {
  it('rejects a path the server does not return as a directory', async () => {
    vi.mocked(api.fetchDirectories).mockRejectedValue(new Error('HTTP 404: Directory does not exist'));
    render(<EditorDirectoryRoots />);
    fireEvent.click(screen.getByLabelText('添加目录'));
    fireEvent.click(screen.getByRole('menuitem', { name: '添加临时目录' }));

    fireEvent.change(screen.getByTestId('add-directory-input'), {
      target: { value: 'D:/nope' },
    });

    await waitFor(() => expect(screen.getByTestId('add-directory-error')).toBeTruthy());
    const confirm = within(screen.getByRole('dialog')).getByRole('button', { name: '添加目录' }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
  });
});

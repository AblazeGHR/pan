// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { FileTree } from './FileTree';
import { EditorConfirmationModal } from './EditorConfirmationModal';
import { normalizeEditorPath, useEditorStore, resetEditorStoreOperationState } from '@/stores/editorStore';
import { deleteFs } from '@/services/api';

vi.mock('@/services/api', () => ({
  listFiles: vi.fn(async () => []),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async () => undefined),
  renameFs: vi.fn(async () => undefined),
  deleteFs: vi.fn(async () => undefined),
}));

const CWD_ID = `cwd:${normalizeEditorPath('D:\\project')}`;

beforeEach(() => {
  vi.clearAllMocks();
  resetEditorStoreOperationState();
  useEditorStore.setState({
    sessionId: 's1',
    workdir: 'D:\\project',
    rootGeneration: 0,
    roots: [{ id: CWD_ID, kind: 'cwd', path: normalizeEditorPath('D:\\project'), label: 'CWD' }],
    rootTrees: {
      [CWD_ID]: {
        nodes: [{
          name: 'delete-me.ts',
          path: 'src/delete-me.ts',
          type: 'file',
          size: 5,
          modified: '',
        }],
        loading: false,
      },
    },
    rootTreeGenerations: {},
    expanded: new Set(),
    openRequestGeneration: 0,
    tempDirs: [],
    workspaceId: null,
    workspaceDirs: [],
    openPaths: ['src/delete-me.ts'],
    activePath: 'src/delete-me.ts',
    selectedPath: 'src/delete-me.ts',
    dirty: new Set(['src/delete-me.ts']),
    contents: { 'src/delete-me.ts': 'draft' },
    pendingConfirmation: null,
  });
});

afterEach(() => cleanup());

describe('FileTree editor deletion confirmation', () => {
  it('does not delete or close the tab when cancellation is chosen', () => {
    render(
      <>
        <FileTree rootId={CWD_ID} />
        <EditorConfirmationModal />
      </>,
    );

    fireEvent.click(screen.getByTitle('Delete'));
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('src/delete-me.ts');
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));

    expect(deleteFs).not.toHaveBeenCalled();
    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['src/delete-me.ts'],
      activePath: 'src/delete-me.ts',
      selectedPath: 'src/delete-me.ts',
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/delete-me.ts']));
  });

  it('confirms deletion only after the explicit dangerous action', async () => {
    render(
      <>
        <FileTree rootId={CWD_ID} />
        <EditorConfirmationModal />
      </>,
    );

    fireEvent.click(screen.getByTitle('Delete'));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '删除文件' }));

    await waitFor(() => expect(deleteFs).toHaveBeenCalledWith('s1', 'src/delete-me.ts'));
    expect(useEditorStore.getState()).toMatchObject({
      openPaths: [],
      activePath: null,
      selectedPath: null,
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set());
  });

  it('uses the same dangerous confirmation for directory deletion', () => {
    useEditorStore.setState({
      rootTrees: {
        [CWD_ID]: {
          nodes: [{
            name: 'empty',
            path: 'empty',
            type: 'dir',
            size: 0,
            modified: '',
            children: [],
          }],
          loading: false,
        },
      },
      openPaths: [],
      activePath: null,
      selectedPath: null,
      dirty: new Set(),
      contents: {},
    });
    render(
      <>
        <FileTree rootId={CWD_ID} />
        <EditorConfirmationModal />
      </>,
    );

    fireEvent.click(screen.getByTitle('Delete'));
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('empty');
    expect(deleteFs).not.toHaveBeenCalled();
  });

  it('only expands directories inside the root it was given', async () => {
    const dirPath = `${normalizeEditorPath('D:\\project')}/src`;
    const otherRootId = 'temp:D:/other';
    useEditorStore.setState((s) => ({
      roots: [...s.roots, { id: otherRootId, kind: 'temp', path: 'D:/other', label: 'other' }],
      rootTrees: {
        ...s.rootTrees,
        [CWD_ID]: { nodes: [{ name: 'src', path: dirPath, type: 'dir', size: 0, modified: '', children: [] }], loading: false },
        [otherRootId]: { nodes: [{ name: 'src', path: dirPath, type: 'dir', size: 0, modified: '', children: [] }], loading: false },
      },
    }));

    render(<FileTree rootId={CWD_ID} />);
    fireEvent.click(screen.getByText('src'));

    await waitFor(() =>
      expect(useEditorStore.getState().expanded.has(`${CWD_ID}\u0000${dirPath}`)).toBe(true),
    );
    expect(useEditorStore.getState().expanded.has(`${otherRootId}\u0000${dirPath}`)).toBe(false);
  });
});

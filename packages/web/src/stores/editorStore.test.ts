// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  normalizeEditorPath,
  resetEditorStoreOperationState,
  useEditorStore,
  type EditorRoot,
} from './editorStore';
import { useUIStore } from '@/stores/uiStore';
import { deleteFs, listFiles, readFile, renameFs, writeFile } from '@/services/api';
import type { ApiFsGenericResponse, ApiFsWriteResponse, FileNode, FsEntry } from '@/types';

vi.mock('@/services/api', () => ({
  listFiles: vi.fn(async () => []),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async () => undefined),
  renameFs: vi.fn(async () => undefined),
  deleteFs: vi.fn(async () => undefined),
}));

type EditorState = ReturnType<typeof useEditorStore.getState>;

const WORKDIR = 'D:\\project';
const WORKDIR_NORM = normalizeEditorPath(WORKDIR);
const CWD_ID = `cwd:${WORKDIR_NORM}`;

function cwdRoot(workdir: string): EditorRoot {
  const path = normalizeEditorPath(workdir);
  return { id: `cwd:${path}`, kind: 'cwd', path, label: 'CWD' };
}

/** Seed a single CWD root for one Session (mirrors what setRoot produces). */
function seedEditor(workdir: string | null = WORKDIR, overrides: Partial<EditorState> = {}): string | null {
  const roots: EditorRoot[] = workdir ? [cwdRoot(workdir)] : [];
  const rootTrees: EditorState['rootTrees'] = {};
  for (const root of roots) rootTrees[root.id] = { nodes: [], loading: false };
  useEditorStore.setState({
    sessionId: 's1',
    workdir,
    rootGeneration: 0,
    roots,
    rootTrees,
    rootTreeGenerations: {},
    expanded: new Set(),
    openRequestGeneration: 0,
    tempDirs: [],
    workspaceId: null,
    workspaceDirs: [],
    selectedPath: null,
    openPaths: [],
    activePath: null,
    dirty: new Set(),
    contents: {},
    imagePreviews: {},
    mdViewMode: {},
    pendingLocation: null,
    pendingConfirmation: null,
    ...overrides,
  });
  return roots[0]?.id ?? null;
}

function cwdNodes(rootId: string = CWD_ID): FileNode[] {
  return useEditorStore.getState().rootTrees[rootId]?.nodes ?? [];
}

function dirNode(path: string): FileNode {
  return { name: path.split('/').pop() ?? path, path, type: 'dir', size: 0, modified: '', children: [] };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetEditorStoreOperationState();
  vi.mocked(listFiles).mockResolvedValue([]);
  seedEditor();
  useUIStore.setState({ toastQueue: [] });
});

describe('editorStore.setRoot', () => {
  it('clears editor state when the session or root changes', async () => {
    seedEditor(WORKDIR, {
      sessionId: 's1',
      openPaths: ['src/old.ts'],
      activePath: 'src/old.ts',
      dirty: new Set(['src/old.ts']),
      contents: { 'src/old.ts': 'old' },
      mdViewMode: { 'src/old.ts': 'edit' },
    });

    await useEditorStore.getState().setRoot('s2', 'D:\\project\\new');

    expect(useEditorStore.getState()).toMatchObject({
      sessionId: 's2',
      workdir: 'D:\\project\\new',
      openPaths: [],
      activePath: null,
      contents: {},
      mdViewMode: {},
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set());
  });

  it('preserves open editor state for a same-session same-root refresh', async () => {
    seedEditor(WORKDIR, {
      openPaths: ['src/current.ts'],
      activePath: 'src/current.ts',
      dirty: new Set(['src/current.ts']),
      contents: { 'src/current.ts': 'current' },
      mdViewMode: { 'src/current.ts': 'edit' },
    });

    await useEditorStore.getState().setRoot('s1', WORKDIR);

    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['src/current.ts'],
      activePath: 'src/current.ts',
      contents: { 'src/current.ts': 'current' },
      mdViewMode: { 'src/current.ts': 'edit' },
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/current.ts']));
  });

  it('clears open editor state when only the session root changes', async () => {
    seedEditor(WORKDIR, {
      openPaths: ['src/old.ts'],
      activePath: 'src/old.ts',
      dirty: new Set(['src/old.ts']),
      contents: { 'src/old.ts': 'old' },
      mdViewMode: { 'src/old.ts': 'edit' },
    });

    await useEditorStore.getState().setRoot('s1', 'D:\\project\\new-root');

    expect(useEditorStore.getState().openPaths).toEqual([]);
    expect(useEditorStore.getState().activePath).toBeNull();
    expect(useEditorStore.getState().dirty).toEqual(new Set());
    expect(useEditorStore.getState().contents).toEqual({});
    expect(useEditorStore.getState().mdViewMode).toEqual({});
  });

  it('builds an absolute CWD root and loads it from the absolute path', async () => {
    await useEditorStore.getState().setRoot('s1', WORKDIR);

    expect(useEditorStore.getState().roots).toEqual([cwdRoot(WORKDIR)]);
    expect(listFiles).toHaveBeenCalledWith('s1', WORKDIR_NORM);
  });

  it('lets the newest same-root setRoot response win', async () => {
    let resolveFirst!: (entries: FsEntry[]) => void;
    let resolveSecond!: (entries: FsEntry[]) => void;
    vi.mocked(listFiles)
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveFirst = resolve; }),
      )
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveSecond = resolve; }),
      );
    seedEditor(WORKDIR, {
      openPaths: ['src/current.ts'],
      activePath: 'src/current.ts',
      dirty: new Set(['src/current.ts']),
      contents: { 'src/current.ts': 'draft' },
      mdViewMode: { 'src/current.ts': 'edit' },
    });

    const first = useEditorStore.getState().setRoot('s1', WORKDIR);
    const second = useEditorStore.getState().setRoot('s1', WORKDIR);
    resolveSecond([{ name: 'new.ts', type: 'file', size: 1, modified: '' }]);
    await second;
    resolveFirst([{ name: 'old.ts', type: 'file', size: 1, modified: '' }]);
    await first;

    expect(cwdNodes().map((node) => node.name)).toEqual(['new.ts']);
    expect(useEditorStore.getState().rootGeneration).toBe(0);
  });
});

describe('editorStore multi-root browsing', () => {
  it('adds workspace roots with absolute paths and loads each independently', async () => {
    seedEditor();
    useEditorStore.getState().setWorkspaceDirs('ws_1', ['D:\\shared\\a', 'D:\\shared\\b']);

    const roots = useEditorStore.getState().roots;
    expect(roots.map((root) => root.id)).toEqual([
      CWD_ID,
      'workspace:D:/shared/a',
      'workspace:D:/shared/b',
    ]);
    expect(roots[1]).toMatchObject({ kind: 'workspace', label: 'a', workspaceId: 'ws_1' });
    await vi.waitFor(() => {
      expect(listFiles).toHaveBeenCalledWith('s1', 'D:/shared/a');
      expect(listFiles).toHaveBeenCalledWith('s1', 'D:/shared/b');
    });
  });

  it('removes a workspace root when the metadata no longer lists it', () => {
    seedEditor();
    useEditorStore.getState().setWorkspaceDirs('ws_1', ['D:\\shared\\a', 'D:\\shared\\b']);
    useEditorStore.getState().setWorkspaceDirs('ws_1', ['D:\\shared\\b']);

    expect(useEditorStore.getState().roots.map((root) => root.id)).toEqual([
      CWD_ID,
      'workspace:D:/shared/b',
    ]);
    expect(useEditorStore.getState().rootTrees['workspace:D:/shared/a']).toBeUndefined();
  });

  it('keeps temp roots across a session switch but replaces workspace roots', () => {
    seedEditor();
    useEditorStore.getState().setWorkspaceDirs('ws_1', ['D:\\shared\\a']);
    useEditorStore.getState().addTempDir('D:\\tmp\\scratch');

    // A new Session clears the previous Workspace roots but not the temp ones.
    void useEditorStore.getState().setRoot('s2', 'D:\\other');

    const roots = useEditorStore.getState().roots;
    expect(roots.map((root) => root.id)).toEqual([
      'cwd:D:/other',
      'temp:D:/tmp/scratch',
    ]);
    expect(useEditorStore.getState().workspaceId).toBeNull();
    expect(useEditorStore.getState().workspaceDirs).toEqual([]);
  });

  it('does not persist temp dirs to localStorage or sessionStorage', () => {
    const localSet = vi.spyOn(Storage.prototype, 'setItem');
    seedEditor();
    useEditorStore.getState().addTempDir('D:\\tmp\\scratch');
    expect(localSet).not.toHaveBeenCalled();
    localSet.mockRestore();
  });

  it('keeps overlapping paths as separate roots with independent expansion', async () => {
    seedEditor();
    // The same absolute path as CWD, but added as a temp root.
    useEditorStore.getState().addTempDir(WORKDIR);

    const ids = useEditorStore.getState().roots.map((root) => root.id);
    expect(ids).toEqual([CWD_ID, `temp:${WORKDIR_NORM}`]);

    const dirPath = `${WORKDIR_NORM}/src`;
    useEditorStore.setState((s) => ({
      rootTrees: {
        ...s.rootTrees,
        [CWD_ID]: { nodes: [dirNode(dirPath)], loading: false },
        [`temp:${WORKDIR_NORM}`]: { nodes: [dirNode(dirPath)], loading: false },
      },
    }));
    vi.mocked(listFiles).mockResolvedValue([{ name: 'in.ts', type: 'file', size: 1, modified: '' }]);

    await useEditorStore.getState().toggleDir(`temp:${WORKDIR_NORM}`, dirPath);
    await vi.waitFor(() => expect(useEditorStore.getState().expanded.size).toBe(1));
    expect(useEditorStore.getState().expanded.has(`${CWD_ID}\u0000${dirPath}`)).toBe(false);
  });

  it('drops an in-flight temp root response after the root is removed', async () => {
    seedEditor();
    useEditorStore.getState().addTempDir('D:\\tmp\\scratch');
    const tempId = 'temp:D:/tmp/scratch';
    useEditorStore.setState((s) => ({ rootTrees: { ...s.rootTrees, [tempId]: { nodes: [], loading: true } } }));
    let resolveChildren!: (entries: FsEntry[]) => void;
    vi.mocked(listFiles).mockImplementationOnce(
      () => new Promise<FsEntry[]>((resolve) => { resolveChildren = resolve; }),
    );

    const refreshing = useEditorStore.getState().refreshRoot(tempId);
    await Promise.resolve();
    useEditorStore.getState().removeTempDir('D:\\tmp\\scratch');
    resolveChildren([{ name: 'late.ts', type: 'file', size: 1, modified: '' }]);
    await refreshing;

    expect(useEditorStore.getState().roots.some((root) => root.id === tempId)).toBe(false);
    expect(useEditorStore.getState().rootTrees[tempId]).toBeUndefined();
  });

  it('drops an in-flight directory response from the previous session', async () => {
    let resolveChildren!: (entries: FsEntry[]) => void;
    const srcPath = `${WORKDIR_NORM}/src`;
    vi.mocked(listFiles).mockImplementation((sessionId, dirPath) => {
      if (sessionId === 's1' && dirPath === srcPath) {
        return new Promise<FsEntry[]>((resolve) => { resolveChildren = resolve; });
      }
      return Promise.resolve([]);
    });
    seedEditor(WORKDIR, {
      rootTrees: {
        [CWD_ID]: { nodes: [dirNode(srcPath)], loading: false },
      },
    });

    const expanding = useEditorStore.getState().toggleDir(CWD_ID, srcPath);
    await useEditorStore.getState().setRoot('s2', 'D:\\project\\new');
    resolveChildren([]);
    await expanding;

    expect(useEditorStore.getState().sessionId).toBe('s2');
    expect(useEditorStore.getState().rootTrees[CWD_ID]).toBeUndefined();
    expect(useEditorStore.getState().expanded).toEqual(new Set());
  });

  it('sets and clears per-root loading around a lazy directory load', async () => {
    let resolveChildren!: (entries: FsEntry[]) => void;
    const srcPath = `${WORKDIR_NORM}/src`;
    vi.mocked(listFiles).mockImplementationOnce(
      () => new Promise<FsEntry[]>((resolve) => { resolveChildren = resolve; }),
    );
    seedEditor(WORKDIR, {
      rootTrees: { [CWD_ID]: { nodes: [dirNode(srcPath)], loading: false } },
    });

    const expanding = useEditorStore.getState().toggleDir(CWD_ID, srcPath);
    await Promise.resolve();
    expect(useEditorStore.getState().rootTrees[CWD_ID]?.loading).toBe(true);
    expect(useEditorStore.getState().expanded).toEqual(new Set());

    resolveChildren([{ name: 'main.ts', type: 'file', size: 1, modified: '' }]);
    await expanding;

    expect(useEditorStore.getState().rootTrees[CWD_ID]?.loading).toBe(false);
    expect(useEditorStore.getState().expanded.has(`${CWD_ID}\u0000${srcPath}`)).toBe(true);
  });

  it('clears per-root loading and stays collapsed when lazy loading fails', async () => {
    const srcPath = `${WORKDIR_NORM}/src`;
    vi.mocked(listFiles).mockRejectedValueOnce(new Error('directory unavailable'));
    seedEditor(WORKDIR, {
      rootTrees: { [CWD_ID]: { nodes: [dirNode(srcPath)], loading: false } },
    });

    await useEditorStore.getState().toggleDir(CWD_ID, srcPath);

    expect(useEditorStore.getState().rootTrees[CWD_ID]?.loading).toBe(false);
    expect(useEditorStore.getState().expanded).toEqual(new Set());
  });

  it('does not let a stale toggle response expand after refreshRoot wins', async () => {
    let resolveChildren!: (entries: FsEntry[]) => void;
    let resolveRefresh!: (entries: FsEntry[]) => void;
    const srcPath = `${WORKDIR_NORM}/src`;
    vi.mocked(listFiles)
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveChildren = resolve; }),
      )
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveRefresh = resolve; }),
      );
    seedEditor(WORKDIR, {
      rootTrees: { [CWD_ID]: { nodes: [dirNode(srcPath)], loading: false } },
    });

    const expanding = useEditorStore.getState().toggleDir(CWD_ID, srcPath);
    const refreshing = useEditorStore.getState().refreshRoot(CWD_ID);
    resolveRefresh([{ name: 'replacement.ts', type: 'file', size: 1, modified: '' }]);
    await refreshing;
    resolveChildren([{ name: 'stale.ts', type: 'file', size: 1, modified: '' }]);
    await expanding;

    expect(cwdNodes().map((node) => node.name)).toEqual(['replacement.ts']);
    expect(useEditorStore.getState().expanded).toEqual(new Set());
    expect(useEditorStore.getState().rootTrees[CWD_ID]?.loading).toBe(false);
  });

  it('lets the newest same-root refreshRoot response win', async () => {
    let resolveFirst!: (entries: FsEntry[]) => void;
    let resolveSecond!: (entries: FsEntry[]) => void;
    vi.mocked(listFiles)
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveFirst = resolve; }),
      )
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveSecond = resolve; }),
      );
    seedEditor(WORKDIR, {
      rootTrees: {
        [CWD_ID]: { nodes: [{ name: 'before.ts', path: `${WORKDIR_NORM}/before.ts`, type: 'file', size: 1, modified: '' }], loading: false },
      },
    });

    const first = useEditorStore.getState().refreshRoot(CWD_ID);
    const second = useEditorStore.getState().refreshRoot(CWD_ID);
    resolveSecond([{ name: 'new.ts', type: 'file', size: 1, modified: '' }]);
    await second;
    resolveFirst([{ name: 'old.ts', type: 'file', size: 1, modified: '' }]);
    await first;

    expect(cwdNodes().map((node) => node.name)).toEqual(['new.ts']);
    expect(useEditorStore.getState().rootTrees[CWD_ID]?.loading).toBe(false);
  });
});

describe('editorStore file operations on absolute tree paths', () => {
  it('opens, saves and downloads using the absolute root path', async () => {
    const openPath = `${WORKDIR_NORM}/src/main.ts`;
    vi.mocked(readFile).mockResolvedValueOnce('export {}');
    vi.mocked(writeFile).mockResolvedValueOnce({ path: openPath, size: 9 } as ApiFsWriteResponse);
    seedEditor(WORKDIR);

    await expect(useEditorStore.getState().openFile(openPath)).resolves.toBe(true);
    expect(readFile).toHaveBeenCalledWith('s1', openPath);

    useEditorStore.getState().markDirty(openPath, 'export {}');
    await useEditorStore.getState().saveFile();
    expect(writeFile).toHaveBeenCalledWith('s1', openPath, 'export {}');
  });

  it('refreshes the owning root only after rename and delete', async () => {
    const from = `${WORKDIR_NORM}/src/old.ts`;
    const to = `${WORKDIR_NORM}/src/new.ts`;
    vi.mocked(renameFs).mockResolvedValueOnce({} as ApiFsGenericResponse);
    vi.mocked(deleteFs).mockResolvedValueOnce({} as ApiFsGenericResponse);
    seedEditor(WORKDIR, {
      rootTrees: { [CWD_ID]: { nodes: [dirNode(`${WORKDIR_NORM}/src`)], loading: false } },
    });
    // The subtree refresh lists the parent directory, not the whole root.
    vi.mocked(listFiles).mockResolvedValue([]);

    await useEditorStore.getState().renameFile(from, to);
    await vi.waitFor(() => expect(listFiles).toHaveBeenCalledWith('s1', `${WORKDIR_NORM}/src`));

    vi.mocked(listFiles).mockClear();
    await useEditorStore.getState().deleteFile(to);
    await vi.waitFor(() => expect(listFiles).toHaveBeenCalledWith('s1', `${WORKDIR_NORM}/src`));
  });
});

describe('editorStore image browsing', () => {
  it('opens recognized raster image files through the authenticated download endpoint', async () => {
    seedEditor(WORKDIR);

    await expect(useEditorStore.getState().openFile('assets/photo.png')).resolves.toBe(true);

    expect(readFile).not.toHaveBeenCalled();
    expect(useEditorStore.getState().imagePreviews['assets/photo.png']).toEqual({
      src: '/api/fs/read?session_id=s1&path=assets%2Fphoto.png&download=1',
      displayName: 'photo.png',
    });
    expect(useEditorStore.getState().activePath).toBe('assets/photo.png');
  });
});

describe('editorStore async root protection', () => {
  it('ignores an open file response from the previous session and root', async () => {
    let resolveRead!: (content: string) => void;
    vi.mocked(readFile).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRead = resolve;
        }),
    );
    vi.mocked(listFiles).mockResolvedValue([]);

    seedEditor('D:\\project\\old');
    const opening = useEditorStore.getState().openFile('old.ts');

    const rootChange = useEditorStore.getState().setRoot('s2', 'D:\\project\\new');
    await rootChange;
    resolveRead('old content');
    await opening;

    expect(useEditorStore.getState()).toMatchObject({
      sessionId: 's2',
      workdir: 'D:\\project\\new',
      openPaths: [],
      activePath: null,
      contents: {},
    });
  });

  it('preserves editor state when a same-root refresh completes', async () => {
    let resolveRead!: (content: string) => void;
    vi.mocked(readFile).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRead = resolve;
        }),
    );
    seedEditor(WORKDIR, {
      openPaths: ['current.ts'],
      activePath: 'current.ts',
      dirty: new Set(['current.ts']),
      contents: { 'current.ts': 'draft' },
    });

    const opening = useEditorStore.getState().openFile('next.ts');
    await useEditorStore.getState().setRoot('s1', WORKDIR);
    resolveRead('next content');
    await opening;

    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['current.ts', 'next.ts'],
      activePath: 'next.ts',
      contents: { 'current.ts': 'draft', 'next.ts': 'next content' },
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set(['current.ts']));
  });

  it('lets the newest same-root open win when reads return out of order', async () => {
    let resolveA!: (content: string) => void;
    let resolveB!: (content: string) => void;
    vi.mocked(readFile)
      .mockImplementationOnce(() => new Promise<string>((resolve) => { resolveA = resolve; }))
      .mockImplementationOnce(() => new Promise<string>((resolve) => { resolveB = resolve; }));
    seedEditor(WORKDIR);

    const openingA = useEditorStore.getState().openFile('a.ts');
    const openingB = useEditorStore.getState().openFile('b.ts');
    resolveB('B content');
    await openingB;
    resolveA('A content');
    await openingA;

    expect(useEditorStore.getState()).toMatchObject({
      selectedPath: 'b.ts',
      activePath: 'b.ts',
      openPaths: ['b.ts'],
      contents: { 'b.ts': 'B content' },
    });
    expect(useEditorStore.getState().contents['a.ts']).toBeUndefined();
  });

  it('publishes a line location only after the asynchronous file open succeeds', async () => {
    let resolveRead!: (content: string) => void;
    vi.mocked(readFile).mockImplementationOnce(
      () => new Promise<string>((resolve) => { resolveRead = resolve; }),
    );
    seedEditor(WORKDIR);

    const opening = useEditorStore.getState().openFile('src/target.ts', {
      path: 'src/target.ts', line: 42, endLine: 48,
    });
    await Promise.resolve();
    expect(useEditorStore.getState().pendingLocation).toBeNull();

    resolveRead('content');
    await expect(opening).resolves.toBe(true);
    expect(useEditorStore.getState()).toMatchObject({
      activePath: 'src/target.ts',
      pendingLocation: { path: 'src/target.ts', line: 42, endLine: 48 },
    });
  });

  it('does not let a pending open reclaim active state after selecting an existing tab', async () => {
    let resolveRead!: (content: string) => void;
    vi.mocked(readFile).mockImplementationOnce(
      () => new Promise<string>((resolve) => { resolveRead = resolve; }),
    );
    seedEditor(WORKDIR, {
      openPaths: ['existing.ts'],
      activePath: 'existing.ts',
      selectedPath: 'existing.ts',
      contents: { 'existing.ts': 'existing' },
    });

    const opening = useEditorStore.getState().openFile('pending-tab.ts');
    await Promise.resolve();
    useEditorStore.getState().setActive('existing.ts');
    resolveRead('late content');
    await opening;

    expect(useEditorStore.getState()).toMatchObject({
      activePath: 'existing.ts',
      selectedPath: 'existing.ts',
      openPaths: ['existing.ts'],
      contents: { 'existing.ts': 'existing' },
    });
    expect(useEditorStore.getState().contents['pending-tab.ts']).toBeUndefined();
  });

  it('shows visible errors for failed open, save, and delete operations', async () => {
    vi.mocked(readFile).mockRejectedValueOnce(new Error('read denied'));
    seedEditor(WORKDIR);
    await useEditorStore.getState().openFile('failed-open.ts');
    expect(useUIStore.getState().toastQueue.at(-1)).toMatchObject({
      type: 'error', message: expect.stringContaining('打开文件失败'),
    });

    vi.mocked(writeFile).mockRejectedValueOnce(new Error('write denied'));
    useEditorStore.setState({
      sessionId: 's1',
      activePath: 'failed-save.ts',
      contents: { 'failed-save.ts': 'draft' },
    });
    await useEditorStore.getState().saveFile();
    expect(useUIStore.getState().toastQueue.at(-1)).toMatchObject({
      type: 'error', message: expect.stringContaining('保存文件失败'),
    });

    vi.mocked(deleteFs).mockRejectedValueOnce(new Error('delete denied'));
    await useEditorStore.getState().deleteFile('failed-delete.ts');
    expect(useUIStore.getState().toastQueue.at(-1)).toMatchObject({
      type: 'error', message: expect.stringContaining('删除文件失败'),
    });
  });

  it('cancels an operation when the session context changes before confirmation', async () => {
    seedEditor(WORKDIR, {
      activePath: 'draft.ts',
      contents: { 'draft.ts': 'draft' },
    });
    useEditorStore.getState().requestSave('draft.ts');
    expect(useEditorStore.getState().pendingConfirmation).toMatchObject({
      kind: 'save', path: 'draft.ts', sessionId: 's1', rootGeneration: 0,
    });

    // Defensive guard: a context change that leaves the request pending must
    // not write into the new root.
    useEditorStore.setState((s) => ({ rootGeneration: s.rootGeneration + 1 }));
    await useEditorStore.getState().confirmPendingOperation();

    expect(writeFile).not.toHaveBeenCalled();
    expect(useUIStore.getState().toastQueue.at(-1)).toMatchObject({
      type: 'error', message: expect.stringContaining('上下文已变化'),
    });
  });

  it('clears a pending confirmation when the Session changes', async () => {
    seedEditor(WORKDIR, {
      activePath: 'draft.ts',
      contents: { 'draft.ts': 'draft' },
    });
    useEditorStore.getState().requestSave('draft.ts');

    await useEditorStore.getState().setRoot('s2', 'D:\\project\\other');

    expect(useEditorStore.getState().pendingConfirmation).toBeNull();
    await useEditorStore.getState().confirmPendingOperation();
    expect(writeFile).not.toHaveBeenCalled();
  });
});

describe('editorStore save/rename/delete serialization', () => {
  it('serializes same-path saves and writes the newest draft last', async () => {
    let resolveFirst!: (response: ApiFsWriteResponse) => void;
    let resolveSecond!: (response: ApiFsWriteResponse) => void;
    const writes: string[] = [];
    vi.mocked(writeFile)
      .mockImplementationOnce((_sessionId, _path, content) => {
        writes.push(content);
        return new Promise<ApiFsWriteResponse>((resolve) => { resolveFirst = resolve; });
      })
      .mockImplementationOnce((_sessionId, _path, content) => {
        writes.push(content);
        return new Promise<ApiFsWriteResponse>((resolve) => { resolveSecond = resolve; });
      });
    seedEditor(WORKDIR, {
      activePath: 'src/current.ts',
      openPaths: ['src/current.ts'],
      dirty: new Set(['src/current.ts']),
      contents: { 'src/current.ts': 'A' },
    });

    const first = useEditorStore.getState().saveFile();
    await Promise.resolve();
    useEditorStore.getState().markDirty('src/current.ts', 'B');
    const second = useEditorStore.getState().saveFile();
    await Promise.resolve();

    expect(writes).toEqual(['A']);
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/current.ts']));

    resolveFirst({ path: 'src/current.ts', size: 1 });
    await first;
    expect(writes).toEqual(['A', 'B']);
    expect(useEditorStore.getState().contents['src/current.ts']).toBe('B');
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/current.ts']));

    resolveSecond({ path: 'src/current.ts', size: 1 });
    await second;
    expect(useEditorStore.getState().contents['src/current.ts']).toBe('B');
    expect(useEditorStore.getState().dirty).toEqual(new Set());
  });

  it('serializes save then rename so the old path cannot be recreated', async () => {
    let resolveWrite!: (response: ApiFsWriteResponse) => void;
    let resolveRename!: (response: ApiFsGenericResponse) => void;
    const operations: string[] = [];
    const disk = new Map([['old.ts', 'original']]);
    vi.mocked(writeFile).mockImplementationOnce((_sessionId, path, content) => {
      operations.push(`write:${path}:${content}`);
      return new Promise<ApiFsWriteResponse>((resolve) => {
        resolveWrite = (response) => {
          disk.set(path, content);
          resolve(response);
        };
      });
    });
    vi.mocked(renameFs).mockImplementationOnce((_sessionId, from, to) => {
      operations.push(`rename:${from}->${to}`);
      return new Promise<ApiFsGenericResponse>((resolve) => {
        resolveRename = (response) => {
          const value = disk.get(from);
          if (value !== undefined) disk.set(to, value);
          disk.delete(from);
          resolve(response);
        };
      });
    });
    seedEditor(WORKDIR, {
      openPaths: ['old.ts'],
      activePath: 'old.ts',
      selectedPath: 'old.ts',
      dirty: new Set(['old.ts']),
      contents: { 'old.ts': 'draft' },
    });

    const saving = useEditorStore.getState().saveFile();
    await Promise.resolve();
    const renaming = useEditorStore.getState().renameFile('old.ts', 'new.ts');
    await Promise.resolve();
    expect(operations).toEqual(['write:old.ts:draft']);

    resolveWrite({ path: 'old.ts', size: 5 });
    await saving;
    await Promise.resolve();
    expect(operations).toEqual(['write:old.ts:draft', 'rename:old.ts->new.ts']);

    resolveRename({});
    await renaming;
    expect([...disk.entries()]).toEqual([['new.ts', 'draft']]);
    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['new.ts'],
      activePath: 'new.ts',
      selectedPath: 'new.ts',
      contents: { 'new.ts': 'draft' },
    });
    expect(useEditorStore.getState().contents['old.ts']).toBeUndefined();
    expect(useEditorStore.getState().dirty).toEqual(new Set());
  });

  it('serializes save then delete so the old path stays deleted', async () => {
    let resolveWrite!: (response: ApiFsWriteResponse) => void;
    let resolveDelete!: (response: ApiFsGenericResponse) => void;
    const operations: string[] = [];
    const disk = new Map([['old.ts', 'original']]);
    vi.mocked(writeFile).mockImplementationOnce((_sessionId, path, content) => {
      operations.push(`write:${path}:${content}`);
      return new Promise<ApiFsWriteResponse>((resolve) => {
        resolveWrite = (response) => {
          disk.set(path, content);
          resolve(response);
        };
      });
    });
    vi.mocked(deleteFs).mockImplementationOnce((_sessionId, path) => {
      operations.push(`delete:${path}`);
      return new Promise<ApiFsGenericResponse>((resolve) => {
        resolveDelete = (response) => {
          disk.delete(path);
          resolve(response);
        };
      });
    });
    seedEditor(WORKDIR, {
      openPaths: ['old.ts'],
      activePath: 'old.ts',
      selectedPath: 'old.ts',
      dirty: new Set(['old.ts']),
      contents: { 'old.ts': 'draft' },
    });

    const saving = useEditorStore.getState().saveFile();
    await Promise.resolve();
    const deleting = useEditorStore.getState().deleteFile('old.ts');
    await Promise.resolve();
    expect(operations).toEqual(['write:old.ts:draft']);

    resolveWrite({ path: 'old.ts', size: 5 });
    await saving;
    await Promise.resolve();
    expect(operations).toEqual(['write:old.ts:draft', 'delete:old.ts']);

    resolveDelete({});
    await deleting;
    expect([...disk.entries()]).toEqual([]);
    expect(useEditorStore.getState()).toMatchObject({
      openPaths: [],
      activePath: null,
      selectedPath: null,
      contents: {},
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set());
  });

  it('cancels a deferred save queued after rename so the old path is not recreated', async () => {
    let resolveRename!: (response: ApiFsGenericResponse) => void;
    const disk = new Map([['deferred-rename.ts', 'original']]);
    vi.mocked(renameFs).mockImplementationOnce((_sessionId, from, to) =>
      new Promise<ApiFsGenericResponse>((resolve) => {
        resolveRename = (response) => {
          const value = disk.get(from);
          if (value !== undefined) disk.set(to, value);
          disk.delete(from);
          resolve(response);
        };
      }),
    );

    seedEditor(WORKDIR, {
      openPaths: ['deferred-rename.ts'],
      activePath: 'deferred-rename.ts',
      selectedPath: 'deferred-rename.ts',
      dirty: new Set(['deferred-rename.ts']),
      contents: { 'deferred-rename.ts': 'draft' },
    });

    const renaming = useEditorStore.getState().renameFile('deferred-rename.ts', 'renamed.ts');
    await Promise.resolve();
    const saving = useEditorStore.getState().saveFile('deferred-rename.ts');
    resolveRename({});
    await Promise.all([renaming, saving]);

    expect(writeFile).not.toHaveBeenCalled();
    expect([...disk.entries()]).toEqual([['renamed.ts', 'original']]);
  });

  it('continues a queued save after an older write fails without clearing the newer draft', async () => {
    let rejectFirst!: (error: Error) => void;
    let resolveSecond!: (response: ApiFsWriteResponse) => void;
    const writes: string[] = [];
    vi.mocked(writeFile)
      .mockImplementationOnce((_sessionId, _path, content) => {
        writes.push(content);
        return new Promise<ApiFsWriteResponse>((_resolve, reject) => { rejectFirst = reject; });
      })
      .mockImplementationOnce((_sessionId, _path, content) => {
        writes.push(content);
        return new Promise<ApiFsWriteResponse>((resolve) => { resolveSecond = resolve; });
      });
    seedEditor(WORKDIR, {
      activePath: 'src/current.ts',
      openPaths: ['src/current.ts'],
      dirty: new Set(['src/current.ts']),
      contents: { 'src/current.ts': 'A' },
    });

    const first = useEditorStore.getState().saveFile();
    await Promise.resolve();
    useEditorStore.getState().markDirty('src/current.ts', 'B');
    const second = useEditorStore.getState().saveFile();
    rejectFirst(new Error('write failed'));
    await first;
    await Promise.resolve();

    expect(writes).toEqual(['A', 'B']);
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/current.ts']));
    resolveSecond({ path: 'src/current.ts', size: 1 });
    await second;
    expect(useEditorStore.getState().dirty).toEqual(new Set());
  });

  it('does not clear a newer draft when an older save completes', async () => {
    let resolveWrite!: () => void;
    vi.mocked(writeFile).mockImplementationOnce(
      () => new Promise<ApiFsWriteResponse>((resolve) => {
        resolveWrite = () => resolve({ path: 'src/current.ts', size: 9 });
      }),
    );
    seedEditor(WORKDIR, {
      activePath: 'src/current.ts',
      openPaths: ['src/current.ts'],
      dirty: new Set(['src/current.ts']),
      contents: { 'src/current.ts': 'old draft' },
    });

    const saving = useEditorStore.getState().saveFile();
    await Promise.resolve();
    useEditorStore.getState().markDirty('src/current.ts', 'new draft');
    resolveWrite();
    await saving;

    expect(useEditorStore.getState().contents['src/current.ts']).toBe('new draft');
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/current.ts']));
  });

  it('renames from current state without losing a concurrent tab or draft', async () => {
    let resolveRename!: () => void;
    vi.mocked(renameFs).mockImplementationOnce(
      () => new Promise<ApiFsGenericResponse>((resolve) => {
        resolveRename = () => resolve({});
      }),
    );
    seedEditor(WORKDIR, {
      openPaths: ['src/old.ts'],
      activePath: 'src/old.ts',
      selectedPath: 'src/old.ts',
      dirty: new Set(['src/old.ts']),
      contents: { 'src/old.ts': 'old draft' },
      mdViewMode: { 'src/old.ts': 'split' },
    });

    const renaming = useEditorStore.getState().renameFile('src/old.ts', 'src/new.ts');
    await Promise.resolve();
    await useEditorStore.getState().openFile('src/other.ts');
    useEditorStore.getState().markDirty('src/old.ts', 'newer old draft');
    useEditorStore.getState().markDirty('src/other.ts', 'other draft');
    resolveRename();
    await renaming;

    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['src/new.ts', 'src/other.ts'],
      activePath: 'src/other.ts',
      selectedPath: 'src/other.ts',
      contents: {
        'src/new.ts': 'newer old draft',
        'src/other.ts': 'other draft',
      },
      mdViewMode: { 'src/new.ts': 'split' },
    });
    expect(useEditorStore.getState().dirty).toEqual(
      new Set(['src/new.ts', 'src/other.ts']),
    );
  });

  it('does not move editor state when the rename target appears during the request', async () => {
    let rejectRename!: (error: Error) => void;
    vi.mocked(renameFs).mockImplementationOnce(
      () => new Promise<ApiFsGenericResponse>((_resolve, reject) => { rejectRename = reject; }),
    );
    seedEditor(WORKDIR, {
      openPaths: ['src/old.ts'],
      activePath: 'src/old.ts',
      selectedPath: 'src/old.ts',
      dirty: new Set(['src/old.ts']),
      contents: { 'src/old.ts': 'source draft' },
    });

    const renaming = useEditorStore.getState().renameFile('src/old.ts', 'src/new.ts');
    await Promise.resolve();
    useEditorStore.getState().markDirty('src/new.ts', 'target draft');
    rejectRename(new Error('target already exists'));
    await renaming;

    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['src/old.ts'],
      activePath: 'src/old.ts',
      contents: { 'src/old.ts': 'source draft', 'src/new.ts': 'target draft' },
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/old.ts', 'src/new.ts']));
    expect(useUIStore.getState().toastQueue.at(-1)).toMatchObject({
      type: 'error',
      message: expect.stringContaining('重命名失败'),
    });
  });

  it('rejects a rename when the target has editor state instead of overwriting it', async () => {
    seedEditor(WORKDIR, {
      openPaths: ['src/old.md', 'src/new.md'],
      activePath: 'src/old.md',
      selectedPath: 'src/old.md',
      dirty: new Set(['src/old.md', 'src/new.md']),
      contents: {
        'src/old.md': 'source draft',
        'src/new.md': 'target draft',
      },
      mdViewMode: {
        'src/old.md': 'split',
        'src/new.md': 'preview',
      },
    });

    await useEditorStore.getState().renameFile('src/old.md', 'src/new.md');

    expect(renameFs).not.toHaveBeenCalled();
    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['src/old.md', 'src/new.md'],
      activePath: 'src/old.md',
      contents: {
        'src/old.md': 'source draft',
        'src/new.md': 'target draft',
      },
      mdViewMode: {
        'src/old.md': 'split',
        'src/new.md': 'preview',
      },
    });
    expect(useEditorStore.getState().dirty).toEqual(
      new Set(['src/old.md', 'src/new.md']),
    );
    expect(useUIStore.getState().toastQueue.at(-1)).toMatchObject({
      type: 'error',
      message: expect.stringContaining('目标路径'),
    });
  });
});

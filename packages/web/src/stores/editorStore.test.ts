// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { resetEditorStoreOperationState, useEditorStore } from './editorStore';
import { useUIStore } from '@/stores/uiStore';
import { deleteFs, listFiles, readFile, renameFs, writeFile } from '@/services/api';
import type { ApiFsGenericResponse, ApiFsWriteResponse, FsEntry } from '@/types';

vi.mock('@/services/api', () => ({
  listFiles: vi.fn(async () => []),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async () => undefined),
  renameFs: vi.fn(async () => undefined),
  deleteFs: vi.fn(async () => undefined),
}));

beforeEach(() => {
  vi.clearAllMocks();
  resetEditorStoreOperationState();
  vi.mocked(listFiles).mockResolvedValue([]);
  useEditorStore.setState({
    sessionId: null,
    workdir: null,
    rootGeneration: 0,
    treeRequestGeneration: 0,
    openRequestGeneration: 0,
    tree: [],
    treeLoading: false,
    expanded: new Set(),
    selectedPath: null,
    openPaths: [],
    activePath: null,
    dirty: new Set(),
    contents: {},
    mdViewMode: {},
    pendingConfirmation: null,
  });
  useUIStore.setState({ toastQueue: [] });
});

describe('editorStore.setRoot', () => {
  it('clears editor state when the session or root changes', async () => {
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\old',
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
      openPaths: ['src/current.ts'],
      activePath: 'src/current.ts',
      dirty: new Set(['src/current.ts']),
      contents: { 'src/current.ts': 'current' },
      mdViewMode: { 'src/current.ts': 'edit' },
    });

    await useEditorStore.getState().setRoot('s1', 'D:\\project\\same');

    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['src/current.ts'],
      activePath: 'src/current.ts',
      contents: { 'src/current.ts': 'current' },
      mdViewMode: { 'src/current.ts': 'edit' },
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/current.ts']));
  });

  it('clears open editor state when only the session root changes', async () => {
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\old-root',
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
      openPaths: ['src/current.ts'],
      activePath: 'src/current.ts',
      dirty: new Set(['src/current.ts']),
      contents: { 'src/current.ts': 'draft' },
      mdViewMode: { 'src/current.ts': 'edit' },
    });

    const first = useEditorStore.getState().setRoot('s1', 'D:\\project\\same');
    const second = useEditorStore.getState().setRoot('s1', 'D:\\project\\same');
    resolveSecond([{ name: 'new.ts', type: 'file', size: 1, modified: '' }]);
    await second;
    resolveFirst([{ name: 'old.ts', type: 'file', size: 1, modified: '' }]);
    await first;

    expect(useEditorStore.getState().tree.map((node) => node.name)).toEqual(['new.ts']);
    expect(useEditorStore.getState().rootGeneration).toBe(0);
    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['src/current.ts'],
      activePath: 'src/current.ts',
      contents: { 'src/current.ts': 'draft' },
      mdViewMode: { 'src/current.ts': 'edit' },
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/current.ts']));
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

    useEditorStore.setState({ sessionId: 's1', workdir: 'D:\\project\\old' });
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
      openPaths: ['current.ts'],
      activePath: 'current.ts',
      dirty: new Set(['current.ts']),
      contents: { 'current.ts': 'draft' },
    });

    const opening = useEditorStore.getState().openFile('next.ts');
    await useEditorStore.getState().setRoot('s1', 'D:\\project\\same');
    resolveRead('next content');
    await opening;

    expect(useEditorStore.getState()).toMatchObject({
      openPaths: ['current.ts', 'next.ts'],
      activePath: 'next.ts',
      contents: { 'current.ts': 'draft', 'next.ts': 'next content' },
    });
    expect(useEditorStore.getState().dirty).toEqual(new Set(['current.ts']));
  });

  it('ignores a directory response from the previous root', async () => {
    let resolveChildren!: (entries: FsEntry[]) => void;
    vi.mocked(listFiles).mockImplementation((sessionId, dirPath) => {
      if (sessionId === 's1' && dirPath === 'src') {
        return new Promise<FsEntry[]>((resolve) => {
          resolveChildren = resolve;
        });
      }
      return Promise.resolve([]);
    });
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\old',
      tree: [
        {
          name: 'src',
          path: 'src',
          type: 'dir',
          size: 0,
          modified: '',
          children: [],
          expanded: false,
        },
      ],
    });

    const expanding = useEditorStore.getState().toggleDir('src');
    await useEditorStore.getState().setRoot('s2', 'D:\\project\\new');
    resolveChildren([]);
    await expanding;

    expect(useEditorStore.getState().sessionId).toBe('s2');
    expect(useEditorStore.getState().tree).toEqual([]);
    expect(useEditorStore.getState().expanded).toEqual(new Set());
  });

  it('sets and clears treeLoading around a lazy directory load', async () => {
    let resolveChildren!: (entries: FsEntry[]) => void;
    vi.mocked(listFiles).mockImplementationOnce(
      () => new Promise<FsEntry[]>((resolve) => { resolveChildren = resolve; }),
    );
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
      tree: [{
        name: 'src', path: 'src', type: 'dir', size: 0, modified: '', children: [], expanded: false,
      }],
    });

    const expanding = useEditorStore.getState().toggleDir('src');
    await Promise.resolve();
    expect(useEditorStore.getState().treeLoading).toBe(true);
    expect(useEditorStore.getState().expanded).toEqual(new Set());

    resolveChildren([{ name: 'main.ts', type: 'file', size: 1, modified: '' }]);
    await expanding;

    expect(useEditorStore.getState().treeLoading).toBe(false);
    expect(useEditorStore.getState().expanded).toEqual(new Set(['src']));
  });

  it('clears treeLoading and stays collapsed when lazy loading fails', async () => {
    vi.mocked(listFiles).mockRejectedValueOnce(new Error('directory unavailable'));
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
      tree: [{
        name: 'src', path: 'src', type: 'dir', size: 0, modified: '', children: [], expanded: false,
      }],
    });

    await useEditorStore.getState().toggleDir('src');

    expect(useEditorStore.getState().treeLoading).toBe(false);
    expect(useEditorStore.getState().expanded).toEqual(new Set());
  });

  it('does not let a stale toggle response expand after refreshTree wins', async () => {
    let resolveChildren!: (entries: FsEntry[]) => void;
    let resolveRefresh!: (entries: FsEntry[]) => void;
    vi.mocked(listFiles)
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveChildren = resolve; }),
      )
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveRefresh = resolve; }),
      );
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
      tree: [{
        name: 'src', path: 'src', type: 'dir', size: 0, modified: '', children: [], expanded: false,
      }],
    });

    const expanding = useEditorStore.getState().toggleDir('src');
    const refreshing = useEditorStore.getState().refreshTree();
    resolveRefresh([{ name: 'replacement.ts', type: 'file', size: 1, modified: '' }]);
    await refreshing;
    resolveChildren([{ name: 'stale.ts', type: 'file', size: 1, modified: '' }]);
    await expanding;

    expect(useEditorStore.getState().tree.map((node) => node.name)).toEqual(['replacement.ts']);
    expect(useEditorStore.getState().expanded).toEqual(new Set());
    expect(useEditorStore.getState().treeLoading).toBe(false);
  });

  it('lets the newest same-root refreshTree response win', async () => {
    let resolveFirst!: (entries: FsEntry[]) => void;
    let resolveSecond!: (entries: FsEntry[]) => void;
    vi.mocked(listFiles)
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveFirst = resolve; }),
      )
      .mockImplementationOnce(
        () => new Promise<FsEntry[]>((resolve) => { resolveSecond = resolve; }),
      );
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
      tree: [{ name: 'before.ts', path: 'before.ts', type: 'file', size: 1, modified: '' }],
    });

    const first = useEditorStore.getState().refreshTree();
    const second = useEditorStore.getState().refreshTree();
    resolveSecond([{ name: 'new.ts', type: 'file', size: 1, modified: '' }]);
    await second;
    resolveFirst([{ name: 'old.ts', type: 'file', size: 1, modified: '' }]);
    await first;

    expect(useEditorStore.getState().tree.map((node) => node.name)).toEqual(['new.ts']);
    expect(useEditorStore.getState().treeLoading).toBe(false);
  });

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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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

    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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

  it('cancels a deferred save queued after delete so the old path is not recreated', async () => {
    let resolveDelete!: (response: ApiFsGenericResponse) => void;
    const disk = new Map([['deferred-delete.ts', 'original']]);
    vi.mocked(deleteFs).mockImplementationOnce((_sessionId, path) =>
      new Promise<ApiFsGenericResponse>((resolve) => {
        resolveDelete = (response) => {
          disk.delete(path);
          resolve(response);
        };
      }),
    );

    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
      openPaths: ['deferred-delete.ts'],
      activePath: 'deferred-delete.ts',
      selectedPath: 'deferred-delete.ts',
      dirty: new Set(['deferred-delete.ts']),
      contents: { 'deferred-delete.ts': 'draft' },
    });

    const deleting = useEditorStore.getState().deleteFile('deferred-delete.ts');
    await Promise.resolve();
    const saving = useEditorStore.getState().saveFile('deferred-delete.ts');
    resolveDelete({});
    await Promise.all([deleting, saving]);

    expect(writeFile).not.toHaveBeenCalled();
    expect([...disk.entries()]).toEqual([]);
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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

  it('lets the newest same-root open win when reads return out of order', async () => {
    let resolveA!: (content: string) => void;
    let resolveB!: (content: string) => void;
    vi.mocked(readFile)
      .mockImplementationOnce(() => new Promise<string>((resolve) => { resolveA = resolve; }))
      .mockImplementationOnce(() => new Promise<string>((resolve) => { resolveB = resolve; }));
    useEditorStore.setState({ sessionId: 's1', workdir: 'D:\\project\\same' });

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

  it('does not let a pending open reclaim active state after selecting an existing tab', async () => {
    let resolveRead!: (content: string) => void;
    vi.mocked(readFile).mockImplementationOnce(
      () => new Promise<string>((resolve) => { resolveRead = resolve; }),
    );
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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
    useEditorStore.setState({ sessionId: 's1', workdir: 'D:\\project\\same' });
    await useEditorStore.getState().openFile('failed-open.ts');
    expect(useUIStore.getState().toastQueue.at(-1)).toMatchObject({
      type: 'error', message: expect.stringContaining('打开文件失败'),
    });

    vi.mocked(writeFile).mockRejectedValueOnce(new Error('write denied'));
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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

  it('does not re-add a file when it is closed while opening', async () => {
    let resolveRead!: (content: string) => void;
    vi.mocked(readFile).mockImplementationOnce(
      () => new Promise<string>((resolve) => { resolveRead = resolve; }),
    );
    useEditorStore.setState({ sessionId: 's1', workdir: 'D:\\project\\same' });

    const opening = useEditorStore.getState().openFile('pending.ts');
    await Promise.resolve();
    useEditorStore.getState().closeFile('pending.ts');
    expect(useEditorStore.getState()).toMatchObject({
      selectedPath: null,
      activePath: null,
      openPaths: [],
      contents: {},
    });

    resolveRead('late content');
    await opening;
    expect(useEditorStore.getState()).toMatchObject({
      selectedPath: null,
      activePath: null,
      openPaths: [],
      contents: {},
    });
  });

  it('does not clear a newer draft when an older save completes', async () => {
    let resolveWrite!: () => void;
    vi.mocked(writeFile).mockImplementationOnce(
      () => new Promise<ApiFsWriteResponse>((resolve) => {
        resolveWrite = () => resolve({ path: 'src/current.ts', size: 9 });
      }),
    );
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project\\same',
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

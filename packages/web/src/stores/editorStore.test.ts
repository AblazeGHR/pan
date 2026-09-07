// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useEditorStore } from './editorStore';
import { useUIStore } from '@/stores/uiStore';
import { listFiles, readFile, renameFs, writeFile } from '@/services/api';
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
  vi.mocked(listFiles).mockResolvedValue([]);
  useEditorStore.setState({
    sessionId: null,
    workdir: null,
    rootGeneration: 0,
    treeRequestGeneration: 0,
    tree: [],
    treeLoading: false,
    expanded: new Set(),
    selectedPath: null,
    openPaths: [],
    activePath: null,
    dirty: new Set(),
    contents: {},
    mdViewMode: {},
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

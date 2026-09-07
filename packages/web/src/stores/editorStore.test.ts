// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useEditorStore } from './editorStore';
import { listFiles, readFile } from '@/services/api';
import type { FsEntry } from '@/types';

vi.mock('@/services/api', () => ({
  listFiles: vi.fn(async () => []),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async () => undefined),
  renameFs: vi.fn(async () => undefined),
  deleteFs: vi.fn(async () => undefined),
}));

beforeEach(() => {
  vi.mocked(listFiles).mockResolvedValue([]);
  useEditorStore.setState({
    sessionId: null,
    workdir: null,
    rootGeneration: 0,
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
});

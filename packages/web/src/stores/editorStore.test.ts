// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useEditorStore } from './editorStore';
import { listFiles } from '@/services/api';

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

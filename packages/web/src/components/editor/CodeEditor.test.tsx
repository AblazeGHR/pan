// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CodeEditor } from './CodeEditor';
import { EditorConfirmationModal } from './EditorConfirmationModal';
import { useEditorStore, resetEditorStoreOperationState } from '@/stores/editorStore';
import { useUIStore } from '@/stores/uiStore';
import { writeFile } from '@/services/api';

const monacoHarness = vi.hoisted(() => ({
  save: undefined as (() => void) | undefined,
  model: null as { getLineCount: () => number; getLineMaxColumn: (line: number) => number } | null,
  editor: {
    addCommand: (_key: number, handler: () => void) => { monacoHarness.save = handler; },
    getModel: () => monacoHarness.model,
    setPosition: vi.fn(),
    revealLineInCenter: vi.fn(),
    setSelection: vi.fn(),
  },
}));

vi.mock('@monaco-editor/react', () => ({
  default: ({ onMount }: { onMount: (editor: unknown, monaco: unknown) => void }) => {
    onMount(
      monacoHarness.editor,
      { KeyMod: { CtrlCmd: 1 }, KeyCode: { KeyS: 2 } },
    );
    return <div data-testid="mock-monaco" />;
  },
}));

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
  monacoHarness.save = undefined;
  monacoHarness.model = null;
  useEditorStore.setState({
    sessionId: 's1',
    workdir: 'D:\\project',
    rootGeneration: 0,
    activePath: 'src/shortcut.ts',
    openPaths: ['src/shortcut.ts'],
    contents: { 'src/shortcut.ts': 'draft' },
    dirty: new Set(['src/shortcut.ts']),
    pendingLocation: null,
    pendingConfirmation: null,
  });
  useUIStore.setState({ toastQueue: [] });
});

describe('CodeEditor save shortcut', () => {
  it('routes Ctrl/Cmd+S through the same confirmation before writeFile', async () => {
    render(
      <>
        <CodeEditor path="src/shortcut.ts" content="draft" />
        <EditorConfirmationModal />
      </>,
    );

    expect(screen.getByTestId('mock-monaco')).toBeTruthy();
    expect(monacoHarness.save).toBeTypeOf('function');
    monacoHarness.save!();
    expect(useEditorStore.getState().pendingConfirmation).toMatchObject({
      kind: 'save', path: 'src/shortcut.ts',
    });
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('src/shortcut.ts'));
    expect(writeFile).not.toHaveBeenCalled();

    fireEvent.keyDown(document, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(writeFile).toHaveBeenCalledWith('s1', 'src/shortcut.ts', 'draft'));
  });

  it('applies a pending Markdown line range when Monaco mounts', () => {
    monacoHarness.model = {
      getLineCount: () => 100,
      getLineMaxColumn: (line: number) => line === 48 ? 12 : 1,
    };
    useEditorStore.setState({
      activePath: 'src/shortcut.ts',
      pendingLocation: { path: 'src/shortcut.ts', line: 42, endLine: 48 },
    });
    render(<CodeEditor path="src/shortcut.ts" content="content" />);

    expect(monacoHarness.editor.setPosition).toHaveBeenCalledWith({ lineNumber: 42, column: 1 });
    expect(monacoHarness.editor.revealLineInCenter).toHaveBeenCalledWith(42);
    expect(monacoHarness.editor.setSelection).toHaveBeenCalledWith({
      startLineNumber: 42,
      startColumn: 1,
      endLineNumber: 48,
      endColumn: 12,
    });
    expect(useEditorStore.getState().pendingLocation).toBeNull();
  });
});

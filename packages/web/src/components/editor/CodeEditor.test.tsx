// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CodeEditor } from './CodeEditor';
import { EditorConfirmationModal } from './EditorConfirmationModal';
import { useEditorStore, resetEditorStoreOperationState } from '@/stores/editorStore';
import { useUIStore } from '@/stores/uiStore';
import { writeFile } from '@/services/api';

const monacoHarness = vi.hoisted(() => ({ save: undefined as (() => void) | undefined }));

vi.mock('@monaco-editor/react', () => ({
  default: ({ onMount }: { onMount: (editor: unknown, monaco: unknown) => void }) => {
    onMount(
      { addCommand: (_key: number, handler: () => void) => { monacoHarness.save = handler; } },
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
  useEditorStore.setState({
    sessionId: 's1',
    workdir: 'D:\\project',
    rootGeneration: 0,
    activePath: 'src/shortcut.ts',
    openPaths: ['src/shortcut.ts'],
    contents: { 'src/shortcut.ts': 'draft' },
    dirty: new Set(['src/shortcut.ts']),
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
});

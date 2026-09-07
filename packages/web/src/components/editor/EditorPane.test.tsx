// @vitest-environment jsdom
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { EditorPane } from './EditorPane';
import { useEditorStore } from '@/stores/editorStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

vi.mock('./CodeEditor', () => ({
  CodeEditor: () => null,
}));

vi.mock('@/services/api', () => ({
  listFiles: vi.fn(async () => []),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async () => undefined),
  renameFs: vi.fn(async () => undefined),
  deleteFs: vi.fn(async () => undefined),
}));

function mockMatchMedia(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

beforeEach(() => {
  mockMatchMedia(true);
  useSessionStore.setState({
    currentSessionId: 's1',
    sessions: [{
      id: 's1',
      name: 'Test',
      workdir: 'D:\\project',
      alwaysThinkingEnabled: false,
      effort: '',
      history: [],
    }],
  });
  useEditorStore.setState({
    sessionId: 's1',
    workdir: 'D:\\project',
    openPaths: ['src/main.ts'],
    activePath: 'src/main.ts',
    contents: { 'src/main.ts': 'export {}' },
    dirty: new Set(),
    mdViewMode: {},
    downloadFile: vi.fn(),
  });
  useUIStore.setState({ toastQueue: [], chatAttachmentRequests: [] });
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('EditorPane editor action wiring', () => {
  it('keeps relative operation paths while displaying and copying the workdir path', async () => {
    render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorPane />
      </MemoryRouter>,
    );

    expect(screen.getByText('main.ts')).toBeTruthy();
    expect(screen.getByTitle('D:\\project\\src\\main.ts').textContent).toBe('D:\\project\\src\\main.ts');

    fireEvent.click(screen.getByRole('button', { name: '复制完整路径' }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('D:\\project\\src\\main.ts'));

    fireEvent.click(screen.getByRole('button', { name: '下载当前文件' }));
    expect(useEditorStore.getState().downloadFile).toHaveBeenCalledWith('src/main.ts');

    fireEvent.click(screen.getByRole('button', { name: '加入聊天' }));
    await waitFor(() => expect(useUIStore.getState().chatAttachmentRequests).toEqual([
      { sessionId: 's1', path: 'src/main.ts' },
    ]));
  });
});

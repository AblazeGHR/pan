// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { EditorFileTopBar, getDisplayPath } from './EditorFileTopBar';
import { EditorConfirmationModal } from './EditorConfirmationModal';
import { useEditorStore } from '@/stores/editorStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { writeFile } from '@/services/api';

vi.mock('@/services/api', () => ({
  listFiles: vi.fn(async () => []),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async () => undefined),
  renameFs: vi.fn(async () => undefined),
  deleteFs: vi.fn(async () => undefined),
}));

function mockMatchMedia(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

beforeEach(() => {
  mockMatchMedia(true);
  useEditorStore.setState({
    sessionId: 's1',
    openPaths: ['src/one.ts', 'src/two.ts'],
    activePath: 'src/one.ts',
    dirty: new Set(),
    contents: {},
    pendingConfirmation: null,
    downloadFile: vi.fn(),
  });
  useSessionStore.setState({
    currentSessionId: 's1',
    sessions: [
      {
        id: 's1',
        name: 'Test',
        workdir: 'D:\\project',
        alwaysThinkingEnabled: false,
        effort: '',
        history: [],
      },
    ],
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

describe('EditorFileTopBar', () => {
  it('copies the displayed full path and resets success feedback when the operation path changes', async () => {
    const { rerender } = render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/one.ts" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '复制完整路径' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '完整路径已复制' })).toBeTruthy(),
    );
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('D:\\project\\src\\one.ts');

    rerender(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/two.ts" />
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: '复制完整路径' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '复制完整路径' }));
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith('D:\\project\\src\\two.ts'),
    );
  });

  it('uses the session-relative operation path for download and chat on mobile', async () => {
    const downloadFile = vi.fn();
    useEditorStore.setState({ downloadFile });
    render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/two.ts" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '下载当前文件' }));
    expect(downloadFile).toHaveBeenCalledWith('src/two.ts');
    fireEvent.click(screen.getByRole('button', { name: '加入聊天' }));

    await waitFor(() =>
      expect(useUIStore.getState().chatAttachmentRequests).toEqual([
        { sessionId: 's1', path: 'src/two.ts' },
      ]),
    );
  });

  it('keeps the mobile-only actions out of the desktop editor TopBar', () => {
    mockMatchMedia(false);
    render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/two.ts" />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('button', { name: '下载当前文件' })).toBeNull();
    expect(screen.queryByRole('button', { name: '加入聊天' })).toBeNull();
  });

  it('requires confirmation before saving and keeps cancellation side-effect free', async () => {
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project',
      activePath: 'src/two.ts',
      contents: { 'src/two.ts': 'draft' },
      dirty: new Set(['src/two.ts']),
    });
    render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/two.ts" />
        <EditorConfirmationModal />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '保存文件' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('src/two.ts');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: '保存文件' }));
    expect(writeFile).not.toHaveBeenCalled();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writeFile).not.toHaveBeenCalled();
    expect(useEditorStore.getState().dirty).toEqual(new Set(['src/two.ts']));

    fireEvent.click(screen.getByRole('button', { name: '保存文件' }));
    fireEvent.keyDown(document, { key: 'Enter' });
    await waitFor(() => expect(writeFile).toHaveBeenCalledWith('s1', 'src/two.ts', 'draft'));
    expect(useEditorStore.getState().dirty).toEqual(new Set());
  });

  it('reports copy failure instead of showing a false success state', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn() });
    vi.spyOn(document, 'execCommand').mockReturnValue(false);
    render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/one.ts" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '复制完整路径' }));
    await waitFor(() =>
      expect(useUIStore.getState().toastQueue.at(-1)?.message).toBe('复制路径失败'),
    );
    expect(screen.getByRole('button', { name: '复制完整路径' })).toBeTruthy();
  });

  it('ignores a pending copy success after switching to another file', async () => {
    let resolveCopy!: () => void;
    const writeText = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveCopy = resolve;
        }),
    );
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const { rerender } = render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/one.ts" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '复制完整路径' }));
    rerender(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/two.ts" />
      </MemoryRouter>,
    );
    resolveCopy();
    await waitFor(() => expect(screen.getByRole('button', { name: '复制完整路径' })).toBeTruthy());

    expect(useUIStore.getState().toastQueue).toEqual([]);
    expect(screen.queryByRole('button', { name: '完整路径已复制' })).toBeNull();
  });

  it('ignores a pending copy failure after switching workdir', async () => {
    let rejectCopy!: (reason?: unknown) => void;
    const writeText = vi.fn(
      () =>
        new Promise<void>((_, reject) => {
          rejectCopy = reject;
        }),
    );
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const { rerender } = render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/one.ts" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '复制完整路径' }));
    useSessionStore.setState({
      sessions: [
        {
          id: 's1',
          name: 'Test',
          workdir: 'D:\\project\\new',
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
        },
      ],
    });
    rerender(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorFileTopBar operationPath="src/one.ts" />
      </MemoryRouter>,
    );
    rejectCopy(new Error('denied'));
    await waitFor(() => expect(screen.getByRole('button', { name: '复制完整路径' })).toBeTruthy());

    expect(useUIStore.getState().toastQueue).toEqual([]);
    expect(screen.queryByRole('button', { name: '完整路径已复制' })).toBeNull();
  });
});

describe('getDisplayPath', () => {
  it.each([
    ['POSIX root', '/', '/src\\a.ts', '/src/a.ts'],
    ['Windows drive root with backslashes', 'C:\\', '/src/a.ts', 'C:\\src\\a.ts'],
    ['Windows drive root with slashes', 'C:/', '\\src\\a.ts', 'C:/src/a.ts'],
    ['UNC root', '\\\\server\\share\\', 'src/a.ts', '\\\\server\\share\\src\\a.ts'],
    ['mixed-separator workdir', 'C:\\project/', 'src\\a.ts', 'C:\\project\\src\\a.ts'],
    ['mixed-separator workdir internals', 'C:\\project/sub\\nested/', 'src\\a.ts', 'C:\\project\\sub\\nested\\src\\a.ts'],
    ['mixed-separator POSIX workdir', '/project\\sub/nested/', '\\src\\a.ts', '/project/sub/nested/src/a.ts'],
    ['mixed-separator UNC workdir', '\\\\server/share\\team/', '/src\\a.ts', '\\\\server\\share\\team\\src\\a.ts'],
    ['absolute Windows operation path', 'D:\\project', 'C:/outside\\file.ts', 'C:\\outside\\file.ts'],
    ['absolute UNC operation path', 'D:\\project', '\\\\server/share\\file.ts', '\\\\server\\share\\file.ts'],
  ])('%s', (_label, workdir, operationPath, expected) => {
    expect(getDisplayPath(workdir, operationPath)).toBe(expected);
  });

  it('falls back to the operation path when there is no workdir', () => {
    expect(getDisplayPath(undefined, '/src/a.ts')).toBe('/src/a.ts');
  });
});

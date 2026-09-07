// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { EditorFileTopBar } from './EditorFileTopBar';
import { useEditorStore } from '@/stores/editorStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

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

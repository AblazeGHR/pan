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
    imagePreviews: {},
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

  it('previews images with zoom, download, close, and visible load failure feedback', () => {
    const downloadFile = vi.fn();
    useEditorStore.setState({
      openPaths: ['assets/photo.png'],
      activePath: 'assets/photo.png',
      imagePreviews: { 'assets/photo.png': {
        src: '/api/fs/read?session_id=s1&path=assets%2Fphoto.png&download=1',
        displayName: 'photo.png',
      } },
      downloadFile,
    });
    render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorPane />
      </MemoryRouter>,
    );

    expect(screen.getByRole('img', { name: 'photo.png' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '保存文件' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '放大图片' }));
    expect(screen.getByText('125%')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '下载当前文件' }));
    expect(downloadFile).toHaveBeenCalledWith('assets/photo.png');
    fireEvent.error(screen.getByRole('img', { name: 'photo.png' }));
    expect(screen.getByRole('alert').textContent).toContain('图片加载失败');

    fireEvent.click(screen.getByRole('button', { name: '关闭 photo.png' }));
    expect(useEditorStore.getState().openPaths).not.toContain('assets/photo.png');
  });

  it('keeps same-name local and attachment image tabs separate while switching and closing', () => {
    const localPath = 'assets/photo.png';
    const attachmentA = `attachment:s1:att_${'a'.repeat(32)}`;
    const attachmentB = `attachment:s1:att_${'b'.repeat(32)}`;
    const localSrc = '/api/fs/read?session_id=s1&path=assets%2Fphoto.png&download=1';
    const attachmentASrc = `/api/attachments/ref/att_${'a'.repeat(32)}?session_id=s1`;
    const attachmentBSrc = `/api/attachments/ref/att_${'b'.repeat(32)}?session_id=s1`;
    useEditorStore.setState({
      openPaths: [localPath, attachmentA, attachmentB],
      activePath: localPath,
      imagePreviews: {
        [localPath]: { src: localSrc, displayName: 'photo.png' },
        [attachmentA]: { src: attachmentASrc, downloadHref: attachmentASrc, displayName: 'photo.png' },
        [attachmentB]: { src: attachmentBSrc, downloadHref: attachmentBSrc, displayName: 'photo.png' },
      },
    });
    const { container } = render(
      <MemoryRouter initialEntries={['/editor']}>
        <EditorPane />
      </MemoryRouter>,
    );

    const tabs = () => [...container.querySelectorAll('[data-testid="editor-tab"]')];
    expect(tabs()).toHaveLength(3);
    expect(screen.getByRole('img', { name: 'photo.png' }).getAttribute('src')).toBe(localSrc);

    fireEvent.click(tabs()[1]!);
    expect(screen.getByRole('img', { name: 'photo.png' }).getAttribute('src')).toBe(attachmentASrc);
    fireEvent.click(within(tabs()[1]!).getByRole('button', { name: '关闭 photo.png' }));
    expect(useEditorStore.getState().openPaths).toEqual([localPath, attachmentB]);
    expect(useEditorStore.getState().imagePreviews[attachmentB]?.src).toBe(attachmentBSrc);

    fireEvent.click(tabs()[1]!);
    expect(screen.getByRole('img', { name: 'photo.png' }).getAttribute('src')).toBe(attachmentBSrc);
  });
});

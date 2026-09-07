// @vitest-environment jsdom
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MarkdownRenderer } from './MarkdownRenderer';
import { parseMarkdownFileLink } from '@/utils/markdownFileLinks';
import { useEditorStore } from '@/stores/editorStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { listFiles, readFile } from '@/services/api';

vi.mock('@/services/api', () => ({
  listFiles: vi.fn(async () => []),
  readFile: vi.fn(async () => 'line 1\nline 2'),
  writeFile: vi.fn(async () => undefined),
  renameFs: vi.fn(async () => undefined),
  deleteFs: vi.fn(async () => undefined),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listFiles).mockResolvedValue([]);
  vi.mocked(readFile).mockResolvedValue('line 1\nline 2');
  useSessionStore.setState({
    currentSessionId: 's1',
    sessions: [{
      id: 's1', name: 'Current', workdir: 'D:\\project\\pan',
      alwaysThinkingEnabled: false, effort: '', history: [],
    }],
  });
  useEditorStore.setState({
    sessionId: null,
    workdir: null,
    openPaths: [],
    activePath: null,
    contents: {},
    pendingLocation: null,
  });
  useUIStore.setState({ toastQueue: [] });
});

describe('MarkdownRenderer', () => {
  it('renders list bullets structure and hljs spans', () => {
    const md = [
      '- item one',
      '- item two',
      '',
      '```js',
      'const x = 1;',
      '```',
    ].join('\n');
    const { container } = render(<MarkdownRenderer content={md} />);
    const ul = container.querySelector('ul');
    const li = container.querySelector('li');
    const hljsKeyword = container.querySelector('.hljs-keyword');
    const codeEl = container.querySelector('code.hljs');
    console.log('UL:', ul ? ul.outerHTML.slice(0, 300) : 'none');
    console.log('LI:', li ? li.outerHTML.slice(0, 120) : 'none');
    console.log('HLJS_KEYWORD:', hljsKeyword ? hljsKeyword.outerHTML : 'none');
    console.log('CODE:', codeEl ? codeEl.outerHTML.slice(0, 300) : 'none');
    expect(ul).toBeTruthy();
    expect(li).toBeTruthy();
    expect(hljsKeyword).toBeTruthy();
  });

  it('parses relative, Windows, file URI and line-range destinations', () => {
    expect(parseMarkdownFileLink('docs/My%20File.md#L42-L48')).toEqual({
      path: 'docs/My File.md',
      location: { path: 'docs/My File.md', line: 42, endLine: 48 },
    });
    expect(parseMarkdownFileLink('C:\\work\\My%20File.md#L42')).toEqual({
      path: 'C:/work/My File.md',
      location: { path: 'C:/work/My File.md', line: 42 },
    });
    expect(parseMarkdownFileLink('file:///C:/work/My%20File.md#L42')).toEqual({
      path: 'C:/work/My File.md',
      location: { path: 'C:/work/My File.md', line: 42 },
    });
    expect(parseMarkdownFileLink('docs/name%23with%2520percent.md')).toEqual({
      path: 'docs/name#with%20percent.md',
    });
  });

  it('keeps web, mailto and document-only anchor destinations unchanged', () => {
    expect(parseMarkdownFileLink('http://example.test/readme.md#L42')).toBeNull();
    expect(parseMarkdownFileLink('https://example.test/readme.md')).toBeNull();
    expect(parseMarkdownFileLink('mailto:user@example.test')).toBeNull();
    expect(parseMarkdownFileLink('#L42')).toBeNull();
    expect(parseMarkdownFileLink('#section')).toBeNull();
  });

  it('opens a relative link through the current Session workdir and preserves its line range', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <MarkdownRenderer content="[open](docs/My%20File.md#L42-L48)" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('link', { name: 'open' }));
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('s1', 'docs/My File.md'));
    expect(useEditorStore.getState()).toMatchObject({
      sessionId: 's1',
      workdir: 'D:\\project\\pan',
      activePath: 'docs/My File.md',
      pendingLocation: { path: 'docs/My File.md', line: 42, endLine: 48 },
    });
  });

  it('shows a visible missing-file error instead of falling back to a web link', async () => {
    vi.mocked(readFile).mockRejectedValueOnce(new Error('Not a file: missing.md'));
    render(
      <MemoryRouter initialEntries={['/']}>
        <MarkdownRenderer content="[missing](missing.md)" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('link', { name: 'missing' }));
    await waitFor(() => expect(useUIStore.getState().toastQueue.at(-1)?.message).toContain('文件不存在'));
    expect(useUIStore.getState().toastQueue.at(-1)?.type).toBe('error');
  });
});

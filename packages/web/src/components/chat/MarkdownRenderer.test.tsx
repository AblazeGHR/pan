// @vitest-environment jsdom
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Profiler } from 'react';
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

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
  it('does not reparse historical Markdown when another message streams', () => {
    const commits = vi.fn();
    render(<Profiler id="historical-markdown" onRender={commits}>
      <MarkdownRenderer content={'# Existing history\n```js\nconst x = 1;\n```'} />
    </Profiler>);
    commits.mockClear();
    act(() => {
      useSessionStore.setState(s => ({ sessions: s.sessions.map(session => ({
        ...session, history: [...session.history, { role: 'assistant', content: 'new delta' }],
      })) }));
    });
    expect(commits).not.toHaveBeenCalled();
  });

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
    expect(parseMarkdownFileLink('/D:/project/Pan-main/docs/plans&overviews/overview.md:20')).toEqual({
      path: 'D:/project/Pan-main/docs/plans&overviews/overview.md',
      location: {
        path: 'D:/project/Pan-main/docs/plans&overviews/overview.md',
        line: 20,
      },
    });
    expect(parseMarkdownFileLink('docs/name%23with%2520percent.md')).toEqual({
      path: 'docs/name#with%20percent.md',
    });
    expect(parseMarkdownFileLink('docs/My%20File.md:42-48')).toEqual({
      path: 'docs/My File.md',
      location: { path: 'docs/My File.md', line: 42, endLine: 48 },
    });
    expect(parseMarkdownFileLink('C:%5Cwork%5Cname%3Aarchive.md:7')).toEqual({
      path: 'C:/work/name:archive.md',
      location: { path: 'C:/work/name:archive.md', line: 7 },
    });
    expect(parseMarkdownFileLink('/docs/readme.md#L3')).toEqual({
      path: '/docs/readme.md',
      location: { path: '/docs/readme.md', line: 3 },
    });
    expect(parseMarkdownFileLink('\\\\server\\share\\readme.md#L3')).toEqual({
      path: '//server/share/readme.md',
      location: { path: '//server/share/readme.md', line: 3 },
    });
  });

  it('strips malformed numeric line targets while preserving ordinary path colons', () => {
    expect(parseMarkdownFileLink('docs/name:0')).toEqual({ path: 'docs/name' });
    expect(parseMarkdownFileLink('docs/name:8-3')).toEqual({ path: 'docs/name' });
    expect(parseMarkdownFileLink('docs/name:part.md')).toEqual({ path: 'docs/name:part.md' });
    expect(parseMarkdownFileLink('C:/work/name:part.md')).toEqual({ path: 'C:/work/name:part.md' });
  });

  it('keeps web, mailto and document-only anchor destinations unchanged', () => {
    expect(parseMarkdownFileLink('http://example.test/readme.md#L42')).toBeNull();
    expect(parseMarkdownFileLink('https://example.test/readme.md')).toBeNull();
    expect(parseMarkdownFileLink('mailto:user@example.test')).toBeNull();
    expect(parseMarkdownFileLink('#L42')).toBeNull();
    expect(parseMarkdownFileLink('#section')).toBeNull();
    expect(parseMarkdownFileLink('/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1')).toBeNull();
    expect(parseMarkdownFileLink('/api/fs/read?session_id=s1&path=x&download=1')).toBeNull();
  });

  it('parses opaque editor links as server references while retaining the line range', () => {
    const attachmentId = `att_${'d'.repeat(32)}`;
    expect(parseMarkdownFileLink(
      `/api/attachments/editor/${attachmentId}?session_id=s1#L12-L15`,
    )).toEqual({
      path: '',
      location: { path: '', line: 12, endLine: 15 },
      serverAttachmentId: attachmentId,
      serverSessionId: 's1',
    });
  });

  it('renders attachment hrefs as clickable standard Markdown and preserves ordinary links', () => {
    const { container } = render(
      <MemoryRouter>
        <MarkdownRenderer content={'[需求说明 \\[最终\\].md](/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1) [普通链接](https://example.test/a_(b))'} />
      </MemoryRouter>,
    );

    const links = [...container.querySelectorAll('a')];
    expect(links.map((link) => ({ text: link.textContent, href: link.getAttribute('href') }))).toEqual([
      {
        text: '需求说明 [最终].md',
        href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
      },
      { text: '普通链接', href: 'https://example.test/a_(b)' },
    ]);
  });

  it('marks safe attachment links as draggable sources with the file icon', () => {
    const { container } = render(
      <MemoryRouter>
        <MarkdownRenderer content={'[接口说明.md](/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1)'} />
      </MemoryRouter>,
    );
    const link = container.querySelector('[data-testid="draggable-attachment"]') as HTMLAnchorElement;
    const setData = vi.fn();
    fireEvent.dragStart(link, { dataTransfer: { setData, effectAllowed: 'none' } });

    expect(link.draggable).toBe(true);
    expect(link.querySelector('svg')).toBeTruthy();
    expect(setData).toHaveBeenCalledWith(
      'application/x-pan-attachment',
      expect.stringContaining('接口说明.md'),
    );
  });

  it('renders an opaque editor link that supports click-to-open and dragstart without a path payload', async () => {
    const attachmentId = `att_${'c'.repeat(32)}`;
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      ok: true, path: 'D:\\project\\Pan\\docs\\readme.md',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    const { container } = render(
      <MemoryRouter initialEntries={['/']}>
        <MarkdownRenderer content={`[readme.md](/api/attachments/editor/${attachmentId}?session_id=s1#L4-L8)`} />
      </MemoryRouter>,
    );
    const link = container.querySelector('a') as HTMLAnchorElement;
    expect(link.draggable).toBe(true);
    const setData = vi.fn();
    fireEvent.dragStart(link, { dataTransfer: { setData, effectAllowed: 'none' } });
    const dragValue = setData.mock.calls.find(([type]) => type === 'application/x-pan-attachment')?.[1] as string;
    expect(dragValue).toContain(`/api/attachments/ref/${attachmentId}`);
    expect(dragValue).toContain('"line":4');
    expect(dragValue).not.toContain('D:');
    fireEvent.dragEnd(link);
    fireEvent.click(link);
    expect(fetch).not.toHaveBeenCalled();

    cleanup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <MarkdownRenderer content={`[readme.md](/api/attachments/editor/${attachmentId}?session_id=s1#L4-L8)`} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('link', { name: 'readme.md' }));
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('s1', 'D:\\project\\Pan\\docs\\readme.md'));
    expect(useEditorStore.getState().pendingLocation).toEqual({
      path: 'D:\\project\\Pan\\docs\\readme.md', line: 4, endLine: 8,
    });
  });

  it('opens raster attachment links in the Editor preview using the server-owned opaque reference', async () => {
    const attachmentId = `att_${'e'.repeat(32)}`;
    const secondAttachmentId = `att_${'f'.repeat(32)}`;
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      path: 'D:\\private\\photo.png',
      displayName: 'photo.png',
      mimeType: 'image/png',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    render(
      <MemoryRouter initialEntries={['/']}>
        <MarkdownRenderer content={[
          `[photo.png](/api/attachments/ref/${attachmentId}?session_id=s1)`,
          `[photo.png](/api/attachments/ref/${secondAttachmentId}?session_id=s1)`,
        ].join(' ')} />
      </MemoryRouter>,
    );

    const imageLinks = screen.getAllByRole('link', { name: 'photo.png' });
    fireEvent.click(imageLinks[0]!);

    await waitFor(() => expect(useEditorStore.getState().imagePreviews[`attachment:s1:${attachmentId}`]).toEqual({
      src: `/api/attachments/ref/${attachmentId}?session_id=s1`,
      downloadHref: `/api/attachments/ref/${attachmentId}?session_id=s1`,
      displayName: 'photo.png',
    }));
    expect(fetch).toHaveBeenCalledWith(`/api/attachments/editor/${attachmentId}?session_id=s1`);
    fireEvent.click(imageLinks[1]!);
    await waitFor(() => expect(useEditorStore.getState().imagePreviews[`attachment:s1:${secondAttachmentId}`]).toEqual({
      src: `/api/attachments/ref/${secondAttachmentId}?session_id=s1`,
      downloadHref: `/api/attachments/ref/${secondAttachmentId}?session_id=s1`,
      displayName: 'photo.png',
    }));
    expect(useEditorStore.getState().imagePreviews[`attachment:s1:${attachmentId}`]?.src)
      .toBe(`/api/attachments/ref/${attachmentId}?session_id=s1`);
    expect(useEditorStore.getState().activePath).toBe(`attachment:s1:${secondAttachmentId}`);
    expect(readFile).not.toHaveBeenCalled();
  });

  it('keeps non-image attachment links on their original browser path', () => {
    const { container } = render(
      <MemoryRouter>
        <MarkdownRenderer content={'[archive.zip](/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.zip?session_id=s1)'} />
      </MemoryRouter>,
    );
    const link = container.querySelector('a')!;
    expect(link.getAttribute('href')).toContain('/api/attachments/upload_');
    expect(fetch).not.toHaveBeenCalled();
    expect(readFile).not.toHaveBeenCalled();
  });

  it('recovers a legacy attachment without display metadata using a safe download href', () => {
    const { container } = render(
      <MemoryRouter>
        <MarkdownRenderer content={'请打开 @"D:\\old\\upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md"'} />
      </MemoryRouter>,
    );

    const link = container.querySelector('a');
    expect(link?.textContent).toBe('upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md');
    expect(link?.getAttribute('href')).toBe('/api/fs/read?session_id=s1&path=D%3A%5Cold%5Cupload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md&download=1');
  });

  it('preserves local file hrefs through react-markdown URL sanitization', () => {
    const { container } = render(
      <MemoryRouter>
        <MarkdownRenderer content={'[windows](D:/work/file.md#L42) [uri](file:///C:/work/file.md#L3) [web](https://example.test)'} />
      </MemoryRouter>,
    );

    const links = [...container.querySelectorAll('a')];
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      'D:/work/file.md#L42',
      'file:///C:/work/file.md#L3',
      'https://example.test',
    ]);
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

  it('opens a root-relative Windows drive href without the synthetic leading slash', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <MarkdownRenderer content="[open](/D:/project/Pan-main/docs/plans&overviews/overview.md:20)" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('link', { name: 'open' }));
    await waitFor(() => expect(readFile).toHaveBeenCalledWith(
      's1',
      'D:/project/Pan-main/docs/plans&overviews/overview.md',
    ));
    expect(useEditorStore.getState().pendingLocation).toEqual({
      path: 'D:/project/Pan-main/docs/plans&overviews/overview.md',
      line: 20,
    });
  });

  it('opens source-style colon line targets without passing the suffix to the file API', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <MarkdownRenderer content="[open](docs/My%20File.md:42-48)" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('link', { name: 'open' }));
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('s1', 'docs/My File.md'));
    expect(useEditorStore.getState().pendingLocation).toEqual({
      path: 'docs/My File.md', line: 42, endLine: 48,
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

// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { registerEditorFileLinks, resolveEditorFileLink } from './editorFileLinks';
import { useEditorStore } from '@/stores/editorStore';
import type { Monaco } from '@monaco-editor/react';

describe('Editor document file destinations', () => {
  it('registers once, publishes string URLs, and routes local clicks through openFile', async () => {
    const provider = vi.fn();
    const opener = vi.fn();
    const monaco = {
      languages: { registerLinkProvider: provider },
      editor: { registerLinkOpener: opener },
      Range: class {
        constructor(
          public startLineNumber: number,
          public startColumn: number,
          public endLineNumber: number,
          public endColumn: number,
        ) {}
      },
      Uri: {
        from: ({ scheme, query }: { scheme: string; query: string }) => ({
          toString: () => `${scheme}:?${query}`,
        }),
        parse: (source: string) => ({ scheme: 'file', fsPath: source }),
      },
    } as unknown as Monaco;
    registerEditorFileLinks(monaco);
    registerEditorFileLinks(monaco);
    expect(provider).toHaveBeenCalledTimes(1);
    const text = '[指南](DEVELOPING.md#标题) [web](https://example.com)';
    const result = provider.mock.calls[0]![1].provideLinks({
      getLineCount: () => 1,
      getLineContent: () => text,
      uri: { toString: () => 'D:/repo/docs/index.md' },
    });
    expect(result.links).toHaveLength(1);
    expect(typeof result.links[0].url).toBe('string');
    expect(result.links[0].range.startColumn).toBe(6);
    const openFile = vi.spyOn(useEditorStore.getState(), 'openFile').mockResolvedValue(true);
    const url = result.links[0].url as string;
    await opener.mock.calls[0]![0].open({
      scheme: 'pan-editor-file',
      query: url.slice(url.indexOf('?') + 1),
    });
    expect(openFile).toHaveBeenCalledWith('D:/repo/docs/DEVELOPING.md', undefined);
    expect(await opener.mock.calls[0]![0].open({ scheme: 'https', query: '' })).toBe(false);
    openFile.mockRestore();
  });
  it.each([
    ['DEVELOPING.md#已有-功能', 'D:/repo/docs/DEVELOPING.md'],
    ['./CHECKLIST.md', 'D:/repo/docs/CHECKLIST.md'],
    ['../examples/demo.py', 'D:/repo/examples/demo.py'],
    ['../design/Pan-Plugin-Capability.md', 'D:/repo/design/Pan-Plugin-Capability.md'],
    ['中文%20文件.md', 'D:/repo/docs/中文 文件.md'],
    ['C:\\other\\file.md#title', 'C:/other/file.md'],
    ['file:///C:/other/file.md', 'C:/other/file.md'],
    ['/tmp/file.md', '/tmp/file.md'],
  ])('resolves %s from the current file directory', (href, path) => {
    expect(resolveEditorFileLink(href, 'D:/repo/docs/index.md', 'D:/repo')?.path).toBe(path);
  });
  it('uses document directory before a same-name workspace file', () => {
    expect(resolveEditorFileLink('CHECKLIST.md', 'docs/index.md', 'D:/repo')?.path).toBe(
      'D:/repo/docs/CHECKLIST.md',
    );
  });
  it('retargets line ranges after resolution', () => {
    expect(resolveEditorFileLink('../file.ts:12-14', 'D:/repo/docs/index.md', null)).toEqual({
      path: 'D:/repo/file.ts',
      location: { path: 'D:/repo/file.ts', line: 12, endLine: 14 },
    });
    expect(resolveEditorFileLink('./file.ts#L8', 'D:/repo/index.md', null)?.location?.line).toBe(8);
  });
  it.each(['https://example.com/a.md', 'http://example.com/a.md', 'mailto:a@b.test', '#section'])(
    'leaves %s to normal link handling',
    (href) => {
      expect(resolveEditorFileLink(href, 'D:/repo/index.md', null)).toBeNull();
    },
  );
});

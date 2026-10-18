import type { Monaco } from '@monaco-editor/react';
import { useEditorStore } from '@/stores/editorStore';
import { parseMarkdownFileLink } from './markdownFileLinks';

/** Resolve against the document, never the browser's origin or active tab. */
export function resolveEditorFileLink(href: string, source: string, workdir: string | null) {
  const target = parseMarkdownFileLink(href);
  if (!target || target.serverAttachmentId) return null;
  const absolute = (value: string) => /^(?:[A-Za-z]:\/|\/)/.test(value);
  let path = target.path;
  if (!absolute(path)) {
    let document = source.replace(/\\/g, '/');
    if (!absolute(document) && workdir) document = `${workdir.replace(/\\/g, '/')}/${document}`;
    path = `${document.slice(0, document.lastIndexOf('/') + 1)}${path}`;
  }
  const prefix = path.startsWith('//') ? '//' : path.startsWith('/') ? '/' : '';
  const parts: string[] = [];
  for (const part of path.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..' && parts.length && !/^[A-Za-z]:$/.test(parts.at(-1)!)) parts.pop();
    else if (part !== '..') parts.push(part);
  }
  path = prefix + parts.join('/');
  return { path, location: target.location ? { ...target.location, path } : undefined };
}

const registered = new WeakSet<Monaco>();

export function registerEditorFileLinks(monaco: Monaco) {
  if (registered.has(monaco)) return;
  registered.add(monaco);
  monaco.languages.registerLinkProvider('markdown', {
    provideLinks(model: {
      getLineCount(): number;
      getLineContent(line: number): string;
      uri: { toString(): string };
    }) {
      const links: {
        range: {
          startLineNumber: number;
          startColumn: number;
          endLineNumber: number;
          endColumn: number;
        };
        url: string;
      }[] = [];
      for (let line = 1; line <= model.getLineCount(); line++) {
        const text = model.getLineContent(line);
        // Inline Markdown destinations, including angle-wrapped paths with spaces.
        const pattern = /\]\((<[^>\r\n]+>|[^)\r\n]+)\)/g;
        for (const match of text.matchAll(pattern)) {
          const raw = match[1]!;
          const href = raw.startsWith('<') ? raw.slice(1, -1) : raw;
          const target = parseMarkdownFileLink(href);
          if (!target || target.serverAttachmentId) continue;
          const start = match.index! + 2;
          links.push({
            range: new monaco.Range(line, start + 1, line, start + raw.length + 1),
            url: monaco.Uri.from({
              scheme: 'pan-editor-file',
              query: JSON.stringify({ href, source: model.uri.toString() }),
            }).toString(),
          });
        }
      }
      return { links };
    },
  });
  monaco.editor.registerLinkOpener({
    async open(uri: { scheme: string; query: string }) {
      if (uri.scheme !== 'pan-editor-file') return false;
      const { href, source } = JSON.parse(uri.query) as { href: string; source: string };
      const store = useEditorStore.getState();
      // Monaco uses a file URI for relative model paths and a drive scheme for
      // Windows paths. Decode its URI before resolving the document directory.
      const sourceUri = monaco.Uri.parse(source);
      const document = sourceUri.scheme === 'file' ? sourceUri.fsPath : decodeURIComponent(source);
      const target = resolveEditorFileLink(href, document, store.workdir);
      // Acknowledge before the read switches tabs and disposes this editor's
      // link operation. openFile owns read failures and their toast feedback.
      if (target) void store.openFile(target.path, target.location);
      return true;
    },
  });
}

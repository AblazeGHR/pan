import type { EditorLocation } from '@/stores/editorStore';

export interface MarkdownFileLink {
  path: string;
  location?: EditorLocation;
}

function decodeUrlPart(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

function normalizeFilePath(path: string): string {
  const isUncPath = path.startsWith('\\\\') || path.startsWith('//');
  const normalized = path.replace(/[\\/]+/g, '/');
  if (/^[A-Za-z]:\/$/.test(normalized) || normalized === '/') return normalized;
  if (/^[A-Za-z]:\//.test(normalized)) return normalized;
  if (isUncPath) return `//${normalized.replace(/^\/+/, '')}`;
  return normalized.replace(/^\.\//, '');
}

function isWindowsAbsolutePath(path: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(path) || path.startsWith('\\\\');
}

function parseLineLocation(fragment: string, path: string): EditorLocation | undefined {
  const decodedFragment = decodeUrlPart(fragment);
  if (!decodedFragment) return undefined;
  const match = /^L(\d+)(?:-L(\d+))?$/i.exec(decodedFragment);
  if (!match) return undefined;
  const line = Number(match[1]);
  const endLine = match[2] ? Number(match[2]) : undefined;
  if (!Number.isSafeInteger(line) || line < 1 || (endLine !== undefined && endLine < line)) {
    return undefined;
  }
  return endLine === undefined ? { path, line } : { path, line, endLine };
}

function pathFromFileUri(decoded: string): string | null {
  // Handle file://C:/path, file:///C:/path and UNC file://server/share/path
  // without letting URL's browser-origin semantics reinterpret a drive letter.
  if (/^file:\/\//i.test(decoded)) {
    const authorityAndPath = decoded.slice(7).replace(/\\/g, '/');
    const slash = authorityAndPath.indexOf('/');
    const authority = slash === -1 ? authorityAndPath : authorityAndPath.slice(0, slash);
    const pathname = slash === -1 ? '' : authorityAndPath.slice(slash);
    if (!authority || authority.toLowerCase() === 'localhost') {
      return /^\/[A-Za-z]:[\\/]/.test(pathname) ? pathname.slice(1) : pathname || null;
    }
    if (/^[A-Za-z]:$/.test(authority)) return `${authority}${pathname || '/'}`;
    return `//${authority}${pathname}`;
  }

  const withoutScheme = decoded.slice(5);
  return withoutScheme || null;
}

/**
 * Classify a Markdown destination as a server file link. Returning null is
 * intentional: ReactMarkdown keeps the original anchor behavior for web
 * URLs, mailto links, and document-only anchors.
 */
export function parseMarkdownFileLink(href: string): MarkdownFileLink | null {
  // Pan attachment/download hrefs are ordinary browser links. Keep them out
  // of the editor-file classifier so clicking an attachment downloads the
  // server-validated target instead of trying to open `/api/...` in Editor.
  if (/^\/api\/(?:attachments\/|fs\/read(?:\?|$))/.test(href)) return null;
  const hashIndex = href.indexOf('#');
  const rawPath = hashIndex === -1 ? href : href.slice(0, hashIndex);
  const rawFragment = hashIndex === -1 ? '' : href.slice(hashIndex + 1);
  if (!rawPath || href.startsWith('#')) return null;

  const decodedPath = decodeUrlPart(rawPath);
  if (decodedPath === null) return null;
  const lowerPath = decodedPath.toLowerCase();
  const isFileUri = lowerPath.startsWith('file://') || lowerPath.startsWith('file:');
  const hasScheme = /^[A-Za-z][A-Za-z\d+.-]*:/.test(decodedPath);
  if (hasScheme && !isFileUri && !isWindowsAbsolutePath(decodedPath)) return null;

  const path = normalizeFilePath(
    isFileUri ? pathFromFileUri(decodedPath) ?? '' : decodedPath,
  );
  if (!path) return null;
  const location = rawFragment ? parseLineLocation(rawFragment, path) : undefined;
  return location ? { path, location } : { path };
}

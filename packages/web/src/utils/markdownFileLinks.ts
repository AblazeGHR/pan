import type { EditorLocation } from '@/stores/editorStore';

export interface MarkdownFileLink {
  path: string;
  location?: EditorLocation;
  serverAttachmentId?: string;
  serverSessionId?: string;
}

const EDITOR_ATTACHMENT_RE = /^\/api\/attachments\/editor\/(att_[A-Za-z0-9]{32}|upload_[A-Za-z0-9]{32}(?:\.[A-Za-z0-9._-]{1,32})?)$/;

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
  // Markdown destinations commonly turn a Windows absolute path into a
  // root-relative href (`/D:/...`). Treat only this drive-letter shape as a
  // Windows path; ordinary Unix-rooted paths such as `/docs/readme.md` stay
  // unchanged.
  if (/^\/[A-Za-z]:(?:\/|$)/.test(normalized)) return normalized.slice(1);
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

interface ColonLineTarget {
  path: string;
  location?: EditorLocation;
}

/** Parse source-style `path:123` / `path:123-125` targets. */
function parseColonLineTarget(path: string): ColonLineTarget {
  const match = /^(.*):(\d+)(?:-(\d+))?$/.exec(path);
  // A drive root such as `C:` is a path, never a line target.
  if (!match || !match[1] || /^[A-Za-z]$/.test(match[1])) return { path };

  const line = Number(match[2]);
  const endLine = match[3] ? Number(match[3]) : undefined;
  if (
    Number.isSafeInteger(line) &&
    line >= 1 &&
    (endLine === undefined || (Number.isSafeInteger(endLine) && endLine >= line))
  ) {
    return {
      path: match[1],
      location: endLine === undefined
        ? { path: match[1], line }
        : { path: match[1], line, endLine },
    };
  }

  // A malformed numeric target (for example :0 or a reversed range) should
  // still open the underlying file rather than becoming part of its path.
  return { path: match[1] };
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
  const editorUrl = (() => {
    try {
      return new URL(href, window.location.origin);
    } catch {
      return null;
    }
  })();
  const editorMatch = editorUrl && EDITOR_ATTACHMENT_RE.exec(editorUrl.pathname);
  if (editorMatch && editorUrl.origin === window.location.origin) {
    const sessionId = editorUrl.searchParams.get('session_id');
    if (!sessionId) return null;
    const rawFragment = editorUrl.hash.slice(1);
    const location = rawFragment
      ? parseLineLocation(rawFragment, '')
      : undefined;
    return {
      path: '',
      ...(location ? { location } : {}),
      serverAttachmentId: editorMatch[1],
      serverSessionId: sessionId,
    };
  }
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

  const decodedFilePath = isFileUri ? pathFromFileUri(decodedPath) ?? '' : decodedPath;
  const colonTarget = parseColonLineTarget(decodedFilePath);
  const path = normalizeFilePath(colonTarget.path);
  if (!path) return null;
  const location = rawFragment
    ? parseLineLocation(rawFragment, path)
    : colonTarget.location
      ? { ...colonTarget.location, path }
      : undefined;
  return location ? { path, location } : { path };
}

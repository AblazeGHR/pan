export interface AttachmentMarkdownRef {
  displayName: string;
  href: string;
}

const INTERNAL_ATTACHMENT_PATH = /^\/api\/(?:attachments\/[^/?#]+|fs\/read(?:\?|$))/;

/** Escape label syntax while preserving the displayed filename. */
export function escapeMarkdownLabel(displayName: string): string {
  return displayName.replace(/[\\[\]()]/g, '\\$&');
}

/**
 * Accept only same-origin routes that Pan creates for an attachment.  In
 * particular, a filename can never turn into a javascript:, data:, protocol
 * relative, absolute, or path traversal URL.
 */
export function isSafeAttachmentHref(href: string): boolean {
  if (!href || !href.startsWith('/')) return false;
  for (const character of href) {
    const code = character.charCodeAt(0);
    if (code < 0x20 || code === 0x7f || character === '\\') return false;
  }
  try {
    const url = new URL(href, window.location.origin);
    if (url.origin !== window.location.origin || !INTERNAL_ATTACHMENT_PATH.test(url.pathname + url.search)) {
      return false;
    }
    if (url.pathname.startsWith('/api/attachments/')) {
      return /^\/api\/attachments\/upload_[A-Za-z0-9]{32}(?:\.[A-Za-z0-9._-]{1,32})?$/.test(url.pathname)
        && !!url.searchParams.get('session_id');
    }
    return !!url.searchParams.get('session_id')
      && url.searchParams.get('path') !== null
      && url.searchParams.get('download') === '1';
  } catch {
    return false;
  }
}

export function attachmentMarkdown(ref: AttachmentMarkdownRef): string | null {
  if (!ref.displayName || !isSafeAttachmentHref(ref.href)) return null;
  return `[${escapeMarkdownLabel(ref.displayName)}](${ref.href})`;
}

export function serverFileDownloadHref(sessionId: string, path: string): string {
  const params = new URLSearchParams({ session_id: sessionId, path, download: '1' });
  return `/api/fs/read?${params.toString()}`;
}

function basename(path: string): string {
  return path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || 'attachment';
}

const LEGACY_ATTACHMENT_RE = /@"([^"\r\n]+)"/g;

/**
 * Render old persisted ``@"path"`` attachment text as a standard Markdown
 * link.  Missing original metadata falls back to the path basename; ordinary
 * Markdown links never match this compatibility-only pattern.
 */
export function normalizeLegacyAttachmentLinks(content: string, sessionId?: string): string {
  if (!sessionId) return content;
  return content.replace(LEGACY_ATTACHMENT_RE, (_match, path: string) => (
    attachmentMarkdown({ displayName: basename(path), href: serverFileDownloadHref(sessionId, path) })
    || _match
  ));
}

import { isSafeAttachmentHref, serverAttachmentDownloadHref } from './attachmentMarkdown';

export const ATTACHMENT_DRAG_MIME = 'application/x-pan-attachment';

export interface AttachmentDragPayload {
  displayName: string;
  href: string;
  path?: string;
  attachmentId?: string;
  /** Server-owned id when the source is a chip/message; UI id stays local. */
  serverAttachmentId?: string;
  /** Source Session is metadata, not a filesystem authority. */
  sourceSessionId?: string;
  location?: { line: number; endLine?: number };
  source?: 'message' | 'attachment-chip' | 'composer';
}

/** Put the small, UI-only attachment description on the native drag payload. */
export function writeAttachmentDragPayload(
  dataTransfer: DataTransfer,
  payload: AttachmentDragPayload,
): void {
  const inferredSessionId = payload.sourceSessionId || (() => {
    try {
      return new URL(payload.href, window.location.origin).searchParams.get('session_id') || undefined;
    } catch {
      return undefined;
    }
  })();
  const href = payload.serverAttachmentId && inferredSessionId
    ? serverAttachmentDownloadHref(inferredSessionId, payload.serverAttachmentId)
    : payload.href;
  // Keep the old optional path property in the TypeScript input shape for
  // T-027.1 compatibility, but never serialize it into the browser payload.
  if (!isSafeAttachmentHref(href) || /\/api\/fs\/read(?:\?|$)/.test(href)) {
    dataTransfer.setData(ATTACHMENT_DRAG_MIME, '');
    return;
  }
  const value = JSON.stringify({
    displayName: payload.displayName,
    href,
    ...(payload.attachmentId ? { attachmentId: payload.attachmentId } : {}),
    ...(payload.serverAttachmentId ? { serverAttachmentId: payload.serverAttachmentId } : {}),
    ...(inferredSessionId ? { sourceSessionId: inferredSessionId } : {}),
    ...(payload.location ? { location: payload.location } : {}),
    ...(payload.source ? { source: payload.source } : {}),
  });
  dataTransfer.setData(ATTACHMENT_DRAG_MIME, value);
  dataTransfer.setData('text/plain', payload.displayName);
  dataTransfer.effectAllowed = payload.source === 'composer' || payload.source === 'attachment-chip'
    ? 'move'
    : 'copy';
}

/** Read only attachment routes produced by Pan. Arbitrary dropped URLs are ignored. */
export function readAttachmentDragPayload(
  dataTransfer: DataTransfer | null,
): AttachmentDragPayload | null {
  if (!dataTransfer) return null;
  try {
    const raw = dataTransfer.getData(ATTACHMENT_DRAG_MIME);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const value = parsed as Record<string, unknown>;
    if (
      typeof value.displayName !== 'string' ||
      !value.displayName.trim() ||
      typeof value.href !== 'string' ||
      !isSafeAttachmentHref(value.href)
    ) return null;
    if (Object.prototype.hasOwnProperty.call(value, 'path')) return null;
    if (/\/api\/fs\/read(?:\?|$)/.test(value.href)) return null;
    const location = value.location;
    const validLocation = location && typeof location === 'object'
      && Number.isSafeInteger((location as { line?: unknown }).line)
      && ((location as { line: number }).line >= 1)
      && ((location as { endLine?: unknown }).endLine === undefined
        || (Number.isSafeInteger((location as { endLine?: unknown }).endLine)
          && (location as { endLine: number }).endLine >= (location as { line: number }).line));
    return {
      displayName: value.displayName,
      href: value.href,
      ...(typeof value.attachmentId === 'string' ? { attachmentId: value.attachmentId } : {}),
      ...(typeof value.serverAttachmentId === 'string' ? { serverAttachmentId: value.serverAttachmentId } : {}),
      ...(typeof value.sourceSessionId === 'string' ? { sourceSessionId: value.sourceSessionId } : {}),
      ...(validLocation ? { location: location as { line: number; endLine?: number } } : {}),
      ...(value.source === 'message' || value.source === 'attachment-chip' || value.source === 'composer'
        ? { source: value.source }
        : {}),
    };
  } catch {
    return null;
  }
}

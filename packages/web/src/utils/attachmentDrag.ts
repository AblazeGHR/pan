import { isSafeAttachmentHref } from './attachmentMarkdown';

export const ATTACHMENT_DRAG_MIME = 'application/x-pan-attachment';

export interface AttachmentDragPayload {
  displayName: string;
  href: string;
  path?: string;
  attachmentId?: string;
  source?: 'message' | 'attachment-chip' | 'composer';
}

/** Put the small, UI-only attachment description on the native drag payload. */
export function writeAttachmentDragPayload(
  dataTransfer: DataTransfer,
  payload: AttachmentDragPayload,
): void {
  const value = JSON.stringify(payload);
  dataTransfer.setData(ATTACHMENT_DRAG_MIME, value);
  dataTransfer.setData('text/plain', payload.displayName);
  dataTransfer.effectAllowed = 'copy';
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
    return {
      displayName: value.displayName,
      href: value.href,
      ...(typeof value.path === 'string' ? { path: value.path } : {}),
      ...(typeof value.attachmentId === 'string' ? { attachmentId: value.attachmentId } : {}),
      ...(value.source === 'message' || value.source === 'attachment-chip' || value.source === 'composer'
        ? { source: value.source }
        : {}),
    };
  } catch {
    return null;
  }
}

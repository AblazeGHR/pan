import {
  ATTACHMENT_DRAG_MIME,
  readAttachmentDragPayload,
  writeAttachmentDragPayload,
  type AttachmentDragPayload,
} from './attachmentDrag';
import type { AttachmentLocation } from '@/types/attachment';

export type AttachmentTransferSource = 'message' | 'editor' | 'attachment-chip' | 'composer';

export type PanAttachmentPayload = Omit<AttachmentDragPayload, 'source'> & {
  source?: AttachmentTransferSource;
  sourceSessionId?: string;
  location?: AttachmentLocation;
};

function readRawPayload(dataTransfer: DataTransfer | null): Record<string, unknown> | null {
  if (!dataTransfer) return null;
  try {
    const raw = dataTransfer.getData(ATTACHMENT_DRAG_MIME);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function readLocation(value: unknown): AttachmentLocation | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const location = value as Record<string, unknown>;
  if (!Number.isSafeInteger(location.line) || (location.line as number) < 1) return undefined;
  if (
    location.endLine !== undefined &&
    (!Number.isSafeInteger(location.endLine) ||
      (location.endLine as number) < (location.line as number))
  ) {
    return undefined;
  }
  return location.endLine === undefined
    ? { line: location.line as number }
    : { line: location.line as number, endLine: location.endLine as number };
}

/**
 * Read the custom Pan transfer while preserving fields that the legacy
 * attachmentDrag helper intentionally does not know about yet.
 */
export function readPanAttachmentPayload(
  dataTransfer: DataTransfer | null,
): PanAttachmentPayload | null {
  const base = readAttachmentDragPayload(dataTransfer);
  if (!base) return null;
  const raw = readRawPayload(dataTransfer);
  const source =
    raw?.source === 'message' ||
    raw?.source === 'editor' ||
    raw?.source === 'attachment-chip' ||
    raw?.source === 'composer'
      ? raw.source
      : base.source;
  const sourceSessionId =
    typeof raw?.sourceSessionId === 'string' && raw.sourceSessionId
      ? raw.sourceSessionId
      : undefined;
  const location = readLocation(raw?.location);
  return {
    ...base,
    ...(source ? { source } : {}),
    ...(sourceSessionId ? { sourceSessionId } : {}),
    ...(location ? { location } : {}),
  };
}

/** Only locally-owned chip/composer transfers are Session-bound. */
export function isAttachmentPayloadForSession(
  payload: PanAttachmentPayload,
  currentSessionId: string | null | undefined,
): boolean {
  if (!payload.sourceSessionId) return true;
  if (payload.source !== 'attachment-chip' && payload.source !== 'composer') return true;
  return payload.sourceSessionId === currentSessionId;
}

/** Serialize the extended payload through the existing safe drag writer. */
export function writePanAttachmentPayload(
  dataTransfer: DataTransfer,
  payload: PanAttachmentPayload,
): void {
  writeAttachmentDragPayload(dataTransfer, payload as AttachmentDragPayload);
}

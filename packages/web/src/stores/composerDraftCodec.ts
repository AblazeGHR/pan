/**
 * Wire codec for composer drafts.
 *
 * This is the only place that decides what a draft looks like on the wire, in
 * both directions.  It lives apart from `InputRow` so the round-trip can be
 * exercised (and its cost measured) without mounting React, and so the
 * persisted contract has one owner.
 *
 * The round trip must be exact for everything the server can resolve:
 * `ComposerValue.parts` order, each occurrence's `occurrenceId`, and
 * recoverable attachment metadata.  Two drafts that differ only in *ordering*
 * or in a `location` are different drafts and must not compare equal.
 */

import type { AttachmentLocation } from '@/types/attachment';
import type { ApiSessionDraft, ApiSessionDraftAttachment } from '@/services/api';

/** The composer-side attachment shape this codec needs.  Structurally a subset
 *  of InputRow's `PendingAttachment`; declared here so the codec does not
 *  depend on the component. */
export interface CodecAttachment {
  occurrenceId: string;
  id: string;
  displayName: string;
  path?: string;
  href?: string;
  status: 'uploading' | 'registering' | 'ready' | 'error';
  fileKey?: string;
  attachmentId?: string;
  mimeType?: string;
  source?: 'upload' | 'server_file';
  location?: AttachmentLocation;
  /** Present only for a local, not-yet-uploaded File.  Deliberately dropped:
   *  see `toDraft`.  Typed as `File | undefined` so this shape stays
   *  assignable to the composer's own attachment type; the codec only ever
   *  *reads* it (by never copying it), so no `File` handling is introduced
   *  here. */
  file?: File;
}

export interface CodecComposerPart {
  type: 'text';
  value: string;
}

export interface CodecAttachmentPart {
  type: 'attachment';
  attachmentId: string;
  occurrenceId?: string;
}

export type CodecPart = CodecComposerPart | CodecAttachmentPart;

export interface CodecComposerValue {
  parts: CodecPart[];
  text: string;
  occurrenceIds: string[];
  attachmentIds: string[];
}

export function occurrenceIdOf(attachment: CodecAttachment): string {
  return attachment.occurrenceId || attachment.id;
}

/**
 * Keep only attachments the server can actually resolve after a restart.
 *
 * A local `File` that was never uploaded has no server identity, so it cannot
 * come back: the bytes are gone and re-uploading would silently duplicate a
 * payload the user never asked to send twice.  Such an attachment is dropped
 * rather than persisted as a chip that would fail to resolve on reload, and it
 * is never re-uploaded.
 */
export function restorableAttachments(draft: ApiSessionDraft | null): ApiSessionDraftAttachment[] {
  if (!draft) return [];
  return draft.attachments.filter((attachment) => !!attachment.attachmentId || !!attachment.path);
}

/** Filter references with the same occurrence identity as the metadata. */
function recoverableParts(
  parts: CodecPart[],
  attachments: ApiSessionDraftAttachment[],
): CodecPart[] {
  const occurrences = new Set(attachments.map((item) => item.occurrenceId));
  return parts
    .filter(
      (part) => part.type === 'text' || occurrences.has(part.occurrenceId || part.attachmentId),
    )
    .map((part) => ({ ...part }));
}

/** Project live composer state into the persistable draft shape. */
export function toDraft(
  value: CodecComposerValue,
  attachments: CodecAttachment[],
): ApiSessionDraft {
  // parts order IS the composer's document order; keeping it verbatim is what
  // makes an interrupted draft restore with identical structure.
  const metadata = restorableAttachments({
    text: value.text,
    parts: [],
    attachments: attachments.map((attachment) => {
      const entry: ApiSessionDraftAttachment = {
        occurrenceId: occurrenceIdOf(attachment),
        displayName: attachment.displayName,
      };
      if (attachment.attachmentId) entry.attachmentId = attachment.attachmentId;
      if (attachment.path) entry.path = attachment.path;
      if (attachment.href) entry.href = attachment.href;
      if (attachment.mimeType) entry.mimeType = attachment.mimeType;
      if (attachment.fileKey) entry.fileKey = attachment.fileKey;
      if (attachment.source) entry.source = attachment.source;
      if (attachment.location) entry.location = { ...attachment.location };
      return entry;
    }),
  });
  return {
    text: value.text,
    parts: recoverableParts(value.parts, metadata),
    attachments: metadata,
  };
}

export interface RestoredDraft {
  value: CodecComposerValue;
  attachments: CodecAttachment[];
}

/**
 * Rebuild composer state from a loaded draft.
 *
 * Returns null for an absent or empty draft so the caller can treat "nothing
 * persisted" as "no restore needed" rather than clearing a composer.
 */
export function fromDraft(draft: ApiSessionDraft, fallbackText: string): RestoredDraft | null {
  const metadata = restorableAttachments(draft);
  const parts: CodecPart[] = draft.parts.length
    ? recoverableParts(draft.parts, metadata)
    : [{ type: 'text', value: fallbackText }];
  const attachments: CodecAttachment[] = metadata.map((item) => ({
    occurrenceId: item.occurrenceId,
    id: item.occurrenceId,
    displayName: item.displayName,
    // A path can survive a restart before registration finishes. Keep that
    // chip retryable via the existing explicit registration action, and block
    // send until it has both a registered identity and a usable link.
    status: item.attachmentId && item.href ? 'ready' : 'error',
    ...(item.attachmentId ? { attachmentId: item.attachmentId } : {}),
    ...(item.path ? { path: item.path } : {}),
    ...(item.href ? { href: item.href } : {}),
    ...(item.mimeType ? { mimeType: item.mimeType } : {}),
    ...(item.fileKey ? { fileKey: item.fileKey } : {}),
    ...(item.source ? { source: item.source } : {}),
    ...(item.location ? { location: item.location } : {}),
  }));
  if (!draft.text && draft.parts.length === 0 && attachments.length === 0) return null;
  const occurrenceIds = parts
    .map((part) => (part.type === 'attachment' ? part.occurrenceId || part.attachmentId : null))
    .filter((id): id is string => !!id);
  return {
    value: {
      parts,
      text: draft.text,
      occurrenceIds,
      attachmentIds: [...occurrenceIds],
    },
    attachments,
  };
}

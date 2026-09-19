/** Browser-native file intake helpers.  These deliberately return File bytes
 * only; client paths, fakepath values and file URI strings are never used. */

export type NativeAttachmentInput =
  { kind: 'files'; files: File[] } | { kind: 'directory' } | { kind: 'uri' } | { kind: 'none' };

export interface NativeAttachmentInputOptions {
  /** Clipboard text is not evidence of a file. */
  treatTextOnlyAsPlainText?: boolean;
}

export const PAN_ATTACHMENT_MIME = 'application/x-pan-attachment';

export function hasPanAttachmentMime(dataTransfer: DataTransfer | null): boolean {
  return Array.from(dataTransfer?.types || []).includes(PAN_ATTACHMENT_MIME);
}

function looksLikeFileUri(value: string): boolean {
  return /(?:^|\r?\n)\s*file:\/\//i.test(value);
}

function looksLikePathOrWebUri(value: string): boolean {
  return /(?:^|\r?\n)\s*(?:https?:\/\/|file:\/\/|[a-z]:[\\/]|[\\/]?[a-z]:[\\/]|\\\\|\/\/)/i.test(
    value,
  );
}

export function inspectNativeAttachmentInput(
  dataTransfer: DataTransfer | null,
  options: NativeAttachmentInputOptions = {},
): NativeAttachmentInput {
  if (!dataTransfer) return { kind: 'none' };
  let directory = false;
  for (const item of Array.from(dataTransfer.items || [])) {
    if (item.kind !== 'file') continue;
    const candidate = item as DataTransferItem & {
      webkitGetAsEntry?: () => { isDirectory?: boolean } | null;
      getAsFileSystemHandle?: () => Promise<{ kind?: string }>;
    };
    const entry = candidate.webkitGetAsEntry?.();
    if (entry?.isDirectory) directory = true;
    // Chromium exposes a directory as a file item with no File payload.  Do
    // not recursively enumerate it in phase 1; surface a clear rejection.
    if (!item.getAsFile?.() && !entry) directory = true;
  }
  const files = Array.from(dataTransfer.files || []) as File[];
  // A directory mixed with files is still one invalid batch.  Returning the
  // files first would silently upload the valid-looking subset and violate
  // the all-or-nothing directory rule.
  if (directory) return { kind: 'directory' };
  if (files.length > 0) return { kind: 'files', files };
  if (options.treatTextOnlyAsPlainText) return { kind: 'none' };
  const uriList = dataTransfer.getData?.('text/uri-list') || '';
  const plainText = dataTransfer.getData?.('text/plain') || '';
  if (
    looksLikeFileUri(uriList) ||
    looksLikePathOrWebUri(uriList) ||
    looksLikePathOrWebUri(plainText)
  ) {
    return { kind: 'uri' };
  }
  return { kind: 'none' };
}

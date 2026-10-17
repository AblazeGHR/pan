import type { QueuedEditAttachment } from '@/types';
import type { ComposerPart, ComposerValue } from '@/components/chat/RichTextComposer';
import { composerUrlMarkdown, newComposerUrlId, parseComposerText } from '@/utils/composerUrl';
import { serverAttachmentDownloadHref } from '@/utils/attachmentMarkdown';

/** Decode the full lease body, never the queue row's truncated preview. */
export function queueComposerFromBody(text: string, format?: string, sessionId?: string) {
  const attachments: QueuedEditAttachment[] = [];
  const parts: ComposerPart[] = [];
  const source: Array<Record<string, unknown>> = format === 'parts'
    ? JSON.parse(text) : [{ type: 'text', text }];
  for (const [index, part] of source.entries()) {
    if (part.type === 'attachment' && typeof part.attachmentId === 'string') {
      const occurrenceId = typeof part.occurrenceId === 'string' ? part.occurrenceId : `queue-occurrence-${index}`;
      attachments.push({ ...part, occurrenceId, id: occurrenceId,
        attachmentId: part.attachmentId,
        ...(sessionId ? { href: serverAttachmentDownloadHref(sessionId, part.attachmentId) } : {}),
        displayName: typeof part.displayName === 'string' ? part.displayName : part.attachmentId,
        status: 'ready' } as QueuedEditAttachment);
      parts.push({ type: 'attachment', attachmentId: occurrenceId, occurrenceId });
    } else if (part.type === 'text') {
      const url = part.editorUrl as { id?: string; url?: string; label?: string } | undefined;
      if (url && typeof url.id === 'string' && typeof url.url === 'string') {
        parts.push({ type: 'url', id: url.id, url: url.url, ...(url.label ? { label: url.label } : {}) });
        continue;
      }
      for (const parsed of parseComposerText(String(part.text ?? part.value ?? ''))) {
        parts.push(parsed.type === 'url' ? { ...parsed, id: newComposerUrlId() } : parsed);
      }
    }
  }
  const ids = attachments.map((attachment) => attachment.occurrenceId);
  return { attachments, value: { parts, text: parts.flatMap((part) => part.type === 'text' ? [part.value] : []).join(''),
    occurrenceIds: ids, attachmentIds: [...ids] } satisfies ComposerValue };
}

export function queueComposerToParts(value: ComposerValue, attachments: QueuedEditAttachment[]) {
  const byOccurrence = new Map(attachments.map((attachment) => [attachment.occurrenceId, attachment]));
  const result = value.parts.map((part) => {
    if (part.type !== 'attachment') return { type: 'text' as const,
      text: part.type === 'url' ? composerUrlMarkdown(part.url, part.label) : part.value,
      ...(part.type === 'url' ? { editorUrl: { id: part.id, url: part.url, ...(part.label ? { label: part.label } : {}) } } : {}) };
    const occurrenceId = part.occurrenceId || part.attachmentId;
    const attachment = byOccurrence.get(occurrenceId);
    if (!attachment?.attachmentId || attachment.status !== 'ready') throw new Error('附件尚未就绪');
    return { type: 'attachment' as const, attachmentId: attachment.attachmentId, occurrenceId };
  });
  // Picker additions may not yet have an inline node. Keep them at the end.
  const used = new Set(value.occurrenceIds);
  for (const attachment of attachments) {
    if (!used.has(attachment.occurrenceId)) {
      if (!attachment.attachmentId || attachment.status !== 'ready') throw new Error('附件尚未就绪');
      result.push({ type: 'attachment', attachmentId: attachment.attachmentId, occurrenceId: attachment.occurrenceId });
    }
  }
  return result;
}

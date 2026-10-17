// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { queueComposerFromBody, queueComposerToParts } from './queueComposerCodec';

describe('queue composer inverse codec', () => {
  it('restores repeated resources as distinct movable occurrences around text and URLs', () => {
    const source = [
      { type: 'text', text: 'before ' },
      { type: 'attachment', attachmentId: 'same', displayName: 'report.txt', size: 42, source: 'upload' },
      { type: 'text', text: '[Docs](https://example.test/a)' },
      { type: 'attachment', attachmentId: 'same', displayName: 'report.txt', size: 42, source: 'upload' },
    ];
    const decoded = queueComposerFromBody(JSON.stringify(source), 'parts');
    expect(decoded.value.parts.map(part => part.type)).toEqual(['text', 'attachment', 'url', 'attachment']);
    expect(new Set(decoded.value.occurrenceIds).size).toBe(2);
    const parts = [...decoded.value.parts].reverse();
    const saved = queueComposerToParts({ ...decoded.value, parts }, decoded.attachments);
    expect(saved.filter(part => part.type === 'attachment').map(part => part.occurrenceId))
      .toEqual([...decoded.value.occurrenceIds].reverse());
    const restored = queueComposerFromBody(JSON.stringify(saved), 'parts');
    expect(restored.value.parts).toEqual(parts);
    expect(source[1]).toEqual({ type: 'attachment', attachmentId: 'same', displayName: 'report.txt', size: 42, source: 'upload' });
  });

  it('removes one occurrence without removing the shared resource or another occurrence', () => {
    const decoded = queueComposerFromBody(JSON.stringify([
      { type: 'attachment', attachmentId: 'same', displayName: 'same.txt' },
      { type: 'attachment', attachmentId: 'same', displayName: 'same.txt' },
    ]), 'parts');
    const remaining = decoded.attachments.slice(1);
    const value = { ...decoded.value, parts: decoded.value.parts.slice(1), occurrenceIds: remaining.map(a => a.id) };
    expect(queueComposerToParts(value, remaining)).toEqual([
      { type: 'attachment', attachmentId: 'same', occurrenceId: remaining[0]!.id },
    ]);
  });

  it('keeps an existing URL identity, title and bytes when reopened', () => {
    const url = { id: 'url-existing', url: 'https://example.test/a?x=1#h', label: 'Docs' };
    const decoded = queueComposerFromBody(JSON.stringify([{ type: 'text', text: '[Docs](https://example.test/a?x=1#h)', editorUrl: url }]), 'parts');
    expect(decoded.value.parts).toEqual([{ type: 'url', ...url }]);
    expect(queueComposerToParts(decoded.value, [])[0]).toEqual({ type: 'text', text: '[Docs](https://example.test/a?x=1#h)', editorUrl: url });
  });
});

// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { fromDraft, toDraft, type CodecAttachment } from './composerDraftCodec';
import type { ApiSessionDraft } from '@/services/api';

const urlPart = {
  type: 'url' as const,
  id: 'url_1',
  url: 'https://example.test/a?q=1#h',
  label: 'Docs',
};

describe('composer draft URL parts', () => {
  it('persists and restores a URL node without attachment metadata', () => {
    const draft = toDraft(
      {
        parts: [
          { type: 'text', value: 'see ' },
          urlPart,
          { type: 'text', value: ' now' },
        ],
        text: 'see  now',
        occurrenceIds: [],
        attachmentIds: [],
      },
      [],
    );

    expect(draft).toEqual({
      text: 'see  now',
      parts: [{ type: 'text', value: 'see ' }, urlPart, { type: 'text', value: ' now' }],
      attachments: [],
    });

    const restored = fromDraft(draft, '');
    expect(restored?.value.parts).toEqual(draft.parts);
    expect(restored?.value.text).toBe('see  now');
    expect(restored?.value.occurrenceIds).toEqual([]);
    expect(restored?.attachments).toEqual([]);
  });

  it('keeps URL nodes in order with a restorable attachment occurrence', () => {
    const attachment: CodecAttachment = {
      occurrenceId: 'occ_1',
      id: 'occ_1',
      displayName: 'note.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
      attachmentId: 'att_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      status: 'ready',
    };
    const draft = toDraft(
      {
        parts: [
          urlPart,
          { type: 'attachment', attachmentId: 'occ_1', occurrenceId: 'occ_1' },
          { type: 'text', value: 'after' },
        ],
        text: 'after',
        occurrenceIds: ['occ_1'],
        attachmentIds: ['occ_1'],
      },
      [attachment],
    );

    expect(draft.parts.map((part) => part.type)).toEqual(['url', 'attachment', 'text']);
    expect(fromDraft(draft, '')?.value.parts).toEqual(draft.parts);
  });

  it('treats a URL-only draft as non-empty', () => {
    const draft: ApiSessionDraft = { text: '', parts: [urlPart], attachments: [] };
    expect(fromDraft(draft, '')).not.toBeNull();
  });
});

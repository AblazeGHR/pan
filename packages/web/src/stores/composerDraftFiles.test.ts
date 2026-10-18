// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
const files = vi.hoisted(() => new Map<string, File>());
vi.mock('./draftFiles', () => ({
  draftFile: (key: string) => files.get(key),
  rememberDraftFile: (key: string, file: File) => files.set(key, file),
}));
import { toDraft, fromDraft } from './composerDraftCodec';

describe('pending draft files', () => {
  it('persists a File separately and restores the original bytes without serializing an empty object', async () => {
    const file = new File(['actual bytes'], 'local.txt', { type: 'text/plain', lastModified: 42 });
    const value = { parts: [{ type: 'attachment' as const, attachmentId: 'occ', occurrenceId: 'occ' }], text: '', occurrenceIds: ['occ'], attachmentIds: ['occ'] };
    const draft = toDraft(value, [{ id: 'occ', occurrenceId: 'occ', file, displayName: file.name, status: 'error' }], 'session');
    expect(draft.attachments[0]?.localFileId).toBe('session:occ');
    expect(JSON.stringify(draft)).not.toContain('"file":');
    const restored = fromDraft(draft, '')!;
    expect(restored.attachments[0]?.file).toBe(file);
    expect(restored.attachments[0]?.file?.size).toBe(12);
    expect(restored.attachments[0]?.status).toBe('error');
    expect(restored.value.parts).toEqual(value.parts);
  });

  it('retains an unavailable local file as an explicit error rather than silently deleting its node', () => {
    const draft = { text: '', parts: [{ type: 'attachment' as const, attachmentId: 'occ', occurrenceId: 'occ' }],
      attachments: [{ occurrenceId: 'occ', displayName: 'missing.txt', localFileId: 'session:missing' }] };
    const restored = fromDraft(draft, '')!;
    expect(restored.attachments[0]?.file).toBeUndefined();
    expect(restored.attachments[0]?.error).toContain('重新选择');
    expect(toDraft(restored.value, restored.attachments, 'session')).toEqual(draft);
  });
});

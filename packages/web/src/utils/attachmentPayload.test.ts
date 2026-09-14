// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { ATTACHMENT_DRAG_MIME } from './attachmentDrag';
import {
  isAttachmentPayloadForSession,
  readPanAttachmentPayload,
  type PanAttachmentPayload,
} from './attachmentPayload';

function transfer(payload: PanAttachmentPayload): DataTransfer {
  return {
    getData: (type: string) => (type === ATTACHMENT_DRAG_MIME ? JSON.stringify(payload) : ''),
  } as unknown as DataTransfer;
}

describe('extended attachment transfer contract', () => {
  it('retains source session and line range while reading custom MIME', () => {
    const payload: PanAttachmentPayload = {
      displayName: 'guide.md',
      href: '/api/fs/read?session_id=s1&path=docs%2Fguide.md&download=1',
      source: 'editor',
      sourceSessionId: 's1',
      location: { line: 42, endLine: 48 },
    };

    expect(readPanAttachmentPayload(transfer(payload))).toEqual(payload);
  });

  it('binds only chip and composer transfers to their source Session', () => {
    const chip: PanAttachmentPayload = {
      displayName: 'chip.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
      source: 'attachment-chip',
      sourceSessionId: 's1',
    };
    const composer: PanAttachmentPayload = { ...chip, source: 'composer' };
    const message: PanAttachmentPayload = { ...chip, source: 'message' };
    const editor: PanAttachmentPayload = { ...chip, source: 'editor' };

    expect(isAttachmentPayloadForSession(chip, 's1')).toBe(true);
    expect(isAttachmentPayloadForSession(composer, 's2')).toBe(false);
    expect(isAttachmentPayloadForSession(message, 's2')).toBe(true);
    expect(isAttachmentPayloadForSession(editor, 's2')).toBe(true);
  });
});

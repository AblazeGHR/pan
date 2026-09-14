// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import {
  ATTACHMENT_DRAG_MIME,
  readAttachmentDragPayload,
  writeAttachmentDragPayload,
} from './attachmentDrag';

function transfer() {
  const values = new Map<string, string>();
  return {
    setData: vi.fn((type: string, value: string) => values.set(type, value)),
    getData: (type: string) => values.get(type) || '',
    values,
    effectAllowed: 'none',
  } as unknown as DataTransfer & { values: Map<string, string> };
}

describe('attachment drag protocol', () => {
  it('projects server paths to an opaque download href and carries range metadata', () => {
    const dataTransfer = transfer();
    writeAttachmentDragPayload(dataTransfer, {
      displayName: 'readme.md',
      href: '/api/fs/read?session_id=source&path=D%3A%5Cprivate%5Creadme.md&download=1',
      path: 'D:\\private\\readme.md',
      serverAttachmentId: `att_${'a'.repeat(32)}`,
      sourceSessionId: 'source',
      location: { line: 4, endLine: 8 },
      source: 'message',
    });
    const raw = dataTransfer.values.get(ATTACHMENT_DRAG_MIME) || '';
    expect(raw).not.toContain('D:');
    expect(raw).not.toContain('path');
    expect(raw).toContain('/api/attachments/ref/');
    expect(readAttachmentDragPayload(dataTransfer)).toMatchObject({
      displayName: 'readme.md',
      serverAttachmentId: `att_${'a'.repeat(32)}`,
      sourceSessionId: 'source',
      location: { line: 4, endLine: 8 },
    });
  });

  it('rejects legacy payloads that smuggle path material', () => {
    const dataTransfer = transfer();
    dataTransfer.values.set(ATTACHMENT_DRAG_MIME, JSON.stringify({
      displayName: 'secret.txt',
      href: '/api/attachments/ref/att_' + 'b'.repeat(32) + '?session_id=s1',
      path: 'C:\\secret.txt',
    }));
    expect(readAttachmentDragPayload(dataTransfer)).toBeNull();
  });
});

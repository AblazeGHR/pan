// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  attachmentMarkdown,
  isSafeAttachmentHref,
  normalizeLegacyAttachmentLinks,
  serverFileDownloadHref,
} from './attachmentMarkdown';

describe('attachment Markdown protocol', () => {
  it('keeps the original display name while targeting the random storage name', () => {
    const href = '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1';
    expect(
      attachmentMarkdown({
        displayName: '需求说明 [v1](最终).md',
        href,
      }),
    ).toBe(
      '[需求说明 \\[v1\\]\\(最终\\).md](/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1)',
    );
  });

  it('keeps a line range in the text fallback link', () => {
    const href = '/api/fs/read?session_id=s1&path=docs%2Fguide.md&download=1';
    expect(
      attachmentMarkdown({
        displayName: 'guide.md',
        href,
        location: { line: 42, endLine: 48 },
      }),
    ).toBe('[guide.md](/api/fs/read?session_id=s1&path=docs%2Fguide.md&download=1#L42-L48)');
  });

  it('URL-encodes path characters and rejects dangerous destinations', () => {
    const href = serverFileDownloadHref('s1', 'D:\\files\\a [b](c)#.md');
    expect(href).toBe(
      '/api/fs/read?session_id=s1&path=D%3A%5Cfiles%5Ca+%5Bb%5D%28c%29%23.md&download=1',
    );
    expect(isSafeAttachmentHref(href)).toBe(true);
    expect(
      isSafeAttachmentHref('https://evil.example/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md'),
    ).toBe(false);
    expect(isSafeAttachmentHref('/api/attachments/../etc/passwd?session_id=s1')).toBe(false);
    expect(
      isSafeAttachmentHref('/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md'),
    ).toBe(false);
  });

  it('converts only the legacy attachment marker and leaves ordinary Markdown links alone', () => {
    const content = 'old @"D:\\old\\stored_name.md" [normal](https://example.test/a_(b))';
    expect(normalizeLegacyAttachmentLinks(content, 's1')).toBe(
      'old [stored_name.md](/api/fs/read?session_id=s1&path=D%3A%5Cold%5Cstored_name.md&download=1) [normal](https://example.test/a_(b))',
    );
  });
});

// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import {
  COMPOSER_URL_MIME,
  composerUrlMarkdown,
  escapeMarkdownUrlDestination,
  normalizeComposerUrl,
  parseComposerHtml,
  parseComposerText,
  readComposerUrlTransfer,
  writeComposerUrlTransfer,
} from './composerUrl';

function clipboard(data: Record<string, string> = {}): DataTransfer {
  const entries = new Map(Object.entries(data));
  return {
    getData: (type: string) => entries.get(type) || '',
    setData: (type: string, value: string) => {
      entries.set(type, value);
    },
  } as unknown as DataTransfer;
}

describe('composer URL detection', () => {
  it('rejects every embedded ASCII control, space and DEL while preserving Unicode paths', () => {
    for (const code of [...Array.from({ length: 33 }, (_, index) => index), 127]) {
      expect(normalizeComposerUrl(`https://example.test/a${String.fromCharCode(code)}b`)).toBeNull();
    }
    expect(normalizeComposerUrl('https://example.test/中文/😀?x=é')).toBe('https://example.test/中文/😀?x=é');
  });
  it('keeps the pasted address bytes, including query and hash', () => {
    const url = 'https://Example.test/a%20b?q=1&next=%2Fx#L42-L48';
    expect(normalizeComposerUrl(url)).toBe(url);
    expect(normalizeComposerUrl(`  ${url}  `)).toBe(url);
  });

  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'data:text/html,<b>x</b>',
    'file:///C:/secret.txt',
    'ftp://example.test/a',
    '/relative/path',
    'https://exa mple.test/a',
  ])('refuses to turn %s into a link node', (value) => {
    expect(normalizeComposerUrl(value)).toBeNull();
  });

  it('splits multiple URLs and mixed text without reordering', () => {
    expect(
      parseComposerText('a https://one.test/x?q=1 b https://two.test/y#top c'),
    ).toEqual([
      { type: 'text', value: 'a ' },
      { type: 'url', url: 'https://one.test/x?q=1' },
      { type: 'text', value: ' b ' },
      { type: 'url', url: 'https://two.test/y#top' },
      { type: 'text', value: ' c' },
    ]);
  });

  it('keeps trailing sentence punctuation outside the address', () => {
    expect(parseComposerText('see https://example.test/a?q=1, ok')).toEqual([
      { type: 'text', value: 'see ' },
      { type: 'url', url: 'https://example.test/a?q=1' },
      { type: 'text', value: ', ok' },
    ]);
  });

  it('recognizes a copied Markdown link as a labeled node (plain-text round trip)', () => {
    expect(parseComposerText('[Docs](https://example.test/a?q=1#h)')).toEqual([
      { type: 'url', url: 'https://example.test/a?q=1#h', label: 'Docs' },
    ]);
  });

  it('round-trips escaped label and destination through composerUrlMarkdown', () => {
    const url = 'https://example.test/a(b)?q=1#h';
    const markdown = composerUrlMarkdown(url, 'a [label] (x)');
    expect(parseComposerText(markdown)).toEqual([
      { type: 'url', url, label: 'a [label] (x)' },
    ]);
  });

  it('keeps an unsafe Markdown link as text and parses the surrounding text', () => {
    expect(parseComposerText('see [bad](javascript:alert(1)) now')).toEqual([
      { type: 'text', value: 'see [bad](javascript:alert(1)) now' },
    ]);
  });

  it('keeps multiple copied Markdown links in order with mixed text', () => {
    expect(
      parseComposerText('a [One](https://one.test/a) b [Two](https://two.test/b) c'),
    ).toEqual([
      { type: 'text', value: 'a ' },
      { type: 'url', url: 'https://one.test/a', label: 'One' },
      { type: 'text', value: ' b ' },
      { type: 'url', url: 'https://two.test/b', label: 'Two' },
      { type: 'text', value: ' c' },
    ]);
  });

  it('keeps balanced parentheses but drops a prose closing paren', () => {
    expect(parseComposerText('(https://example.test/a)')).toEqual([
      { type: 'text', value: '(' },
      { type: 'url', url: 'https://example.test/a' },
      { type: 'text', value: ')' },
    ]);
    expect(
      parseComposerText('https://en.wikipedia.test/wiki/Pan_(software)').at(0),
    ).toEqual({ type: 'url', url: 'https://en.wikipedia.test/wiki/Pan_(software)' });
  });

  it('leaves non-URL text as one text part', () => {
    expect(parseComposerText('plain text')).toEqual([{ type: 'text', value: 'plain text' }]);
  });
});

describe('composer HTML links', () => {
  it('preserves href and visible title in document order', () => {
    expect(
      parseComposerHtml(
        '<p>Docs: <a href="https://example.test/docs?a=1&amp;b=2#part">Example <b>Docs</b></a> now</p>',
      ),
    ).toEqual([
      { type: 'text', value: 'Docs: ' },
      {
        type: 'url',
        url: 'https://example.test/docs?a=1&b=2#part',
        label: 'Example Docs',
      },
      { type: 'text', value: ' now' },
    ]);
  });

  it('keeps a line break that belongs to a block boundary before a link', () => {
    expect(parseComposerHtml('<div>line</div><div><a href="https://x.test/a">X</a></div>')).toEqual([
      { type: 'text', value: 'line\n\n' },
      { type: 'url', url: 'https://x.test/a', label: 'X' },
    ]);
  });

  it('keeps an unsafe anchor as text when a safe anchor is also present', () => {
    expect(
      parseComposerHtml(
        '<span>safe <a href="https://ok.test/a">OK</a></span>'
          + '<span> bad <a href="javascript:alert(1)">Bad</a></span>',
      ),
    ).toEqual([
      { type: 'text', value: 'safe ' },
      { type: 'url', url: 'https://ok.test/a', label: 'OK' },
      { type: 'text', value: ' bad Bad' },
    ]);
  });

  it('never turns a dangerous anchor into a URL part', () => {
    // null lets the composer keep its existing sanitize-to-text fallback.
    expect(
      parseComposerHtml(
        '<div>safe <a href="javascript:alert(1)">label</a></div><script>x()</script><span hidden>hidden</span>',
      ),
    ).toBeNull();
  });

  it('returns null when the fragment has no safe link', () => {
    expect(parseComposerHtml('<div>plain</div>')).toBeNull();
    expect(parseComposerHtml('<a href="/relative">relative</a>')).toBeNull();
  });
});

describe('composer URL serialization', () => {
  it('escapes label brackets and destination parentheses without touching query/hash', () => {
    const url = 'https://example.test/a(b)?q=(1)#frag(2)';
    expect(escapeMarkdownUrlDestination(url)).toBe(
      'https://example.test/a\\(b\\)?q=\\(1\\)#frag\\(2\\)',
    );
    expect(composerUrlMarkdown(url, 'a [label] (x)')).toBe(
      '[a \\[label\\] \\(x\\)](https://example.test/a\\(b\\)?q=\\(1\\)#frag\\(2\\))',
    );
  });

  it('uses the URL as its own label when HTML supplied no title', () => {
    expect(composerUrlMarkdown('https://example.test/a?q=1#h')).toBe(
      '[https://example.test/a?q=1#h](https://example.test/a?q=1#h)',
    );
  });

  it('round-trips a copied node through the Pan transfer MIME', () => {
    const data = clipboard();
    writeComposerUrlTransfer(data, {
      items: [{ url: 'https://example.test/a?q=1#h', label: 'Docs' }, { url: 'https://two.test' }],
    });
    expect(readComposerUrlTransfer(data)).toEqual({
      items: [{ url: 'https://example.test/a?q=1#h', label: 'Docs' }, { url: 'https://two.test' }],
    });
    expect(data.getData(COMPOSER_URL_MIME)).toContain('https://example.test/a?q=1#h');
  });

  it('refuses to read an unsafe transfer payload', () => {
    const data = clipboard({
      [COMPOSER_URL_MIME]: JSON.stringify({ items: [{ url: 'javascript:alert(1)' }] }),
    });
    expect(readComposerUrlTransfer(data)).toBeNull();
  });

  it('does not write a transfer when every item is unsafe', () => {
    const data = clipboard();
    const setData = vi.spyOn(data, 'setData');
    writeComposerUrlTransfer(data, { items: [{ url: 'javascript:alert(1)' }] });
    expect(setData).not.toHaveBeenCalled();
  });
});

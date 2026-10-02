// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { groupMessages } from './MessageBubble';
import { searchTextPlugin } from './searchText';
import type { Message } from '@/types';

describe('scoped search rendering', () => {
  it('splits tool/thinking targets out of folded groups without changing normal grouping', () => {
    const rows: Message[] = [
      { role: 'tool', content: 'before', messageId: 'a' },
      { role: 'thinking', content: 'target', messageId: 'b' },
      { role: 'tool', content: 'after', messageId: 'c' },
    ];
    expect(groupMessages(rows, true)).toHaveLength(1);
    const searched = groupMessages(rows, true, new Set(), 'b');
    expect(searched).toHaveLength(3);
    expect(searched[1]).toBe(rows[1]);
    expect(groupMessages(rows, true)).toHaveLength(1);
  });

  it('creates safe owned mark nodes, preserving text, links and literal markup', () => {
    const tree = { type: 'root', children: [{ type: 'element', tagName: 'a',
      properties: { href: 'https://example.com' }, children: [{ type: 'text', value: 'Needle <script> needle' }] }] };
    searchTextPlugin('needle')()(tree);
    expect(tree.children[0]!.properties.href).toBe('https://example.com');
    const children = tree.children[0]!.children as Array<{ type: string; value?: string; tagName?: string; children?: Array<{value: string}> }>;
    expect(children.filter((node) => node.tagName === 'mark')).toHaveLength(2);
    expect(children.map((node) => node.value ?? node.children?.[0]?.value).join('')).toBe('Needle <script> needle');
  });

  it('bounds dense markup while retaining the selected occurrence beyond the budget', () => {
    const tree = { type: 'root', children: [{ type: 'text', value: 'needle '.repeat(1000) }] };
    searchTextPlugin('needle', 750)()(tree);
    const marks = tree.children as Array<{ tagName?: string; properties?: {className: string[]} }>;
    expect(marks.filter((node) => node.tagName === 'mark')).toHaveLength(501);
    expect(marks.filter((node) => node.properties?.className.includes('history-search-word-active'))).toHaveLength(1);
  });
});

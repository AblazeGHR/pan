import { describe, expect, it } from 'vitest';
import { applyViewEntryOrder, paginateViewEntries, reorderViewEntryIds, type ViewEntry } from './viewEntries';
import { MessageSquare } from 'lucide-react';

const all: ViewEntry[] = [
  { id: 'builtin:chat', to: '/', label: 'Chat', icon: MessageSquare },
  { id: 'builtin:jobs', to: '/jobs', label: 'Jobs', icon: MessageSquare },
  { id: 'plugin:alpha/panel', to: '/plugins/alpha/panel', label: 'Plugin', icon: MessageSquare },
];

describe('applyViewEntryOrder', () => {
  it('keeps saved order, drops unknown ids, removes duplicates, appends new/plugin ids', () => {
    expect(applyViewEntryOrder(all, ['unknown', 'builtin:jobs', 'builtin:jobs', 'plugin:alpha/panel']).map((entry) => entry.id))
      .toEqual(['builtin:jobs', 'plugin:alpha/panel', 'builtin:chat']);
  });
  it('falls back to default catalog order for an invalid value', () => {
    expect(applyViewEntryOrder(all, null)).toEqual(all);
  });
  it('supports insertion before/after and repaginates in groups of three', () => {
    const moved = reorderViewEntryIds(['a', 'b', 'c', 'd', 'e'], 'e', 'b', 'before');
    expect(moved).toEqual(['a', 'e', 'b', 'c', 'd']);
    expect(paginateViewEntries(moved)).toEqual([['a', 'e', 'b'], ['c', 'd']]);
  });
});

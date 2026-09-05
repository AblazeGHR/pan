import { describe, expect, it } from 'vitest';
import { collectDescendantIds, hasManagedChildren } from './sessionDeletePlan';
import type { Session } from '@/types';

const mk = (id: string, managed?: string[]): Session => ({
  id, name: id, history: [], alwaysThinkingEnabled: false, effort: '', managed,
});

describe('session delete plan', () => {
  it('uses managed-list presence, including stale entries, as the special signal', () => {
    expect(hasManagedChildren(mk('empty', []))).toBe(false);
    expect(hasManagedChildren(mk('stale', ['missing']))).toBe(true);
  });

  it('deduplicates shared descendants and survives cycles/missing targets', () => {
    const sessions = [
      mk('a', ['b', 'missing']), mk('b', ['a', 'c']), mk('c'), mk('other'),
    ];
    expect(collectDescendantIds(sessions, ['a'])).toEqual(['c', 'b']);
    expect(collectDescendantIds([mk('root', ['x']), mk('x')], ['root'])).toEqual(['x']);
  });
});

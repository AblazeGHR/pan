import { afterEach, expect, it } from 'vitest';
import { getSessionListCandidates } from './sessionFilters';
import { useHistorySearchViewStore } from '@/stores/historySearchViewStore';
import type { Session } from '@/types';

afterEach(() => useHistorySearchViewStore.getState().update(null));
it('applies a temporary complete hit scope alongside existing filters and restores without persisting settings', () => {
  const sessions = [{ id: 'a', name: 'Alpha' }, { id: 'b', name: 'Beta' }, { id: 'c', name: 'Gamma' }] as Session[];
  const options = { multiSelectMode: false, hiddenSessionIds: new Set<string>(), searchQuery: '', specialFilters: new Set<never>() };
  useHistorySearchViewStore.getState().update(['b', 'c'], true);
  const filtered = () => getSessionListCandidates(sessions, { ...options, matchingSessionIds: useHistorySearchViewStore.getState().matchingSessionIds });
  expect(filtered().map((session) => session.id)).toEqual(['b', 'c']);
  options.hiddenSessionIds.add('c');
  expect(filtered().map((session) => session.id)).toEqual(['b']);
  useHistorySearchViewStore.getState().update([], true);
  expect(filtered()).toEqual([]);
  useHistorySearchViewStore.getState().update(null);
  expect(filtered().map((session) => session.id)).toEqual(['a', 'b']);
  expect(options.searchQuery).toBe('');
});

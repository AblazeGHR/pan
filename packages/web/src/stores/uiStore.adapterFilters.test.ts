// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { useUIStore } from './uiStore';

/** Snapshot of every localStorage key the UI store persists. */
function persistedSnapshot(): Record<string, string | null> {
  const snapshot: Record<string, string | null> = {};
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key) snapshot[key] = localStorage.getItem(key);
  }
  return snapshot;
}

function resetStore() {
  useUIStore.setState({
    specialFilters: new Set(),
    adapterFilters: new Set(),
  });
}

describe('uiStore adapter filters', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });

  it('starts with both filter groups empty', () => {
    expect(useUIStore.getState().adapterFilters.size).toBe(0);
    expect(useUIStore.getState().specialFilters.size).toBe(0);
  });

  it('toggles an adapter on and off', () => {
    useUIStore.getState().toggleAdapterFilter('codex');
    expect([...useUIStore.getState().adapterFilters]).toEqual(['codex']);
    useUIStore.getState().toggleAdapterFilter('kimi');
    expect([...useUIStore.getState().adapterFilters]).toEqual(['codex', 'kimi']);
    useUIStore.getState().toggleAdapterFilter('codex');
    expect([...useUIStore.getState().adapterFilters]).toEqual(['kimi']);
  });

  it('keeps the adapter group independent from the state group', () => {
    useUIStore.getState().toggleSpecialFilter('running');
    useUIStore.getState().toggleAdapterFilter('codex');
    expect([...useUIStore.getState().specialFilters]).toEqual(['running']);
    expect([...useUIStore.getState().adapterFilters]).toEqual(['codex']);

    useUIStore.getState().clearAdapterFilters();
    expect([...useUIStore.getState().specialFilters]).toEqual(['running']);
    expect(useUIStore.getState().adapterFilters.size).toBe(0);
  });

  it('toggles the idle state filter on and off like running', () => {
    useUIStore.getState().toggleSpecialFilter('idle');
    expect([...useUIStore.getState().specialFilters]).toEqual(['idle']);
    useUIStore.getState().toggleSpecialFilter('idle');
    expect(useUIStore.getState().specialFilters.size).toBe(0);
  });

  it('toggles the held and offline state filters on and off', () => {
    useUIStore.getState().toggleSpecialFilter('held');
    useUIStore.getState().toggleSpecialFilter('offline');
    expect([...useUIStore.getState().specialFilters]).toEqual(['held', 'offline']);
    useUIStore.getState().toggleSpecialFilter('held');
    expect([...useUIStore.getState().specialFilters]).toEqual(['offline']);
    useUIStore.getState().toggleSpecialFilter('offline');
    expect(useUIStore.getState().specialFilters.size).toBe(0);
  });

  it('clearAllFilters clears both groups (the menu Clear filters action)', () => {
    useUIStore.getState().toggleSpecialFilter('running');
    useUIStore.getState().toggleSpecialFilter('unread');
    useUIStore.getState().toggleAdapterFilter('codex');
    useUIStore.getState().clearAllFilters();
    expect(useUIStore.getState().specialFilters.size).toBe(0);
    expect(useUIStore.getState().adapterFilters.size).toBe(0);
  });

  it('accepts a stale/unknown adapter id without validation errors', () => {
    // Adapter ids are free-form strings from the live list; a persisted or
    // vanished adapter must never crash the store — it simply matches nothing.
    useUIStore.getState().toggleAdapterFilter('adapter-that-no-longer-exists');
    expect([...useUIStore.getState().adapterFilters]).toEqual(['adapter-that-no-longer-exists']);
    useUIStore.getState().toggleAdapterFilter('adapter-that-no-longer-exists');
    expect(useUIStore.getState().adapterFilters.size).toBe(0);
  });

  it('does not persist filter selections (the existing non-persistence contract)', () => {
    const before = persistedSnapshot();
    useUIStore.getState().toggleSpecialFilter('running');
    useUIStore.getState().toggleAdapterFilter('codex');
    useUIStore.getState().clearAllFilters();
    // Filters are session-scoped like searchQuery: nothing is written, so a
    // reload starts unfiltered exactly as before this feature.
    expect(persistedSnapshot()).toEqual(before);
  });

  it('leaves the persisted collapse state untouched (no key collision)', () => {
    useUIStore.setState({ collapsedGroups: new Set(['session-1']) });
    localStorage.setItem('pan:collapsedGroups', JSON.stringify(['session-1']));
    const before = persistedSnapshot();
    useUIStore.getState().toggleAdapterFilter('codex');
    useUIStore.getState().toggleSpecialFilter('unread');
    useUIStore.getState().clearAllFilters();
    expect(persistedSnapshot()).toEqual(before);
    expect(useUIStore.getState().collapsedGroups.has('session-1')).toBe(true);
  });
});

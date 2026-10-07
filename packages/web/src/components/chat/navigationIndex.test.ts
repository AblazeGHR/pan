import { describe, it, expect } from 'vitest';
import { NavigationIndex, NAVIGATION_CACHE_PAGES, NAVIGATION_PAGE_SIZE, nearestNavigationTarget, type NavigationTarget } from './navigationIndex';
const settings = { showMetaAgent: true, showTaskAgent: true, showQQ: true };
const target = (offset: number): NavigationTarget => ({ offset, fromEnd: 999 - offset, kind: 'user', preview: String(offset) });
describe('canonical navigation distance and coverage', () => {
  it('prefers inside, then center, then newer; outside uses interval distance first', () => {
    expect(nearestNavigationTarget([target(1), target(12), target(18), target(99)], { start: 10, end: 20 })?.offset).toBe(18);
    expect(nearestNavigationTarget([target(8), target(22)], { start: 10, end: 20 })?.offset).toBe(22);
    expect(nearestNavigationTarget([target(8), target(30)], { start: 10, end: 20 })?.offset).toBe(8);
    expect(nearestNavigationTarget([], { start: 10, end: 20 })).toBeUndefined();
  });
  it('never proves absence through an unloaded gap, including filtered rows', () => {
    const index = new NavigationIndex(1000, settings);
    index.addRows([{ offset: 496, message: { role: 'user', content: 'local target' } }]);
    expect(index.proven({ start: 498, end: 502 })).toBe(false);
    index.addPage(Array.from({ length: 200 }, (_, i) => ({ role: i === 100 ? 'user' : 'assistant', content: 'row' })), 400);
    expect(index.proven({ start: 498, end: 502 })).toBe(true);
    expect(index.targets().map(t => t.offset)).toEqual([500]);
  });
  it('filters source visibility while still proving canonical coverage', () => {
    const index = new NavigationIndex(2, { ...settings, showQQ: false });
    index.addPage([{ role: 'user', source: 'qq', content: '@@@@by qq: fixture\nhidden' }, { role: 'user', content: 'visible' }], 0);
    expect(index.proven({ start: 0, end: 0 })).toBe(true);
    expect(index.targets().map(t => t.offset)).toEqual([1]);
  });
  it('bounds retained pages and missing-page lookup for huge sparse histories', () => {
    const index = new NavigationIndex(1_000_000, settings);
    for (let page = 0; page < NAVIGATION_CACHE_PAGES + 5; page++) index.addPage(Array.from({ length: NAVIGATION_PAGE_SIZE }, () => ({ role: 'assistant', content: 'not navigable' })), page * NAVIGATION_PAGE_SIZE);
    expect(index.has(0)).toBe(false);
    expect(index.proven({ start: 500000, end: 500002 })).toBe(false);
    expect(index.nextPage({ start: 500000, end: 500002 })).toBe(2500);
  });
});

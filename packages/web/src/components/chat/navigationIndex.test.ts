import { describe, it, expect } from 'vitest';
import { NavigationIndex, NavigationPager, NavigationSearch, NAVIGATION_CACHE_PAGES, NAVIGATION_PAGE_SIZE, nearestNavigationTarget, type NavigationTarget } from './navigationIndex';
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


describe('exclusive canonical directional paging', () => {
  const rows = (start: number, end: number, stride = 1) => Array.from({ length: end - start }, (_, i) => ({ role: (start + i) % stride === 0 ? 'user' : 'assistant', content: `row ${start + i}`, messageId: `id-${start + i}` }));
  const complete = (pager: NavigationPager, index: NavigationIndex, stride = 1) => {
    let count = 0;
    while (!pager.done(index)) {
      expect(++count).toBeLessThan(1000);
      if (!pager.consume(index)) {
        const start = pager.nextPage() * 200;
        index.addPage(rows(start, Math.min(index.total, start + 200), stride), start);
      }
    }
    return pager.page(index);
  };
  it('reproduces the old 400..599 later-centering gap, then traverses every dense ID both ways across eviction', () => {
    const all = Array.from({ length: 3200 }, (_, i) => i);
    const oldCenter = 599 + 200;
    expect(all.slice(oldCenter - 100, oldCenter + 100)).not.toContain(600);
    const index = new NavigationIndex(all.length, settings);
    let boundary = -1; const visited: number[] = [];
    while (boundary < all.length - 1) {
      const page = complete(new NavigationPager(boundary, 1), index);
      expect(page.map(t => t.offset)).toEqual(all.slice(boundary + 1, boundary + 201));
      expect(page.map(t => t.messageId)).toEqual(page.map(t => `id-${t.offset}`));
      visited.push(...page.map(t => t.offset)); boundary = page.at(-1)!.offset;
    }
    expect(visited).toEqual(all); expect(index.has(0)).toBe(false);
    boundary = all.length;
    const backwards: number[] = [];
    while (boundary > 0) {
      const page = complete(new NavigationPager(boundary, -1), index);
      expect(page.map(t => t.offset)).toEqual(all.slice(Math.max(0, boundary - 200), boundary));
      backwards.unshift(...page.map(t => t.offset)); boundary = page[0]!.offset;
    }
    expect(backwards).toEqual(all);
  });
  it('continues empty sparse batches beyond cache capacity and visits all filtered navigation IDs', () => {
    const index = new NavigationIndex(12001, settings);
    const pager = new NavigationPager(-1, 1);
    for (let batch = 0; !pager.done(index); batch++) {
      expect(batch).toBeLessThan(30);
      const previous = pager.cursor;
      for (let reads = 0; reads < 6 && !pager.done(index); reads++) {
        const start = pager.nextPage() * 200;
        index.addPage(rows(start, Math.min(index.total, start + 200), 3000), start);
        expect(pager.consume(index)).toBe(true);
      }
      expect(pager.cursor).toBeGreaterThan(previous);
    }
    expect(pager.page(index).map(t => [t.offset, t.messageId])).toEqual([0,3000,6000,9000,12000].map(i => [i,`id-${i}`]));
    const reverse = complete(new NavigationPager(12001, -1), index, 3000);
    expect(reverse.map(t => t.offset)).toEqual([0,3000,6000,9000,12000]);
  });
  it('nearest partial continuation proves the result after more than 12 scanned pages despite LRU eviction', () => {
    const index = new NavigationIndex(24000, settings);
    const search = new NavigationSearch({ start: 12001, end: 12004 }, index);
    let reads = 0;
    while (!search.proven(index)) {
      const key = search.nextPage(index)!;
      expect(key).not.toBeNull(); expect(++reads).toBeLessThan(50);
      index.addPage(rows(key * 200, Math.min(index.total, key * 200 + 200), 4800), key * 200);
      expect(search.consume(key, index)).toBe(true);
    }
    expect(reads).toBeGreaterThan(12); expect(search.best?.offset).toBe(14400);
  });
  it('proves an entirely non-navigable transcript without retaining its rows or restarting', () => {
    const index = new NavigationIndex(8000, settings);
    const search = new NavigationSearch({ start: 3999, end: 4001 }, index);
    const pages = new Set<number>();
    while (!search.proven(index)) {
      const key = search.nextPage(index)!;
      expect(pages.has(key)).toBe(false); pages.add(key);
      index.addPage(Array.from({ length: 200 }, () => ({ role: 'assistant', content: 'ordinary' })), key * 200);
      expect(search.consume(key,index)).toBe(true);
    }
    expect(pages.size).toBe(40); expect(search.best).toBeUndefined();
    expect(index.has(4000)).toBe(false);
  });
  it('same-context appends extend the exhausted boundary without repeating previous IDs', () => {
    const index = new NavigationIndex(401, settings);
    const first = complete(new NavigationPager(199, 1), index);
    expect(first.map(t => t.offset)).toEqual(Array.from({ length: 200 }, (_, i) => 200 + i));
    const tail = new NavigationPager(399, 1);
    expect(complete(tail, index).map(t => t.offset)).toEqual([400]);
    index.total = 405;
    const appended = complete(new NavigationPager(400, 1), index);
    expect(appended.map(t => [t.offset,t.fromEnd])).toEqual([[401,3],[402,2],[403,1],[404,0]]);
  });
});

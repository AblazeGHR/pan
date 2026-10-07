import type { Message } from '@/types';
import type { CanonicalRow } from '@/stores/messageOrdering';
import { getQuickJumpIndexItemsByOffset, type MessageVisibilitySettings, type QuickJumpIndexItem } from './messageFilter';

export const NAVIGATION_PAGE_SIZE = 200;
export const NAVIGATION_AUTO_PAGES = 6;
export const NAVIGATION_CACHE_PAGES = 12;
export interface NavigationTarget extends QuickJumpIndexItem {
  offset: number;
  messageId?: string;
}
export interface NavigationRange { start: number; end: number }

export function nearestNavigationTarget(targets: readonly NavigationTarget[], range: NavigationRange) {
  const center = (range.start + range.end) / 2;
  const distance = (offset: number) => Math.max(range.start - offset, offset - range.end, 0);
  let best: NavigationTarget | undefined;
  for (const target of targets) {
    if (!best || distance(target.offset) < distance(best.offset)
      || (distance(target.offset) === distance(best.offset)
        && (Math.abs(target.offset - center) < Math.abs(best.offset - center)
          || (Math.abs(target.offset - center) === Math.abs(best.offset - center) && target.offset > best.offset)))) best = target;
  }
  return best;
}

/** Bounded compact canonical pages. Null entries prove a filtered/non-navigation
 * row was examined; gaps are never treated as evidence of absence. */
export class NavigationIndex {
  private pages = new Map<number, Map<number, NavigationTarget | null>>();
  constructor(public total: number, readonly settings: MessageVisibilitySettings) {}

  addRows(rows: readonly CanonicalRow[]) {
    const buckets = new Map<number, CanonicalRow[]>();
    for (const row of rows) {
      if (row.offset < 0 || row.offset >= this.total) continue;
      const key = Math.floor(row.offset / NAVIGATION_PAGE_SIZE);
      const bucket = buckets.get(key) ?? [];
      bucket.push(row); buckets.set(key, bucket);
    }
    for (const [key, bucket] of buckets) {
      const page = this.pages.get(key) ?? new Map<number, NavigationTarget | null>();
      const targets = getQuickJumpIndexItemsByOffset(bucket, this.total, this.settings);
      const byOffset = new Map(targets.map(t => [this.total - 1 - t.fromEnd, t]));
      for (const { offset, message } of bucket) {
        const target = byOffset.get(offset);
        page.set(offset, target ? { ...target, offset, messageId: message.messageId } : null);
      }
      this.pages.delete(key); this.pages.set(key, page);
      while (this.pages.size > NAVIGATION_CACHE_PAGES) this.pages.delete(this.pages.keys().next().value!);
    }
  }
  addPage(history: Message[], start: number) {
    this.addRows(history.map((message, index) => ({ message, offset: start + index })));
  }
  targets() {
    return [...this.pages.values()].flatMap(page => [...page.values()].filter((t): t is NavigationTarget => t !== null))
      .map(target => ({ ...target, fromEnd: this.total - 1 - target.offset }))
      .sort((a, b) => a.offset - b.offset);
  }
  row(offset: number) { return this.pages.get(Math.floor(offset / NAVIGATION_PAGE_SIZE))?.get(offset); }
  contiguousRange(offset: number) {
    if (!this.has(offset)) return { start: offset, end: offset };
    let start = offset, end = offset;
    while (start > 0 && this.has(start - 1)) start--;
    while (end + 1 < this.total && this.has(end + 1)) end++;
    return { start, end };
  }
  has(offset: number) { return this.pages.get(Math.floor(offset / NAVIGATION_PAGE_SIZE))?.has(offset) ?? false; }
  /** Every row that could beat or tie the candidate must have been examined. */
  proofRange(range: NavigationRange, best = nearestNavigationTarget(this.targets(), range)): NavigationRange {
    if (best && best.offset >= range.start && best.offset <= range.end) {
      const center = (range.start + range.end) / 2;
      const radius = Math.abs(best.offset - center);
      return { start: Math.max(0, Math.ceil(center - radius)), end: Math.min(this.total - 1, Math.floor(center + radius)) };
    }
    const distance = best ? Math.max(range.start - best.offset, best.offset - range.end, 0) : this.total;
    return { start: Math.max(0, range.start - distance), end: Math.min(this.total - 1, range.end + distance) };
  }
  proven(range: NavigationRange) {
    const proof = this.proofRange(range);
    let next = proof.start;
    const offsets = [...this.pages.values()].flatMap(page => [...page.keys()]).sort((a, b) => a - b);
    for (const offset of offsets) {
      if (offset < next) continue;
      if (offset > next) return false;
      if (++next > proof.end) return true;
    }
    return next > proof.end;
  }
  /** Find the nearest missing page without walking an unbounded history. */
  nextPage(range: NavigationRange): number | null {
    const proof = this.proofRange(range);
    const center = Math.max(0, Math.min(this.total - 1, Math.round((range.start + range.end) / 2)));
    const centerPage = Math.floor(center / NAVIGATION_PAGE_SIZE);
    const firstPage = Math.floor(proof.start / NAVIGATION_PAGE_SIZE);
    const lastPage = Math.floor(proof.end / NAVIGATION_PAGE_SIZE);
    // At most the retained cache size can be complete; the next missing page
    // is found within this bound even for a million-row sparse transcript.
    for (let distance = 0; distance <= NAVIGATION_CACHE_PAGES; distance++) {
      for (const key of distance === 0 ? [centerPage] : [centerPage + distance, centerPage - distance]) {
        if (key < firstPage || key > lastPage) continue;
        const start = Math.max(proof.start, key * NAVIGATION_PAGE_SIZE);
        const end = Math.min(proof.end, (key + 1) * NAVIGATION_PAGE_SIZE - 1);
        for (let offset = start; offset <= end; offset++) if (!this.has(offset)) return key;
      }
    }
    return null;
  }
}


/** A directional cursor consumes every canonical row after/before an exclusive
 * target boundary. Progress and the <=200 target buffer survive cache eviction.
 * Unknown rows stop traversal; offset arithmetic never guesses target ordinals. */
export class NavigationPager {
  readonly targets: NavigationTarget[] = [];
  cursor: number;
  constructor(readonly boundary: number, readonly direction: -1 | 1) {
    this.cursor = boundary + direction;
  }
  exhausted(index: NavigationIndex) { return this.cursor < 0 || this.cursor >= index.total; }
  done(index: NavigationIndex) { return this.targets.length === NAVIGATION_PAGE_SIZE || this.exhausted(index); }
  nextPage() { return Math.floor(this.cursor / NAVIGATION_PAGE_SIZE); }
  consume(index: NavigationIndex) {
    // Only one canonical page per call, even if many pages are cached.
    const key = this.nextPage();
    while (!this.done(index) && this.nextPage() === key) {
      const row = index.row(this.cursor);
      if (row === undefined) return false;
      if (row) this.targets.push(row);
      this.cursor += this.direction;
    }
    return true;
  }
  page(index: NavigationIndex) {
    const targets = this.direction === 1 ? [...this.targets] : [...this.targets].reverse();
    return targets.map(target => ({ ...target, fromEnd: index.total - 1 - target.offset }));
  }
}

/** One nearest search keeps only a best target and a contiguous examined
 * interval. Unlike cached rows, these proofs survive LRU eviction. Reopen or
 * context change creates a new search; explicit partial/retry continues it. */
export class NavigationSearch {
  best: NavigationTarget | undefined;
  private first: number;
  private last: number;
  constructor(readonly range: NavigationRange, index: NavigationIndex) {
    const center = Math.floor((range.start + range.end) / 2 / NAVIGATION_PAGE_SIZE);
    this.first = center; this.last = center - 1;
    this.best = nearestNavigationTarget(index.targets(), range);
  }
  proven(index: NavigationIndex) {
    if (index.total === 0) return true;
    const proof = index.proofRange(this.range, this.best);
    return this.last >= this.first && this.first * NAVIGATION_PAGE_SIZE <= proof.start
      && Math.min(index.total - 1, (this.last + 1) * NAVIGATION_PAGE_SIZE - 1) >= proof.end;
  }
  nextPage(index: NavigationIndex) {
    if (this.last < this.first) return this.first;
    const proof = index.proofRange(this.range, this.best);
    const left = this.first - 1, right = this.last + 1;
    const needLeft = left >= 0 && this.first * NAVIGATION_PAGE_SIZE > proof.start;
    const needRight = right * NAVIGATION_PAGE_SIZE < index.total && (this.last + 1) * NAVIGATION_PAGE_SIZE <= proof.end;
    if (!needLeft) return needRight ? right : null;
    if (!needRight) return left;
    const center = (this.range.start + this.range.end) / 2;
    return center - (left + 1) * NAVIGATION_PAGE_SIZE < right * NAVIGATION_PAGE_SIZE - center ? left : right;
  }
  consume(key: number, index: NavigationIndex) {
    const start = key * NAVIGATION_PAGE_SIZE, end = Math.min(index.total, start + NAVIGATION_PAGE_SIZE);
    for (let offset = start; offset < end; offset++) if (!index.has(offset)) return false;
    for (let offset = start; offset < end; offset++) {
      const row = index.row(offset);
      if (row) this.best = nearestNavigationTarget(this.best ? [this.best, row] : [row], this.range);
    }
    this.first = Math.min(this.first, key); this.last = Math.max(this.last, key);
    return true;
  }
}

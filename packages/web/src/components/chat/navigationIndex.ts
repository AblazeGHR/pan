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

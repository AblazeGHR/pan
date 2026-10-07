import type { Message } from '@/types';
import type { CanonicalRow } from '@/stores/messageOrdering';
import { getQuickJumpIndexItemsByOffset, type MessageVisibilitySettings, type QuickJumpIndexItem } from './messageFilter';

export const NAVIGATION_PAGE_SIZE = 200;
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

/** Full compact index: bodies are discarded, canonical coverage survives.
 * Memory is proportional to navigation targets plus merged loaded intervals,
 * never full history text. The UI independently virtualizes its complete list. */
export class NavigationIndex {
  private entries = new Map<number, NavigationTarget>();
  private coverage: NavigationRange[] = [];
  constructor(public total: number, readonly settings: MessageVisibilitySettings) {}
  addRows(rows: readonly CanonicalRow[]) {
    const targets = getQuickJumpIndexItemsByOffset(rows, this.total, this.settings);
    const byOffset = new Map(targets.map(target => [this.total - 1 - target.fromEnd,target]));
    const offsets: number[] = [];
    for (const { offset, message } of rows) {
      if (offset < 0 || offset >= this.total) continue;
      const target = byOffset.get(offset);
      if (target) this.entries.set(offset,{ ...target,offset,messageId:message.messageId,
        preview: 'navigationPreview' in message && typeof message.navigationPreview === 'string' ? message.navigationPreview : target.preview });
      else this.entries.delete(offset);
      offsets.push(offset);
    }
    offsets.sort((a,b)=>a-b);
    let start = offsets[0], end = start;
    for (const offset of offsets.slice(1)) {
      if (offset > end! + 1) { this.cover(start!,end!); start = offset; }
      end = offset;
    }
    if (start !== undefined) this.cover(start,end!);
  }
  private cover(start: number,end: number) {
    const merged: NavigationRange[] = [];
    for (const range of [...this.coverage,{start,end}].sort((a,b)=>a.start-b.start)) {
      const last = merged.at(-1);
      if (last && range.start <= last.end+1) last.end=Math.max(last.end,range.end);
      else merged.push({...range});
    }
    this.coverage=merged;
  }
  addPage(history: Message[], start: number) { this.addRows(history.map((message,index)=>({message,offset:start+index}))); }
  targets() { return [...this.entries.values()].sort((a,b)=>a.offset-b.offset).map(target=>({...target,fromEnd:this.total-1-target.offset})); }
  has(offset: number) { return this.coverage.some(range=>offset>=range.start&&offset<=range.end); }
  complete() { return this.total===0 || this.coverage.some(range=>range.start===0&&range.end>=this.total-1); }
  proofRange(range: NavigationRange,best=nearestNavigationTarget(this.targets(),range)): NavigationRange {
    if (best&&best.offset>=range.start&&best.offset<=range.end) {
      const center=(range.start+range.end)/2,radius=Math.abs(best.offset-center);
      return {start:Math.max(0,Math.ceil(center-radius)),end:Math.min(this.total-1,Math.floor(center+radius))};
    }
    const distance=best?Math.max(range.start-best.offset,best.offset-range.end,0):this.total;
    return {start:Math.max(0,range.start-distance),end:Math.min(this.total-1,range.end+distance)};
  }
  proven(range: NavigationRange) { const proof=this.proofRange(range); return this.total===0||this.coverage.some(c=>c.start<=proof.start&&c.end>=proof.end); }
  nextMissing(range?: NavigationRange): number | null {
    const desired=range?this.proofRange(range):{start:0,end:this.total-1};
    const center=range?Math.floor((range.start+range.end)/2):this.total-1;
    let best: number | null=null;
    let cursor=desired.start;
    const consider=(start:number,end:number)=>{
      if(start>end)return;
      const offset=Math.max(start,Math.min(end,center));
      if(best===null||Math.abs(offset-center)<Math.abs(best-center))best=offset;
    };
    for(const covered of this.coverage){
      if(covered.end<cursor)continue;
      if(covered.start>desired.end)break;
      consider(cursor,Math.min(desired.end,covered.start-1)); cursor=Math.max(cursor,covered.end+1);
    }
    consider(cursor,desired.end);return best;
  }
}

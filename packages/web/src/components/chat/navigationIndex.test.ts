import { describe, it, expect } from 'vitest';
import { NavigationIndex, nearestNavigationTarget, type NavigationTarget } from './navigationIndex';
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
  it('retains compact coverage and targets for the complete continuous index',()=>{
    const index=new NavigationIndex(10000,settings);
    for(let start=0;start<10000;start+=200)index.addPage(Array.from({length:200},(_,i)=>({role:'user',content:'row',messageId:`id-${start+i}`})),start);
    expect(index.complete()).toBe(true);expect(index.targets().map(t=>t.offset)).toEqual(Array.from({length:10000},(_,i)=>i));
    index.total+=1;expect(index.complete()).toBe(false);index.addPage([{role:'user',content:'append'}],10000);expect(index.complete()).toBe(true);
  });
});

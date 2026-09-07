// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  AUTO_SCROLL_EDGE_PX,
  AUTO_SCROLL_MAX_PX_PER_FRAME,
  findScrollableAncestor,
  getAutoScrollDelta,
} from './sessionDragAutoScroll';

describe('session drag auto-scroll', () => {
  const viewport = { top: 100, bottom: 500 };

  it('triggers at the top and bottom edges, but not in the middle', () => {
    expect(getAutoScrollDelta(100, viewport)).toBe(-AUTO_SCROLL_MAX_PX_PER_FRAME);
    expect(getAutoScrollDelta(500, viewport)).toBe(AUTO_SCROLL_MAX_PX_PER_FRAME);
    expect(getAutoScrollDelta(300, viewport)).toBe(0);
  });

  it('accelerates toward the edge and never exceeds the maximum', () => {
    expect(Math.abs(getAutoScrollDelta(100 + AUTO_SCROLL_EDGE_PX - 1, viewport))).toBeLessThan(
      Math.abs(getAutoScrollDelta(100, viewport)),
    );
    expect(Math.abs(getAutoScrollDelta(100, viewport))).toBe(AUTO_SCROLL_MAX_PX_PER_FRAME);
    expect(Math.abs(getAutoScrollDelta(500, viewport))).toBe(AUTO_SCROLL_MAX_PX_PER_FRAME);
  });

  it('stops outside the edge zones', () => {
    expect(getAutoScrollDelta(20, viewport)).toBe(0);
    expect(getAutoScrollDelta(580, viewport)).toBe(0);
  });

  it('finds the nearest overflowing scroll container, not the page', () => {
    const outer = document.createElement('div');
    const list = document.createElement('div');
    outer.style.overflowY = 'auto';
    Object.defineProperties(outer, { clientHeight: { value: 100 }, scrollHeight: { value: 300 } });
    outer.append(list);
    document.body.append(outer);
    expect(findScrollableAncestor(list)).toBe(outer);
    outer.remove();
  });
});

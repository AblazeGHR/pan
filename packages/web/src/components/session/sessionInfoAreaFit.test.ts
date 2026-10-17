// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  INFO_AREA_GAP,
  INFO_AREA_RIGHT_INSET,
  jobBadgeFits,
  measureInfoArea,
} from './sessionInfoAreaFit';

/** A DOMRect stub with a width; jsdom performs no real layout. */
function rect(width: number): DOMRect {
  return {
    width,
    height: 14,
    top: 0,
    left: 0,
    right: width,
    bottom: 14,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect;
}

function stubWidth(el: HTMLElement, width: number): HTMLElement {
  el.getBoundingClientRect = () => rect(width);
  return el;
}

/** row (72px rail, right inset 1) with a J badge plus the given siblings. */
function buildRow(siblings: Array<[string, number]>, jWidth = 14, withBadge = true) {
  const rail = document.createElement('div');
  rail.className = 'session-quick-action-rail';
  Object.defineProperty(rail, 'clientWidth', { value: 72, configurable: true });
  const row = document.createElement('span');
  row.className = 'session-quick-action-adapter-row';
  const badge = stubWidth(document.createElement('span'), jWidth);
  badge.className = 'session-job-badge';
  if (withBadge) row.appendChild(badge);
  for (const [className, width] of siblings) {
    const child = stubWidth(document.createElement('span'), width);
    child.className = className;
    row.appendChild(child);
  }
  rail.appendChild(row);
  return { rail, row, badge };
}

describe('jobBadgeFits', () => {
  const base = { available: 71, jWidth: 14, othersWidth: 0, othersCount: 0, gap: INFO_AREA_GAP, zoom: 1 };

  it('shows J when it is the only info-area item', () => {
    expect(jobBadgeFits(base)).toBe(true);
  });

  it('counts every sibling and the gap that J introduces', () => {
    // J + queue(9) + done(3) + adapter(cbc): 14 + 52 + 9 = 75 -> does not fit.
    expect(jobBadgeFits({ ...base, othersWidth: 52, othersCount: 3 })).toBe(false);
    // Exactly at the budget (14 + 48 + 9 = 71) it still fits.
    expect(jobBadgeFits({ ...base, othersWidth: 48, othersCount: 3 })).toBe(true);
    // Half a pixel more flips to a full hide.
    expect(jobBadgeFits({ ...base, othersWidth: 48.5, othersCount: 3 })).toBe(false);
  });

  it('keeps J beside multi-digit numbers and a short adapter when they fit', () => {
    // J + done(99+) + adapter(cbc): 14 + 48 + 6 = 68 -> fits.
    expect(jobBadgeFits({ ...base, othersWidth: 48, othersCount: 2 })).toBe(true);
    // A long adapter pushes the same numbers past the rail -> full hide.
    // J + done(99+) + adapter(opencode): 14 + 64 + 6 = 84.
    expect(jobBadgeFits({ ...base, othersWidth: 64, othersCount: 2 })).toBe(false);
  });

  it('treats an unmeasurable layout (jsdom/SSR) as visible', () => {
    expect(jobBadgeFits({ ...base, available: 0 })).toBe(true);
    expect(jobBadgeFits({ ...base, available: -1 })).toBe(true);
  });
});

describe('measureInfoArea', () => {
  it('sums the other items and derives the available width from the rail', () => {
    const { row, badge } = buildRow([
      ['session-queue-badge', 14],
      ['session-unread-done-badge', 14],
      ['session-quick-action-adapter', 24],
    ]);
    const m = measureInfoArea(row, badge, 0);
    expect(m.available).toBe(72 - INFO_AREA_RIGHT_INSET);
    expect(m.othersWidth).toBe(52);
    expect(m.othersCount).toBe(3);
    expect(m.jWidth).toBe(14);
  });

  it('falls back to the cached J width while the badge is unmounted', () => {
    // J unmounted: the row holds only the sibling (as after a full hide).
    const { row } = buildRow([['session-queue-badge', 14]], 14, false);
    const m = measureInfoArea(row, null, 14);
    expect(m.jWidth).toBe(14);
    expect(m.othersCount).toBe(1);
    expect(m.othersWidth).toBe(14);
  });

  it('measures only live siblings (no J in the sum) and tolerates an empty row', () => {
    const { row, badge } = buildRow([]);
    const m = measureInfoArea(row, badge, 14);
    expect(m.othersCount).toBe(0);
    expect(m.othersWidth).toBe(0);
    expect(m.jWidth).toBe(14);
  });

  it('normalizes zoom-scaled rects back to CSS px via the rail factor', () => {
    // CSS zoom 1.25: rects scale (90/16.83) while clientWidth stays CSS px (72).
    const { row, badge } = buildRow([['session-quick-action-adapter', 29.25]], 16.83);
    Object.defineProperty(row.parentElement!, 'clientWidth', { value: 72, configurable: true });
    row.parentElement!.getBoundingClientRect = () => rect(90);
    const m = measureInfoArea(row, badge, 14);
    expect(m.zoom).toBeCloseTo(1.25, 5);
    // 16.83 / 1.25 = 13.464 CSS px; 29.25 / 1.25 = 23.4 CSS px.
    expect(m.jWidth).toBeCloseTo(13.464, 2);
    expect(m.othersWidth).toBeCloseTo(23.4, 2);
    expect(m.available).toBe(71);
    // The whole inequality now shares one unit: 13.464 + 23.4 + 3 = 39.864.
    expect(jobBadgeFits(m)).toBe(true);
  });

  it('uses the documented gap constant', () => {
    expect(INFO_AREA_GAP).toBe(3);
  });
});

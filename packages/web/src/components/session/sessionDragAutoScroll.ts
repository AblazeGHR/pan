export const AUTO_SCROLL_EDGE_PX = 72;
export const AUTO_SCROLL_MAX_PX_PER_FRAME = 18;

/** Return the bounded per-frame scroll delta for a pointer inside a viewport. */
export function getAutoScrollDelta(pointerY: number, viewport: Pick<DOMRect, 'top' | 'bottom'>): number {
  if (pointerY < viewport.top || pointerY > viewport.bottom) return 0;
  if (pointerY - viewport.top < AUTO_SCROLL_EDGE_PX) {
    const intensity = 1 - (pointerY - viewport.top) / AUTO_SCROLL_EDGE_PX;
    return -Math.min(AUTO_SCROLL_MAX_PX_PER_FRAME, intensity * AUTO_SCROLL_MAX_PX_PER_FRAME);
  }
  if (viewport.bottom - pointerY < AUTO_SCROLL_EDGE_PX) {
    const intensity = 1 - (viewport.bottom - pointerY) / AUTO_SCROLL_EDGE_PX;
    return Math.min(AUTO_SCROLL_MAX_PX_PER_FRAME, intensity * AUTO_SCROLL_MAX_PX_PER_FRAME);
  }
  return 0;
}

/** Find the nearest actual vertical scrolling element, not the document. */
export function findScrollableAncestor(element: HTMLElement | null): HTMLElement | null {
  let current = element?.parentElement ?? null;
  while (current) {
    const overflowY = window.getComputedStyle(current).overflowY;
    if ((overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay') &&
        current.scrollHeight > current.clientHeight) return current;
    current = current.parentElement;
  }
  return null;
}

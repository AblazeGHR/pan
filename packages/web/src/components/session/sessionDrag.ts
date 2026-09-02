/**
 * Drag-to-reorder hit-zone configuration and pure hit-testing logic.
 *
 * The card is divided into three vertical bands:
 *
 *   ┌──────────────────────┐ ─┐
 *   │   insert-BEFORE band │  │ edge = max(edgeFraction·h, minEdgePx)
 *   ├──────────────────────┤ ─┤
 *   │   CENTER band (manage)│ │ rest of the card
 *   ├──────────────────────┤ ─┤
 *   │   insert-AFTER band  │  │ edge
 *   └──────────────────────┘ ─┘
 *
 * Both insert bands map to the SAME boundary line (top edge of the card =
 * boundary above it; bottom edge = boundary below it), so the gap between two
 * cards has a combined hit height of two edge bands — deliberately generous
 * (never a hard-to-hit 1px line). Tune the constants below after trying the
 * demo; center-vs-edge conflicts are impossible by construction because the
 * bands partition the card.
 */

export type DropZone = 'before' | 'center' | 'after';

export const DRAG_HIT = {
  /** Insert-band height as a fraction of the card height (top & bottom). */
  edgeFraction: 0.3,
  /** Minimum insert-band height in px — keeps bands generous on short cards. */
  minEdgePx: 20,
} as const;

/** Pure hit-test: classify a pointer Y against a card's vertical extent. */
export function resolveDropZone(
  cardTop: number,
  cardHeight: number,
  pointerY: number,
): DropZone {
  const edge = Math.max(DRAG_HIT.edgeFraction * cardHeight, DRAG_HIT.minEdgePx);
  const rel = pointerY - cardTop;
  if (rel <= edge) return 'before';
  if (rel >= cardHeight - edge) return 'after';
  return 'center';
}

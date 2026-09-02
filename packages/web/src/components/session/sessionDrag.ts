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
 *
 * Manager-tree drop semantics are also resolved here (pure, testable): an
 * edge drop lands the dragged session next to the target card and adopts the
 * management context of that slot (entering another manager's group, or
 * leaving one's own group toward a top-level slot), while drops that would
 * put a session under itself or its own descendant are rejected before they
 * can form a management cycle.
 */

import type { Session } from '@/types';

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

/** Management edges among a VISIBLE session list (mirrors buildManagerTree:
 *  an edge counts only when the manager session is present in the list). */
export interface ManagerEdges {
  /** childId → managerId (only when the manager exists in the list). */
  parentOf: Map<string, string>;
  /** managerId → child ids (list order follows the input order). */
  childrenOf: Map<string, string[]>;
}

export function buildManagerEdges(sessions: Session[]): ManagerEdges {
  const parentOf = new Map<string, string>();
  const childrenOf = new Map<string, string[]>();
  const present = new Set(sessions.map((s) => s.id));
  for (const s of sessions) {
    const p = s.managedBy;
    if (p && p !== s.id && present.has(p)) {
      parentOf.set(s.id, p);
      const arr = childrenOf.get(p);
      if (arr) arr.push(s.id);
      else childrenOf.set(p, [s.id]);
    }
  }
  return { parentOf, childrenOf };
}

/** All descendant ids of `id` (excluding `id` itself), cycle-safe. */
export function collectDescendants(edges: ManagerEdges, id: string): Set<string> {
  const out = new Set<string>();
  const stack = [...(edges.childrenOf.get(id) ?? [])];
  while (stack.length > 0) {
    const cur = stack.pop()!;
    if (out.has(cur)) continue;
    out.add(cur);
    stack.push(...(edges.childrenOf.get(cur) ?? []));
  }
  return out;
}

export interface ManagerDropDecision {
  /** Session id that should manage the dragged session after the drop
   *  (null = the dragged session stays/becomes a top-level root). */
  newManager: string | null;
  /** True when the drop would create a management cycle (the dragged session
   *  would be moved under itself or under one of its own descendants). */
  blockedByCycle: boolean;
}

/**
 * Decide the management outcome of a drop in the manager tree:
 *
 *  - CENTER on target T → T manages the dragged session (newManager = T).
 *  - EDGE (before/after) on a CHILD row → the dragged session joins that
 *    child's manager group next to it (newManager = child's manager).
 *  - EDGE before a ROOT row → top-level slot (newManager = null); dropping a
 *    managed child here MOVES IT OUT of its group.
 *  - EDGE after a ROOT row → if that root has children the slot is its
 *    first-child position (newManager = root); otherwise top level.
 *
 * A drop is blocked when newManager is the dragged session itself or one of
 * its own descendants (would create a management cycle / recursive nesting).
 */
export function decideManagerDrop(
  visibleSessions: Session[],
  dragId: string,
  targetId: string,
  zone: DropZone,
): ManagerDropDecision {
  const edges = buildManagerEdges(visibleSessions);

  let newManager: string | null;
  if (zone === 'center') {
    // Center = the target manages the dragged session.
    newManager = targetId;
  } else {
    const tParent = edges.parentOf.get(targetId);
    if (tParent !== undefined) {
      // Target is a child row → drop lands inside its manager's group.
      newManager = tParent;
    } else if (zone === 'after' && (edges.childrenOf.get(targetId) ?? []).length > 0) {
      // Target is a manager row → after its row is the first-child slot.
      newManager = targetId;
    } else {
      // Target is a top-level row: before it (or after a leaf) → top level.
      newManager = null;
    }
  }

  const blockedByCycle =
    newManager !== null &&
    (newManager === dragId || collectDescendants(edges, dragId).has(newManager));

  return { newManager, blockedByCycle };
}

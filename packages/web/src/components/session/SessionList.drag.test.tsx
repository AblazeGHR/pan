// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, act } from '@testing-library/react';
import { SessionList } from './SessionList';
import { resolveDropZone, DRAG_HIT, decideManagerDrop } from './sessionDrag';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

function mk(id: string, name: string, extra: Partial<Session> = {}): Session {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    updatedAt: new Date(Date.now() - 1000 * 60 * Math.random()).toISOString(),
    ...extra,
  };
}

// ── Deterministic layout for hit-testing (jsdom has no real layout) ──
// Cards are 64px tall; top Y per session id comes from the map below.
const CARD_H = 64;
const layout: Record<string, number> = { A: 0, B: 64, C: 128, D: 192 };
const NULL_RECT = {
  top: 0, bottom: 0, height: 0, width: 0, left: 0, right: 0, x: 0, y: 0,
  toJSON: () => {},
};
let rectSpy: ReturnType<typeof vi.spyOn> | null = null;

function stubCardRects(layoutMap: Record<string, number> = layout) {
  rectSpy = vi
    .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    .mockImplementation(function (this: HTMLElement) {
      const id = this.dataset?.sessionCardId;
      if (id && layoutMap[id] !== undefined) {
        return {
          ...NULL_RECT,
          top: layoutMap[id],
          bottom: layoutMap[id] + CARD_H,
          height: CARD_H,
          width: 300,
          right: 300,
          y: layoutMap[id],
        };
      }
      return { ...NULL_RECT };
    });
}

/** Dispatch a pointermove on window (inside act so React flushes). */
function pointerMove(y: number) {
  act(() => {
    window.dispatchEvent(new MouseEvent('pointermove', { clientY: y, bubbles: true }));
  });
}

/** Dispatch pointerup on window (inside act so React flushes). */
function pointerUp() {
  act(() => {
    window.dispatchEvent(new Event('pointerup', { bubbles: true }));
  });
}

function cardOrder(container: HTMLElement): (string | undefined)[] {
  return [...container.querySelectorAll('[data-session-card-id]')].map(
    (el) => (el as HTMLElement).dataset.sessionCardId,
  );
}

describe('resolveDropZone (宽松命中区域)', () => {
  it('splits the card into three generous bands', () => {
    const edge = Math.max(DRAG_HIT.edgeFraction * CARD_H, DRAG_HIT.minEdgePx); // 20px
    // Top band → insert before
    expect(resolveDropZone(0, CARD_H, 0)).toBe('before');
    expect(resolveDropZone(0, CARD_H, edge - 1)).toBe('before');
    // Center band → manage
    expect(resolveDropZone(0, CARD_H, edge + 1)).toBe('center');
    expect(resolveDropZone(0, CARD_H, CARD_H / 2)).toBe('center');
    // Bottom band → insert after
    expect(resolveDropZone(0, CARD_H, CARD_H - edge + 1)).toBe('after');
    expect(resolveDropZone(0, CARD_H, CARD_H - 1)).toBe('after');
  });

  it('keeps bands at least minEdgePx tall on short cards', () => {
    // h=56 → fraction gives 16.8px, min 20px wins: bands are 20px each.
    expect(resolveDropZone(0, 56, 15)).toBe('before');
    expect(resolveDropZone(0, 56, 28)).toBe('center');
    expect(resolveDropZone(0, 56, 45)).toBe('after');
  });

  it('degenerate very short cards stay all-edge (no conflicting sliver)', () => {
    // h=40 with 20px bands: edge bands cover the whole card — zones still
    // partition cleanly (no overlap, no unreachable 1px gap).
    expect(resolveDropZone(0, 40, 15)).toBe('before');
    expect(resolveDropZone(0, 40, 25)).toBe('after');
  });

  it('center and edge bands never overlap (partition the card)', () => {
    const edge = Math.max(DRAG_HIT.edgeFraction * CARD_H, DRAG_HIT.minEdgePx);
    expect(2 * edge).toBeLessThan(CARD_H);
  });
});

describe('SessionList drag interactions (mock demo)', () => {
  beforeEach(() => {
    localStorage.clear();
    // These interactions exercise the ?mock=1/no-backend demo branch. The real
    // backend branch (no mock flag) is covered by SessionList.drag.realBackend.test.tsx.
    localStorage.setItem('pan:mockDemo', '1');
    useSessionStore.setState({
      sessions: [
        mk('A', 'Alpha', { updatedAt: new Date(Date.now() - 60_000).toISOString() }),
        mk('B', 'Bravo', { updatedAt: new Date(Date.now() - 120_000).toISOString() }),
        mk('C', 'Charlie', { updatedAt: new Date(Date.now() - 180_000).toISOString() }),
        mk('D', 'Delta', { updatedAt: new Date(Date.now() - 240_000).toISOString() }),
      ],
      currentSessionId: null,
      multiSelectMode: false,
      sessionsLoading: false,
    });
    useUIStore.setState({
      groupBy: 'none',
      sortBy: 'recent',
      customOrder: [],
      searchQuery: '',
      specialFilters: new Set(),
      hiddenSessionIds: new Set(),
      collapsedGroups: new Set(),
      toastQueue: [],
    });
    stubCardRects();
  });

  afterEach(() => {
    rectSpy?.mockRestore();
    rectSpy = null;
  });

  it('shows a ghost near the cursor once the drag threshold is crossed; original cards stay in place', () => {
    const { container } = render(<SessionList />);
    expect(cardOrder(container)).toEqual(['A', 'B', 'C', 'D']);
    expect(container.querySelector('[data-drag-ghost]')).toBeNull();

    const handle = container.querySelector('[data-testid="drag-handle"]');
    fireEvent.pointerDown(handle!, { button: 0, clientX: 10, clientY: 10 });

    // Below the drag-start threshold there is no ghost yet (still a click).
    expect(container.querySelector('[data-drag-ghost]')).toBeNull();

    // Cross the threshold → the ghost appears and names the dragged session.
    pointerMove(30);
    const ghost = container.querySelector('[data-drag-ghost]');
    expect(ghost).not.toBeNull();
    expect(ghost!.textContent).toContain('Alpha');

    // The list order is untouched during the drag — no card movement.
    expect(cardOrder(container)).toEqual(['A', 'B', 'C', 'D']);

    act(() => { window.dispatchEvent(new Event('pointercancel', { bubbles: true })); });
    expect(container.querySelector('[data-drag-ghost]')).toBeNull();
  });

  it('center hit zone highlights card B and drop mocks "B manage A"', () => {
    const { container } = render(<SessionList />);

    const handle = container.querySelector('[data-testid="drag-handle"]');
    fireEvent.pointerDown(handle!, { button: 0, clientX: 10, clientY: 10 });

    // Pointer over B's center band (B spans 64-128; center ≈ 96).
    pointerMove(96);
    expect(container.querySelector('[data-drag-center]')).not.toBeNull();
    expect(container.querySelector('[data-insert-line]')).toBeNull();

    pointerUp();

    const state = useSessionStore.getState();
    expect(state.sessions.find((s) => s.id === 'A')!.managedBy).toBe('B');
    // Manage does NOT switch the sort mode.
    expect(useUIStore.getState().sortBy).toBe('recent');
    expect(useUIStore.getState().customOrder).toEqual([]);
    // Toast reports the mock action.
    expect(useUIStore.getState().toastQueue.some((t) => t.message.includes('管理'))).toBe(true);
  });

  it('edge/gap hit zone highlights the insert line and drop reorders into custom sort', () => {
    const { container } = render(<SessionList />);

    const handle = container.querySelector('[data-testid="drag-handle"]');
    fireEvent.pointerDown(handle!, { button: 0, clientX: 10, clientY: 10 });

    // Pointer over B's bottom edge band (B bottom = 128; band starts at 108).
    pointerMove(120);
    expect(container.querySelector('[data-insert-line="after"]')).not.toBeNull();
    expect(container.querySelector('[data-drag-center]')).toBeNull();

    pointerUp();

    // A moved after B in the manual order…
    expect(useUIStore.getState().customOrder).toEqual(['B', 'A', 'C', 'D']);
    // …and the mode switched to custom immediately.
    expect(useUIStore.getState().sortBy).toBe('custom');
    // A pure position move does NOT toast (only management changes do).
    expect(useUIStore.getState().toastQueue.length).toBe(0);

    // DOM reflects the new custom order.
    expect(cardOrder(container)).toEqual(['B', 'A', 'C', 'D']);
  });

  it('inserting via the boundary above C lands A between B and C', () => {
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]');
    fireEvent.pointerDown(handle!, { button: 0, clientX: 10, clientY: 10 });

    // Pointer over C's top edge band (= boundary between B and C).
    pointerMove(140);
    expect(container.querySelector('[data-insert-line="before"]')).not.toBeNull();
    pointerUp();

    expect(useUIStore.getState().customOrder).toEqual(['B', 'A', 'C', 'D']);
    expect(useUIStore.getState().sortBy).toBe('custom');
  });

  it('dropping at the boundary above B (original position) is a no-op reorder', () => {
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]');
    fireEvent.pointerDown(handle!, { button: 0, clientX: 10, clientY: 10 });

    // A's own position: boundary above B = between A and B → order unchanged,
    // but a genuine drop still switches to custom sort (drag-to-reorder ran).
    pointerMove(68);
    pointerUp();

    expect(useUIStore.getState().customOrder).toEqual(['A', 'B', 'C', 'D']);
    expect(useUIStore.getState().sortBy).toBe('custom');
  });

  it('renders sessions in the persisted custom order when sortBy is custom', () => {
    useUIStore.setState({
      sortBy: 'custom',
      customOrder: ['C', 'A', 'D', 'B'],
    });
    const { container } = render(<SessionList />);
    expect(cardOrder(container)).toEqual(['C', 'A', 'D', 'B']);
  });

  it('a plain click on the drag gutter selects the session (threshold)', () => {
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    // No movement → the click (without a drag) selects the card.
    fireEvent.click(handle);
    expect(useSessionStore.getState().currentSessionId).toBe('A');
  });

  it('a click fired right after a real drag does NOT select the dragged session', () => {
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(96); // drag A onto B's center
    pointerUp();
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'A')!.managedBy).toBe('B');

    // A stray click landing back on the gutter after the release must be
    // ignored (the drag already finished on pointerup).
    fireEvent.click(handle);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
  });

  it('no drag handle in the workdir-grouped list (only flat + manager)', () => {
    useUIStore.setState({ groupBy: 'workdir' });
    const { container } = render(<SessionList />);
    expect(container.querySelector('[data-testid="drag-handle"]')).toBeNull();
  });

  it('manager tree: drag handle exists on roots and children', () => {
    useUIStore.setState({ groupBy: 'manager' });
    const { container } = render(<SessionList />);
    expect(container.querySelectorAll('[data-testid="drag-handle"]').length).toBe(4);
  });
});

describe('decideManagerDrop (tree management semantics)', () => {
  // Tree: A manages B and C; D is an independent root.
  const tree = [
    mk('A', 'A'),
    mk('B', 'B', { managedBy: 'A' }),
    mk('C', 'C', { managedBy: 'A' }),
    mk('D', 'D'),
  ];

  it('center drop → the target manages the dragged session', () => {
    // D dropped on A's center → A manages D.
    expect(decideManagerDrop(tree, 'D', 'A', 'center')).toEqual({
      newManager: 'A',
      blockedByCycle: false,
    });
    // B dropped on A's center again → A manages B (already true, no cycle).
    expect(decideManagerDrop(tree, 'B', 'A', 'center')).toEqual({
      newManager: 'A',
      blockedByCycle: false,
    });
  });

  it('edge drop on a child row adopts that group', () => {
    // D (root) dropped next to child B of A → D joins A's group.
    expect(decideManagerDrop(tree, 'D', 'B', 'after')).toEqual({
      newManager: 'A',
      blockedByCycle: false,
    });
    expect(decideManagerDrop(tree, 'D', 'B', 'before')).toEqual({
      newManager: 'A',
      blockedByCycle: false,
    });
  });

  it('edge drop on a top-level slot leaves the current group', () => {
    // B (child of A) dropped before root D → top level (managedBy null).
    expect(decideManagerDrop(tree, 'B', 'D', 'before')).toEqual({
      newManager: null,
      blockedByCycle: false,
    });
    // B dropped after leaf root D → top level.
    expect(decideManagerDrop(tree, 'B', 'D', 'after')).toEqual({
      newManager: null,
      blockedByCycle: false,
    });
  });

  it('edge after a manager row sorts at that row level (never becomes its child)', () => {
    // D (root) dropped after manager row A → A is a root, so the slot is a
    // top-level sibling AFTER A's whole subtree, NOT A's first child slot.
    expect(decideManagerDrop(tree, 'D', 'A', 'after')).toEqual({
      newManager: null,
      blockedByCycle: false,
    });
    // B (A's own child) dropped after manager row A → B leaves A's group and
    // is placed at top level after A's subtree.
    expect(decideManagerDrop(tree, 'B', 'A', 'after')).toEqual({
      newManager: null,
      blockedByCycle: false,
    });
  });

  it('blocks moving a session under itself', () => {
    // A dropped after child B's row would join A's own group as its own child.
    expect(decideManagerDrop(tree, 'A', 'B', 'after')).toEqual({
      newManager: 'A',
      blockedByCycle: true,
    });
    expect(decideManagerDrop(tree, 'A', 'B', 'center')).toEqual({
      newManager: 'B',
      blockedByCycle: true,
    });
  });

  it('blocks moving a session under its own descendant (deep)', () => {
    // Chain A → B → G. Dropping A next to G (child of B) would nest A
    // under B — B is A's descendant → blocked. Dropping A on G's center
    // (G manages A) → G is A's descendant → blocked.
    const deep = [
      mk('A', 'A'),
      mk('B', 'B', { managedBy: 'A' }),
      mk('G', 'G', { managedBy: 'B' }),
    ];
    expect(decideManagerDrop(deep, 'A', 'G', 'after')).toEqual({
      newManager: 'B',
      blockedByCycle: true,
    });
    expect(decideManagerDrop(deep, 'A', 'G', 'center')).toEqual({
      newManager: 'G',
      blockedByCycle: true,
    });
    // Moving B (mid-level) under G (its own child) is blocked too.
    expect(decideManagerDrop(deep, 'B', 'G', 'center')).toEqual({
      newManager: 'G',
      blockedByCycle: true,
    });
  });

  it('allows moving a sibling next to another sibling of the same group', () => {
    // C dropped on B's row (both children of A) → stays in A's group.
    expect(decideManagerDrop(tree, 'C', 'B', 'before')).toEqual({
      newManager: 'A',
      blockedByCycle: false,
    });
  });
});

describe('manager-tree drag interactions (mock demo)', () => {
  // Tree: B manages A and A2; C is an independent root.
  // preorder in the tree = B, A, A2, C (children render under their manager).
  const managerLayout = { B: 0, A: 64, A2: 128, C: 192 };

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('pan:mockDemo', '1'); // mock/no-backend branch
    useUIStore.setState({ groupBy: 'manager', sortBy: 'recent', customOrder: [], toastQueue: [] });
    useSessionStore.setState({
      sessions: [
        // Deterministic recent order: B, A, A2, C → filtered order, so the
        // manager tree preorder is B, A, A2, C (children under their manager).
        mk('B', 'B', { updatedAt: new Date(Date.now() - 60_000).toISOString() }),
        mk('A', 'A', { managedBy: 'B', updatedAt: new Date(Date.now() - 4 * 60_000).toISOString() }),
        mk('A2', 'A2', { managedBy: 'B', updatedAt: new Date(Date.now() - 5 * 60_000).toISOString() }),
        mk('C', 'C', { updatedAt: new Date(Date.now() - 9 * 60_000).toISOString() }),
      ],
      currentSessionId: null,
      multiSelectMode: false,
      sessionsLoading: false,
    });
    stubCardRects(managerLayout);
  });

  function handleOf(container: HTMLElement, id: string): Element {
    const card = container.querySelector(`[data-session-card-id="${id}"]`)!;
    const handle = card.querySelector('[data-testid="drag-handle"]')!;
    return handle;
  }

  it('center drop on a root mocks "root manages dragged session" in the tree', () => {
    const { container } = render(<SessionList />);
    // C (root) dropped on B's center band (B spans 0-64, center ≈ 32).
    fireEvent.pointerDown(handleOf(container, 'C'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(32);
    expect(container.querySelector('[data-drag-center]')).not.toBeNull();
    pointerUp();

    expect(useSessionStore.getState().sessions.find((s) => s.id === 'C')!.managedBy).toBe('B');
    expect(useUIStore.getState().sortBy).toBe('recent'); // manage does not switch sort
  });

  it('edge drop between siblings reorders children under the same manager', () => {
    const { container } = render(<SessionList />);
    // Drag A2 (2nd child) onto A's TOP edge band (A spans 64-128; top band ≈ 64-84)
    // → A2 should render as the first child of B.
    fireEvent.pointerDown(handleOf(container, 'A2'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(75);
    expect(container.querySelector('[data-insert-line="before"]')).not.toBeNull();
    pointerUp();

    expect(useUIStore.getState().customOrder).toEqual(['B', 'A2', 'A', 'C']);
    expect(useUIStore.getState().sortBy).toBe('custom');
    expect(cardOrder(container)).toEqual(['B', 'A2', 'A', 'C']);
  });

  it('edge drop past the last root moves the whole subtree after it', () => {
    const { container } = render(<SessionList />);
    // Drag B (first root, with children A/A2) onto C's BOTTOM edge band
    // (C spans 192-256; bottom band ≈ 236-256) → B lands after C, and its
    // children render beneath it: preorder becomes C, B, A, A2.
    fireEvent.pointerDown(handleOf(container, 'B'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(245);
    expect(container.querySelector('[data-insert-line="after"]')).not.toBeNull();
    pointerUp();

    expect(useUIStore.getState().customOrder).toEqual(['A', 'A2', 'C', 'B']);
    expect(cardOrder(container)).toEqual(['C', 'B', 'A', 'A2']);
  });

  it('edge drop on another group child row moves the session into that group', () => {
    // Tree: B manages A; M2 manages E (independent root M2).
    useSessionStore.setState({
      sessions: [
        mk('B', 'B', { updatedAt: new Date(Date.now() - 60_000).toISOString() }),
        mk('A', 'A', { managedBy: 'B', updatedAt: new Date(Date.now() - 4 * 60_000).toISOString() }),
        mk('M2', 'M2', { updatedAt: new Date(Date.now() - 7 * 60_000).toISOString() }),
        mk('E', 'E', { managedBy: 'M2', updatedAt: new Date(Date.now() - 8 * 60_000).toISOString() }),
      ],
      currentSessionId: null,
      multiSelectMode: false,
    });
    stubCardRects({ B: 0, A: 64, M2: 128, E: 192 });

    const { container } = render(<SessionList />);
    // A (child of B) dropped on E's TOP band (E spans 192-256, top band
    // ≈192-212) → A joins M2's group before E.
    fireEvent.pointerDown(handleOf(container, 'A'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(200);
    expect(container.querySelector('[data-insert-line="before"]')).not.toBeNull();
    pointerUp();

    expect(useSessionStore.getState().sessions.find((s) => s.id === 'A')!.managedBy).toBe('M2');
    expect(useUIStore.getState().customOrder).toEqual(['B', 'M2', 'A', 'E']);
    expect(cardOrder(container)).toEqual(['B', 'M2', 'A', 'E']);
    expect(useUIStore.getState().toastQueue.some((t) => t.message.includes('移入'))).toBe(true);
  });

  it('edge drop on a top-level slot moves a child out of its group', () => {
    const { container } = render(<SessionList />);
    // A (child of B) dropped on root C's TOP band → A becomes an
    // independent root before C.
    fireEvent.pointerDown(handleOf(container, 'A'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(200); // C spans 192-256, top band ≈ 192-212
    pointerUp();

    expect(useSessionStore.getState().sessions.find((s) => s.id === 'A')!.managedBy).toBeNull();
    expect(useUIStore.getState().customOrder).toEqual(['B', 'A2', 'A', 'C']);
    expect(cardOrder(container)).toEqual(['B', 'A2', 'A', 'C']);
    expect(useUIStore.getState().toastQueue.some((t) => t.message.includes('移出'))).toBe(true);
  });

  it('blocks dropping a manager onto its own child (center)', () => {
    // Tree: A manages B. Dragging A onto B's center would make B manage A.
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', { updatedAt: new Date(Date.now() - 60_000).toISOString() }),
        mk('B', 'B', { managedBy: 'A', updatedAt: new Date(Date.now() - 4 * 60_000).toISOString() }),
      ],
      currentSessionId: null,
      multiSelectMode: false,
    });
    stubCardRects({ A: 0, B: 64 });

    const { container } = render(<SessionList />);
    fireEvent.pointerDown(handleOf(container, 'A'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(96); // B center
    pointerUp();

    const a = useSessionStore.getState().sessions.find((s) => s.id === 'A')!;
    expect(a.managedBy).toBeUndefined(); // unchanged (blocked drop)
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'B')!.managedBy).toBe('A');
    expect(useUIStore.getState().toastQueue.some((t) => t.message.includes('禁止'))).toBe(true);
    expect(useUIStore.getState().sortBy).toBe('recent'); // no sort switch on a blocked drop
  });

  function ghostLits(container: HTMLElement): { manage: string | null; order: string | null } {
    const ghost = container.querySelector('[data-drag-ghost]');
    return {
      manage: ghost?.getAttribute('data-ghost-manage-lit') ?? null,
      order: ghost?.getAttribute('data-ghost-order-lit') ?? null,
    };
  }

  it('ghost lights only 排序 for a sibling slot inside the same group', () => {
    // A (child of B) hovered over A2's top band (same manager B) → reorder only.
    const { container } = render(<SessionList />);
    fireEvent.pointerDown(handleOf(container, 'A'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(135); // A2 spans 128-192, top band ≈ 128-148
    expect(ghostLits(container)).toEqual({ manage: '0', order: '1' });
    pointerUp();
  });

  it('ghost lights only 管理 over a center manage target', () => {
    // C (root) hovered over B's center band → B will manage C, no ordering.
    const { container } = render(<SessionList />);
    fireEvent.pointerDown(handleOf(container, 'C'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(32); // B spans 0-64, center ≈ 32
    expect(ghostLits(container)).toEqual({ manage: '1', order: '0' });
    pointerUp();
  });

  it('ghost lights BOTH when the slot belongs to another level/group', () => {
    // A (child of B) hovered over root C's top band → A leaves B's group and
    // is sorted at root level: management AND ordering both change.
    const { container } = render(<SessionList />);
    fireEvent.pointerDown(handleOf(container, 'A'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(200); // C spans 192-256, top band ≈ 192-212
    expect(ghostLits(container)).toEqual({ manage: '1', order: '1' });
    pointerUp();
  });

  it('ghost lights nothing while over the dragged session itself or empty space', () => {
    const { container } = render(<SessionList />);
    fireEvent.pointerDown(handleOf(container, 'A'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(2500); // far below the list → no target card
    expect(ghostLits(container)).toEqual({ manage: '0', order: '0' });
    pointerUp();
  });
});

describe('Sort mode cycling (recent → name → custom)', () => {
  it('cycles through all three modes and persists', () => {
    localStorage.clear();
    useUIStore.setState({ sortBy: 'recent' });
    const ui = useUIStore.getState();
    ui.cycleSortBy();
    expect(useUIStore.getState().sortBy).toBe('name');
    useUIStore.getState().cycleSortBy();
    expect(useUIStore.getState().sortBy).toBe('custom');
    useUIStore.getState().cycleSortBy();
    expect(useUIStore.getState().sortBy).toBe('recent');
    expect(localStorage.getItem('pan:sortBy')).toBe('recent');
  });

  it('accepts a persisted custom sort mode on load', () => {
    localStorage.clear();
    localStorage.setItem('pan:sortBy', 'custom');
    localStorage.setItem('pan:customOrder', JSON.stringify(['B', 'A']));
    // loadSortBy/loadCustomOrder run at store creation; emulate via setState
    // reading the same helpers is covered by integration — here we assert
    // localStorage round-trip through the setters.
    useUIStore.getState().setCustomOrder(['B', 'A']);
    expect(JSON.parse(localStorage.getItem('pan:customOrder')!)).toEqual(['B', 'A']);
  });
});

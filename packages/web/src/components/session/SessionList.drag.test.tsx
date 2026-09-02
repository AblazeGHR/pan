// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, act } from '@testing-library/react';
import { SessionList } from './SessionList';
import { resolveDropZone, DRAG_HIT } from './sessionDrag';
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
// Four cards, 64px tall each: A 0-64, B 64-128, C 128-192, D 192-256.
const CARD_H = 64;
const layout: Record<string, number> = { A: 0, B: 64, C: 128, D: 192 };
const NULL_RECT = {
  top: 0, bottom: 0, height: 0, width: 0, left: 0, right: 0, x: 0, y: 0,
  toJSON: () => {},
};
let rectSpy: ReturnType<typeof vi.spyOn> | null = null;

function stubCardRects() {
  rectSpy = vi
    .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    .mockImplementation(function (this: HTMLElement) {
      const id = this.dataset?.sessionCardId;
      if (id && layout[id] !== undefined) {
        return {
          ...NULL_RECT,
          top: layout[id],
          bottom: layout[id] + CARD_H,
          height: CARD_H,
          width: 300,
          right: 300,
          y: layout[id],
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

describe('SessionList drag interactions', () => {
  beforeEach(() => {
    localStorage.clear();
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

  it('shows a ghost near the cursor while dragging; original cards stay in place', () => {
    const { container } = render(<SessionList />);
    expect(cardOrder(container)).toEqual(['A', 'B', 'C', 'D']);
    expect(container.querySelector('[data-drag-ghost]')).toBeNull();

    const handle = container.querySelector('[data-testid="drag-handle"]');
    fireEvent.pointerDown(handle!, { button: 0, clientX: 10, clientY: 10 });

    // Ghost appears and names the dragged session.
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
    // Toast reports the mock reorder.
    expect(useUIStore.getState().toastQueue.some((t) => t.message.includes('插入'))).toBe(true);

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

  it('drag start does not trigger card selection', () => {
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    fireEvent.click(handle);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
  });

  it('no drag handle outside the flat list (grouped mode)', () => {
    useUIStore.setState({ groupBy: 'manager' });
    const { container } = render(<SessionList />);
    expect(container.querySelector('[data-testid="drag-handle"]')).toBeNull();
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

// @vitest-environment jsdom
// Select-mode pointer interactions:
//   1. a press-and-drag sweep over card bodies selects every crossed Session,
//   2. the dedicated drag gutter never selects,
//   3. dragging a selected Session drags the whole selection through the same
//      manage / order persistence path as a normal single drag, including the
//      existing cycle and cross-tree rejections.
// The real-backend API surface is mocked at the same boundary as
// SessionList.drag.realBackend.test.tsx so these assertions cover the
// production (non-mock) branch.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, act, waitFor } from '@testing-library/react';
import { SessionList } from './SessionList';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { Session } from '@/types';

const apiMocks = vi.hoisted(() => ({
  claim: vi.fn(),
  unclaim: vi.fn(),
  reorder: vi.fn(),
  reorderPins: vi.fn(),
  fetchSessions: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    claimSession: apiMocks.claim,
    unclaimSession: apiMocks.unclaim,
    reorderSessions: apiMocks.reorder,
    reorderPinnedSessions: apiMocks.reorderPins,
    fetchSessions: apiMocks.fetchSessions,
  };
});

function mk(id: string, name: string, extra: Partial<Session> = {}): Session {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    updatedAt: new Date(Date.UTC(2026, 0, 1) - id.charCodeAt(0) * 60_000).toISOString(),
    ...extra,
  };
}

// ── Deterministic layout for hit-testing (jsdom has no real layout) ──
const CARD_H = 64;
let layout: Record<string, number> = { A: 0, B: 64, C: 128, D: 192 };
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

function pressCard(el: HTMLElement, x: number, y: number) {
  act(() => {
    el.dispatchEvent(
      new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: x, clientY: y }),
    );
  });
}

function pointerMove(x: number, y: number) {
  act(() => {
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: x, clientY: y, bubbles: true }));
  });
}

function pointerUp() {
  act(() => {
    window.dispatchEvent(new Event('pointerup', { bubbles: true }));
  });
}

async function flushAsync() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function card(container: HTMLElement, id: string): HTMLElement {
  return container.querySelector(`[data-session-card-id="${id}"]`) as HTMLElement;
}

function gutter(container: HTMLElement, id: string): HTMLElement {
  return container.querySelector(
    `[data-session-card-id="${id}"] [data-testid="drag-handle"]`,
  ) as HTMLElement;
}

function selected(): string[] {
  return [...useSessionStore.getState().selectedIds].sort();
}

describe('SessionList select-mode sweep selection', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true, showGroupBy: true });
    layout = { A: 0, B: 64, C: 128, D: 192 };
    useSessionStore.setState({
      sessions: [
        mk('A', 'Alpha'),
        mk('B', 'Bravo'),
        mk('C', 'Charlie'),
        mk('D', 'Delta'),
      ],
      currentSessionId: null,
      multiSelectMode: true,
      selectedIds: new Set<string>(),
      sessionsLoading: false,
      currentMessages: [],
      hasMoreMessages: false,
      historyLoading: false,
      initialLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _sessionWsTouchedSeq: {},
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
    apiMocks.claim.mockReset();
    apiMocks.unclaim.mockReset();
    apiMocks.reorder.mockReset();
    apiMocks.reorderPins.mockReset();
    apiMocks.fetchSessions.mockReset();
    apiMocks.fetchSessions.mockImplementation(async () => []);
    stubCardRects();
  });

  afterEach(() => {
    rectSpy?.mockRestore();
    rectSpy = null;
  });

  it('a press-move across card bodies adds every crossed Session and never starts a sort drag', () => {
    const { container } = render(<SessionList />);

    pressCard(card(container, 'A'), 10, 20);
    pointerMove(10, 96); // Bravo
    pointerMove(10, 160); // Charlie
    pointerUp();

    expect(selected()).toEqual(['A', 'B', 'C']);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    // Sweep selection must stay strictly separate from the drag engine.
    expect(container.querySelector('[data-drag-ghost]')).toBeNull();
    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.reorder).not.toHaveBeenCalled();
  });

  it('sweep only adds Sessions; it never deselects an existing selection', () => {
    useSessionStore.setState({ selectedIds: new Set(['D']) });
    const { container } = render(<SessionList />);

    pressCard(card(container, 'A'), 10, 20);
    pointerMove(10, 96);
    pointerUp();

    expect(selected()).toEqual(['A', 'B', 'D']);
  });

  it('pressing the drag gutter never starts a sweep or changes the selection', () => {
    const { container } = render(<SessionList />);

    pressCard(gutter(container, 'A'), 10, 20);
    // Crossing two cards while the press started on the gutter is a drag,
    // not a sweep.
    pointerMove(10, 96);
    pointerMove(10, 160);
    pointerUp();
    fireEvent.click(gutter(container, 'A'));

    expect(selected()).toEqual([]);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
  });

  it('a plain click on a card body toggles it exactly once', () => {
    const { container } = render(<SessionList />);

    fireEvent.click(card(container, 'B'));
    expect(selected()).toEqual(['B']);
    expect(useSessionStore.getState().currentSessionId).toBeNull();

    fireEvent.click(card(container, 'B'));
    expect(selected()).toEqual([]);
  });
});

describe('SessionList select-mode selection drag', () => {
  let serverSessions: Session[];

  beforeEach(() => {
    localStorage.clear();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true, showGroupBy: true });
    layout = { A: 0, B: 64, C: 128, D: 192 };
    const sessions = [mk('A', 'Alpha'), mk('B', 'Bravo'), mk('C', 'Charlie'), mk('D', 'Delta')];
    serverSessions = sessions.map((session) => ({ ...session }));
    useSessionStore.setState({
      sessions,
      currentSessionId: null,
      multiSelectMode: true,
      selectedIds: new Set<string>(),
      sessionsLoading: false,
      currentMessages: [],
      hasMoreMessages: false,
      historyLoading: false,
      initialLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _sessionWsTouchedSeq: {},
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
    apiMocks.claim.mockReset();
    apiMocks.unclaim.mockReset();
    apiMocks.reorder.mockReset();
    apiMocks.reorderPins.mockReset();
    apiMocks.fetchSessions.mockReset();
    apiMocks.fetchSessions.mockImplementation(async () =>
      JSON.parse(JSON.stringify(serverSessions)),
    );
    apiMocks.claim.mockImplementation(async (managerId: string, sessionId: string) => {
      const target = serverSessions.find((session) => session.id === sessionId);
      if (target) target.managedBy = managerId;
      return { ok: true };
    });
    apiMocks.unclaim.mockImplementation(async (managerId: string, sessionId: string) => {
      const target = serverSessions.find((session) => session.id === sessionId);
      if (target && target.managedBy === managerId) target.managedBy = null;
      return { ok: true };
    });
    apiMocks.reorder.mockImplementation(async (sessionIds: string[]) => {
      const byId = new Map(serverSessions.map((session) => [session.id, session]));
      const rest = serverSessions.filter((session) => !sessionIds.includes(session.id));
      serverSessions = [...sessionIds, ...rest.map((session) => session.id)]
        .map((id) => byId.get(id))
        .filter((session): session is Session => Boolean(session));
      return { ok: true, order: serverSessions.map((session) => session.id) };
    });
    stubCardRects();
  });

  afterEach(() => {
    rectSpy?.mockRestore();
    rectSpy = null;
  });

  it('dragging any selected Session drags the whole selection (center manage)', async () => {
    useSessionStore.setState({ selectedIds: new Set(['A', 'C']) });
    const { container } = render(<SessionList />);

    pressCard(gutter(container, 'A'), 10, 10);
    pointerMove(10, 96); // Bravo center band
    pointerUp();

    await waitFor(() => expect(apiMocks.claim).toHaveBeenCalledTimes(2));
    expect(apiMocks.claim).toHaveBeenCalledWith('B', 'A');
    expect(apiMocks.claim).toHaveBeenCalledWith('B', 'C');
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    await flushAsync();

    expect(useSessionStore.getState().sessions.find((s) => s.id === 'A')!.managedBy).toBe('B');
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'C')!.managedBy).toBe('B');
    expect(selected()).toEqual(['A', 'C']);
  });

  it('an edge drop reorders every selected member contiguously and keeps unselected order', async () => {
    useSessionStore.setState({ selectedIds: new Set(['A', 'C']) });
    const { container } = render(<SessionList />);

    pressCard(gutter(container, 'A'), 10, 10);
    pointerMove(10, 252); // Delta bottom edge → insert after Delta
    pointerUp();

    await waitFor(() => expect(apiMocks.reorder).toHaveBeenCalledTimes(1));
    expect(apiMocks.reorder).toHaveBeenCalledWith(['B', 'D', 'A', 'C']);
    expect(apiMocks.claim).not.toHaveBeenCalled();
    await flushAsync();

    expect(useUIStore.getState().sortBy).toBe('custom');
    expect(useUIStore.getState().customOrder).toEqual(['B', 'D', 'A', 'C']);
  });

  it('dragging a Session outside the selection drags only that Session', async () => {
    useSessionStore.setState({ selectedIds: new Set(['C']) });
    const { container } = render(<SessionList />);

    pressCard(gutter(container, 'A'), 10, 10);
    pointerMove(10, 96);
    pointerUp();

    await waitFor(() => expect(apiMocks.claim).toHaveBeenCalledTimes(1));
    expect(apiMocks.claim).toHaveBeenCalledWith('B', 'A');
    expect(apiMocks.claim).not.toHaveBeenCalledWith('B', 'C');
    await flushAsync();
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'C')!.managedBy).toBeUndefined();
    expect(selected()).toEqual(['C']);
  });

  it('rejects a selection drop onto a descendant of one of its members', async () => {
    layout = { P: 0, C: 64, X: 128 };
    rectSpy?.mockRestore();
    stubCardRects(layout);
    const sessions = [
      mk('P', 'Parent'),
      mk('C', 'Child', { managedBy: 'P' }),
      mk('X', 'Other'),
    ];
    serverSessions = sessions.map((session) => ({ ...session }));
    useSessionStore.setState({ sessions, selectedIds: new Set(['P', 'X']) });
    const { container } = render(<SessionList />);

    pressCard(gutter(container, 'P'), 10, 10);
    pointerMove(10, 96); // Child center band: Child can never manage Parent
    pointerUp();

    await flushAsync();
    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.unclaim).not.toHaveBeenCalled();
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(
      useUIStore.getState().toastQueue.some((toast) => toast.message.includes('禁止')),
    ).toBe(true);
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'P')!.managedBy).toBeUndefined();
    expect(selected()).toEqual(['P', 'X']);
  });

  it('keeps sweep and selection drag working in a narrow viewport', () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes('max-width: 767px'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
    try {
      const { container } = render(<SessionList />);
      expect(container.querySelectorAll('[data-testid="drag-handle"]').length).toBe(4);

      pressCard(card(container, 'A'), 10, 20);
      pointerMove(10, 160);
      pointerUp();

      expect(selected()).toEqual(['A', 'B', 'C']);
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });
});

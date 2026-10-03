// @vitest-environment jsdom
// Real-backend (non-mock) drag integration tests.
//
// These verify the fix for the missing front↔back wiring: in real mode a drag
// drop must call the real HTTP APIs — POST /api/claim + /api/unclaim for
// management changes and POST /api/sessions/order for reorders — and must NOT
// pretend success when the server rejects. The mock/no-backend demo branch is
// covered by SessionList.drag.test.tsx.
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
    updatedAt: new Date(Date.now() - 1000 * 60 * Math.random()).toISOString(),
    ...extra,
  };
}

// ── Deterministic layout for hit-testing (jsdom has no real layout) ──
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

function pointerMove(y: number) {
  act(() => {
    window.dispatchEvent(new MouseEvent('pointermove', { clientY: y, bubbles: true }));
  });
}

function pointerUp() {
  act(() => {
    window.dispatchEvent(new Event('pointerup', { bubbles: true }));
  });
}

/** Let the async real-drop persistence (API + refresh) settle. */
async function flushAsync() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe('SessionList drag → real backend APIs (no ?mock=1)', () => {
  // Fake server snapshot: claim/reorder mocks mutate it so the follow-up
  // loadSessions() refresh (GET /api/sessions) returns the persisted state —
  // mirroring the real backend contract.
  let serverSessions: Session[];

  const seed = () => [
    mk('A', 'Alpha', { updatedAt: new Date(Date.now() - 60_000).toISOString() }),
    mk('B', 'Bravo', { updatedAt: new Date(Date.now() - 120_000).toISOString() }),
    mk('C', 'Charlie', { updatedAt: new Date(Date.now() - 180_000).toISOString() }),
    mk('D', 'Delta', { updatedAt: new Date(Date.now() - 240_000).toISOString() }),
  ];

  beforeEach(() => {
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true, showGroupBy: true });
    localStorage.clear(); // no pan:mockDemo → real mode
    const sessions = seed();
    serverSessions = sessions.map((s) => ({ ...s }));

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
    useSessionStore.setState({
      sessions: sessions.map((s) => ({ ...s })),
      currentSessionId: null,
      multiSelectMode: false,
      sessionsLoading: false,
      currentMessages: [],
      hasMoreMessages: false,
      historyLoading: false,
      initialLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _sessionWsTouchedSeq: {},
    });

    apiMocks.claim.mockReset();
    apiMocks.unclaim.mockReset();
    apiMocks.reorder.mockReset();
    apiMocks.reorderPins.mockReset();
    apiMocks.fetchSessions.mockReset();

    apiMocks.fetchSessions.mockImplementation(async () =>
      JSON.parse(JSON.stringify(serverSessions)),
    );
    // Simulate the server semantics of POST /api/claim: bidirectional persist.
    apiMocks.claim.mockImplementation(async (managerId: string, sessionId: string) => {
      const target = serverSessions.find((s) => s.id === sessionId);
      const manager = serverSessions.find((s) => s.id === managerId);
      if (target) target.managedBy = managerId;
      if (manager) {
        const managed = manager.managed ? [...manager.managed] : [];
        if (!managed.includes(sessionId)) managed.push(sessionId);
        manager.managed = managed;
      }
      return { ok: true };
    });
    apiMocks.unclaim.mockImplementation(async (managerId: string, sessionId: string) => {
      const target = serverSessions.find((s) => s.id === sessionId);
      const manager = serverSessions.find((s) => s.id === managerId);
      if (target && target.managedBy === managerId) target.managedBy = null;
      if (manager) {
        manager.managed = (manager.managed ?? []).filter((id) => id !== sessionId);
      }
      return { ok: true };
    });
    // Simulate POST /api/sessions/order (apply_order): reorder + dense order.
    apiMocks.reorder.mockImplementation(async (sessionIds: string[]) => {
      const byId = new Map(serverSessions.map((s) => [s.id, s]));
      const rest = serverSessions.filter((s) => !sessionIds.includes(s.id));
      serverSessions = [...sessionIds, ...rest.map((s) => s.id)]
        .map((id) => byId.get(id))
        .filter((s): s is Session => Boolean(s))
        .map((s, i) => ({ ...s, order: i }));
      return { ok: true, order: serverSessions.map((s) => s.id) };
    });
    apiMocks.reorderPins.mockImplementation(async (sessionIds: string[], pinRevision: number) => {
      const current = serverSessions
        .filter((session) => session.pinned)
        .sort((a, b) => (a.pinOrder ?? Number.MAX_SAFE_INTEGER) - (b.pinOrder ?? Number.MAX_SAFE_INTEGER))
        .map((session) => session.id);
      if (serverSessions.some((session) => (session.pinRevision ?? 0) !== pinRevision)) {
        throw new Error('Pinned Session state changed; refresh and retry');
      }
      const selected = new Set(sessionIds);
      const slots = current.flatMap((id, index) => selected.has(id) ? [index] : []);
      const ordered = current.slice();
      slots.forEach((slot, index) => { ordered[slot] = sessionIds[index]!; });
      const nextRevision = pinRevision + 1;
      for (const session of serverSessions) {
        const pinOrder = ordered.indexOf(session.id);
        session.pinned = pinOrder >= 0;
        session.pinOrder = pinOrder >= 0 ? pinOrder : null;
        session.pinRevision = nextRevision;
      }
      return { ok: true, pinRevision: nextRevision, sessionIds: ordered };
    });

    stubCardRects();
  });

  afterEach(() => {
    rectSpy?.mockRestore();
    rectSpy = null;
  });

  it('center drop calls POST /api/claim (B manage A) and applies managedBy only after success', async () => {
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(96); // A over B's center band
    pointerUp();

    await waitFor(() => expect(apiMocks.claim).toHaveBeenCalledWith('B', 'A'));
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    // Reconcile refresh follows the server response.
    await flushAsync();

    expect(useSessionStore.getState().sessions.find((s) => s.id === 'A')!.managedBy).toBe('B');
    // A manage-only drop does NOT switch the sort mode.
    expect(useUIStore.getState().sortBy).toBe('recent');
    expect(useUIStore.getState().customOrder).toEqual([]);
  });

  it('center drop where the target already manages A is a silent no-op (no API call)', async () => {
    useSessionStore.setState({
      sessions: [
        mk('A', 'Alpha', { managedBy: 'B', updatedAt: '2026-10-03T00:00:00.000Z' }),
        mk('B', 'Bravo', { managed: ['A'], updatedAt: '2026-10-03T00:00:00.000Z' }),
        mk('C', 'Charlie', { updatedAt: '2026-10-03T00:00:00.000Z' }),
        mk('D', 'Delta', { updatedAt: '2026-10-03T00:00:00.000Z' }),
      ],
    });
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(96); // A center onto B — but B already manages A
    pointerUp();
    await flushAsync();

    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.unclaim).not.toHaveBeenCalled();
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(useUIStore.getState().toastQueue.length).toBe(0);
  });

  it('moving a session from C onto B center transfers management: unclaim(C,A) then claim(B,A)', async () => {
    useSessionStore.setState({
      sessions: [
        mk('A', 'Alpha', { managedBy: 'C', updatedAt: new Date().toISOString() }),
        mk('B', 'Bravo', { updatedAt: new Date().toISOString() }),
        mk('C', 'Charlie', { managed: ['A'], updatedAt: new Date().toISOString() }),
        mk('D', 'Delta', { updatedAt: new Date().toISOString() }),
      ],
    });
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-session-card-id="A"] [data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(96); // A onto B's center
    pointerUp();

    await waitFor(() => expect(apiMocks.unclaim).toHaveBeenCalledWith('C', 'A'));
    await waitFor(() => expect(apiMocks.claim).toHaveBeenCalledWith('B', 'A'));
    await flushAsync();

    expect(useSessionStore.getState().sessions.find((s) => s.id === 'A')!.managedBy).toBe('B');
  });

  it('cancelling a cross-workspace reparent sends no relationship request', async () => {
    useSessionStore.setState({ sessions: [
      mk('A', 'Alpha', { workspaceIds: ['workspace-a'], updatedAt: new Date().toISOString() }),
      mk('B', 'Bravo', { workspaceIds: ['workspace-b'], updatedAt: new Date().toISOString() }),
      mk('C', 'Charlie', { workspaceIds: ['workspace-a'] }),
      mk('D', 'Delta', { workspaceIds: ['workspace-b'] }),
    ] });
    const confirmation = new Promise<void>((resolve) => {
      window.addEventListener('pan:confirm-workspace-manager-change', ((event: Event) => {
        const detail = (event as CustomEvent<{ changeType: string; sessionName: string; subtreeCount: number; resolve: (accepted: boolean) => void }>).detail;
        expect(detail.changeType).toBe('attach');
        expect(detail.sessionName).toBe('Alpha');
        expect(detail.subtreeCount).toBe(1);
        detail.resolve(false);
        resolve();
      }) as EventListener, { once: true });
    });
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(96);
    pointerUp();

    await confirmation;
    await flushAsync();
    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.unclaim).not.toHaveBeenCalled();
  });

  it('reparents across workspaces without a prompt when confirmation is disabled', async () => {
    useAppSettingsStore.setState({
      notifications: { codexWarningToast: true, confirmCrossWorkspaceManagement: false, confirmAgentSystemQueueEdit: true },
    });
    useSessionStore.setState({ sessions: [
      mk('A', 'Alpha', { workspaceIds: ['workspace-a'], updatedAt: new Date().toISOString() }),
      mk('B', 'Bravo', { workspaceIds: ['workspace-b'], updatedAt: new Date().toISOString() }),
      mk('C', 'Charlie', { workspaceIds: ['workspace-a'] }),
      mk('D', 'Delta', { workspaceIds: ['workspace-b'] }),
    ] });
    const prompt = vi.fn();
    window.addEventListener('pan:confirm-workspace-manager-change', prompt);
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(96);
    pointerUp();

    await waitFor(() => expect(apiMocks.claim).toHaveBeenCalledWith('B', 'A'));
    window.removeEventListener('pan:confirm-workspace-manager-change', prompt);
    expect(prompt).not.toHaveBeenCalled();
  });

  it('edge drop calls POST /api/sessions/order with the full new order and adopts custom sort', async () => {
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(120); // A over B's bottom edge band (insert after B)
    pointerUp();

    await waitFor(() =>
      expect(apiMocks.reorder).toHaveBeenCalledWith(['B', 'A', 'C', 'D']),
    );
    expect(apiMocks.claim).not.toHaveBeenCalled();
    await flushAsync();

    expect(useUIStore.getState().sortBy).toBe('custom');
    expect(useUIStore.getState().customOrder).toEqual(['B', 'A', 'C', 'D']);
    // The refreshed list (server snapshot) reflects the new order.
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual(['B', 'A', 'C', 'D']);
  });

  it('does NOT fake success when the order endpoint fails', async () => {
    apiMocks.reorder.mockRejectedValue(new Error('boom'));
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(120); // A after B
    pointerUp();

    await waitFor(() =>
      expect(useUIStore.getState().toastQueue.some((t) => t.type === 'error')).toBe(true),
    );
    // No fake success: sort stays 'recent' and no custom order was recorded.
    expect(useUIStore.getState().sortBy).toBe('recent');
    expect(useUIStore.getState().customOrder).toEqual([]);
  });

  it('does NOT fake success when the claim endpoint rejects', async () => {
    apiMocks.claim.mockRejectedValue(new Error('Session A is managed by C, not B'));
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(96); // A onto B's center
    pointerUp();

    await waitFor(() =>
      expect(useUIStore.getState().toastQueue.some((t) => t.type === 'error')).toBe(true),
    );
    // managedBy untouched — the UI does not claim a state the server refused.
    expect(
      useSessionStore.getState().sessions.find((s) => s.id === 'A')!.managedBy,
    ).toBeUndefined();
  });

  it('blocks a management cycle in real mode without touching the server', async () => {
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', { updatedAt: new Date(Date.now() - 60_000).toISOString() }),
        mk('B', 'B', { managedBy: 'A', updatedAt: new Date(Date.now() - 120_000).toISOString() }),
      ],
    });
    stubCardRects({ A: 0, B: 64 });
    const { container } = render(<SessionList />);
    const handleOf = (id: string) =>
      container.querySelector(`[data-session-card-id="${id}"]`)!.querySelector('[data-testid="drag-handle"]')!;
    // Drag A (manager of B) onto B's center → B would manage its own manager.
    fireEvent.pointerDown(handleOf('A'), { button: 0, clientX: 5, clientY: 5 });
    pointerMove(96); // B center
    pointerUp();

    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(
      useUIStore.getState().toastQueue.some((t) => t.message.includes('禁止')),
    ).toBe(true);
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'A')!.managedBy).toBeUndefined();
  });

  it.each([150, 180, 210, 250])(
    'pinned flat drag at y=%i uses only the pin endpoint and clamps over/beyond unpinned rows',
    async (dropY) => {
    const sessions = [
      mk('A', 'Alpha', { pinned: true, pinOrder: 0, pinRevision: 4, order: 17, managedBy: 'manager-a', workspaceIds: ['workspace-a'] }),
      mk('B', 'Bravo', { pinned: true, pinOrder: 1, pinRevision: 4, order: 29 }),
      mk('C', 'Charlie', { pinRevision: 4, order: 31, managedBy: 'manager-b', workspaceIds: ['workspace-b'] }),
      mk('D', 'Delta', { pinRevision: 4, order: 43 }),
    ];
    serverSessions = sessions.map((session) => ({ ...session }));
    useSessionStore.setState({ sessions: sessions.map((session) => ({ ...session })) });
    useUIStore.setState({ sortBy: 'name', customOrder: ['D', 'C', 'B', 'A'] });
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-session-card-id="A"] [data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(dropY); // On, below, and beyond the first unpinned card clamp to the pin-region end.
    pointerUp();

    await waitFor(() => expect(apiMocks.reorderPins).toHaveBeenCalledWith(['B', 'A'], 4));
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.unclaim).not.toHaveBeenCalled();
    expect(useUIStore.getState().sortBy).toBe('name');
    expect(useUIStore.getState().customOrder).toEqual(['D', 'C', 'B', 'A']);
    expect(useSessionStore.getState().sessions.map((session) => session.order)).toEqual([17, 29, 31, 43]);
    expect(useSessionStore.getState().sessions.map((session) => [session.managedBy, session.workspaceIds]))
      .toEqual([['manager-a', ['workspace-a']], [undefined, undefined], ['manager-b', ['workspace-b']], [undefined, undefined]]);
    expect(useSessionStore.getState().sessions.map((session) => session.id))
      .toEqual(['A', 'B', 'C', 'D']);
    expect(useSessionStore.getState().sessions.find((session) => session.id === 'A')?.pinOrder).toBe(1);
    },
  );

  it('filtered pinned reorder preserves the hidden pin slot and its relative order', async () => {
    const sessions = [
      mk('A', 'Needle Alpha', { pinned: true, pinOrder: 0, pinRevision: 6 }),
      mk('B', 'Hidden Bravo', { pinned: true, pinOrder: 1, pinRevision: 6 }),
      mk('C', 'Needle Charlie', { pinned: true, pinOrder: 2, pinRevision: 6 }),
      mk('D', 'Needle Delta', { pinRevision: 6 }),
    ];
    serverSessions = sessions.map((session) => ({ ...session }));
    useSessionStore.setState({ sessions: sessions.map((session) => ({ ...session })) });
    useUIStore.setState({ searchQuery: 'Needle', sortBy: 'recent', customOrder: [] });
    const { container } = render(<SessionList />);
    expect([...container.querySelectorAll<HTMLElement>('[data-session-card-id]')]
      .map((card) => card.dataset.sessionCardId)).toEqual(['A', 'C', 'D']);
    const handle = container.querySelector('[data-session-card-id="A"] [data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
    pointerMove(180); // Drop below C; hidden pin B stays at its existing server slot.
    pointerUp();

    await waitFor(() => expect(apiMocks.reorderPins).toHaveBeenCalledWith(['C', 'A'], 6));
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(serverSessions.filter((session) => session.pinned)
      .sort((a, b) => (a.pinOrder ?? 99) - (b.pinOrder ?? 99))
      .map((session) => session.id)).toEqual(['C', 'B', 'A']);
    expect(useSessionStore.getState().sessions.find((session) => session.id === 'B')?.pinOrder).toBe(1);
  });

  it('manager pin drag is sibling-only and rejects a cross-parent drop without relationship calls', async () => {
    const sessions = [
      mk('P', 'Parent', { pinRevision: 2, updatedAt: '2026-01-05T00:00:00Z' }),
      mk('A', 'Alpha', { pinned: true, pinOrder: 0, pinRevision: 2, managedBy: 'P', order: 12, updatedAt: '2026-01-04T00:00:00Z' }),
      mk('B', 'Bravo', { pinned: true, pinOrder: 1, pinRevision: 2, managedBy: 'P', order: 13, updatedAt: '2026-01-03T00:00:00Z' }),
      mk('C', 'Charlie', { pinRevision: 2, order: 14, updatedAt: '2026-01-02T00:00:00Z' }),
    ];
    serverSessions = sessions.map((session) => ({ ...session }));
    useSessionStore.setState({ sessions: sessions.map((session) => ({ ...session })) });
    useUIStore.setState({ groupBy: 'manager', sortBy: 'recent', customOrder: [] });
    stubCardRects({ P: 0, A: 64, B: 128, C: 192 });
    const { container } = render(<SessionList />);
    const handle = container.querySelector('[data-session-card-id="B"] [data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 130 });
    pointerMove(80); // Before sibling A; never the manager center action.
    pointerUp();

    await waitFor(() => expect(apiMocks.reorderPins).toHaveBeenCalledWith(['B', 'A'], 2));
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.unclaim).not.toHaveBeenCalled();
    expect(useSessionStore.getState().sessions.find((session) => session.id === 'A')?.managedBy).toBe('P');
    expect(useSessionStore.getState().sessions.find((session) => session.id === 'B')?.managedBy).toBe('P');

    apiMocks.reorderPins.mockClear();
    useUIStore.setState({ toastQueue: [] });
    const secondHandle = container.querySelector('[data-session-card-id="A"] [data-testid="drag-handle"]')!;
    fireEvent.pointerDown(secondHandle, { button: 0, clientX: 10, clientY: 70 });
    pointerMove(210); // Root C is a different sibling group from A.
    pointerUp();
    await flushAsync();

    expect(apiMocks.reorderPins).not.toHaveBeenCalled();
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.unclaim).not.toHaveBeenCalled();
    expect(useSessionStore.getState().sessions.find((session) => session.id === 'A')?.managedBy).toBe('P');
    expect(useUIStore.getState().toastQueue.some((toast) => toast.type === 'error')).toBe(true);
  });

  it('workdir sections allow pinned reorder but reject a cross-workdir drop', async () => {
    const sessions = [
      mk('A', 'Alpha', { pinned: true, pinOrder: 0, pinRevision: 3, order: 5, workdir: 'D:/repo/a' }),
      mk('B', 'Bravo', { pinned: true, pinOrder: 1, pinRevision: 3, order: 6, workdir: 'D:/repo/a' }),
      mk('C', 'Charlie', { pinRevision: 3, order: 7, workdir: 'D:/repo/c' }),
    ];
    serverSessions = sessions.map((session) => ({ ...session }));
    useSessionStore.setState({ sessions: sessions.map((session) => ({ ...session })) });
    useUIStore.setState({ groupBy: 'workdir', sortBy: 'recent', customOrder: [] });
    stubCardRects({ A: 0, B: 64, C: 128 });
    const { container } = render(<SessionList />);
    const handles = [...container.querySelectorAll<HTMLElement>('[data-testid="drag-handle"]')];
    expect(handles).toHaveLength(2);
    const handleB = container.querySelector('[data-session-card-id="B"] [data-testid="drag-handle"]')!;
    fireEvent.pointerDown(handleB, { button: 0, clientX: 10, clientY: 70 });
    pointerMove(20); // Before A within the same workdir section.
    pointerUp();

    await waitFor(() => expect(apiMocks.reorderPins).toHaveBeenCalledWith(['B', 'A'], 3));
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.unclaim).not.toHaveBeenCalled();
    expect(useSessionStore.getState().sessions.map((session) => session.order)).toEqual([5, 6, 7]);
    expect(useSessionStore.getState().sessions.map((session) => session.workdir)).toEqual(['D:/repo/a', 'D:/repo/a', 'D:/repo/c']);

    apiMocks.reorderPins.mockClear();
    useUIStore.setState({ toastQueue: [] });
    const crossHandle = container.querySelector('[data-session-card-id="A"] [data-testid="drag-handle"]')!;
    fireEvent.pointerDown(crossHandle, { button: 0, clientX: 10, clientY: 5 });
    pointerMove(150); // A pinned dragged over the other workdir's C.
    pointerUp();
    expect(apiMocks.reorderPins).not.toHaveBeenCalled();
    expect(apiMocks.reorder).not.toHaveBeenCalled();
    expect(apiMocks.claim).not.toHaveBeenCalled();
    expect(apiMocks.unclaim).not.toHaveBeenCalled();
    expect(useSessionStore.getState().sessions.map((session) => session.workdir)).toEqual(['D:/repo/a', 'D:/repo/a', 'D:/repo/c']);
    expect(useUIStore.getState().toastQueue.some((toast) => toast.type === 'error')).toBe(true);
  });
});

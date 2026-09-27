// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';
import type { Session } from '@/types';

const apiMocks = vi.hoisted(() => ({
  fetchSessions: vi.fn(),
  setSessionPinned: vi.fn(),
  reorderPinnedSessions: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchSessions: apiMocks.fetchSessions,
    setSessionPinned: apiMocks.setSessionPinned,
    reorderPinnedSessions: apiMocks.reorderPinnedSessions,
  };
});

import { useSessionStore } from './sessionStore';
import { useUIStore } from './uiStore';

function session(id: string, extra: Partial<Session> = {}): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    ...extra,
  };
}

function snapshot(pinRevision: number, sessionIds: string[]): Session[] {
  const order = new Map(sessionIds.map((id, index) => [id, index]));
  return ['A', 'B'].map((id) => {
    const pinOrder = order.get(id);
    return session(id, {
      pinned: pinOrder !== undefined,
      pinOrder: pinOrder ?? null,
      pinRevision,
    });
  });
}

beforeEach(() => {
  window.history.replaceState({}, '', '/');
  apiMocks.fetchSessions.mockReset();
  apiMocks.setSessionPinned.mockReset();
  apiMocks.reorderPinnedSessions.mockReset();
  useSessionStore.setState({
    sessions: [session('A', { pinRevision: 0 }), session('B', { pinRevision: 0 })],
    sessionsLoading: false,
    currentSessionId: null,
    _loadSeq: 0,
    _sessionWsTouchedSeq: {},
    _pinStateTouchedSeq: 0,
    _sessionEventPatches: {},
    _sessionSettingsTouchedSeq: {},
    _sessionLocalTouchedSeq: {},
  });
  useUIStore.setState({ sortBy: 'recent', customOrder: [] });
});

describe('SessionStore pinned state reconciliation', () => {
  it('ignores an older mutation response and reloads authoritative server state', async () => {
    let resolveFirst!: (value: { ok: true; pinRevision: number; sessionIds: string[] }) => void;
    let resolveSecond!: (value: { ok: true; pinRevision: number; sessionIds: string[] }) => void;
    apiMocks.setSessionPinned
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    const first = useSessionStore.getState().setSessionPinned('A', true);
    const second = useSessionStore.getState().setSessionPinned('A', false);

    await act(async () => {
      resolveSecond({ ok: true, pinRevision: 2, sessionIds: ['B'] });
      await second;
    });
    expect(useSessionStore.getState().sessions.find((item) => item.id === 'A')?.pinned).toBe(false);
    expect(useSessionStore.getState().sessions.find((item) => item.id === 'B')?.pinOrder).toBe(0);

    apiMocks.fetchSessions.mockResolvedValueOnce(snapshot(2, ['B']));
    await act(async () => {
      resolveFirst({ ok: true, pinRevision: 1, sessionIds: ['A'] });
      await first;
    });
    expect(useSessionStore.getState().sessions.map((item) => [item.id, item.pinned, item.pinOrder, item.pinRevision]))
      .toEqual([['A', false, null, 2], ['B', true, 0, 2]]);
    expect(apiMocks.fetchSessions).toHaveBeenCalledWith(true);
  });

  it('preserves a newer pin snapshot against a list request already in flight', async () => {
    let resolveFetch!: (sessions: Session[]) => void;
    apiMocks.fetchSessions.mockImplementationOnce(() => new Promise((resolve) => { resolveFetch = resolve; }));
    const load = useSessionStore.getState().loadSessions();

    await act(async () => {
      useSessionStore.getState().applyPinnedSnapshot({ pinRevision: 3, sessionIds: ['B'] });
      resolveFetch(snapshot(2, ['A']));
      await load;
    });

    expect(useSessionStore.getState().sessions.map((item) => [item.id, item.pinned, item.pinOrder, item.pinRevision]))
      .toEqual([['A', false, null, 3], ['B', true, 0, 3]]);
  });

  it('reloads the authoritative state after a failed pin request', async () => {
    apiMocks.setSessionPinned.mockRejectedValueOnce(new Error('write failed'));
    apiMocks.fetchSessions.mockResolvedValueOnce(snapshot(5, ['B']));

    await expect(useSessionStore.getState().setSessionPinned('A', true)).rejects.toThrow('write failed');

    expect(useSessionStore.getState().sessions.map((item) => [item.id, item.pinned, item.pinOrder, item.pinRevision]))
      .toEqual([['A', false, null, 5], ['B', true, 0, 5]]);
    expect(apiMocks.fetchSessions).toHaveBeenCalledWith(true);
  });
});

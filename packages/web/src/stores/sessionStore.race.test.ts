// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import type { Session } from '@/types';

function mk(id: string, name: string, managedBy?: string | null): Session {
  return {
    id,
    name,
    managedBy,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
  };
}

// Deferred createSession so a test can interleave a loadSessions() while the
// create call is still in flight — the exact race that previously dropped the
// new session until a page refresh.
let serverSessions: Session[] = [];
let resolveCreate: (() => void) | null = null;

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchSessions: vi.fn(async () => serverSessions),
    createSession: vi.fn(
      (name: string) =>
        new Promise<Session>((resolve) => {
          resolveCreate = () =>
            resolve({
              id: `real_${name}`,
              name,
              adapter: 'cbc',
              model: null,
              permissionMode: null,
              alwaysThinkingEnabled: false,
              effort: '',
              history: [],
            } as Session);
        }),
    ),
  };
});

describe('sessionStore createNewSession race', () => {
  beforeEach(() => {
    serverSessions = [mk('M', 'M'), mk('A', 'A', 'M')];
    resolveCreate = null;
    useSessionStore.setState({
      sessions: [mk('M', 'M'), mk('A', 'A', 'M')],
      currentSessionId: null,
      currentMessages: [],
    });
  });

  it('keeps the new session when loadSessions interleaves during creation', async () => {
    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().createNewSession('X');
    });
    // Placeholder present while create is pending.
    expect(
      useSessionStore.getState().sessions.some((s) => s.id === '__pending_X'),
    ).toBe(true);

    // Concurrent loadSessions (server hasn't committed X yet) overwrites the
    // list and wipes the client-only placeholder.
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });
    expect(
      useSessionStore.getState().sessions.some((s) => s.id === '__pending_X'),
    ).toBe(false);

    // Create finally resolves — the real session must still be inserted.
    await act(async () => {
      resolveCreate?.();
      await promise!;
    });

    const ids = useSessionStore.getState().sessions.map((s) => s.id);
    expect(ids).toContain('real_X');
    expect(useSessionStore.getState().currentSessionId).toBe('real_X');
  });

  it('does not duplicate a session a concurrent loadSessions already returned', async () => {
    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().createNewSession('X');
    });
    // Server commits X while the create call is still in flight; a reload
    // brings it in.
    serverSessions = [
      mk('M', 'M'),
      mk('A', 'A', 'M'),
      mk('real_X', 'X', 'M'),
    ];
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });
    expect(
      useSessionStore.getState().sessions.filter((s) => s.id === 'real_X').length,
    ).toBe(1);

    await act(async () => {
      resolveCreate?.();
      await promise!;
    });

    const ids = useSessionStore.getState().sessions.map((s) => s.id);
    expect(ids.filter((id) => id === 'real_X').length).toBe(1);
    expect(useSessionStore.getState().currentSessionId).toBe('real_X');
  });
});

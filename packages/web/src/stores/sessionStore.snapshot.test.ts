// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore } from './sessionStore';
import type { Session } from '@/types';

const api = vi.hoisted(() => ({ fetchSessions: vi.fn() }));

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchSessions: api.fetchSessions,
}));

function mk(id: string, extra: Partial<Session> = {}): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    historyTotal: 0,
    workerStatus: null,
    workerId: null,
    ...extra,
  };
}

describe('session summary reconciliation', () => {
  beforeEach(() => {
    api.fetchSessions.mockReset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      _loadSeq: 0,
      _sessionWsTouchedSeq: {},
      _sessionLocalTouchedSeq: {},
      _deliveredQueueIds: {},
    });
  });

  it('reuses unchanged Session objects across summary refreshes', async () => {
    const first = mk('s1', { name: 'stable', lastMessage: 'same', historyTotal: 3 });
    useSessionStore.setState({ sessions: [first] });
    api.fetchSessions.mockResolvedValueOnce([mk('s1', {
      name: 'stable', lastMessage: 'same', historyTotal: 3,
    })]);

    await useSessionStore.getState().loadSessions();

    expect(useSessionStore.getState().sessions[0]).toBe(first);
  });

  it('does not let an older summary erase a local completion during the fetch', async () => {
    const first = mk('s1', { historyTotal: 1, lastMessage: 'old' });
    let resolveSnapshot!: (sessions: Session[]) => void;
    api.fetchSessions.mockReturnValueOnce(new Promise<Session[]>((resolve) => {
      resolveSnapshot = resolve;
    }));
    useSessionStore.setState({ sessions: [first] });

    const refresh = useSessionStore.getState().loadSessions();
    useSessionStore.getState().applyResultToSession('s1', {
      status: 'done', result: 'new result',
    });
    resolveSnapshot([mk('s1', { historyTotal: 1, lastMessage: 'old' })]);
    await refresh;

    const updated = useSessionStore.getState().sessions[0];
    expect(updated?.historyTotal).toBe(2);
    expect(updated?.lastMessage).toBe('new result');
  });
});

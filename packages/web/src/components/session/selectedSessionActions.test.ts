// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore } from '@/stores/sessionStore';
import {
  describeActionResults,
  runSelectedSessionAction,
  snapshotSelectedSessions,
} from '@/utils/selectedSessionActions';
import { ackSessionUnreadDone, setSessionMsgBridge } from '@/services/api';

vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  ackSessionUnreadDone: vi.fn(),
  setSessionMsgBridge: vi.fn(),
}));

describe('selected Session actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSessionStore.setState({
      sessions: [
        {
          id: 'a',
          name: 'A',
          unreadDoneGeneration: 4,
          unreadDoneReadGeneration: 1,
          unreadDoneCount: 3,
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
        },
        {
          id: 'b',
          name: 'B',
          unreadDoneGeneration: 2,
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
        },
      ],
    });
  });
  it('keeps click-time targets and observed generations, preserving later unread completions', async () => {
    const targets = snapshotSelectedSessions(new Set(['a']), useSessionStore.getState().sessions);
    useSessionStore.getState().updateSession('a', { unreadDoneGeneration: 5, unreadDoneCount: 4 });
    vi.mocked(ackSessionUnreadDone).mockResolvedValue({
      ok: true,
      unreadDoneGeneration: 5,
      unreadDoneReadGeneration: 4,
      unreadDoneCount: 1,
    });
    const result = await runSelectedSessionAction(targets, 'read');
    expect(ackSessionUnreadDone).toHaveBeenCalledTimes(1);
    expect(ackSessionUnreadDone).toHaveBeenCalledWith('a', 4);
    expect(result[0]?.error).toBeUndefined();
    expect(useSessionStore.getState().sessions[0]?.unreadDoneCount).toBe(1);
  });
  it('does not clear the badge on failure and reports target-level partial success', async () => {
    vi.mocked(ackSessionUnreadDone)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({
        ok: true,
        unreadDoneGeneration: 2,
        unreadDoneReadGeneration: 2,
        unreadDoneCount: 0,
      });
    const result = await runSelectedSessionAction(
      snapshotSelectedSessions(new Set(['a', 'b']), useSessionStore.getState().sessions),
      'read',
    );
    expect(useSessionStore.getState().sessions[0]?.unreadDoneCount).toBe(3);
    expect(describeActionResults(result)).toBe('1 succeeded, 1 failed: A: offline');
  });
  it('uses the connection API for both Bell directions and retains validation failures', async () => {
    vi.mocked(setSessionMsgBridge).mockRejectedValueOnce(new Error('Default contact unavailable'));
    const targets = snapshotSelectedSessions(new Set(['a']), useSessionStore.getState().sessions);
    expect((await runSelectedSessionAction(targets, 'bell-on'))[0]?.error).toBe(
      'Default contact unavailable',
    );
    expect(setSessionMsgBridge).toHaveBeenCalledWith('a', true);
    useSessionStore.getState().updateSession('a', { msgBridgeEnabled: true });
    vi.mocked(setSessionMsgBridge).mockResolvedValue({ ok: true, msgBridgeEnabled: false });
    await runSelectedSessionAction(targets, 'bell-off');
    expect(setSessionMsgBridge).toHaveBeenLastCalledWith('a', false);
  });
});

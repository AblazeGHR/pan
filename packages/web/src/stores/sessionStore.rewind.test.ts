// @vitest-environment jsdom
// Coverage for the rewind store flow: WS progress application across
// CONCURRENT jobs, the draft-before-select ordering guarantee, the
// minimize (background-run) semantics, and the friendly error mapping for
// checkpoints without code changes.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { StreamEvent } from '@/types';

const rewindSessionHistoryMock = vi.fn();

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    rewindSessionHistory: (...args: unknown[]) => rewindSessionHistoryMock(...args),
  };
});

function rewindEvent(patch: Partial<StreamEvent>): StreamEvent {
  return { type: 'session.rewind.progress', jobId: 'j1', ...patch };
}

const ANCHOR = { role: 'user' as const, content: 'anchor text', messageId: 'msg_1' };

describe('sessionStore rewind', () => {
  const original = {
    setInputDraft: useSessionStore.getState().setInputDraft,
    loadSessions: useSessionStore.getState().loadSessions,
    selectSession: useSessionStore.getState().selectSession,
  };

  beforeEach(() => {
    rewindSessionHistoryMock.mockReset();
    useSessionStore.setState({
      activeRewinds: [],
      currentSessionId: 's1',
      currentMessages: [ANCHOR],
      setInputDraft: original.setInputDraft,
      loadSessions: original.loadSessions,
      selectSession: original.selectSession,
    });
    useUIStore.setState({ toastQueue: [], composerFocusToken: 0 });
  });

  it('starts a rewind job and tracks running progress', async () => {
    rewindSessionHistoryMock.mockResolvedValue({ ok: true, jobId: 'j1', stage: 'starting' });
    await useSessionStore.getState().rewindCurrentMessage(ANCHOR, 2);
    expect(rewindSessionHistoryMock).toHaveBeenCalledWith('s1', 'msg_1', 2);
    expect(useSessionStore.getState().activeRewinds[0]?.jobId).toBe('j1');

    useSessionStore.getState().applyRewindProgress(rewindEvent({ stage: 'rewinding-files' }));
    expect(useSessionStore.getState().activeRewinds[0]?.stage).toBe('rewinding-files');
    expect(useSessionStore.getState().activeRewinds[0]?.status).toBe('running');
  });

  it('adopts WS events that arrive BEFORE the POST resolves (first-click race)', async () => {
    // Bug 3: the backend broadcasts session.rewind.progress 'starting'
    // before the POST response returns, so the first click used to drop the
    // early stages (and the modal closed instantly on the empty state).
    let resolvePost!: (value: unknown) => void;
    rewindSessionHistoryMock.mockReturnValue(new Promise((resolve) => {
      resolvePost = resolve;
    }));
    const promise = useSessionStore.getState().rewindCurrentMessage(ANCHOR, 1);
    // A pending entry must exist synchronously after the call starts.
    const pending = useSessionStore.getState().activeRewinds[0];
    expect(pending?.status).toBe('running');
    expect(pending?.jobId).toBe('');

    // Early events arrive before the POST resolves.
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ sessionId: 's1', stage: 'starting' }),
    );
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ sessionId: 's1', stage: 'resuming' }),
    );
    expect(useSessionStore.getState().activeRewinds[0]?.stage).toBe('resuming');

    // Events for OTHER sessions must not be adopted by the pending job.
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ jobId: 'jX', sessionId: 'other', stage: 'truncating' }),
    );
    expect(useSessionStore.getState().activeRewinds[0]?.stage).toBe('resuming');

    resolvePost({ ok: true, jobId: 'j1', stage: 'starting' });
    await promise;
    const active = useSessionStore.getState().activeRewinds[0];
    expect(active?.jobId).toBe('j1');
    // The POST response must not regress the stage the events advanced to.
    expect(active?.stage).toBe('resuming');
  });

  it('clears the pending rewind when the POST fails synchronously', async () => {
    rewindSessionHistoryMock.mockRejectedValue(new Error('任务运行中，无法撤回'));
    await expect(
      useSessionStore.getState().rewindCurrentMessage(ANCHOR, 1),
    ).rejects.toThrow('任务运行中');
    expect(useSessionStore.getState().activeRewinds).toEqual([]);
  });

  it('rejects a SECOND rewind on the same session (one job per session)', async () => {
    rewindSessionHistoryMock.mockResolvedValue({ ok: true, jobId: 'j1', stage: 'starting' });
    await useSessionStore.getState().rewindCurrentMessage(ANCHOR, 1);
    await expect(
      useSessionStore.getState().rewindCurrentMessage(ANCHOR, 1),
    ).rejects.toThrow('该会话已有撤回进行中');
    // The rejection must not spawn a second entry or fire a second POST.
    expect(useSessionStore.getState().activeRewinds).toHaveLength(1);
    expect(rewindSessionHistoryMock).toHaveBeenCalledTimes(1);
  });

  it('tracks rewinds on DIFFERENT sessions side by side', () => {
    useSessionStore.setState({
      activeRewinds: [
        { jobId: 'j1', sessionId: 's1', anchorText: 'a', scope: 1, stage: 'starting', status: 'running' },
        { jobId: 'j2', sessionId: 's2', anchorText: 'b', scope: 2, stage: 'resuming', status: 'running' },
      ],
    });
    // Progress for s1's job must not touch s2's entry.
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ jobId: 'j1', sessionId: 's1', stage: 'truncating' }),
    );
    const rewinds = useSessionStore.getState().activeRewinds;
    expect(rewinds.find((item) => item.jobId === 'j1')?.stage).toBe('truncating');
    expect(rewinds.find((item) => item.jobId === 'j2')?.stage).toBe('resuming');
  });

  it('ignores progress events for unknown jobs', () => {
    useSessionStore.setState({
      activeRewinds: [{ jobId: 'j1', sessionId: 's1', anchorText: 'a', scope: 1, stage: 'starting', status: 'running' }],
    });
    useSessionStore.getState().applyRewindProgress(rewindEvent({ jobId: 'j2', sessionId: 's2', stage: 'failed', error: 'x' }));
    expect(useSessionStore.getState().activeRewinds[0]?.stage).toBe('starting');
  });

  it('minimizeRewind closes the popup but KEEPS the job running', async () => {
    rewindSessionHistoryMock.mockResolvedValue({ ok: true, jobId: 'j1', stage: 'starting' });
    useSessionStore.setState({ rewindTarget: ANCHOR });
    await useSessionStore.getState().rewindCurrentMessage(ANCHOR, 1);
    expect(useSessionStore.getState().rewindTarget).not.toBeNull();

    useSessionStore.getState().minimizeRewind('j1');
    // Popup anchor cleared (modal unmounts) — but the job entry survives.
    expect(useSessionStore.getState().rewindTarget).toBeNull();
    expect(useSessionStore.getState().activeRewinds).toHaveLength(1);
    expect(useSessionStore.getState().activeRewinds[0]?.status).toBe('running');

    // Events still land, and completion still performs the jump + prefill.
    const calls: string[] = [];
    useSessionStore.setState({
      setInputDraft: (id: string, draft: string) => { calls.push(`draft:${id}:${draft}`); },
      loadSessions: () => { calls.push('load'); return Promise.resolve(); },
      selectSession: (id: string) => { calls.push(`select:${id}`); return Promise.resolve(); },
    });
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ jobId: 'j1', sessionId: 's1', stage: 'truncating' }),
    );
    expect(useSessionStore.getState().activeRewinds[0]?.stage).toBe('truncating');
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ jobId: 'j1', sessionId: 's1', stage: 'completed', newSessionId: 's2' }),
    );
    expect(calls[0]).toBe('draft:s2:anchor text');
    await vi.waitFor(() => expect(calls).toContain('select:s2'));
    expect(useSessionStore.getState().activeRewinds[0]?.status).toBe('completed');
  });

  it('dismissRewind drops settled jobs but never a running one', () => {
    useSessionStore.setState({
      activeRewinds: [
        { jobId: 'j1', sessionId: 's1', anchorText: 'a', scope: 1, stage: 'starting', status: 'running' },
        { jobId: 'j2', sessionId: 's2', anchorText: 'b', scope: 1, stage: 'failed', status: 'failed', error: 'x' },
      ],
    });
    useSessionStore.getState().dismissRewind();
    const rewinds = useSessionStore.getState().activeRewinds;
    expect(rewinds).toHaveLength(1);
    expect(rewinds[0]?.jobId).toBe('j1');
  });

  it('writes the draft BEFORE selecting the new session on completion', async () => {
    const calls: string[] = [];
    useSessionStore.setState({
      activeRewinds: [{ jobId: 'j1', sessionId: 's1', anchorText: 'anchor text', scope: 1, stage: 'truncating', status: 'running' }],
      setInputDraft: (id: string, draft: string) => {
        calls.push(`draft:${id}:${draft}`);
      },
      loadSessions: () => {
        calls.push('load');
        return Promise.resolve();
      },
      selectSession: (id: string) => {
        calls.push(`select:${id}`);
        return Promise.resolve();
      },
    });
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ sessionId: 's1', stage: 'completed', newSessionId: 's2' }),
    );
    // The draft write must be the very first side effect; the InputRow
    // restore effect only reads inputDrafts when currentSessionId changes.
    expect(calls[0]).toBe('draft:s2:anchor text');
    await vi.waitFor(() => expect(calls).toContain('select:s2'));
    expect(calls.indexOf('draft:s2:anchor text')).toBeLessThan(calls.indexOf('select:s2'));
    expect(useSessionStore.getState().activeRewinds[0]?.status).toBe('completed');
    expect(useUIStore.getState().composerFocusToken).toBe(1);
  });

  it('does not jump when the user has left the originating session', async () => {
    const calls: string[] = [];
    useSessionStore.setState({
      currentSessionId: 'other',
      activeRewinds: [{ jobId: 'j1', sessionId: 's1', anchorText: 'anchor text', scope: 1, stage: 'truncating', status: 'running' }],
      setInputDraft: (id: string, draft: string) => { calls.push(`draft:${id}:${draft}`); },
      loadSessions: () => { calls.push('load'); return Promise.resolve(); },
      selectSession: (id: string) => { calls.push(`select:${id}`); return Promise.resolve(); },
    });
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ sessionId: 's1', stage: 'completed', newSessionId: 's2' }),
    );
    await Promise.resolve();
    expect(calls).toEqual([]);
    expect(useSessionStore.getState().activeRewinds[0]?.status).toBe('completed');
    expect(useUIStore.getState().toastQueue.at(-1)?.message).toContain('\u64a4\u56de\u5b8c\u6210');
  });

  it('maps the dynamic-menu rejection for code-only scope to a friendly error', () => {
    useSessionStore.setState({
      activeRewinds: [{ jobId: 'j1', sessionId: 's1', anchorText: 'a', scope: 3, stage: 'rewinding-files', status: 'running' }],
    });
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({
        sessionId: 's1',
        stage: 'failed',
        error: 'RuntimeError: scope option 3 (Restore code) could not be selected',
      }),
    );
    const active = useSessionStore.getState().activeRewinds[0];
    expect(active?.status).toBe('failed');
    expect(active?.error).toBe('该检查点没有代码变更，无法仅回滚代码');
    expect(useUIStore.getState().toastQueue.at(-1)?.type).toBe('error');
  });

  it('strips stacked prefixes from the no-code-changes business error', () => {
    useSessionStore.setState({
      activeRewinds: [{ jobId: 'j1', sessionId: 's1', anchorText: 'a', scope: 1, stage: 'rewinding-files', status: 'running' }],
    });
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({
        sessionId: 's1',
        stage: 'failed',
        error: 'RuntimeError: NoCodeChangesAtCheckpointError: 该检查点之后没有代码变更，无法回滚代码；可改用「仅对话」',
      }),
    );
    expect(useSessionStore.getState().activeRewinds[0]?.error)
      .toBe('该检查点之后没有代码变更，无法回滚代码；可改用「仅对话」');
  });

  it('keeps the raw error for non-menu failures', () => {
    useSessionStore.setState({
      activeRewinds: [{ jobId: 'j1', sessionId: 's1', anchorText: 'a', scope: 1, stage: 'resuming', status: 'running' }],
    });
    useSessionStore.getState().applyRewindProgress(
      rewindEvent({ sessionId: 's1', stage: 'failed', error: 'cbc fork timed out' }),
    );
    expect(useSessionStore.getState().activeRewinds[0]?.error).toBe('cbc fork timed out');
  });
});

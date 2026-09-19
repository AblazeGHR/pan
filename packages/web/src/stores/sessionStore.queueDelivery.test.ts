// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { useSessionStore } from './sessionStore';
import type { Message, Session } from '@/types';

function session(id: string, history: Message[] = [], historyTotal = history.length): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'running',
    workerId: 'worker-1',
    history,
    historyTotal,
  };
}

describe('optimistic queue message convergence', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [session('s1', [{ role: 'assistant', content: 'ready' }]), session('s2')],
      currentSessionId: 's1',
      currentMessages: [{ role: 'assistant', content: 'ready' }],
      _deliveredQueueIds: {},
      _sessionLocalTouchedSeq: {},
    });
  });

  it('keeps inline attachment parts and merges repeated delivery by queue id', () => {
    const parts = [
      { type: 'text' as const, text: '请阅读 ' },
      {
        type: 'attachment' as const,
        attachmentId: 'att_1',
        displayName: 'guide.md',
        source: 'upload' as const,
      },
    ];
    useSessionStore.getState().appendQueuedMessage('s1', {
      id: 'q-1', text: '请阅读 [guide.md](...)', parts,
    });
    expect(useSessionStore.getState().currentMessages.at(-1)).toMatchObject({
      role: 'user', content: '请阅读 [guide.md](...)', parts, queueItemIds: ['q-1'],
    });

    const delivered = [{
      role: 'user', content: '请阅读 [guide.md](...)', parts, queueItemIds: ['q-1'],
    } satisfies Message];
    useSessionStore.getState().appendDeliveredMessages('s1', delivered);
    useSessionStore.getState().appendDeliveredMessages('s1', delivered);

    expect(useSessionStore.getState().currentMessages.filter((message) => message.queueItemIds?.includes('q-1')))
      .toHaveLength(1);
    expect(useSessionStore.getState().sessions.find((item) => item.id === 's1')?.historyTotal).toBe(2);
  });

  it('does not render another Session delivery in the current Session', () => {
    useSessionStore.getState().appendQueuedMessage('s2', {
      id: 'q-cross', text: 'other session',
    });
    useSessionStore.getState().appendDeliveredMessages('s2', [{
      role: 'user', content: 'other session', queueItemIds: ['q-cross'],
    }]);
    expect(useSessionStore.getState().currentMessages.map((message) => message.content))
      .toEqual(['ready']);
    expect(useSessionStore.getState().sessions.find((item) => item.id === 's2')?.historyTotal)
      .toBe(1);
  });

  it('increments a summary-only card once even when delivery is replayed', () => {
    useSessionStore.setState({
      sessions: [session('s1', [], 0), session('s2')],
      currentSessionId: 's1',
      currentMessages: [],
    });
    const message = { role: 'user', content: 'queued', queueItemIds: ['q-summary'] } satisfies Message;
    useSessionStore.getState().appendDeliveredMessages('s1', [message]);
    useSessionStore.getState().appendDeliveredMessages('s1', [message]);
    expect(useSessionStore.getState().sessions[0]?.historyTotal).toBe(1);
  });
});

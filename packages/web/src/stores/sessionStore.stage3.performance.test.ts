// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { useSessionStore } from '@/stores/sessionStore';
import type { Message, Session } from '@/types';

const make = (role: string, content: string, nativeItemId?: string): Message => ({
  role,
  content,
  ...(nativeItemId ? { nativeItemId } : {}),
});

describe('stage 3 delta hot-path benchmark', () => {
  it('reports bounded repeated delta update cost for H/L matrix', () => {
    const rows: Array<{ history: number; live: number; medianMs: number }> = [];
    for (const historySize of [50, 500, 5000]) {
      for (const liveSize of [1, 100, 1000]) {
        const history = Array.from({ length: historySize }, (_, index) =>
          make(index % 2 ? 'assistant' : 'user', `history-${index}`, `history-${index}`),
        );
        const session: Session = {
          id: 'benchmark',
          name: 'benchmark',
          adapter: 'codex',
          alwaysThinkingEnabled: false,
          effort: 'high',
          history,
          historyTotal: history.length,
        };
        useSessionStore.setState({
          sessions: [session],
          currentSessionId: 'benchmark',
          currentMessages: history,
          liveStreamBuffers: {},
          sessionTranscripts: {},
          terminalWatermarks: {},
        });
        const initial = Array.from({ length: liveSize }, (_, index) =>
          make('assistant', `live-${index}`, `live-${index}`),
        );
        useSessionStore.getState().applyLiveStream(
          'benchmark',
          initial,
          { workerId: 'w', generation: 0, taskSeq: 1 },
        );
        const samples: number[] = [];
        for (let iteration = 0; iteration < 5; iteration += 1) {
          const next = initial.map((message, index) => ({
            ...message,
            content: `live-${index}-${iteration}`,
          }));
          const started = performance.now();
          useSessionStore.getState().applyLiveStream(
            'benchmark',
            next,
            { workerId: 'w', generation: 0, taskSeq: 1 },
          );
          samples.push(performance.now() - started);
        }
        samples.sort((a, b) => a - b);
        rows.push({ history: historySize, live: liveSize, medianMs: samples[2]! });
      }
    }
    console.table(rows);
    expect(rows).toHaveLength(9);
    expect(rows.every((row) => Number.isFinite(row.medianMs))).toBe(true);
  });
});

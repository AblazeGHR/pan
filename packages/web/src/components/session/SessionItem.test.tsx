// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { SessionItem } from './SessionItem';
import type { Session } from '@/types';

function session(
  lastMessage: string,
  workerStatus: string = 'idle',
  extra: Partial<Session> = {},
): Session {
  return {
    id: 'session-1',
    name: 'Codex',
    adapter: 'codex',
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    historyTotal: 1,
    lastMessage,
    workerStatus,
    ...extra,
  };
}

/**
 * A string-like value whose first `.replace()` call is counted. `stripMarkdown`
 * begins with `text.replace(...)`, so each derivation increments `calls` exactly
 * once while still returning the real stripped preview. This lets a test observe
 * whether the memoized card-text derivation actually ran.
 */
function countingText(value: string): { source: string; calls: () => number } {
  let calls = 0;
  const boxed = new String(value) as unknown as {
    replace: (...args: unknown[]) => string;
  };
  boxed.replace = (...args) => {
    calls += 1;
    return (String.prototype.replace as unknown as (...a: unknown[]) => string).apply(
      String(value),
      args,
    );
  };
  return { source: boxed as unknown as string, calls: () => calls };
}

describe('SessionItem streaming preview', () => {
  afterEach(() => cleanup());

  it('shows the last message for a selected completed session', () => {
    render(
      <SessionItem session={session('## Answer\n\n**body**')} isActive />,
    );

    expect(screen.getByText('Answer body')).toBeTruthy();
  });

  it('keeps the selected running session preview visible', () => {
    render(
      <SessionItem
        session={session('## Answer\n\n**body**', 'running')}
        isActive
      />,
    );

    expect(screen.getByText('Answer body')).toBeTruthy();
  });

  it('keeps the preview visible for a background Codex session while it runs', () => {
    render(
      <SessionItem
        session={session('## Answer\n\n**body**', 'running')}
        isActive={false}
      />,
    );

    expect(screen.getByText('Answer body')).toBeTruthy();
  });

  it('shows an explicit unknown count when the cold summary total is null', () => {
    const { rerender } = render(
      <SessionItem
        session={session('preview', 'idle', { historyTotal: null, history: [] })}
        isActive={false}
      />,
    );

    expect(screen.getByText('—')).toBeTruthy();
    expect(screen.queryByText(/^0$/)).toBeNull();

    rerender(
      <SessionItem
        session={session('preview', 'idle', { historyTotal: undefined, history: [] })}
        isActive={false}
      />,
    );
    expect(screen.getByText('—')).toBeTruthy();
    expect(screen.queryByText(/^0$/)).toBeNull();
  });

  it('keeps an explicit zero distinct from unknown and loaded local history', () => {
    const { rerender } = render(
      <SessionItem
        session={session('', 'idle', { historyTotal: 0, history: [] })}
        isActive={false}
      />,
    );
    expect(screen.getByText('0')).toBeTruthy();

    rerender(
      <SessionItem
        session={session('', 'idle', {
          historyTotal: null,
          history: [{ role: 'user', content: 'loaded' }],
        })}
        isActive={false}
      />,
    );
    expect(screen.getByText('1')).toBeTruthy();
  });

  it('does not recompute the derived preview when only unrelated props change', () => {
    const text = countingText('## Answer\n\n**body**');
    const parent = session(text.source);
    const { rerender } = render(<SessionItem session={parent} isActive={false} />);
    const afterMount = text.calls();
    expect(afterMount).toBeGreaterThan(0);
    expect(screen.getByText('Answer body')).toBeTruthy();

    // Unrelated prop change (active flag) — source text is unchanged.
    rerender(<SessionItem session={parent} isActive />);
    expect(text.calls()).toBe(afterMount);

    // A new session object carrying the same source text stays cached too.
    rerender(<SessionItem session={{ ...parent }} isActive />);
    expect(text.calls()).toBe(afterMount);

    // A changed source text must recompute.
    const next = countingText('## Next');
    rerender(<SessionItem session={{ ...parent, lastMessage: next.source }} isActive />);
    expect(next.calls()).toBeGreaterThan(0);
  });
});

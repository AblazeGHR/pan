// @vitest-environment jsdom
// The session/worker status indicator must render every state the backend can
// emit with a meaningful colour. `done` (and `queued`/`restarting`) previously
// fell through to the `offline` fallback, so a freshly finished worker looked
// exactly like one that had never reported anything — i.e. the indicator
// appeared not to have updated at all (T-030).
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { WorkerDot } from '@/components/worker/WorkerDot';

function dotClass(status: string | null | undefined): string {
  const { container } = render(<WorkerDot status={status} />);
  return container.firstElementChild?.className ?? '';
}

afterEach(cleanup);

describe('WorkerDot status colours', () => {
  it('renders a completed worker as settled success, not offline grey', () => {
    const cls = dotClass('done');
    expect(cls).toContain('bg-success');
    expect(cls).not.toContain('bg-text-tertiary');
  });

  it.each([
    ['idle', 'bg-success'],
    ['running', 'bg-accent'],
    ['queued', 'bg-accent'],
    ['restarting', 'bg-warning'],
    ['held', 'bg-warning'],
    ['error', 'bg-danger'],
    ['cancelled', 'bg-warning'],
    ['offline', 'bg-text-tertiary'],
  ])('maps %s to %s', (status, expected) => {
    expect(dotClass(status)).toContain(expected);
  });

  it('falls back to offline for a missing or unknown status', () => {
    expect(dotClass(null)).toContain('bg-text-tertiary');
    expect(dotClass(undefined)).toContain('bg-text-tertiary');
    expect(dotClass('something-new')).toContain('bg-text-tertiary');
  });
});

describe('WorkerDot durable Illegal semantics', () => {
  it('renders a failed execution red using the authoritative flag', () => {
    const { container } = render(<WorkerDot status="idle" illegalOffline={true} />);
    expect(container.firstElementChild?.className).toContain('bg-danger');
    expect(screen.getByLabelText(/Illegal offline: previous execution/)).toBeTruthy();
    expect(screen.getByTitle('Previous execution ended without safe finalization')).toBeTruthy();
  });

  it.each(['idle', 'offline', 'restarting', 'done'])('does not infer Illegal from old running ledger with %s', (status) => {
    const { container } = render(<WorkerDot status={status} legalState="running" illegalOffline={false} />);
    expect(container.firstElementChild?.className).not.toContain('bg-danger');
  });

  it('a running execution with False legality is not Illegal', () => {
    expect(dotClass('running')).toContain('bg-accent');
  });

  it('converges from failed execution to settled Idle on recovery', () => {
    const { container, rerender } = render(<WorkerDot status="offline" illegalOffline={true} />);
    expect(container.firstElementChild?.className).toContain('bg-danger');
    rerender(<WorkerDot status="idle" illegalOffline={false} />);
    expect(container.firstElementChild?.className).toContain('bg-success');
    expect(screen.queryByLabelText(/Illegal offline/)).toBeNull();
  });

  it('keeps a runtime error visible independently of legality', () => {
    expect(dotClass('error')).toContain('bg-danger');
    expect(screen.queryByLabelText(/Illegal offline/)).toBeNull();
  });
});

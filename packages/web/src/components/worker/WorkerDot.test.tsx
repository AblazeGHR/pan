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

// The red dot carries two distinct meanings that must stay distinguishable:
// a genuine runtime error status, and the deliberate legal-running mismatch
// ("Illegal state") that a preserve-running Pan restart leaves behind until
// an explicit recovery choice resolves it.
describe('WorkerDot legal-running mismatch semantics', () => {
  function dotClassWith(status: string | null | undefined, legalState: string | null | undefined): string {
    const { container } = render(<WorkerDot status={status} legalState={legalState} />);
    return container.firstElementChild?.className ?? '';
  }

  it('renders the legal-running mismatch red and labels it as a mismatch', () => {
    const { container } = render(
      <WorkerDot status="idle" legalState="running" />,
    );
    expect(container.firstElementChild?.className).toContain('bg-danger');
    expect(screen.getByLabelText(/Legal Worker state is running, but actual status is idle/))
      .toBeTruthy();
    expect(screen.getByTitle('Legal state: running; actual Worker: idle')).toBeTruthy();
  });

  it.each([
    ['offline', 'running'],
    ['restarting', 'running'],
    ['done', 'running'],
  ] as const)('renders the mismatch red for legal running with %s runtime', (status, legalState) => {
    expect(dotClassWith(status, legalState)).toContain('bg-danger');
  });

  it('does not mark a matched running pair', () => {
    const cls = dotClassWith('running', 'running');
    expect(cls).toContain('bg-accent');
    expect(cls).not.toContain('bg-danger');
  });

  it('converges to settled green once legal and runtime agree on idle', () => {
    const cls = dotClassWith('idle', 'idle');
    expect(cls).toContain('bg-success');
    expect(cls).not.toContain('bg-danger');
    expect(screen.queryByLabelText(/Legal Worker state is running/)).toBeNull();
  });

  it('keeps the pure runtime error red distinct from the mismatch', () => {
    const { container } = render(<WorkerDot status="error" legalState={null} />);
    expect(container.firstElementChild?.className).toContain('bg-danger');
    expect(screen.queryByLabelText(/Legal Worker state is running/)).toBeNull();
  });
});

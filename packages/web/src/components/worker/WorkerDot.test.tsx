// @vitest-environment jsdom
// The session/worker status indicator must render every state the backend can
// emit with a meaningful colour. `done` (and `queued`/`restarting`) previously
// fell through to the `offline` fallback, so a freshly finished worker looked
// exactly like one that had never reported anything — i.e. the indicator
// appeared not to have updated at all (T-030).
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { WorkerDot } from '@/components/worker/WorkerDot';

function dotClass(status: string | null | undefined): string {
  const { container } = render(<WorkerDot status={status} />);
  return container.firstElementChild?.className ?? '';
}

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

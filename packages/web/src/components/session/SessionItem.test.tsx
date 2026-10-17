// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SessionItem } from './SessionItem';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useJobStore } from '@/stores/jobStore';
import type { Job } from '@/types/jobs';
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

const job = (overrides: Partial<Job> = {}): Job => ({
  jobId: 'job-1',
  kind: 'session-message',
  status: 'pending',
  name: 'Job',
  description: '',
  source: { type: 'agent' },
  target: { sessionId: 'session-1' },
  paused: false,
  runCount: 0,
  createdAt: 1,
  updatedAt: 1,
  ...overrides,
}) as Job;

const resetJobStore = () => useJobStore.setState({
  jobs: [], loaded: false, loading: false, error: null, pinMetadataUnavailable: false,
  loadSeq: 0, mutationSeq: 0,
});

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

  it('places the adapter in the quick action rail and hides the Workspace badge at mobile widths', () => {
    useWorkspaceStore.setState({
      workspaces: [{ id: 'ws-mobile', name: 'Mobile Space', order: null }],
    });
    useUIStore.setState({ activeWorkspaceId: 'all' });
    const { container } = render(
      <SessionItem
        session={session('preview', 'idle', { workspaceIds: ['ws-mobile'] })}
        isActive={false}
      />,
    );

    expect(container.querySelector('[data-testid="session-adapter-badge"]')?.className).toContain('session-quick-action-adapter');
    expect(container.querySelector('[data-testid="session-adapter-badge"]')?.closest('[data-testid="session-quick-action-rail"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="session-workspace-badge"]')?.className).toContain('max-md:hidden');
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

describe('SessionItem Job indicator (J)', () => {
  afterEach(() => {
    cleanup();
    resetJobStore();
  });

  const jobBadge = (container: HTMLElement) =>
    container.querySelector('[data-testid="session-job-badge"]');

  it('shows a single blue J for a running job targeting the session', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'r1', kind: 'background-process', status: 'running' })],
    });
    const { container } = render(<SessionItem session={session('preview')} isActive={false} />);

    const badge = jobBadge(container);
    expect(badge).toBeTruthy();
    expect(badge!.textContent).toBe('J');
    expect(badge!.getAttribute('data-job-state')).toBe('running');
    expect(badge!.className).toContain('session-job-badge');
    // Same info-area row as the numeric badges; J is the leftmost in-flow item
    // so it yields first under crowding, and the row stays right-anchored.
    // Only the always-mounted hidden measurement probe is out of flow.
    const row = badge!.closest('.session-quick-action-adapter-row');
    expect(row).toBeTruthy();
    const firstInFlow = Array.from(row!.children)
      .find((child) => child.getAttribute('data-session-job-metric') !== 'probe');
    expect(firstInFlow).toBe(badge);
    expect(row!.className).toContain('session-quick-action-adapter-row');
    expect(container.querySelectorAll('[data-testid="session-job-badge"]')).toHaveLength(1);
  });

  it('shows a yellow J (scheduled) for an armed scheduled/message job', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 's1', kind: 'session-message', status: 'pending' })],
    });
    const { container } = render(<SessionItem session={session('preview')} isActive={false} />);

    const badge = jobBadge(container);
    expect(badge!.getAttribute('data-job-state')).toBe('scheduled');
  });

  it('keeps J out of viewport-based hiding (width decides, see SessionJobBadge)', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'm1', kind: 'background-process', status: 'running' })],
    });
    const { container } = render(<SessionItem session={session('preview')} isActive={false} />);
    const badge = jobBadge(container)!;
    // No max-md class: hiding is decided by the info-area width, not the
    // viewport breakpoint (SessionJobBadge.test.tsx covers the width ladder).
    expect(badge.className).not.toContain('max-md:hidden');
    expect(badge.className).toContain('session-job-badge');
    expect(badge.closest('[data-testid="session-quick-action-rail"]')).toBeTruthy();
  });

  it('aggregates multiple jobs into one J with running priority', () => {
    useJobStore.setState({
      jobs: [
        job({ jobId: 'a1', kind: 'session-message', status: 'scheduled' }),
        job({ jobId: 'a2', kind: 'session-message', status: 'pending' }),
      ],
    });
    const { container, rerender } = render(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)!.getAttribute('data-job-state')).toBe('scheduled');
    expect(container.querySelectorAll('[data-testid="session-job-badge"]')).toHaveLength(1);

    useJobStore.setState({
      jobs: [
        job({ jobId: 'a1', kind: 'session-message', status: 'scheduled' }),
        job({ jobId: 'a2', kind: 'session-message', status: 'pending' }),
        job({ jobId: 'a3', kind: 'background-process', status: 'running' }),
      ],
    });
    rerender(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)!.getAttribute('data-job-state')).toBe('running');
    expect(container.querySelectorAll('[data-testid="session-job-badge"]')).toHaveLength(1);
  });

  it('shows no J for a job targeting another session', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'o1', status: 'running', target: { sessionId: 'other-session' } })],
    });
    const { container } = render(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)).toBeNull();
  });

  it('removes J when the last targeted job reaches a terminal state', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 't1', kind: 'background-process', status: 'running' })],
    });
    const { container, rerender } = render(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)).toBeTruthy();

    useJobStore.getState().upsertJob(
      job({ jobId: 't1', kind: 'background-process', status: 'completed' }),
    );
    rerender(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)).toBeNull();
  });

  it('shows no J while the process job is only starting, paused, or disabled', () => {
    useJobStore.setState({
      jobs: [
        job({ jobId: 'st', kind: 'background-process', status: 'starting' }),
        job({ jobId: 'pa', kind: 'scheduled-task', status: 'scheduled', paused: true }),
        job({ jobId: 'pb', kind: 'scheduled-task', status: 'scheduled', enabled: false }),
        job({ jobId: 'pc', kind: 'scheduled-task', status: 'pending' }),
      ],
    });
    const { container } = render(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)).toBeNull();
  });

  it('appears from a cold mount when the WS-created job lands, and clears on delete', () => {
    const { container, rerender } = render(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)).toBeNull();

    // job.created (WS) after the card mounted.
    useJobStore.setState({
      jobs: [job({ jobId: 'w1', kind: 'session-message', status: 'scheduled' })],
    });
    rerender(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)!.getAttribute('data-job-state')).toBe('scheduled');

    // job.deleted (WS).
    useJobStore.getState().removeJob('w1');
    rerender(<SessionItem session={session('preview')} isActive={false} />);
    expect(jobBadge(container)).toBeNull();
  });

  it('keeps numeric badges right-aligned and intact next to J', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'n1', kind: 'background-process', status: 'running' })],
    });
    const { container } = render(
      <SessionItem
        session={session('preview', 'idle', { queuePendingCount: 12, unreadDoneCount: 3 })}
        isActive={false}
      />,
    );

    const row = container.querySelector('.session-quick-action-adapter-row')!;
    // In-flow items only: the hidden measurement probe (metric=probe) is out
    // of flow; the badge (metric=badge) stays as the left-most visible item.
    const children = Array.from(row.children)
      .filter((child) => child.getAttribute('data-session-job-metric') !== 'probe');
    expect(children[0]!.getAttribute('data-testid')).toBe('session-job-badge');
    expect(children[1]!.getAttribute('data-testid')).toBe('session-queue-badge');
    expect(children[2]!.getAttribute('data-testid')).toBe('session-unread-done-badge');
    // The adapter chip stays the right-most item: the whole row (J included)
    // is anchored to the rail's bottom-right, so numbers stay right-aligned.
    expect(children[3]!.getAttribute('data-testid')).toBe('session-adapter-badge');
    expect(container.querySelector('[data-testid="session-queue-badge"]')!.textContent).toBe('12');
    expect(container.querySelector('[data-testid="session-unread-done-badge"]')!.textContent).toBe('3');
  });

  it('renders the info-area row for a job-only session (no adapter counts needed)', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'only', kind: 'session-message', status: 'scheduled' })],
    });
    const { container } = render(
      <SessionItem
        session={session('preview', 'idle', {
          adapter: undefined as unknown as string,
          queuePendingCount: 0,
          unreadDoneCount: 0,
        })}
        isActive={false}
      />,
    );
    expect(jobBadge(container)).toBeTruthy();
    expect(container.querySelector('.session-quick-action-adapter-row')).toBeTruthy();
  });

  it('does not select the card when the read-only J is clicked', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'c1', kind: 'background-process', status: 'running' })],
    });
    const onSelect = vi.fn();
    const { container } = render(
      <SessionItem session={session('preview')} isActive={false} onSelect={onSelect} />,
    );
    fireEvent.click(jobBadge(container)!);
    fireEvent.doubleClick(jobBadge(container)!);
    expect(onSelect).not.toHaveBeenCalled();
  });
});

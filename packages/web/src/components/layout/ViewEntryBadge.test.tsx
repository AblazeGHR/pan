// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { ViewEntryBadge, ViewEntryDirectionDot } from './ViewEntryBadge';
import { useSessionStore } from '@/stores/sessionStore';
import { useJobStore } from '@/stores/jobStore';
import { useAutomationAuth } from '@/stores/automationStore';

const session = (id: string, unreadDoneCount: number) => ({ id, unreadDoneCount } as never);

describe('navigation unread badges', () => {
  afterEach(() => {
    cleanup();
    useSessionStore.setState({ sessions: [] });
    useJobStore.setState({ jobs: [] });
    useAutomationAuth.setState({ token: null });
    vi.restoreAllMocks();
  });
  it('sums Chat unread counts, hides zero and caps at 99+', () => {
    useSessionStore.setState({ sessions: [session('a', 2), session('b', 3)] });
    const { rerender } = render(<ViewEntryBadge kind="chat" />);
    expect(screen.getByTestId('chat-unread-badge').textContent).toBe('5');
    useSessionStore.setState({ sessions: [] }); rerender(<ViewEntryBadge kind="chat" />);
    expect(screen.queryByTestId('chat-unread-badge')).toBeNull();
    useSessionStore.setState({ sessions: [session('a', 120)] }); rerender(<ViewEntryBadge kind="chat" />);
    expect(screen.getByTestId('chat-unread-badge').textContent).toBe('99+');
  });
  it('uses one user-lockers request and sums only returned unread totals', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ unread: 6 }), { status: 200 }));
    render(<ViewEntryBadge kind="locker" />);
    await waitFor(() => expect(screen.getByTestId('locker-unread-badge').textContent).toBe('6'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/locker-unread-summary');
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: 'GET' });
  });
  it('keeps the last value after a transient failure and refreshes when auth changes', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ unread: 3 }), { status: 200 }))
      .mockRejectedValueOnce(new Error('temporary'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ unread: 4 }), { status: 200 }));
    render(<ViewEntryBadge kind="locker" />);
    await waitFor(() => expect(screen.getByTestId('locker-unread-badge').textContent).toBe('3'));
    useAutomationAuth.setState({ token: 'new-token' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    useAutomationAuth.setState({ token: 'newer-token' });
    await waitFor(() => expect(screen.getByTestId('locker-unread-badge').textContent).toBe('4'));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
  it('places directional dots on the outer side of each arrow', () => {
    const { container } = render(<><ViewEntryDirectionDot direction="left" /><ViewEntryDirectionDot direction="right" /></>);
    expect(container.querySelector('[aria-label="更前的导航组有未读"]')?.className).toContain('left-0');
    expect(container.querySelector('[aria-label="更后的导航组有未读"]')?.className).toContain('right-0');
  });
});

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
    useAutomationAuth.setState({ token: 'test-token' });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ lockers: [{ unread: 2 }, { unread: 0 }, { unread: 4 }] }), { status: 200 }));
    render(<ViewEntryBadge kind="locker" />);
    await waitFor(() => expect(screen.getByTestId('locker-unread-badge').textContent).toBe('6'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/user-lockers');
  });
  it('places directional dots on the outer side of each arrow', () => {
    const { container } = render(<><ViewEntryDirectionDot direction="left" /><ViewEntryDirectionDot direction="right" /></>);
    expect(container.querySelector('[aria-label="更前的导航组有未读"]')?.className).toContain('left-0');
    expect(container.querySelector('[aria-label="更后的导航组有未读"]')?.className).toContain('right-0');
  });
});

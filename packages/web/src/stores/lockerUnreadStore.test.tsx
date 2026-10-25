// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { useLockerSessionUnread, useLockerUnread } from './lockerUnreadStore';
import { useAutomationAuth } from './automationStore';

function Probe() {
  return (
    <>
      <span data-testid="total">{useLockerUnread()}</span>
      <span data-testid="a">{useLockerSessionUnread('ses_a')}</span>
      <span data-testid="b">{useLockerSessionUnread('ses_b')}</span>
      <span data-testid="zero">{useLockerSessionUnread('ses_zero')}</span>
    </>
  );
}

const respond = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200 });

describe('locker unread store', () => {
  beforeEach(() => {
    useAutomationAuth.setState({ token: null });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
  });

  it('parses the aggregate and per-Session counts, ignoring non-positive values', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(respond({ unread: 4, sessions: { ses_a: 3, ses_b: 1, ses_zero: 0 } }));
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('4'));
    expect(screen.getByTestId('a').textContent).toBe('3');
    expect(screen.getByTestId('b').textContent).toBe('1');
    expect(screen.getByTestId('zero').textContent).toBe('0');
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/locker-unread-summary');
  });

  it('keeps the last counts when the endpoint fails and ignores a missing map', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(respond({ unread: 4, sessions: { ses_a: 3, ses_b: 1 } }))
      .mockRejectedValue(new Error('temporary'));
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('4'));
    expect(screen.getByTestId('a').textContent).toBe('3');

    // A failed refresh must not clear what is already shown.
    useAutomationAuth.setState({ token: 'failing-token' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId('total').textContent).toBe('4');
    expect(screen.getByTestId('a').textContent).toBe('3');

    // An older server without the per-Session map still drives the total.
    fetchMock.mockResolvedValue(respond({ unread: 1 }));
    useAutomationAuth.setState({ token: 'new-token' });
    await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('1'));
    expect(screen.getByTestId('a').textContent).toBe('0');
    expect(screen.getByTestId('b').textContent).toBe('0');
  });

  it('pauses while the page is hidden and refetches when visible again', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(respond({ unread: 5, sessions: { ses_a: 5 } }));
    render(<Probe />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('a').textContent).toBe('5');

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    });
    useAutomationAuth.setState({ token: 'hidden-token' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('total').textContent).toBe('5');

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});

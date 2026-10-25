// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { useLockerSessionUnread, useLockerUnread } from './lockerUnreadStore';
import { useAutomationAuth } from './automationStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from './appSettingsStore';
import { resetLockerUnreadNotificationBaseline } from '@/services/lockerDesktopNotify';

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn(async () => undefined) }));
vi.mock('@/router', () => ({ router: { navigate: navigateMock } }));

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

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: hidden ? 'hidden' : 'visible',
  });
}

interface FakeNotification {
  title: string;
  options: NotificationOptions;
  onclick: (() => void) | null;
  close: () => void;
}

function stubNotification(permission: NotificationPermission): FakeNotification[] {
  const instances: FakeNotification[] = [];
  const ctor = vi.fn(function (
    this: FakeNotification,
    title: string,
    options: NotificationOptions,
  ) {
    this.title = title;
    this.options = options;
    this.onclick = null;
    this.close = vi.fn();
    instances.push(this);
  });
  Object.defineProperty(ctor, 'permission', { value: permission, configurable: true });
  Object.defineProperty(ctor, 'requestPermission', {
    value: vi.fn(async () => permission),
    configurable: true,
  });
  vi.stubGlobal('Notification', ctor);
  return instances;
}

const setLockerDesktop = (on: boolean) => useAppSettingsStore.setState({
  notifications: { ...DEFAULT_SETTINGS.notifications, lockerDesktop: on },
});

describe('locker unread store', () => {
  beforeEach(() => {
    useAutomationAuth.setState({ token: null });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
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

  describe('Locker desktop notifications', () => {
    beforeEach(() => {
      navigateMock.mockClear();
      resetLockerUnreadNotificationBaseline();
      setLockerDesktop(false);
    });

    it('stays silent by default even when the unread total grows', async () => {
      const notifications = stubNotification('granted');
      const fetchMock = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(respond({ unread: 1 }))
        .mockResolvedValueOnce(respond({ unread: 4 }));
      render(<Probe />);
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('1'));
      useAutomationAuth.setState({ token: 'grow-token' });
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('4'));
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(notifications).toHaveLength(0);
    });

    it('notifies once for an increase while hidden once the switch is on', async () => {
      const notifications = stubNotification('granted');
      setLockerDesktop(true);
      setHidden(true);
      const fetchMock = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(respond({ unread: 3, sessions: { ses_a: 3 } }))
        .mockResolvedValueOnce(respond({ unread: 5, sessions: { ses_a: 5 } }));
      render(<Probe />);
      // The hidden-page pause is lifted only because the switch needs the poll.
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('3'));
      expect(fetchMock).toHaveBeenCalledTimes(1);
      // First observation is the baseline: loading the app never notifies.
      expect(notifications).toHaveLength(0);

      useAutomationAuth.setState({ token: 'hidden-token' });
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('5'));
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(notifications).toHaveLength(1);
      expect(notifications[0]?.title).toBe('Pan Locker');
      expect(notifications[0]?.options).toEqual({
        body: '有 2 条新消息',
        tag: 'pan-locker-unread',
      });
    });

    it('stays silent while the page is visible', async () => {
      const notifications = stubNotification('granted');
      setLockerDesktop(true);
      vi.spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(respond({ unread: 2 }))
        .mockResolvedValueOnce(respond({ unread: 6 }));
      render(<Probe />);
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('2'));
      useAutomationAuth.setState({ token: 'visible-token' });
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('6'));
      expect(notifications).toHaveLength(0);
    });

    it('stays silent when the browser permission was denied', async () => {
      const notifications = stubNotification('denied');
      setLockerDesktop(true);
      setHidden(true);
      vi.spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(respond({ unread: 2 }))
        .mockResolvedValueOnce(respond({ unread: 6 }));
      render(<Probe />);
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('2'));
      useAutomationAuth.setState({ token: 'denied-token' });
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('6'));
      expect(notifications).toHaveLength(0);
    });

    it('never notifies when unread decreases (messages read)', async () => {
      const notifications = stubNotification('granted');
      setLockerDesktop(true);
      setHidden(true);
      vi.spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(respond({ unread: 7 }))
        .mockResolvedValueOnce(respond({ unread: 2 }));
      render(<Probe />);
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('7'));
      useAutomationAuth.setState({ token: 'read-token' });
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('2'));
      expect(notifications).toHaveLength(0);
    });

    it('focuses Pan and opens Locker when the notification is clicked', async () => {
      const notifications = stubNotification('granted');
      const focusSpy = vi.spyOn(window, 'focus').mockImplementation(() => {});
      setLockerDesktop(true);
      setHidden(true);
      vi.spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(respond({ unread: 1 }))
        .mockResolvedValueOnce(respond({ unread: 2 }));
      render(<Probe />);
      await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('1'));
      useAutomationAuth.setState({ token: 'click-token' });
      await waitFor(() => expect(notifications).toHaveLength(1));

      notifications[0]!.onclick!();
      await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/locker'));
      expect(focusSpy).toHaveBeenCalled();
    });
  });
});

// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/services/api', () => ({
  fetchQqChannels: vi.fn().mockResolvedValue([]),
  fetchQqContacts: vi.fn().mockResolvedValue([]),
  qqSubscribe: vi.fn(),
  qqUnsubscribe: vi.fn(),
  fetchSession: vi.fn().mockResolvedValue({ id: 'ses_1', name: 'demo', qqSubscriptions: [], notificationSettings: { browser: false, system: false } }),
  patchSession: vi.fn().mockResolvedValue({ id: 'ses_1', name: 'demo', qqSubscriptions: [], notificationSettings: { browser: true, system: false } }),
}));

vi.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => selector({
    sessions: [{ id: 'ses_1', name: 'demo' }],
    loadSessions: vi.fn().mockResolvedValue(undefined),
  }),
}));
vi.mock('@/stores/uiStore', () => ({
  useUIStore: (selector: (state: unknown) => unknown) => selector({ showToast: vi.fn() }),
}));

import { PostboxModal } from './PostboxModal';

describe('msgBridge settings tabs', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: { permission: 'default', requestPermission: vi.fn().mockResolvedValue('granted') },
    });
  });

  it('shows QQ, System, Browser tabs and explicit browser permission UX', async () => {
    render(<PostboxModal open onClose={vi.fn()} sessionId="ses_1" />);
    expect(screen.getByText('msgBridge')).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'QQ' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'System' })).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'Browser' }));
    expect(screen.getByText(/requested only by this button/i)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Request browser permission/i }));
    await waitFor(() => expect(window.Notification.requestPermission).toHaveBeenCalledTimes(1));
  });
});

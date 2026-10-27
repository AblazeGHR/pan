// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SourceNotificationPanel } from './SourceNotificationPanel';
import { SessionErrorBridgeSetting } from './SessionErrorBridgeSetting';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { updateUiSettings } from '@/services/api';

vi.mock('@/services/api', () => ({
  fetchUiSettings: vi.fn().mockResolvedValue({}),
  updateUiSettings: vi.fn(),
  fetchQqChannels: vi.fn().mockResolvedValue([{ connected: true, bot_uin: '456', name: 'Recorded fake bot' }]),
  fetchQqContacts: vi.fn().mockResolvedValue([{ peerUin: '123', peerName: 'Recorded fake contact', chatType: 1 }]),
}));

beforeEach(() => {
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, notifications: { ...DEFAULT_SETTINGS.notifications } });
  vi.mocked(updateUiSettings).mockImplementation(async (patch) => patch);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('source notifications', () => {
  it('offers independent sources with System/Browser and QQ Report, no Subscribe', async () => {
    render(<><SourceNotificationPanel source="lockerUnread" /><SourceNotificationPanel source="newError" /></>);
    const locker = within(screen.getByTestId('notification-source-lockerUnread'));
    const error = within(screen.getByTestId('notification-source-newError'));
    expect(locker.queryByRole('checkbox', { name: /Subscribe/i })).toBeNull();
    fireEvent.click(locker.getByRole('checkbox', { name: 'Enabled' }));
    fireEvent.click(locker.getByRole('checkbox', { name: 'QQ Report' }));
    await screen.findByText('Recorded fake contact');
    const save = locker.getByRole('button', { name: 'Save Locker new unread' });
    expect(save.hasAttribute('disabled')).toBe(true);
    fireEvent.click(locker.getByRole('checkbox', { name: /Recorded fake contact/ }));
    fireEvent.click(locker.getByRole('checkbox', { name: 'System' }));
    fireEvent.click(save);
    await waitFor(() => expect(updateUiSettings).toHaveBeenCalledWith({ notifications: { lockerUnread: {
      enabled: true, qqReport: true, qqTargets: ['user:123@456'], system: true, browser: false,
    } } }));
    expect((error.getByRole('checkbox', { name: 'Enabled' }) as HTMLInputElement).checked).toBe(false);
    expect(useAppSettingsStore.getState().notifications.lockerUnread?.enabled).toBe(true);
    expect(updateUiSettings).toHaveBeenCalledTimes(1);
  });

  it('preserves a source draft and shows a failed save', async () => {
    vi.mocked(updateUiSettings).mockRejectedValueOnce(new Error('Disk write failed'));
    render(<SourceNotificationPanel source="newError" />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Enabled' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'System' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save New errors' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('alert').textContent).toBe('Disk write failed');
    expect((screen.getByRole('checkbox', { name: 'Enabled' }) as HTMLInputElement).checked).toBe(true);
    expect(useAppSettingsStore.getState().notifications.newError?.enabled).not.toBe(true);
  });

  it('Behavior persists Session error routing independently of Notifications', async () => {
    render(<SessionErrorBridgeSetting />);
    fireEvent.click(screen.getByRole('checkbox', { name: /Send Session errors/ }));
    await waitFor(() => expect(updateUiSettings).toHaveBeenCalledWith({ sessionErrorBridge: false }));
    expect(useAppSettingsStore.getState().sessionErrorBridge).toBe(false);
  });
});

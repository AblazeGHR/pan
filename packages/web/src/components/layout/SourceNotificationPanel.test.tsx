// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SourceNotificationPanel } from './SourceNotificationPanel';
import { SessionErrorBridgeSetting } from './SessionErrorBridgeSetting';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { updateUiSettings } from '@/services/api';
vi.mock('@/views/lockerModel', () => ({ lockerRequest: vi.fn().mockResolvedValue({ types: [] }) }));

vi.mock('@/services/api', () => ({
  fetchUiSettings: vi.fn().mockResolvedValue({}),
  updateUiSettings: vi.fn(),
  fetchQqChannels: vi.fn().mockResolvedValue([{ connected: true, bot_uin: '456', name: 'Recorded fake bot' }]),
  fetchQqContacts: vi.fn().mockResolvedValue([{ peerUin: '123', peerName: 'Recorded fake contact', chatType: 1 }]),
}));

beforeEach(() => {
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, notifications: { ...DEFAULT_SETTINGS.notifications } });
  // Reset first so a one-shot rejection from the previous test can never leak into this one.
  vi.mocked(updateUiSettings).mockReset().mockImplementation(async (patch) => patch);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const saveCalls = () => vi.mocked(updateUiSettings).mock.calls.length;

/** Click a control and wait for its in-flight save, so the next change starts from a settled panel. */
async function change(element: HTMLElement) {
  await waitFor(() => expect((element as HTMLInputElement).disabled).toBe(false));
  fireEvent.click(element);
  await waitFor(() => expect((element as HTMLInputElement).disabled).toBe(false));
}

describe('source notifications', () => {
  it('saves each checkbox change immediately and offers no Save button', async () => {
    render(<><SourceNotificationPanel source="lockerUnread" /><SourceNotificationPanel source="newError" /></>);
    const locker = within(screen.getByTestId('notification-source-lockerUnread'));
    const errors = within(screen.getByTestId('notification-source-newError'));
    expect(screen.queryByRole('button', { name: /^Save/ })).toBeNull();
    expect(locker.queryByRole('checkbox', { name: /Subscribe/i })).toBeNull();

    await change(locker.getByRole('checkbox', { name: 'Enabled' }));
    await waitFor(() => expect(saveCalls()).toBe(1));
    expect(updateUiSettings).toHaveBeenLastCalledWith({ notifications: { lockerUnread: {
      enabled: true, qqReport: false, system: false, browser: false, qqTargets: [],
    } } });
    expect(useAppSettingsStore.getState().notifications.lockerUnread?.enabled).toBe(true);
    expect((errors.getByRole('checkbox', { name: 'Enabled' }) as HTMLInputElement).checked).toBe(false);

    await change(locker.getByRole('checkbox', { name: 'System' }));
    await waitFor(() => expect(saveCalls()).toBe(2));
    expect(updateUiSettings).toHaveBeenLastCalledWith({ notifications: { lockerUnread: {
      enabled: true, qqReport: false, system: true, browser: false, qqTargets: [],
    } } });
    expect(useAppSettingsStore.getState().notifications.newError?.enabled).toBe(false);
  });

  it('saves QQ Report and every selected QQ target immediately', async () => {
    render(<SourceNotificationPanel source="lockerUnread" />);
    const panel = within(screen.getByTestId('notification-source-lockerUnread'));

    await change(panel.getByRole('checkbox', { name: 'QQ Report' }));
    await waitFor(() => expect(saveCalls()).toBe(1));
    expect(updateUiSettings).toHaveBeenLastCalledWith({ notifications: { lockerUnread: {
      enabled: false, qqReport: true, system: false, browser: false, qqTargets: [],
    } } });

    await change(await panel.findByRole('checkbox', { name: /Recorded fake contact/ }));
    await waitFor(() => expect(saveCalls()).toBe(2));
    expect(updateUiSettings).toHaveBeenLastCalledWith({ notifications: { lockerUnread: {
      enabled: false, qqReport: true, system: false, browser: false, qqTargets: ['user:123@456'],
    } } });
    expect(panel.getByText('QQ Report contacts · 1 selected')).toBeTruthy();

    await change(panel.getByRole('checkbox', { name: 'Enabled' }));
    await waitFor(() => expect(saveCalls()).toBe(3));
    expect(useAppSettingsStore.getState().notifications.lockerUnread).toEqual({
      enabled: true, qqReport: true, system: false, browser: false, qqTargets: ['user:123@456'],
    });
  });

  it('keeps an invalid combination unsaved and says so until a target is chosen', async () => {
    render(<SourceNotificationPanel source="newError" />);
    const panel = within(screen.getByTestId('notification-source-newError'));

    await change(panel.getByRole('checkbox', { name: 'Enabled' }));
    await waitFor(() => expect(saveCalls()).toBe(1));

    await change(panel.getByRole('checkbox', { name: 'QQ Report' }));
    expect((await panel.findByRole('alert')).textContent)
      .toBe('Select at least one QQ Report contact. That change was not saved.');
    expect(saveCalls()).toBe(1);
    expect(useAppSettingsStore.getState().notifications.newError?.qqReport).toBe(false);
    expect((panel.getByRole('checkbox', { name: 'QQ Report' }) as HTMLInputElement).checked).toBe(true);

    await change(await panel.findByRole('checkbox', { name: /Recorded fake contact/ }));
    await waitFor(() => expect(saveCalls()).toBe(2));
    expect(updateUiSettings).toHaveBeenLastCalledWith({ notifications: { newError: {
      enabled: true, qqReport: true, system: false, browser: false, qqTargets: ['user:123@456'],
    } } });
    expect(panel.queryByRole('alert')).toBeNull();
  });

  it('reports a failed save, keeps the change, and persists it with the next change', async () => {
    vi.mocked(updateUiSettings).mockRejectedValueOnce(new Error('Disk write failed'));
    render(<SourceNotificationPanel source="newError" />);
    const panel = within(screen.getByTestId('notification-source-newError'));

    await change(panel.getByRole('checkbox', { name: 'Enabled' }));
    expect((await panel.findByRole('alert')).textContent).toBe('Disk write failed');
    expect((panel.getByRole('checkbox', { name: 'Enabled' }) as HTMLInputElement).checked).toBe(true);
    expect(useAppSettingsStore.getState().notifications.newError?.enabled).toBe(false);
    expect(saveCalls()).toBe(1);

    await change(panel.getByRole('checkbox', { name: 'System' }));
    await waitFor(() => expect(saveCalls()).toBe(2));
    expect(updateUiSettings).toHaveBeenLastCalledWith({ notifications: { newError: {
      enabled: true, qqReport: false, system: true, browser: false, qqTargets: [],
    } } });
    await waitFor(() => expect(panel.queryByRole('alert')).toBeNull());
    expect(useAppSettingsStore.getState().notifications.newError).toEqual({
      enabled: true, qqReport: false, system: true, browser: false, qqTargets: [],
    });
  });

  it('Behavior persists Session error routing independently of Notifications', async () => {
    render(<SessionErrorBridgeSetting />);
    fireEvent.click(screen.getByRole('checkbox', { name: /Send Session errors/ }));
    await waitFor(() => expect(updateUiSettings).toHaveBeenCalledWith({ sessionErrorBridge: false }));
    expect(useAppSettingsStore.getState().sessionErrorBridge).toBe(false);
  });
});

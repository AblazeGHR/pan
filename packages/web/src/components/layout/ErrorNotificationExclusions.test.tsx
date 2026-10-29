// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SessionErrorBridgeSetting } from './SessionErrorBridgeSetting';
import { SourceNotificationPanel } from './SourceNotificationPanel';
import { DEFAULT_SETTINGS, sanitizeErrorExclusions, useAppSettingsStore } from '@/stores/appSettingsStore';
import { updateUiSettings } from '@/services/api';
import { lockerRequest } from '@/views/lockerModel';

const catalog = Array.from({ length: 80 }, (_, index) => ({ id: `source/code${index}`, label: `Source · code${index}` }));
vi.mock('@/views/lockerModel', () => ({ lockerRequest: vi.fn(async () => ({ types: catalog })) }));
vi.mock('@/services/api', () => ({ fetchUiSettings: vi.fn(), updateUiSettings: vi.fn() }));
beforeEach(() => {
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  vi.mocked(updateUiSettings).mockImplementation(async (patch) => patch);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('shares enabled and selections across both entrances; disabling retains them', async () => {
  render(<><SessionErrorBridgeSetting /><SourceNotificationPanel source="newError" /><SourceNotificationPanel source="lockerUnread" /></>);
  await screen.findAllByTestId('error-notification-exclusions');
  const behavior = within(screen.getByTestId('session-error-bridge-section')).getByTestId('error-notification-exclusions');
  const global = within(screen.getByTestId('notification-source-newError')).getByTestId('error-notification-exclusions');
  await within(behavior).findByText('Source · code79');
  expect(within(screen.getByTestId('notification-source-lockerUnread')).queryByTestId('error-notification-exclusions')).toBeNull();
  fireEvent.click(within(behavior).getByRole('checkbox', { name: 'Source · code79' }));
  await waitFor(() => expect((within(global).getByRole('checkbox', { name: 'Source · code79' }) as HTMLInputElement).checked).toBe(true));
  fireEvent.click(within(global).getByRole('checkbox', { name: 'Enable error notification exclusions' }));
  await waitFor(() => expect(useAppSettingsStore.getState().errorNotificationExclusions.enabled).toBe(true));
  fireEvent.click(within(behavior).getByRole('checkbox', { name: 'Enable error notification exclusions' }));
  await waitFor(() => expect(useAppSettingsStore.getState().errorNotificationExclusions).toEqual({ enabled: false, types: ['source/code79'] }));
  expect(updateUiSettings).toHaveBeenLastCalledWith({ errorNotificationExclusions: { enabled: false, types: ['source/code79'] } });
});

it('searches a bounded scroll window with stable checkbox selection', async () => {
  render(<SessionErrorBridgeSetting />);
  await screen.findByText('Source · code79');
  const list = screen.getByLabelText('Error types');
  expect(list.style.height).toBe('160px');
  expect(list.style.overflowY).toBe('auto');
  fireEvent.scroll(list, { target: { scrollTop: 1000 } });
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'CODE79' } });
  expect(within(list).getAllByRole('checkbox')).toHaveLength(1);
  fireEvent.click(within(list).getByRole('checkbox'));
  await waitFor(() => expect(screen.getByText('1 selected · 1 of 80 types')).toBeTruthy());
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'no match' } });
  expect(screen.getByText('No error types match.')).toBeTruthy();
});

it('failed write leaves both saved values unchanged and displays the failure', async () => {
  vi.mocked(updateUiSettings).mockRejectedValueOnce(new Error('Disk write failed'));
  render(<SessionErrorBridgeSetting />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Enable error notification exclusions' }));
  expect((await screen.findByRole('alert')).textContent).toBe('Disk write failed');
  expect(useAppSettingsStore.getState().errorNotificationExclusions).toEqual({ enabled: false, types: [] });
});

it('legacy missing settings normalize to disabled with no selection', () => {
  expect(sanitizeErrorExclusions(undefined)).toEqual({ enabled: false, types: [] });
});

it('searches literal tool_failure and shares kind selections while preserving saved provider types', async () => {
  const types = [{ id: 'kind/tool_failure', label: 'Pan kind · tool_failure' },
    { id: 'cli.error/rate_limit', label: 'cli.error · rate_limit' }];
  vi.mocked(lockerRequest).mockResolvedValue({ types });
  useAppSettingsStore.setState({ errorNotificationExclusions: { enabled: false, types: ['cli.error/rate_limit'] } });
  render(<><SessionErrorBridgeSetting /><SourceNotificationPanel source="newError" /></>);
  const behavior = within(screen.getByTestId('session-error-bridge-section'));
  const global = within(screen.getByTestId('notification-source-newError'));
  await behavior.findByRole('checkbox', { name: 'Pan kind · tool_failure' });
  fireEvent.change(behavior.getByRole('searchbox'), { target: { value: 'TOOL_FAILURE' } });
  expect(within(behavior.getByLabelText('Error types')).getAllByRole('checkbox')).toHaveLength(1);
  fireEvent.click(behavior.getByRole('checkbox', { name: 'Pan kind · tool_failure' }));
  await waitFor(() => expect((global.getByRole('checkbox', { name: 'Pan kind · tool_failure' }) as HTMLInputElement).checked).toBe(true));
  expect((global.getByRole('checkbox', { name: 'cli.error · rate_limit' }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(global.getByRole('checkbox', { name: 'Enable error notification exclusions' }));
  await waitFor(() => expect(useAppSettingsStore.getState().errorNotificationExclusions).toEqual({ enabled: true, types: ['cli.error/rate_limit', 'kind/tool_failure'] }));
  expect(updateUiSettings).toHaveBeenLastCalledWith({ errorNotificationExclusions: { enabled: true, types: ['cli.error/rate_limit', 'kind/tool_failure'] } });
});

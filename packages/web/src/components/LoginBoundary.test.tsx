// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LoginBoundary } from './LoginBoundary';
import { useAutomationAuth } from '@/stores/automationStore';

const loadSettings = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock('@/stores/appSettingsStore', () => ({ useAppSettingsStore: { getState: () => ({ loadSettings }) } }));
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
afterEach(() => { cleanup(); useAutomationAuth.getState().clear(); vi.unstubAllGlobals(); loadSettings.mockClear(); });

it('mounts no business child before authentication and keeps login credentials out of state', async () => {
  let restore!: (value: unknown) => void;
  const fetch = vi.fn().mockImplementationOnce(() => new Promise(resolve => { restore = resolve; }))
    .mockResolvedValueOnce(response({ token: 'must-not-be-retained', expiresIn: 2592000 }));
  vi.stubGlobal('fetch', fetch);
  const mounted = vi.fn();
  function Business() { mounted(); return <p>Business content</p>; }
  render(<LoginBoundary><Business /></LoginBoundary>);
  expect(mounted).not.toHaveBeenCalled();
  await act(async () => restore(response({ authenticated: false, expiresAt: null, sessionDays: 30 })));
  expect(screen.queryByText('Business content')).toBeNull();
  expect(loadSettings).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('管理员密码'), { target: { value: 'owned-test-input' } });
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }));
  await screen.findByText('Business content');
  expect(useAutomationAuth.getState()).toMatchObject({ token: null, authenticated: true });
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
  expect(loadSettings).toHaveBeenCalledTimes(1);
});

it('restores a cookie session and hides business content immediately after revocation', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ authenticated: true, expiresAt: 2000000000, sessionDays: 7 })));
  render(<LoginBoundary><p>Owned business</p></LoginBoundary>);
  await screen.findByText('Owned business');
  expect(screen.queryByLabelText('管理员密码')).toBeNull();
  act(() => useAutomationAuth.getState().clear());
  await waitFor(() => expect(screen.queryByText('Owned business')).toBeNull());
  expect(screen.getByLabelText('管理员密码')).toBeTruthy();
});

// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { automationRequest, restoreAutomationSession, useAutomationAuth } from './automationStore';
import { JobUserAuthorization } from '@/components/automation/JobUserAuthorization';
import { AuthorizationSettings } from '@/components/layout/AuthorizationSettings';
import { actionRequest } from '@/services/api';

const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
afterEach(() => { cleanup(); useAutomationAuth.getState().clear(); vi.unstubAllGlobals(); });

it('restores the shared login on startup without a Bearer and renders logout instead of a required key', async () => {
  const fetch = vi.fn().mockResolvedValue(response({ authenticated: true, expiresAt: 2000000000, sessionDays: 30 }));
  vi.stubGlobal('fetch', fetch);
  render(<JobUserAuthorization />);
  expect(screen.getByLabelText('Local user key')).toBeTruthy();
  await act(() => restoreAutomationSession());
  expect(fetch).toHaveBeenCalledWith('/api/automation/session', { credentials: 'same-origin' });
  expect(useAutomationAuth.getState()).toMatchObject({ token: null, authenticated: true, sessionDays: 30, expiresAt: 2000000000000 });
  expect(screen.queryByLabelText('Local user key')).toBeNull();
  expect(screen.getByRole('button', { name: 'Log out' })).toBeTruthy();
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});

it.each([automationRequest, actionRequest])('uses Cookie credentials without a Bearer and lets the server decide sliding expiry (%#)', async (request) => {
  useAutomationAuth.setState({ authenticated: true, token: null, expiresAt: 1 });
  const fetch = vi.fn().mockResolvedValue(response({ ok: true }));
  vi.stubGlobal('fetch', fetch);
  await request('automation/settings', 'PUT', { sessionDays: 7 });
  expect(fetch.mock.calls[0]?.[1]).toMatchObject({ credentials: 'same-origin', method: 'PUT' });
  expect(fetch.mock.calls[0]?.[1].headers).not.toHaveProperty('Authorization');
  expect(useAutomationAuth.getState().authenticated).toBe(true);
});

it.each([automationRequest, actionRequest])('clears restored Cookie login on 401 (%#)', async (request) => {
  useAutomationAuth.setState({ authenticated: true, token: null });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({}, 401)));
  await expect(request('automation/authorization')).rejects.toMatchObject({ status: 401 });
  expect(useAutomationAuth.getState().authenticated).toBe(false);
});

it('does not revive login when a pending startup probe finishes after logout', async () => {
  let resolve!: (value: ReturnType<typeof response>) => void;
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise((done) => { resolve = done; })));
  const pending = restoreAutomationSession();
  useAutomationAuth.getState().clear();
  resolve(response({ authenticated: true, expiresAt: 2000000000, sessionDays: 90 }));
  await pending;
  expect(useAutomationAuth.getState().authenticated).toBe(false);
});

it('logs out through the Cookie and clears the login form state', async () => {
  useAutomationAuth.setState({ authenticated: true, token: null });
  const fetch = vi.fn().mockResolvedValue(response({ ok: true }));
  vi.stubGlobal('fetch', fetch);
  render(<JobUserAuthorization />);
  fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
  await screen.findByLabelText('Local user key');
  expect(fetch).toHaveBeenCalledWith('/api/automation/logout', expect.objectContaining({ credentials: 'same-origin', method: 'POST' }));
});

it('saves the global duration and reloads it from the server after remount', async () => {
  useAutomationAuth.setState({ authenticated: true, token: null });
  let sessionDays = 0;
  const fetch = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/settings')) {
      sessionDays = (JSON.parse(options?.body as string) as { sessionDays: number }).sessionDays;
      return response({ sessionDays });
    }
    return response({ configured: true, revision: 'key-rev', authorizationRevision: 0, permissions: null, sessionDays });
  });
  vi.stubGlobal('fetch', fetch);
  const view = render(<AuthorizationSettings />);
  await screen.findByText(/key configured: Yes/);
  expect(screen.queryByLabelText('Current local password')).toBeNull();
  const select = screen.getByLabelText('Long login duration') as HTMLSelectElement;
  expect(Array.from(select.options).map((option) => option.value)).toEqual(['0', '7', '30', '90']);
  fireEvent.change(select, { target: { value: '90' } });
  await screen.findByText('Login duration saved for this profile.');
  expect(fetch).toHaveBeenCalledWith('/api/automation/settings', expect.objectContaining({
    method: 'PUT', credentials: 'same-origin', body: '{"sessionDays":90}',
  }));
  view.unmount();
  render(<AuthorizationSettings />);
  await waitFor(() => expect((screen.getByLabelText('Long login duration') as HTMLSelectElement).value).toBe('90'));
  expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
});

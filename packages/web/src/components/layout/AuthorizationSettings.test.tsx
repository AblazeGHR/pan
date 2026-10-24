// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AuthorizationSettings } from './AuthorizationSettings';
import { useAutomationAuth } from '@/stores/automationStore';
const permissions = {
  trigger: { read: false, create: false, updateConfig: false, lifecycle: false, delete: false },
  action: { read: false, create: false, updateConfig: false, lifecycle: false, delete: false, manageSystemActions: false },
  jobTemplate: { read: false, create: false, updateConfig: false, use: false, delete: false },
  job: { read: false, create: false, updateConfig: false, lifecycle: false, execute: false, delete: false },
};
afterEach(() => { cleanup(); useAutomationAuth.getState().clear(); vi.unstubAllGlobals(); });
it('loads Agent matrix with user Bearer and saves exact permission CAS', async () => {
  useAutomationAuth.setState({ token: 'user-token', expiresAt: Date.now() + 60000 });
  const fetch = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ configured: true, revision: 'pw', authorizationRevision: 4, permissions }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ revision: 5, permissions: { ...permissions, job: { ...permissions.job, execute: true } } }) });
  vi.stubGlobal('fetch', fetch);render(<AuthorizationSettings />);
  const execute = await screen.findByLabelText('job.execute');
  expect(screen.queryByLabelText('jobTemplate.lifecycle')).toBeNull();
  expect(screen.queryByLabelText('jobTemplate.execute')).toBeNull();
  expect((screen.getByLabelText('action.manageSystemActions') as HTMLInputElement).checked).toBe(false);
  fireEvent.click(execute);fireEvent.click(screen.getByRole('button', { name: 'Save permissions' }));
  await screen.findByText('Permission matrix saved and active for new operations.');
  const [path, options] = fetch.mock.calls[1] as [string, RequestInit];
  expect(path).toBe('/api/automation/authorization/permissions');
  expect(options.headers).toMatchObject({ Authorization: 'Bearer user-token' });
  expect(JSON.parse(options.body as string)).toEqual({ expectedRevision: 4, permissions: { ...permissions, job: { ...permissions.job, execute: true } } });
  expect(useAutomationAuth.getState().token).toBe('user-token');
});
it('preserves draft after CAS conflict and never treats conflict as save', async () => {
  useAutomationAuth.setState({ token: 'user-token', expiresAt: Date.now() + 60000 });
  const fetch = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ configured: true, revision: 'pw', authorizationRevision: 2, permissions }) })
    .mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({ detail: 'Authorization permission revision conflict' }) });
  vi.stubGlobal('fetch', fetch);render(<AuthorizationSettings />);
  fireEvent.click(await screen.findByLabelText('trigger.create'));
  fireEvent.click(screen.getByRole('button', { name: 'Save permissions' }));
  expect((await screen.findByRole('alert')).textContent).toContain('revision conflict');
  expect((screen.getByLabelText('trigger.create') as HTMLInputElement).checked).toBe(true);
  expect(screen.queryByText('Permission matrix saved and active for new operations.')).toBeNull();
});

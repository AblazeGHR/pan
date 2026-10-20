// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LoginStartupSettings } from './LoginStartupSettings';

const status = (enabled = false) => ({ enabled, registered: enabled, available: true,
  conflict: false, blocked: false, reason: '', targetRoot: '/owned/中文 Pan',
  dataDirectory: '/owned/中文 Pan/data', entry: 'owned entry', registration: null, revision: enabled ? 'on' : 'off' });
const response = (body: object, ok = true) => Promise.resolve({ ok, json: async () => body });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('native login startup settings', () => {
  it('only reads on mount; serializes clicks and displays confirmed state', async () => {
    let finish: (value: unknown) => void = () => {};
    const fetch = vi.fn().mockImplementationOnce(() => response(status()))
      .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    vi.stubGlobal('fetch', fetch);
    render(<LoginStartupSettings />);
    const toggle = screen.getByRole('switch', { name: 'Start Pan after login' });
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(toggle); fireEvent.click(toggle);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ enabled: true, revision: 'off' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    finish({ ok: true, json: async () => status(true) });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    expect(screen.getByText(/not a system service/)).toBeTruthy();
  });

  it('re-reads actual state on failure and allows explicit retry', async () => {
    const fetch = vi.fn().mockImplementationOnce(() => response(status()))
      .mockImplementationOnce(() => response({ detail: 'Permission denied' }, false))
      .mockImplementationOnce(() => response(status()));
    vi.stubGlobal('fetch', fetch);
    render(<LoginStartupSettings />);
    const toggle = screen.getByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));
    fireEvent.click(toggle);
    await screen.findByRole('alert');
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(toggle.hasAttribute('disabled')).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('shows unknown when failure cannot be reconciled', async () => {
    const fetch = vi.fn().mockImplementationOnce(() => response(status()))
      .mockRejectedValue(new Error('network unavailable'));
    vi.stubGlobal('fetch', fetch);
    render(<LoginStartupSettings />);
    const toggle = screen.getByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));
    fireEvent.click(toggle);
    await screen.findByRole('alert');
    expect(toggle.hasAttribute('disabled')).toBe(true);
    expect(screen.getByText('Unknown')).toBeTruthy();
  });

  it('displays a foreign entry and prevents mutation', async () => {
    vi.stubGlobal('fetch', vi.fn(() => response({ ...status(), available: false,
      conflict: true, registered: true, registration: '/other/checkout/start', reason: 'Foreign entry' })));
    render(<LoginStartupSettings />);
    await screen.findByText('/other/checkout/start');
    expect(screen.getByRole('switch').hasAttribute('disabled')).toBe(true);
    expect(screen.getByRole('status').textContent).toBe('Foreign entry');
  });
});

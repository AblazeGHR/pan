// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CodexNativePassthrough } from './CodexNativePassthrough';
import { useNativeControlStore as store } from '@/stores/nativeControlStore';

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('@/services/nativeControl', () => ({ nativeRequest: request }));
const pan = {
  workerId: 'w',
  workerGeneration: 1,
  mode: 'pan' as const,
  generation: 0,
  error: null,
};
beforeEach(() => {
  request.mockReset();
  store.setState({ sessions: {}, switching: {}, terminalViews: {} });
});
afterEach(cleanup);

describe('Codex native passthrough policy', () => {
  it('does not offer a mutation without a selected Codex Session', () => {
    render(<CodexNativePassthrough sessionId={null} />);
    expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/请先选择/)).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });
  it('is default off, hot-saves only this Session, and labels the global-config risk', async () => {
    request.mockResolvedValueOnce({ ...pan, passthrough: false, settingsUncertain: false });
    render(<CodexNativePassthrough sessionId="s" />);
    const checkbox = screen.getByRole('checkbox') as HTMLInputElement;
    await waitFor(() => expect(checkbox.disabled).toBe(false));
    expect(checkbox.checked).toBe(false);
    expect(screen.getByText(/Codex 全局配置/)).toBeTruthy();
    request.mockResolvedValueOnce({ ...pan, passthrough: true, settingsUncertain: false });
    fireEvent.click(checkbox);
    await waitFor(() => expect(checkbox.checked).toBe(true));
    expect(request).toHaveBeenLastCalledWith('s', '/passthrough', { enabled: true });
    expect(store.getState().sessions.s?.mode).toBe('pan');
    expect(store.getState().terminalViews).toEqual({});
  });
  it('keeps uncertainty after disabling and clears it only on authoritative refresh', async () => {
    request.mockResolvedValueOnce({ ...pan, passthrough: true, settingsUncertain: true });
    render(<CodexNativePassthrough sessionId="s" />);
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/可能与事实不符/));
    request.mockResolvedValueOnce({ ...pan, passthrough: false, settingsUncertain: true });
    fireEvent.click(screen.getByRole('checkbox'));
    await waitFor(() =>
      expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false),
    );
    expect(screen.getByRole('status').textContent).toMatch(/可能与事实不符/);
    request.mockResolvedValueOnce({
      ...pan,
      workerGeneration: 2,
      settingsUncertain: false,
      passthrough: false,
    });
    await store.getState().refresh('s');
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  });
  it('lost receipt reconciles saved intent but never pretends the kernel confirmed', async () => {
    request.mockResolvedValueOnce({ ...pan, passthrough: false });
    render(<CodexNativePassthrough sessionId="s" />);
    await waitFor(() =>
      expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(false),
    );
    request.mockRejectedValueOnce(new Error('native-policy-unconfirmed'));
    request.mockResolvedValueOnce({ ...pan, passthrough: true, passthroughPending: true });
    fireEvent.click(screen.getByRole('checkbox'));
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(screen.getByRole('status').textContent).toMatch(/尚未确认/);
    expect(store.getState().sessions.s?.mode).toBe('pan');
  });
});

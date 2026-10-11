// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, act } from '@testing-library/react';
import { UnreadRecordingPanel } from './UnreadRecordingPanel';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { UnreadRecordingScope } from '@/stores/appSettingsStore';
import { reapplyUnreadRecordingSettings, updateUiSettings } from '@/services/api';

const { reapplyMock } = vi.hoisted(() => ({ reapplyMock: vi.fn() }));

vi.mock('@/services/api', () => ({
  fetchUiSettings: vi.fn(async () => ({})),
  updateUiSettings: vi.fn(async () => ({})),
  reapplyUnreadRecordingSettings: reapplyMock,
}));

const mockedUpdate = vi.mocked(updateUiSettings);
const mockedReapply = vi.mocked(reapplyUnreadRecordingSettings);

function resetStore(unreadRecording: UnreadRecordingScope[] = ['all']) {
  useAppSettingsStore.setState({
    ...DEFAULT_SETTINGS,
    notifications: { ...DEFAULT_SETTINGS.notifications, unreadRecording },
    loaded: true,
  });
}

function checked(testId: string): boolean {
  return (document.querySelector(`[data-testid="${testId}"]`) as HTMLInputElement).checked;
}

describe('UnreadRecordingPanel', () => {
  beforeEach(() => {
    resetStore();
    mockedUpdate.mockReset();
    // The real PUT returns the full canonical ui object; echo the unread key.
    mockedUpdate.mockImplementation(async (patch) => {
      const notifications = (patch as { notifications?: Record<string, unknown> }).notifications;
      return notifications ? { notifications } : {};
    });
    mockedReapply.mockReset();
    mockedReapply.mockResolvedValue({ ok: true, alignedSessionCount: 1, failedSessions: [] });
  });
  afterEach(() => cleanup());

  it('renders the five scopes from the persisted selection', () => {
    resetStore(['bell-on', 'pinned']);
    render(<UnreadRecordingPanel />);
    expect(checked('unread-recording-bell-on')).toBe(true);
    expect(checked('unread-recording-pinned')).toBe(true);
    expect(checked('unread-recording-bell-off')).toBe(false);
    expect(checked('unread-recording-all')).toBe(false);
    expect(checked('unread-recording-none')).toBe(false);
  });

  it('keeps the legacy all default visible for old settings', () => {
    render(<UnreadRecordingPanel />);
    expect(checked('unread-recording-all')).toBe(true);
    expect(checked('unread-recording-none')).toBe(false);
  });

  it('enforces exclusivity while editing and saves the canonical set', async () => {
    render(<UnreadRecordingPanel />);
    fireEvent.click(document.querySelector('[data-testid="unread-recording-bell-on"]')!);
    // A category clears All / No sessions.
    expect(checked('unread-recording-all')).toBe(false);
    expect(checked('unread-recording-bell-on')).toBe(true);
    // Bell on / Bell off are mutually exclusive.
    fireEvent.click(document.querySelector('[data-testid="unread-recording-bell-off"]')!);
    expect(checked('unread-recording-bell-on')).toBe(false);
    expect(checked('unread-recording-bell-off')).toBe(true);
    // Categories combine into a union.
    fireEvent.click(document.querySelector('[data-testid="unread-recording-pinned"]')!);
    expect(checked('unread-recording-bell-off')).toBe(true);
    expect(checked('unread-recording-pinned')).toBe(true);
    await act(async () => {
      fireEvent.click(document.querySelector('[data-testid="unread-recording-save"]')!);
    });
    expect(mockedUpdate).toHaveBeenCalledWith({
      notifications: { unreadRecording: ['bell-off', 'pinned'] },
    });
    expect(useAppSettingsStore.getState().notifications.unreadRecording)
      .toEqual(['bell-off', 'pinned']);
  });

  it('clearing the last category falls back to No sessions', () => {
    resetStore(['pinned']);
    render(<UnreadRecordingPanel />);
    fireEvent.click(document.querySelector('[data-testid="unread-recording-pinned"]')!);
    expect(checked('unread-recording-none')).toBe(true);
    expect(checked('unread-recording-pinned')).toBe(false);
  });

  it('choosing All clears the categories and No sessions', () => {
    resetStore(['bell-off', 'pinned']);
    render(<UnreadRecordingPanel />);
    fireEvent.click(document.querySelector('[data-testid="unread-recording-all"]')!);
    expect(checked('unread-recording-all')).toBe(true);
    expect(checked('unread-recording-bell-off')).toBe(false);
    expect(checked('unread-recording-pinned')).toBe(false);
    expect(checked('unread-recording-none')).toBe(false);
  });

  it('is a no-op to disable No sessions (empty set means none)', () => {
    resetStore(['none']);
    render(<UnreadRecordingPanel />);
    fireEvent.click(document.querySelector('[data-testid="unread-recording-none"]')!);
    expect(checked('unread-recording-none')).toBe(true);
  });

  it('reports a failed save and keeps the unsaved draft', async () => {
    mockedUpdate.mockRejectedValueOnce(new Error('backend down'));
    render(<UnreadRecordingPanel />);
    fireEvent.click(document.querySelector('[data-testid="unread-recording-bell-on"]')!);
    await act(async () => {
      fireEvent.click(document.querySelector('[data-testid="unread-recording-save"]')!);
    });
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('backend down');
    // The persisted value is unchanged; the draft keeps the user's intent.
    expect(useAppSettingsStore.getState().notifications.unreadRecording).toEqual(['all']);
    expect(checked('unread-recording-bell-on')).toBe(true);
  });

  it('reloads the draft when another client changes the persisted value', () => {
    render(<UnreadRecordingPanel />);
    expect(checked('unread-recording-all')).toBe(true);
    act(() => { resetStore(['none']); });
    expect(checked('unread-recording-none')).toBe(true);
  });

  it('reapplies the persisted settings even when nothing changed', async () => {
    render(<UnreadRecordingPanel />);
    await act(async () => {
      fireEvent.click(document.querySelector('[data-testid="unread-recording-reapply"]')!);
    });
    expect(mockedReapply).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[role="status"]')?.textContent)
      .toContain('Per-Session overrides were cleared');
  });

  it('reports failed Sessions honestly on reapply', async () => {
    mockedReapply.mockResolvedValueOnce({ ok: true, alignedSessionCount: 0, failedSessions: ['ses_x'] });
    render(<UnreadRecordingPanel />);
    await act(async () => {
      fireEvent.click(document.querySelector('[data-testid="unread-recording-reapply"]')!);
    });
    expect(document.querySelector('[role="status"]')?.textContent)
      .toContain('1 Session(s) could not be aligned');
  });

  it('shows an error when reapply fails', async () => {
    mockedReapply.mockRejectedValueOnce(new Error('backend down'));
    render(<UnreadRecordingPanel />);
    await act(async () => {
      fireEvent.click(document.querySelector('[data-testid="unread-recording-reapply"]')!);
    });
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('backend down');
  });

  it('reports a superseded reapply instead of claiming alignment', async () => {
    mockedReapply.mockResolvedValueOnce({
      ok: true, alignedSessionCount: 0, failedSessions: [], superseded: true,
    });
    render(<UnreadRecordingPanel />);
    await act(async () => {
      fireEvent.click(document.querySelector('[data-testid="unread-recording-reapply"]')!);
    });
    expect(document.querySelector('[role="status"]')?.textContent)
      .toContain('A newer settings apply superseded this reapply');
  });

  it('surfaces partial apply failures while keeping the saved values', async () => {
    mockedUpdate.mockResolvedValueOnce({
      notifications: { unreadRecording: ['none'] },
      unreadRecordingApply: { ok: false, failedSessions: ['ses_a'] },
    });
    render(<UnreadRecordingPanel />);
    fireEvent.click(document.querySelector('[data-testid="unread-recording-none"]')!);
    await act(async () => {
      fireEvent.click(document.querySelector('[data-testid="unread-recording-save"]')!);
    });
    expect(document.querySelector('[role="alert"]')?.textContent)
      .toContain('1 Session(s) could not be aligned');
    // The saved scope set is the persisted truth and stays displayed.
    expect(checked('unread-recording-none')).toBe(true);
    expect(useAppSettingsStore.getState().notifications.unreadRecording).toEqual(['none']);
  });
});

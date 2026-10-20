// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Session } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { setReportsToManager, setSessionMsgBridge } from '@/services/api';
import {
  notificationDisabled,
  reportToManagerDisabled,
  runSessionToggleShortcut,
} from './sessionQuickActions';

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    setReportsToManager: vi.fn(),
    setSessionMsgBridge: vi.fn(),
  };
});

function session(overrides: Partial<Session> = {}): Session {
  return {
    id: 's1',
    name: 'Selected',
    adapter: 'cbc',
    model: null,
    permissionMode: null,
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'idle',
    history: [],
    ...overrides,
  } as Session;
}

function installSession(current: Session, all: Session[] = [current]): void {
  const loadSessions = vi.fn(async () => undefined);
  useSessionStore.setState({
    currentSessionId: current.id,
    sessions: all,
    loadSessions,
  });
}

beforeEach(() => {
  vi.mocked(setReportsToManager).mockReset().mockResolvedValue(undefined as never);
  vi.mocked(setSessionMsgBridge).mockReset().mockResolvedValue(undefined as never);
  useSessionStore.setState({ currentSessionId: null, sessions: [] });
  useUIStore.setState({ showToast: vi.fn(), toastQueue: [] });
});

describe('selected Session shortcut toggles', () => {
  it('reuses the card Stop report semantics and server-confirmed refresh', async () => {
    const current = session({ managedBy: 'm1', reportsToManager: true });
    installSession(current);
    await expect(runSessionToggleShortcut('s1', 'stopReport')).resolves.toBe('ok');
    expect(setReportsToManager).toHaveBeenCalledWith('s1', 'm1', false);
    expect(useSessionStore.getState().loadSessions).toHaveBeenCalledTimes(1);
  });

  it('is disabled without a manager exactly like the card', async () => {
    installSession(session({ reportsToManager: false }));
    expect(reportToManagerDisabled(useSessionStore.getState().sessions[0]!)).toBe(true);
    await expect(runSessionToggleShortcut('s1', 'stopReport')).resolves.toBe('disabled');
    expect(setReportsToManager).not.toHaveBeenCalled();
  });

  it('reuses the card notification semantics and blocks readonly', async () => {
    installSession(session({ msgBridgeEnabled: false }));
    await expect(runSessionToggleShortcut('s1', 'notification')).resolves.toBe('ok');
    expect(setSessionMsgBridge).toHaveBeenCalledWith('s1', true);

    const readonly = session({ readonlySession: true, msgBridgeEnabled: true });
    installSession(readonly);
    expect(notificationDisabled(readonly)).toBe(true);
    await expect(runSessionToggleShortcut('s1', 'notification')).resolves.toBe('disabled');
    expect(setSessionMsgBridge).toHaveBeenCalledTimes(1);
  });

  it('does not retry a failed write and reports it once', async () => {
    installSession(session({ managedBy: 'm1', reportsToManager: true }));
    vi.mocked(setReportsToManager).mockRejectedValueOnce(new Error('offline'));
    await expect(runSessionToggleShortcut('s1', 'stopReport')).resolves.toBe('failed');
    expect(setReportsToManager).toHaveBeenCalledTimes(1);
    expect(useUIStore.getState().showToast).toHaveBeenCalledTimes(1);
  });

  it('keeps acting on the captured Session after an async switch', async () => {
    let resolveWrite!: () => void;
    vi.mocked(setReportsToManager).mockReturnValueOnce(
      new Promise<void>((resolve) => { resolveWrite = resolve; }) as never,
    );
    const first = session({ id: 's1', managedBy: 'm1', reportsToManager: true });
    const second = session({ id: 's2', managedBy: 'm2', reportsToManager: true });
    installSession(first, [first, second]);

    const pending = runSessionToggleShortcut('s1', 'stopReport');
    useSessionStore.setState({ currentSessionId: 's2' });
    resolveWrite();
    await expect(pending).resolves.toBe('ok');

    expect(setReportsToManager).toHaveBeenCalledTimes(1);
    expect(setReportsToManager).toHaveBeenCalledWith('s1', 'm1', false);
  });
});

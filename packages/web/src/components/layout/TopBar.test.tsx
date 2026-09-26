// @vitest-environment jsdom
import { Profiler } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TopBar } from './TopBar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import * as api from '@/services/api';

function mockMatchMedia() {
  vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

beforeEach(() => {
  mockMatchMedia();
  useSessionStore.setState({
    currentSessionId: 'session-123456789',
    sessions: [{
      id: 'session-123456789', name: 'Session', cliSessionId: 'cli-session-123', model: 'secret-model',
      workerStatus: 'running', workerId: 'worker-123', alwaysThinkingEnabled: false, effort: '', history: [],
    }],
  });
  useUIStore.setState({ toastQueue: [] });
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  useWorkerStore.setState({
    currentWorker: {
      id: 'worker-123', sessionId: 'session-123456789', status: 'running',
      nativeStatus: { type: 'active', activeFlags: ['waitingOnApproval'] },
    },
    restart: vi.fn(async () => {}),
    interrupt: vi.fn(async () => {}),
    takeover: vi.fn(async () => ({})),
    killCurrent: vi.fn(async () => {}),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('TopBar compact worker presentation', () => {
  it('keeps its chat style action synchronized with the persistent app setting', () => {
    render(<TopBar />);
    const toggle = screen.getByRole('button', { name: 'Switch to Bubble view' });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(toggle);
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('bubble');
    expect(toggle.getAttribute('aria-label')).toBe('Switch to TUI view');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
  });

  it('hides model/status/worker text while retaining the dot and worker actions', () => {
    render(<TopBar />);

    expect(screen.queryByText('secret-model')).toBeNull();
    expect(screen.queryByText(/waiting for approval|active|running|worker-123|no worker/)).toBeNull();
    expect(screen.getByTitle('running')).toBeTruthy();
    expect(screen.getByTitle('Restart worker')).toBeTruthy();
    expect(screen.getByTitle('Interrupt')).toBeTruthy();
    expect(screen.getByTitle('Takeover')).toBeTruthy();
    expect(screen.getByTitle('Kill worker')).toBeTruthy();
    expect(screen.getByTitle('Copy session ID')).toBeTruthy();
    expect(screen.getByTitle('Copy CLI session ID')).toBeTruthy();
  });

  it('shows persisted five-hour and weekly quota when the worker is offline', async () => {
    useSessionStore.setState({
      currentSessionId: 'session-codex',
      sessions: [{
        id: 'session-codex', name: 'Codex', adapter: 'codex', cliSessionId: 'cli-codex',
        workerStatus: 'offline', workerId: null, alwaysThinkingEnabled: false, effort: '', history: [],
      }],
    });
    useWorkerStore.setState({ currentWorker: null, currentWorkerId: null, workers: {} });
    vi.spyOn(api, 'fetchSessionUsage').mockResolvedValue({
      sessionId: 'session-codex', adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
      codexQuota: {
        ok: true, cacheMode: 'persisted', profileKey: 'profile-a',
        windows: {
          first: { kind: 'five_hour', usage: { usedPercent: 20 } },
          secondary: { kind: 'weekly', usage: { usedPercent: 45 } },
        },
      },
    });

    render(<TopBar />);

    expect(await screen.findByText('quota 5h 20% / 周 45%')).toBeTruthy();
    expect(screen.getByTitle('Codex account rate-limit usage (persisted profile cache)')).toBeTruthy();
    expect(api.fetchSessionUsage).toHaveBeenCalledWith('session-codex');
  });

  it('does not display a quota label for missing or unknown windows', async () => {
    useSessionStore.setState({
      currentSessionId: 'session-codex',
      sessions: [{
        id: 'session-codex', name: 'Codex', adapter: 'codex', cliSessionId: 'cli-codex',
        workerStatus: 'offline', workerId: null, alwaysThinkingEnabled: false, effort: '', history: [],
      }],
    });
    useWorkerStore.setState({ currentWorker: null, currentWorkerId: null, workers: {} });
    vi.spyOn(api, 'fetchSessionUsage').mockResolvedValue({
      sessionId: 'session-codex', adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
      codexQuota: {
        ok: true,
        windows: {
          first: { kind: 'unknown', usage: { usedPercent: 20 } },
          secondary: { kind: 'weekly', usage: {} },
        },
      },
    });

    render(<TopBar />);

    await waitFor(() => expect(api.fetchSessionUsage).toHaveBeenCalledWith('session-codex'));
    expect(screen.queryByTitle('Codex account rate-limit usage (persisted profile cache)')).toBeNull();
    expect(screen.queryByText(/quota/)).toBeNull();
  });
});

// FE-1: TopBar must only re-render for the slice it reads. Toasts, interactive
// requests and other sessions' worker updates are irrelevant to it.
describe('TopBar render isolation (fine-grained selectors)', () => {
  it('ignores unrelated UI-store and worker-store updates', () => {
    const commits: number[] = [];
    render(
      <Profiler id="topbar" onRender={() => commits.push(1)}>
        <TopBar />
      </Profiler>,
    );
    const afterMount = commits.length;
    expect(afterMount).toBeGreaterThan(0);

    act(() => {
      useUIStore.setState({ toastQueue: [{ id: 't1', message: 'hi', type: 'info' }] });
      useUIStore.setState({
        approvalRequests: [
          { sessionId: 'other', workerId: 'w', requestId: 1, method: 'm', params: {} },
        ],
      });
    });
    act(() => {
      // Another session's worker changes; currentWorker keeps its identity.
      useWorkerStore.setState((s) => ({
        workers: {
          ...s.workers,
          other: { id: 'w-other', sessionId: 'other', status: 'running' },
        },
        workerTouchedSeq: { ...s.workerTouchedSeq, other: 1 },
      }));
    });
    expect(commits.length).toBe(afterMount);
  });
});

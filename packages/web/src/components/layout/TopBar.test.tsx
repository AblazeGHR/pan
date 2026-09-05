// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { TopBar } from './TopBar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';

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
});

describe('TopBar compact worker presentation', () => {
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
});

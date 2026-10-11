// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { useSessionDeepLinkParam } from '@/utils/session-deeplink';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

// The hook selects via the store (no API beyond the history fetch).
vi.mock('@/services/api', () => ({
  fetchSessionHistory: vi.fn().mockResolvedValue({ history: [], total: 0, hasMore: false, start: 0 }),
}));

function mkSession(id: string): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: 'idle',
    workerId: 'w1',
  };
}

function Harness() {
  useSessionDeepLinkParam();
  return null;
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-search">{location.search}</div>;
}

function renderAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <LocationProbe />
      <Harness />
    </MemoryRouter>,
  );
}

function currentSearch(): string {
  return screen.getByTestId('location-search').textContent ?? '';
}

describe('useSessionDeepLinkParam', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [mkSession('A')],
      currentSessionId: null,
      currentMessages: [],
      sessionsLoading: false,
      _loadSeq: 1,
    });
    useUIStore.setState({ toastQueue: [] });
  });

  afterEach(() => {
    cleanup();
  });

  it('selects an existing session from ?session= and clears the param', async () => {
    renderAt('/?session=A');

    await vi.waitFor(() => {
      expect(useSessionStore.getState().currentSessionId).toBe('A');
    });
    await vi.waitFor(() => {
      expect(currentSearch()).toBe('');
    });
    expect(useUIStore.getState().toastQueue).toHaveLength(0);
  });

  it('degrades with a toast when the addressed session is missing after the list settled', async () => {
    renderAt('/?session=gone');

    await vi.waitFor(() => {
      expect(useUIStore.getState().toastQueue.at(-1)?.message)
        .toBe('通知对应的会话不存在或已被删除，无法跳转');
    });
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(currentSearch()).toBe('');
  });

  it('does not declare missing while the first session load is still in flight', async () => {
    useSessionStore.setState({ sessions: [], sessionsLoading: true, _loadSeq: 1 });
    renderAt('/?session=gone');

    await act(async () => { await Promise.resolve(); });
    expect(useUIStore.getState().toastQueue).toHaveLength(0);
    expect(currentSearch()).toBe('?session=gone');

    act(() => { useSessionStore.setState({ sessionsLoading: false }); });
    await vi.waitFor(() => {
      expect(useUIStore.getState().toastQueue).toHaveLength(1);
    });
    expect(currentSearch()).toBe('');
  });

  it('rejects a malformed session reference without touching the store', async () => {
    renderAt(`/?session=${encodeURIComponent('bad ref!')}`);

    await vi.waitFor(() => {
      expect(useUIStore.getState().toastQueue.at(-1)?.message).toBe('会话链接无效');
    });
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(currentSearch()).toBe('');
  });
});

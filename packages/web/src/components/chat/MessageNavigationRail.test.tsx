// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { MessageNavigationRail } from './MessageNavigationRail';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { fetchSessionHistory } from '@/services/api';
import type { Message } from '@/types';

vi.mock('@/services/api', () => ({
  fetchSessionHistory: vi.fn(),
}));

const mockedHistory = vi.mocked(fetchSessionHistory);

const USER_MESSAGE: Message = { role: 'user', content: 'hello from the user' };

function historyPage(history: Message[], total = history.length, start = 0) {
  return {
    history,
    total,
    start,
    hasMore: start > 0,
  } as Awaited<ReturnType<typeof fetchSessionHistory>>;
}

/** The rail indexes the whole history on mount, so every test needs a page. */
function seedSession(sessionId: string) {
  useSessionStore.setState({
    currentSessionId: sessionId,
    currentMessages: [USER_MESSAGE],
    historyLoadEnd: 0,
    hasMoreMessages: false,
    sessions: [],
  });
}

const markers = (container: HTMLElement) =>
  [...container.querySelectorAll<HTMLButtonElement>('button.message-navigation-marker')];

beforeEach(() => {
  mockedHistory.mockReset();
  mockedHistory.mockResolvedValue(historyPage([USER_MESSAGE]));
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  seedSession('rail-1');
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('jump single-flight lock across session switches', () => {
  it('re-enables the markers when the session changes while a jump is in flight', async () => {
    // A jump that never resolves: exactly the window in which the old code left
    // `jumpingFromEnd` set forever after the user switched sessions.
    const ensureMessageLoaded = vi.fn(() => new Promise<Message | null>(() => {}));
    useSessionStore.setState({ ensureMessageLoaded });

    const { container } = render(<MessageNavigationRail chatRef={{ current: null }} />);
    await act(async () => {
      await Promise.resolve();
    });

    const firstMarker = markers(container)[0];
    expect(firstMarker).toBeDefined();
    expect(firstMarker!.disabled).toBe(false);

    await act(async () => {
      fireEvent.click(firstMarker!);
    });
    expect(ensureMessageLoaded).toHaveBeenCalledTimes(1);
    // In flight: the lock disables every marker of the current session.
    expect(markers(container).every((marker) => marker.disabled)).toBe(true);

    // Switch sessions while the jump is still pending.
    await act(async () => {
      seedSession('rail-2');
      await Promise.resolve();
    });

    const afterSwitch = markers(container);
    expect(afterSwitch.length).toBeGreaterThan(0);
    expect(afterSwitch.every((marker) => marker.disabled)).toBe(false);

    // …and the new session can start its own jump.
    await act(async () => {
      fireEvent.click(afterSwitch[0]!);
    });
    expect(ensureMessageLoaded).toHaveBeenCalledTimes(2);
  });

  it('keeps the lock while staying in the same session', async () => {
    const ensureMessageLoaded = vi.fn(() => new Promise<Message | null>(() => {}));
    useSessionStore.setState({ ensureMessageLoaded });

    const { container } = render(<MessageNavigationRail chatRef={{ current: null }} />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.click(markers(container)[0]!);
    });
    // A re-render that is not a session switch must not release the lock.
    await act(async () => {
      useSessionStore.setState({ currentMessages: [USER_MESSAGE, { role: 'assistant', content: 'still here' }] });
      await Promise.resolve();
    });
    expect(markers(container).every((marker) => marker.disabled)).toBe(true);
    expect(ensureMessageLoaded).toHaveBeenCalledTimes(1);
  });
});

describe('rail mounting cost', () => {
  it('indexes the full history when it is mounted', async () => {
    const { container } = render(<MessageNavigationRail chatRef={{ current: null }} />);
    await act(async () => {
      await Promise.resolve();
    });

    // Mounting the rail is what triggers the full-history index — the cost the
    // `Show message navigation rail` switch removes by not mounting it at all.
    expect(mockedHistory).toHaveBeenCalled();
    expect(mockedHistory.mock.calls[0]![0]).toBe('rail-1');
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
  });
});

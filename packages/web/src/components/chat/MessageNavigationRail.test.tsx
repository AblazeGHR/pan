// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import type { RefObject } from 'react';
import { MessageNavigationRail } from './MessageNavigationRail';
import { MessageNavigationDock } from './MessageNavigationDock';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { fetchSessionHistory } from '@/services/api';
import type { Message } from '@/types';

vi.mock('@/services/api', () => ({
  fetchSessionHistory: vi.fn(),
}));

const mockedHistory = vi.mocked(fetchSessionHistory);

const USER_MESSAGE: Message = { role: 'user', content: 'hello from the user' };
const SCRUB_MESSAGES: Message[] = [
  { role: 'user', content: 'scrub preview one' },
  { role: 'user', content: 'scrub preview two' },
  { role: 'user', content: 'scrub preview three' },
  { role: 'user', content: 'scrub preview four' },
];

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

function moveTouchPointer(element: HTMLElement, pointerId: number, clientX: number, clientY: number) {
  const event = new Event('pointermove', { bubbles: true });
  Object.defineProperties(event, {
    pointerType: { configurable: true, value: 'touch' },
    pointerId: { configurable: true, value: pointerId },
    clientX: { configurable: true, value: clientX },
    clientY: { configurable: true, value: clientY },
  });
  const originalHitTest = Object.getOwnPropertyDescriptor(document, 'elementFromPoint');
  Object.defineProperty(document, 'elementFromPoint', {
    configurable: true,
    value: () => element,
  });
  try {
    return element.dispatchEvent(event);
  } finally {
    if (originalHitTest) Object.defineProperty(document, 'elementFromPoint', originalHitTest);
    else Reflect.deleteProperty(document, 'elementFromPoint');
  }
}

beforeEach(() => {
  mockedHistory.mockReset();
  mockedHistory.mockResolvedValue(historyPage([USER_MESSAGE]));
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  seedSession('rail-1');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('message navigation dock', () => {
  it('waits to index until desktop hover and keeps the indexed rail mounted when folded', async () => {
    vi.useFakeTimers();
    const dockRef = { current: null } as RefObject<HTMLDivElement | null>;
    const { container } = render(
      <MessageNavigationDock
        chatRef={{ current: null }}
        dockRef={dockRef}
        isMobile={false}
        mobileExpanded={false}
        onMobileClose={() => {}}
        onRestoreFocus={() => {}}
      />,
    );
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    const panel = container.querySelector<HTMLElement>('#message-navigation-panel')!;

    expect(panel.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();

    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    expect(dock.getAttribute('data-expanded')).toBe('true');
    await act(async () => { await Promise.resolve(); });
    expect(panel.getAttribute('aria-hidden')).toBe('false');
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(1);

    fireEvent.pointerLeave(dock, { pointerType: 'mouse' });
    // The short grace period lets a pointer finish an in-rail action before
    // the panel becomes inert, and the rail remains mounted after it folds.
    expect(panel.getAttribute('aria-hidden')).toBe('false');
    await act(async () => { vi.advanceTimersByTime(90); });
    expect(panel.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();

    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    expect(panel.getAttribute('aria-hidden')).toBe('false');
    expect(mockedHistory).toHaveBeenCalledTimes(1);
  });

  it('stays open within the dock, then collapses after pointer click focus leaves', async () => {
    vi.useFakeTimers();
    const dockRef = { current: null } as RefObject<HTMLDivElement | null>;
    const { container } = render(
      <MessageNavigationDock
        chatRef={{ current: null }}
        dockRef={dockRef}
        isMobile={false}
        mobileExpanded={false}
        onMobileClose={() => {}}
        onRestoreFocus={() => {}}
      />,
    );
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    const handle = container.querySelector<HTMLButtonElement>('.message-navigation-dock__handle')!;

    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    expect(dock.getAttribute('data-expanded')).toBe('true');
    fireEvent.pointerOut(dock, { relatedTarget: container.querySelector('#message-navigation-panel') });
    fireEvent.pointerMove(dock, { pointerType: 'mouse' });
    expect(dock.getAttribute('data-expanded')).toBe('true');

    fireEvent.pointerDown(handle, { pointerType: 'mouse' });
    act(() => handle.focus());
    fireEvent.click(handle, { detail: 1 });
    // Hover still holds it open while the pointer is over the dock.
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    expect(dock.getAttribute('data-expanded')).toBe('true');

    fireEvent.pointerLeave(dock, { pointerType: 'mouse' });
    await act(async () => { vi.advanceTimersByTime(90); });
    expect(dock.getAttribute('data-expanded')).toBe('false');
    expect(document.activeElement).toBe(handle);
  });

  it('restores keyboard expansion after pointer focus leaves and supports Escape', async () => {
    vi.useFakeTimers();
    const dockRef = { current: null } as RefObject<HTMLDivElement | null>;
    const { container } = render(
      <MessageNavigationDock
        chatRef={{ current: null }}
        dockRef={dockRef}
        isMobile={false}
        mobileExpanded={false}
        onMobileClose={() => {}}
        onRestoreFocus={() => {}}
      />,
    );
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    const handle = container.querySelector<HTMLButtonElement>('.message-navigation-dock__handle')!;
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    await act(async () => { await Promise.resolve(); });

    const filter = container.querySelector<HTMLButtonElement>('.message-navigation-filter')!;
    fireEvent.pointerDown(filter, { pointerType: 'mouse' });
    act(() => filter.focus());
    fireEvent.click(filter, { detail: 1 });
    fireEvent.pointerLeave(dock, { pointerType: 'mouse' });
    await act(async () => { vi.advanceTimersByTime(90); });
    expect(dock.getAttribute('data-expanded')).toBe('false');
    expect(document.activeElement).toBe(handle);

    const outside = document.createElement('button');
    document.body.append(outside);
    act(() => outside.focus());
    await act(async () => { vi.advanceTimersByTime(1); });
    fireEvent.keyDown(outside, { key: 'Tab' });
    act(() => handle.focus());
    expect(dock.getAttribute('data-expanded')).toBe('true');

    const marker = container.querySelector<HTMLButtonElement>('.message-navigation-marker')!;
    act(() => marker.focus());
    fireEvent.keyDown(marker, { key: 'Escape' });
    expect(dock.getAttribute('data-expanded')).toBe('false');
    expect(document.activeElement).toBe(handle);
    outside.remove();
  });

  it('does not auto-collapse while keyboard focus is using a marker and supports Escape', async () => {
    const dockRef = { current: null } as RefObject<HTMLDivElement | null>;
    const { container } = render(
      <MessageNavigationDock
        chatRef={{ current: null }}
        dockRef={dockRef}
        isMobile={false}
        mobileExpanded={false}
        onMobileClose={() => {}}
        onRestoreFocus={() => {}}
      />,
    );
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    const handle = container.querySelector<HTMLButtonElement>('button.message-navigation-dock__handle')!;
    act(() => handle.focus());
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();

    const marker = container.querySelector<HTMLButtonElement>('.message-navigation-marker')!;
    act(() => marker.focus());
    fireEvent.pointerLeave(dock, { pointerType: 'mouse' });
    expect(container.querySelector('#message-navigation-panel')?.getAttribute('aria-hidden')).toBe('false');

    fireEvent.keyDown(marker, { key: 'Escape' });
    expect(container.querySelector('#message-navigation-panel')?.getAttribute('aria-hidden')).toBe('true');
    expect(document.activeElement).toBe(handle);
  });

  it('jumps to a history item after the dock is expanded', async () => {
    const scrollToMessage = vi.fn(() => true);
    const ensureMessageLoaded = vi.fn().mockResolvedValue(USER_MESSAGE);
    useSessionStore.setState({ ensureMessageLoaded });
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callback(performance.now());
      return 1;
    });
    const dockRef = { current: null } as RefObject<HTMLDivElement | null>;
    const { container } = render(
      <MessageNavigationDock
        chatRef={{ current: { scrollToMessage } as never }}
        dockRef={dockRef}
        isMobile={false}
        mobileExpanded={false}
        onMobileClose={() => {}}
        onRestoreFocus={() => {}}
      />,
    );
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    await act(async () => { await Promise.resolve(); });

    const marker = container.querySelector<HTMLButtonElement>('.message-navigation-marker')!;
    await act(async () => { fireEvent.click(marker); });
    expect(ensureMessageLoaded).toHaveBeenCalledWith(0, 1);
    expect(scrollToMessage).toHaveBeenCalledWith(USER_MESSAGE, 0);
  });
});

describe('mobile message navigation scrub', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedHistory.mockResolvedValue(historyPage(SCRUB_MESSAGES));
    seedSession('rail-scrub');
  });

  async function renderOpenMobileRail() {
    const ensureMessageLoaded = vi.fn().mockResolvedValue(USER_MESSAGE);
    useSessionStore.setState({ ensureMessageLoaded });
    const view = render(
      <MessageNavigationRail
        chatRef={{ current: null }}
        isMobile
        mobileExpanded
      />,
    );
    await act(async () => { await Promise.resolve(); });
    return { ...view, ensureMessageLoaded };
  }

  const touchDown = (marker: HTMLButtonElement, pointerId = 7) => {
    const event = new Event('pointerdown', { bubbles: true });
    Object.defineProperties(event, {
      pointerType: { configurable: true, value: 'touch' },
      pointerId: { configurable: true, value: pointerId },
      button: { configurable: true, value: 0 },
      clientX: { configurable: true, value: 20 },
      clientY: { configurable: true, value: 20 },
    });
    marker.dispatchEvent(event);
  };

  it('keeps a mobile short press as a normal jump', async () => {
    const scrollToMessage = vi.fn(() => true);
    const ensureMessageLoaded = vi.fn().mockResolvedValue(USER_MESSAGE);
    useSessionStore.setState({ ensureMessageLoaded });
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callback(performance.now());
      return 1;
    });
    const { container } = render(
      <MessageNavigationRail
        chatRef={{ current: { scrollToMessage } as never }}
        isMobile
        mobileExpanded
      />,
    );
    await act(async () => { await Promise.resolve(); });
    const marker = markers(container)[0]!;

    touchDown(marker);
    fireEvent.pointerUp(marker, { pointerType: 'touch', pointerId: 7, button: 0 });
    await act(async () => {
      fireEvent.click(marker, { detail: 1 });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(ensureMessageLoaded).toHaveBeenCalledWith(SCRUB_MESSAGES.length - 1, SCRUB_MESSAGES.length);
    expect(scrollToMessage).toHaveBeenCalledWith(USER_MESSAGE, 0);
  });

  it('previews multiple markers after a long press and suppresses the release click', async () => {
    const { container, ensureMessageLoaded } = await renderOpenMobileRail();
    const [first, second, third] = markers(container);
    touchDown(first!);
    await act(async () => { vi.advanceTimersByTime(450); });

    const preview = document.querySelector('[role="tooltip"]')!;
    expect(preview.getAttribute('data-preview-mode')).toBe('scrub');
    expect(preview.textContent).toContain('scrub preview one');

    await act(async () => { moveTouchPointer(second!, 7, 20, 60); });
    expect(document.querySelector('[role="tooltip"]')?.textContent).toContain('scrub preview two');
    const secondPreview = document.querySelector('[role="tooltip"]');
    await act(async () => { moveTouchPointer(second!, 7, 20, 61); });
    expect(document.querySelector('[role="tooltip"]')).toBe(secondPreview);

    await act(async () => { moveTouchPointer(third!, 7, 20, 100); });
    expect(document.querySelector('[role="tooltip"]')?.textContent).toContain('scrub preview three');
    fireEvent.pointerUp(third!, { pointerType: 'touch', pointerId: 7, button: 0 });
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    fireEvent.click(third!, { detail: 1 });

    expect(ensureMessageLoaded).not.toHaveBeenCalled();
  });

  it('leaves pre-long-press movement unprevented so the rail can scroll normally', async () => {
    const { container, ensureMessageLoaded } = await renderOpenMobileRail();
    const [first, second] = markers(container);
    touchDown(first!);
    expect(moveTouchPointer(second!, 7, 24, 36)).toBe(true);
    await act(async () => { vi.advanceTimersByTime(500); });
    expect(document.querySelector('[role="tooltip"]')).toBeNull();

    fireEvent.pointerUp(second!, { pointerType: 'touch', pointerId: 7, button: 0 });
    fireEvent.click(second!, { detail: 1 });
    expect(ensureMessageLoaded).not.toHaveBeenCalled();
  });

  it('clears the preview on pointer cancellation and suppresses its compatibility click', async () => {
    const { container, ensureMessageLoaded } = await renderOpenMobileRail();
    const marker = markers(container)[0]!;
    touchDown(marker);
    await act(async () => { vi.advanceTimersByTime(450); });
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();

    fireEvent.pointerCancel(marker, { pointerType: 'touch', pointerId: 7 });
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    fireEvent.click(marker, { detail: 1 });
    expect(ensureMessageLoaded).not.toHaveBeenCalled();
  });

  it('ends the preview when the active finger leaves the rail bounds', async () => {
    const { container } = await renderOpenMobileRail();
    const marker = markers(container)[0]!;
    const list = container.querySelector<HTMLElement>('.message-navigation-list')!;
    vi.spyOn(list, 'getBoundingClientRect').mockReturnValue({
      x: 200,
      y: 100,
      left: 200,
      top: 100,
      right: 252,
      bottom: 400,
      width: 52,
      height: 300,
      toJSON: () => ({}),
    } as DOMRect);
    touchDown(marker);
    await act(async () => { vi.advanceTimersByTime(450); });
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();

    await act(async () => { moveTouchPointer(marker, 7, 270, 180); });
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
  });

  it('clears the gesture on session changes, rail close, and filter changes', async () => {
    const { container, rerender } = await renderOpenMobileRail();
    let marker = markers(container)[0]!;
    touchDown(marker);
    await act(async () => { vi.advanceTimersByTime(450); });
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();

    await act(async () => {
      seedSession('rail-scrub-next');
      await Promise.resolve();
    });
    expect(document.querySelector('[role="tooltip"]')).toBeNull();

    marker = markers(container)[0]!;
    touchDown(marker, 8);
    await act(async () => { vi.advanceTimersByTime(450); });
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
    rerender(
      <MessageNavigationRail
        chatRef={{ current: null }}
        isMobile
        mobileExpanded={false}
      />,
    );
    expect(document.querySelector('[role="tooltip"]')).toBeNull();

    rerender(
      <MessageNavigationRail
        chatRef={{ current: null }}
        isMobile
        mobileExpanded
      />,
    );
    marker = markers(container)[0]!;
    touchDown(marker, 9);
    await act(async () => { vi.advanceTimersByTime(450); });
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
    fireEvent.click(container.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="false"]')!);
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
  });

  it('does not start scrubbing on desktop pointer interactions', async () => {
    const ensureMessageLoaded = vi.fn().mockResolvedValue(USER_MESSAGE);
    useSessionStore.setState({ ensureMessageLoaded });
    const { container } = render(<MessageNavigationRail chatRef={{ current: null }} />);
    await act(async () => { await Promise.resolve(); });
    const marker = markers(container)[0]!;

    fireEvent.pointerDown(marker, { pointerType: 'touch', pointerId: 7, button: 0 });
    await act(async () => { vi.advanceTimersByTime(500); });
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    fireEvent.click(marker, { detail: 1 });
    expect(ensureMessageLoaded).toHaveBeenCalledTimes(1);
  });
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

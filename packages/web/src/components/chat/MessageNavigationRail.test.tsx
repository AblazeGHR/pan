// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import type { RefObject } from 'react';
import { MessageNavigationRail } from './MessageNavigationRail';
import { MessageNavigationDock } from './MessageNavigationDock';
import { createPortal } from 'react-dom';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { fetchSessionNavigation } from '@/services/api';
import type { Message } from '@/types';
import * as messageOrdering from '@/stores/messageOrdering';

vi.mock('@/services/api', () => ({
  fetchSessionNavigation: vi.fn(),
}));

const mockedHistory = vi.mocked(fetchSessionNavigation);

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
  } as Awaited<ReturnType<typeof fetchSessionNavigation>>;
}

/** The rail indexes the whole history on mount, so every test needs a page. */
function seedSession(sessionId: string) {
  useSessionStore.setState({
    currentSessionId: sessionId,
    currentMessages: [USER_MESSAGE],
    historyLoadEnd: 0,
    hasMoreMessages: false,
    sessions: [{ id: sessionId, historyTotal: sessionId.startsWith('rail-scrub') ? 4 : 1 } as never],
    sessionTranscripts: {},
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
  it('ignores pointer, focus and Escape events from a physically external portal', async () => {
    vi.useFakeTimers();
    const target = document.createElement('div');
    document.body.append(target);
    const close = vi.fn();
    const view = render(<MessageNavigationDock chatRef={{ current: null }} dockRef={{ current: null }}
      isMobile={false} mobileExpanded={false} onMobileClose={close} onRestoreFocus={vi.fn()} showNavigation={false}>
      {createPortal(<input aria-label="Portaled search" />, target)}
    </MessageNavigationDock>);
    try {
      const dock = view.getByTestId('message-navigation-dock');
      const input = view.getByRole('textbox', { name: 'Portaled search' });
      fireEvent.pointerEnter(input, { pointerType: 'mouse' });
      fireEvent.focus(input);
      expect(dock.getAttribute('data-expanded')).toBe('false');
      fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
      expect(dock.getAttribute('data-expanded')).toBe('true');
      fireEvent.pointerLeave(dock, { pointerType: 'mouse', relatedTarget: input });
      fireEvent.pointerEnter(input, { pointerType: 'mouse' });
      await act(async () => { vi.advanceTimersByTime(120); });
      expect(dock.getAttribute('data-expanded')).toBe('false');
      fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
      fireEvent.keyDown(input, { key: 'Escape' });
      expect(dock.getAttribute('data-expanded')).toBe('true');
    } finally { view.unmount(); target.remove(); }
  });
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

  it('does not scan a newly selected Session behind a dock opened for the previous Session', async () => {
    vi.useFakeTimers();
    const { container } = render(<MessageNavigationDock chatRef={{ current: null }} dockRef={{ current: null }}
      isMobile={false} mobileExpanded={false} onMobileClose={() => {}} onRestoreFocus={() => {}} />);
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    await act(async () => { await Promise.resolve(); });
    fireEvent.pointerLeave(dock, { pointerType: 'mouse' });
    await act(async () => { vi.advanceTimersByTime(100); });
    const previousRequests = mockedHistory.mock.calls.length;
    act(() => { useSessionStore.setState({ currentSessionId: 'cold-next', currentMessages: [] }); });
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(previousRequests);
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(previousRequests + 1);
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

    const firstMarker = container.querySelector<HTMLButtonElement>('.message-navigation-marker')!;
    fireEvent.pointerDown(firstMarker, { pointerType: 'mouse' });
    act(() => firstMarker.focus());
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
    useSessionStore.setState({ sessions: [{ id: 'rail-scrub', historyTotal: 4 } as never] });
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

  it('clears the gesture on session changes and rail close', async () => {
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
  it('does not rescan a complete transcript on streaming renders once the full index is ready', async () => {
    const scan = vi.spyOn(messageOrdering, 'canonicalRowsWithOffsets');
    const { container } = render(<MessageNavigationRail chatRef={{ current: null }} />);
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-navigation-rail')?.getAttribute('data-index-status')).toBe('ready');
    scan.mockClear();
    await act(async () => {
      useSessionStore.setState({ currentMessages: Array.from({ length: 5000 }, (_, i) => ({ role: 'user', content: `row ${i}` })) });
    });
    expect(scan).not.toHaveBeenCalled();
    expect(markers(container)).toHaveLength(1);
  });

  it('loads a bounded tail page without a reliable viewport', async () => {
    const { container } = render(<MessageNavigationRail chatRef={{ current: null }} />);
    await act(async () => {
      await Promise.resolve();
    });

    // Without a viewport handle, show a bounded useful page without highlighting.
    expect(mockedHistory).toHaveBeenCalled();
    expect(mockedHistory.mock.calls[0]![0]).toBe('rail-1');
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
  });
});


describe('open nearest canonical regression', () => {
  const seedRows = (total: number, start: number, count: number, id = 'nearest') => {
    const rows = Array.from({ length: count }, (_, i): Message => ({ role: (start + i) % 10 === 0 ? 'user' : 'assistant', content: `row ${start + i}`, messageId: `canonical-${start + i}` }));
    rows.forEach((m, i) => messageOrdering.markDurableRow(m, start + i));
    useSessionStore.setState({ currentSessionId: id, sessions: [{ id, historyTotal: total } as never], currentMessages: rows, sessionTranscripts: {} });
    return rows;
  };
  const selected = (container: HTMLElement) => container.querySelector<HTMLElement>('.is-viewport-target')?.dataset.canonicalOffset;
  it('locates from the loaded canonical window without history requests or body jumps; every reopen resnapshots', async () => {
    seedRows(1000, 400, 200);
    const snapshot = vi.fn().mockReturnValue({ start: 493, end: 506 });
    const scrollToMessage = vi.fn();
    const ref = { current: { getViewportHistoryRange: snapshot, scrollToMessage } } as never;
    const view = render(<MessageNavigationRail chatRef={ref} expanded />);
    await act(async () => { await Promise.resolve(); });
    expect(selected(view.container)).toBe('500'); expect(scrollToMessage).not.toHaveBeenCalled();
    snapshot.mockReturnValue({ start: 533, end: 546 });
    view.rerender(<MessageNavigationRail chatRef={ref} expanded={false} />);
    view.rerender(<MessageNavigationRail chatRef={ref} expanded />);
    expect(selected(view.container)).toBe('540'); expect(snapshot).toHaveBeenCalledTimes(2);
  });
  it('does not count local Task completed as a canonical navigation row', async () => {
    const rows = seedRows(1000, 490, 20);
    const terminal: Message = { role: 'user', content: '[DONE] Task completed' };
    messageOrdering.markLocalMarker(terminal);
    useSessionStore.setState({ currentMessages: [...rows, terminal] });
    const view = render(<MessageNavigationRail chatRef={{ current: { getViewportHistoryRange: () => ({ start: 499, end: 501 }) } as never }} />);
    expect(selected(view.container)).toBe('500');
    expect(markers(view.container).some(m => m.title.includes('Task completed'))).toBe(false);
  });
  it('converges from an initially unavailable viewport via a notification, once only', async () => {
    seedRows(1000, 400, 200);
    let notify: ((range: { start: number; end: number }) => void) | undefined;
    const unsubscribe = vi.fn();
    const view = render(<MessageNavigationRail chatRef={{ current: { getViewportHistoryRange: () => null,
      observeViewportHistoryRange: (callback: typeof notify) => { notify = callback; return unsubscribe; } } as never }} />);
    expect(selected(view.container)).toBeUndefined();
    act(() => { notify?.({ start: 490, end: 510 }); });
    expect(selected(view.container)).toBe('500'); expect(unsubscribe).toHaveBeenCalledTimes(1);
    act(() => { notify?.({ start: 530, end: 550 }); });
    expect(selected(view.container)).toBe('500');
  });
  it('programmatic scroll does not cancel pending pagination; genuine wheel does', async () => {
    seedRows(1000, 400, 1);
    let resolve!: (value: ReturnType<typeof historyPage>) => void;
    mockedHistory.mockImplementation(() => new Promise(r => { resolve = r; }));
    const view = render(<MessageNavigationRail chatRef={{ current: { getViewportHistoryRange: () => ({ start: 499, end: 501 }) } as never }} />);
    fireEvent.scroll(window);
    await act(async () => { resolve(historyPage(Array.from({ length: 200 }, (_, i) => ({ role: i === 100 ? 'user' : 'assistant', content: `fetched ${i}` })), 1000, 400)); });
    expect(selected(view.container)).toBe('500');
    view.unmount(); seedRows(1000, 400, 1);
    const second = render(<MessageNavigationRail chatRef={{ current: { getViewportHistoryRange: () => ({ start: 499, end: 501 }) } as never }} />);
    fireEvent.wheel(window);
    await act(async () => { resolve(historyPage(Array.from({ length: 200 }, () => ({ role: 'user', content: 'late' })), 1000, 400)); });
    expect(selected(second.container)).toBeUndefined();
  });
  it('bounds transient retry, retains local targets, and keyboard retry recovers single-flight', async () => {
    vi.useFakeTimers(); seedRows(1000, 400, 1);
    mockedHistory.mockRejectedValue(new Error('transient network'));
    const view = render(<MessageNavigationRail chatRef={{ current: { getViewportHistoryRange: () => ({ start: 499, end: 501 }) } as never }} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(mockedHistory).toHaveBeenCalledTimes(3); expect(markers(view.container)).toHaveLength(1);
    const retry = view.getByRole('button', { name: 'Retry message navigation' });
    expect(view.getByRole('status').textContent).toContain('transient network');
    mockedHistory.mockResolvedValue(historyPage(Array.from({ length: 200 }, (_, i) => ({ role: i === 100 ? 'user' : 'assistant', content: `recovered ${i}` })), 1000, 400));
    await act(async () => { fireEvent.click(retry); fireEvent.click(retry); });
    expect(mockedHistory).toHaveBeenCalledTimes(4); expect(selected(view.container)).toBe('500');
    expect(view.queryByRole('button', { name: 'Retry message navigation' })).toBeNull();
  });
  it('aborts retries on close and rejects late responses after Session or filter changes', async () => {
    vi.useFakeTimers(); seedRows(1000, 400, 1);
    mockedHistory.mockRejectedValue(new Error('transient'));
    const ref = { current: { getViewportHistoryRange: () => ({ start: 499, end: 501 }) } } as never;
    const view = render(<MessageNavigationRail chatRef={ref} />);
    await act(async () => { await Promise.resolve(); });
    view.rerender(<MessageNavigationRail chatRef={ref} expanded={false} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(mockedHistory).toHaveBeenCalledTimes(1);
    let resolve!: (value: ReturnType<typeof historyPage>) => void;
    mockedHistory.mockImplementation(() => new Promise(r => { resolve = r; }));
    view.rerender(<MessageNavigationRail chatRef={ref} />);
    const oldResolve = resolve;
    act(() => { seedRows(1000, 400, 200, 'new-session'); });
    await act(async () => { oldResolve(historyPage(Array.from({ length: 200 }, () => ({ role: 'user', content: 'OLD' })), 1000, 400)); });
    expect(markers(view.container).some(m => m.title === 'OLD')).toBe(false);
    act(() => { useAppSettingsStore.setState({ showQQ: false }); });
    expect(markers(view.container).some(m => m.title === 'OLD')).toBe(false);
  });
  it('same-epoch appends refresh fromEnd without another snapshot, request or rail scroll', async () => {
    seedRows(1000, 0, 1000);
    const snapshot = vi.fn().mockReturnValue({ start: 499, end: 501 });
    const view = render(<MessageNavigationRail chatRef={{ current: { getViewportHistoryRange: snapshot } as never }} />);
    expect(selected(view.container)).toBe('500');
    const list = view.container.querySelector<HTMLElement>('.message-navigation-list')!;
    list.scrollTop = 123;
    act(() => { useSessionStore.setState({ sessions: [{ id: 'nearest', historyTotal: 1001 } as never] }); });
    expect(snapshot).toHaveBeenCalledTimes(1); expect(list.scrollTop).toBe(123);
    expect(view.container.querySelector<HTMLElement>('.is-viewport-target')?.dataset.fromEnd).toBe('500');
    expect(mockedHistory).toHaveBeenCalledTimes(1);
  });
  it('history epoch invalidation cannot reuse cached offsets or a late old page', async () => {
    seedRows(1000, 400, 1);
    let oldResolve!: (value: ReturnType<typeof historyPage>) => void;
    mockedHistory.mockImplementationOnce(() => new Promise(resolve => { oldResolve = resolve; }));
    const ref = { current: { getViewportHistoryRange: () => ({ start: 499, end: 501 }) } } as never;
    const view = render(<MessageNavigationRail chatRef={ref} />);
    act(() => { useSessionStore.setState({ sessionTranscripts: { nearest: { window: { epoch: 'new', rows: new Map() } } as never } }); });
    await act(async () => { oldResolve({ ...historyPage(Array.from({ length: 200 }, () => ({ role: 'user', content: 'OLD-EPOCH' })), 1000, 400), historyEpoch: 'old' }); });
    expect(markers(view.container).some(m => m.title === 'OLD-EPOCH')).toBe(false);
  });

  it('loads a complete continuous index automatically without visible paging controls', async()=>{
    vi.useFakeTimers();seedRows(1600,1400,200);
    vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>setTimeout(()=>callback(0),0));
    mockedHistory.mockImplementation(async(_id,before=0)=>historyPage(Array.from({length:Math.min(200,before)},(_,i)=>({role:'user',content:`row ${before-200+i}`,messageId:`canonical-${before-200+i}`})),1600,before-200));
    const view=render(<MessageNavigationRail chatRef={{current:{getViewportHistoryRange:()=>({start:1499,end:1501})} as never}}/>);
    await act(async()=>{await vi.runAllTimersAsync();});
    expect(view.container.querySelector('.message-navigation-rail')?.getAttribute('data-index-status')).toBe('ready');
    expect(view.container.querySelector('.message-navigation-rail')?.getAttribute('data-indexed-targets')).toBe('1420');
    expect(view.queryByRole('button',{name:'Load earlier navigation'})).toBeNull();expect(view.queryByRole('button',{name:'Load more nearby navigation'})).toBeNull();
    expect(markers(view.container).length).toBeLessThan(100);
  });
});

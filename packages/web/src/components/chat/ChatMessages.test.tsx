// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, act, fireEvent, cleanup } from '@testing-library/react';
import { ChatMessages } from './ChatMessages';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

// ── Mock @tanstack/react-virtual ──
// The real virtualizer needs real layout / ResizeObserver, which jsdom does not
// provide. We stub it with a fake whose total size the test controls, so we can
// simulate: (a) history arriving after a session switch, (b) the virtualizer
// re-measuring items and growing/shrinking the total size.
const m = vi.hoisted(() => {
  const state: {
    totalSize: number;
    virtualItems: Array<{ index: number; start: number; size: number }>;
    options: { getItemKey?: (index: number) => string | number } | null;
  } = { totalSize: 0, virtualItems: [], options: null };
  return {
    state,
    setTotalSize: (n: number) => {
      state.totalSize = n;
    },
    setVirtualItems: (items: Array<{ index: number; start: number; size: number }>) => {
      state.virtualItems = items;
    },
  };
});

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (options: { getItemKey?: (index: number) => string | number }) => {
    m.state.options = options;
    return {
      getTotalSize: () => m.state.totalSize,
      getVirtualItems: () =>
        m.state.virtualItems.map((item) => ({
          ...item,
          key: options.getItemKey?.(item.index) ?? item.index,
        })),
      measureElement: () => {},
    };
  },
}));

// ── jsdom has no layout engine. Give the chat scroll container a realistic
// scrollHeight (the explicit height ChatMessages sets on the inner virtualizer
// div) and a fixed clientHeight, so isNearBottom() / scrollToBottom() make
// decisions from real numbers. ──
function mockScrollMetrics() {
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get(this: HTMLElement) {
      const child = this.firstElementChild as HTMLElement | null;
      const h = child?.style?.height || child?.style?.minHeight;
      if (h) {
        const px = parseFloat(h);
        if (!Number.isNaN(px)) return px;
      }
      return this.clientHeight;
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get(this: HTMLElement) {
      if (this.classList?.contains('overflow-auto')) return 400;
      return 0;
    },
  });
}

const msgs = (n: number, prefix = 'm') =>
  Array.from({ length: n }, (_, i) => ({
    role: (i % 2 === 0 ? 'user' : 'assistant') as 'user' | 'assistant',
    content: `${prefix}-${i}`,
  }));

let rafId = 0;

beforeEach(() => {
  mockScrollMetrics();
  // jsdom may or may not ship requestAnimationFrame — polyfill to be safe.
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    const id = ++rafId;
    setTimeout(() => cb(Date.now()), 0);
    return id;
  }) as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = ((id: number) => {
    clearTimeout(id);
  }) as typeof cancelAnimationFrame;

  m.setTotalSize(0);
  m.setVirtualItems([]);
  m.state.options = null;
  useSessionStore.setState({
    currentSessionId: null,
    currentMessages: [],
    sessions: [],
    hasMoreMessages: false,
    historyLoading: false,
    initialLoading: false,
    historyLoadEnd: 0,
  });
  useUIStore.setState({ tuiViewEnabled: true });
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe('ChatMessages scroll positioning', () => {
  it('scrolls to the bottom when history finishes loading after entering a session', () => {
    // Refresh: no session selected, no messages → empty state, no scroll element.
    const { container } = render(<ChatMessages />);
    expect(container.querySelector('.overflow-auto')).toBeNull();

    // selectSession(): currentSessionId is set synchronously, but the summary=1
    // snapshot carries no history → messages still empty.
    act(() => {
      useSessionStore.setState({ currentSessionId: 's1', currentMessages: [] });
    });
    expect(container.querySelector('.overflow-auto')).toBeNull();

    // The async fresh-history fetch resolves → messages arrive. This is the
    // bug scenario: the fresh container mounts with scrollTop = 0 and tall
    // content; we must still land at the bottom.
    m.setTotalSize(2000);
    act(() => {
      useSessionStore.setState({ currentMessages: msgs(5) });
    });

    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl).not.toBeNull();
    expect(scrollEl.scrollTop).toBe(2000);
  });

  it('re-scrolls to the true bottom when the virtualizer measures the real item heights', () => {
    useSessionStore.setState({ currentSessionId: 's1' });
    const { container } = render(<ChatMessages />);

    m.setTotalSize(1000);
    act(() => {
      useSessionStore.setState({ currentMessages: msgs(3) });
    });
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(1000);

    // Items get measured → total size changes while still pinned at the bottom.
    m.setTotalSize(1600);
    act(() => {
      useSessionStore.setState({ currentMessages: msgs(3) });
    });
    expect(scrollEl.scrollTop).toBe(1600);
  });

  it('switching sessions also lands on the latest messages', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    // Simulate selectSession('s2'): the target session's history arrives async
    // after the id switch, like the refresh case.
    m.setTotalSize(3000);
    act(() => {
      useSessionStore.setState({ currentSessionId: 's2', currentMessages: msgs(6) });
    });
    expect(scrollEl.scrollTop).toBe(3000);
  });

  it('auto-scrolls on new messages while pinned at the bottom', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(0); // empty before history arrives

    m.setTotalSize(2000);
    act(() => {
      useSessionStore.setState({ currentMessages: msgs(4) });
    });
    expect(scrollEl.scrollTop).toBe(2000);

    m.setTotalSize(2600);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(1, 'new')] });
    });
    expect(scrollEl.scrollTop).toBe(2600);
  });

  it('does not auto-scroll when exactly 1px remains below the viewport', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    // 2000 - 1599 - 400 = 1px. Do not dispatch a scroll event: the strict
    // distance check in the update effect must protect against measurement or
    // render changes even if the browser has not emitted another scroll event.
    scrollEl.scrollTop = 1599;
    m.setTotalSize(2200);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(1, 'new')] });
    });

    expect(scrollEl.scrollTop).toBe(1599);
  });

  it('does not auto-scroll when the user is farther from the bottom', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    scrollEl.scrollTop = 700;
    fireEvent.scroll(scrollEl);
    m.setTotalSize(2600);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(1, 'new')] });
    });

    expect(scrollEl.scrollTop).toBe(700);
  });

  it('does not pull an away-from-bottom user down on measurement changes', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    // Simulate a layout change occurring after the user has moved 1px up.
    // No scroll event is dispatched so this specifically covers the
    // measurement effect's direct bottom check.
    scrollEl.scrollTop = 1599;
    m.setTotalSize(2400);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4)] });
    });

    expect(scrollEl.scrollTop).toBe(1599);
  });

  it('does NOT yank the user to the bottom when older messages are prepended while scrolled up', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    // Land at the bottom on entry, then the user scrolls up to the top.
    expect(scrollEl.scrollTop).toBe(2000);
    scrollEl.scrollTop = 0;
    fireEvent.scroll(scrollEl);
    expect(scrollEl.scrollTop).toBe(0);

    // loadOlderMessages prepends messages → total size grows.
    m.setTotalSize(3000);
    act(() => {
      useSessionStore.setState({
        currentMessages: [...msgs(2, 'old'), ...msgs(4)],
      });
    });
    // Scroll position is preserved at the top — NOT pulled back to the bottom.
    expect(scrollEl.scrollTop).toBe(0);
  });

  it('does NOT force-scroll on new messages when the user has scrolled up', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    scrollEl.scrollTop = 0;
    fireEvent.scroll(scrollEl); // user scrolls away → unpinned

    m.setTotalSize(2600);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(1, 'new')] });
    });
    expect(scrollEl.scrollTop).toBe(0);
  });

  it('keeps the user position during streaming deltas to the current message', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    scrollEl.scrollTop = 700;
    fireEvent.scroll(scrollEl);

    // A streaming update changes the same assistant message and its measured
    // height, rather than appending a new message.
    m.setTotalSize(2600);
    act(() => {
      useSessionStore.setState({
        currentMessages: [...msgs(3), { role: 'assistant', content: 'm-3\nmore streamed text' }],
      });
    });

    expect(scrollEl.scrollTop).toBe(700);
  });

  it('resumes following after the user returns near the bottom', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    scrollEl.scrollTop = 500;
    fireEvent.scroll(scrollEl);
    m.setTotalSize(2200);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(1, 'paused')] });
    });
    expect(scrollEl.scrollTop).toBe(500);

    // Explicitly returning to the bottom re-enables follow mode.
    scrollEl.scrollTop = 1800;
    fireEvent.scroll(scrollEl);
    m.setTotalSize(2800);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(2, 'follow')] });
    });
    expect(scrollEl.scrollTop).toBe(2800);
  });

  it('preserves the viewport anchor when older history is loaded above it', async () => {
    vi.useFakeTimers();
    const loadOlderMessages = vi.fn(async () => {
      // Simulate the store update caused by the async history response.
      m.setTotalSize(3000);
      useSessionStore.setState({
        currentMessages: [...msgs(2, 'old'), ...msgs(4)],
        historyLoading: false,
      });
    });
    useSessionStore.setState({
      currentSessionId: 's1',
      currentMessages: msgs(4),
      hasMoreMessages: true,
      historyLoading: false,
      loadOlderMessages,
    });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    act(() => {
      vi.runOnlyPendingTimers();
    });

    // Pagination is triggered near the top, but not at the exact top.
    scrollEl.scrollTop = 100;
    fireEvent.scroll(scrollEl);
    act(() => {
      vi.advanceTimersByTime(150);
    });
    await act(async () => {
      await Promise.resolve();
      vi.runOnlyPendingTimers();
    });

    expect(loadOlderMessages).toHaveBeenCalledOnce();
    expect(scrollEl.scrollTop).toBe(1100);
  });

  it('shows a spinner instead of the empty state while history is loading, then the empty state after', () => {
    // Enter a session whose snapshot has no history: messages empty + the
    // fresh-history fetch in flight (initialLoading=true) → spinner, no empty
    // state text.
    useSessionStore.setState({
      currentSessionId: 's1',
      currentMessages: [],
      initialLoading: true,
    });
    const { container, queryByText } = render(<ChatMessages />);
    expect(container.querySelector('.animate-spin')).not.toBeNull();
    expect(queryByText('No messages yet. Start a conversation.')).toBeNull();
    expect(container.querySelector('.overflow-auto')).toBeNull();

    // Fetch resolves and the session is genuinely empty → empty state appears.
    act(() => {
      useSessionStore.setState({ initialLoading: false });
    });
    expect(container.querySelector('.animate-spin')).toBeNull();
    expect(container.textContent).toContain('No messages yet. Start a conversation.');
  });

  it('keeps virtual item identity and DOM order when a preceding stream block appears', () => {
    const thinking = {
      role: 'thinking' as const,
      content: 'planning',
      nativeItemId: 'thinking-1',
    };
    const tool = {
      role: 'tool' as const,
      content: 'Command({"command":"true"})',
      nativeItemId: 'tool-1',
    };
    const answer = {
      role: 'assistant' as const,
      content: 'answer',
      nativeItemId: 'answer-1',
    };

    useSessionStore.setState({ currentSessionId: 's1', currentMessages: [tool, answer] });
    m.setVirtualItems([
      { index: 0, start: 0, size: 120 },
      { index: 1, start: 120, size: 120 },
    ]);
    const { container } = render(<ChatMessages />);

    const initialGetItemKey = m.state.options?.getItemKey;
    expect(initialGetItemKey).toBeTypeOf('function');
    const toolKey = initialGetItemKey!(0);
    const answerKey = initialGetItemKey!(1);

    // A late thinking block is a normal history/stream update. The existing
    // tool and answer must retain their identities after their indexes shift.
    m.setVirtualItems([
      { index: 0, start: 0, size: 120 },
      { index: 1, start: 120, size: 120 },
      { index: 2, start: 240, size: 120 },
    ]);
    act(() => {
      useSessionStore.setState({ currentMessages: [thinking, tool, answer] });
    });

    const nextGetItemKey = m.state.options?.getItemKey;
    expect(nextGetItemKey).toBeTypeOf('function');
    expect(nextGetItemKey!(1)).toBe(toolKey);
    expect(nextGetItemKey!(2)).toBe(answerKey);
    expect(
      [...container.querySelectorAll('[data-index]')].map((node) =>
        node.getAttribute('data-index'),
      ),
    ).toEqual(['0', '1', '2']);
    const rows = [...container.querySelectorAll('[data-index]')] as HTMLElement[];
    expect(rows.map((row) => row.style.position)).toEqual(['', '', '']);
    expect(rows.map((row) => row.style.marginTop)).toEqual(['0px', '0px', '0px']);

    // A stale measurement can report a later start before the preceding row's
    // actual streamed height. The flow offset is clamped, so the browser's
    // normal layout, rather than an absolute transform, keeps rows disjoint.
    m.setVirtualItems([
      { index: 0, start: 0, size: 240 },
      { index: 1, start: 120, size: 120 },
      { index: 2, start: 240, size: 120 },
    ]);
    act(() => {
      useSessionStore.setState({ currentMessages: [thinking, tool, answer] });
    });
    const overlappedRows = [...container.querySelectorAll('[data-index]')] as HTMLElement[];
    expect(overlappedRows.map((row) => row.style.marginTop)).toEqual(['0px', '0px', '0px']);
  });

  it('keeps a tall streamed block in document flow while preserving a scrolled-up viewport', () => {
    const messages = [
      { role: 'thinking', content: 'planning', nativeItemId: 'thinking-1' },
      { role: 'tool', content: 'Command({"command":"true"})', nativeItemId: 'tool-1' },
      { role: 'assistant', content: 'short answer', nativeItemId: 'answer-1' },
    ];
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: messages });
    m.setTotalSize(1800);
    m.setVirtualItems([
      { index: 0, start: 0, size: 100 },
      { index: 1, start: 100, size: 100 },
      { index: 2, start: 200, size: 100 },
    ]);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(1800);

    // The user scrolls away from the bottom while the answer is still
    // streaming. A later, much taller content delta must not auto-scroll or
    // use an old absolute position to cover the tool/thinking rows.
    scrollEl.scrollTop = 500;
    fireEvent.scroll(scrollEl);
    const tallAnswer = Array.from({ length: 100 }, (_, index) => `line ${index}`).join('\n');
    m.setTotalSize(2400);
    act(() => {
      useSessionStore.setState({
        currentMessages: [
          messages[0]!,
          messages[1]!,
          { ...messages[2]!, content: tallAnswer },
        ],
      });
    });

    expect(scrollEl.scrollTop).toBe(500);
    expect(container.textContent).toContain('line 99');
    const rows = [...container.querySelectorAll('[data-index]')] as HTMLElement[];
    expect(rows.map((row) => row.getAttribute('data-index'))).toEqual(['0', '1', '2']);
    // jsdom has no layout engine, so this verifies the structural guarantee:
    // rows are normal-flow elements and there is no transform/absolute
    // positioning that could paint the stale virtual coordinates on top of a
    // newly expanded row. Browser geometry still needs a real-browser check.
    expect(rows.every((row) => row.style.position === '' && !row.style.transform)).toBe(true);
  });
});

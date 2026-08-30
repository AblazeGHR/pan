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
  const state = { totalSize: 0 };
  return {
    state,
    setTotalSize: (n: number) => {
      state.totalSize = n;
    },
  };
});

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: () => ({
    getTotalSize: () => m.state.totalSize,
    getVirtualItems: () => [],
    measureElement: () => {},
  }),
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
      const h = child?.style?.height;
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
  useSessionStore.setState({
    currentSessionId: null,
    currentMessages: [],
    sessions: [],
    hasMoreMessages: false,
    historyLoading: false,
    initialLoading: false,
    historyLoadEnd: 0,
  });
  useUIStore.setState({ bubbleViewEnabled: false });
});

afterEach(cleanup);

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
});

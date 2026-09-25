// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, act, fireEvent, cleanup, screen } from '@testing-library/react';
import { ChatMessages, SCROLL_BOTTOM_THRESHOLD } from './ChatMessages';
import { groupMessages, getItemRole } from './MessageBubble';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { Message } from '@/types';

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
// div) and a fixed clientHeight, so the bottom-zone / scrollToBottom() make
// decisions from real numbers. ──
function mockScrollMetrics() {
  const clientHeight = () => mockClientHeight;
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
      if (this.classList?.contains('overflow-auto')) return clientHeight();
      return 0;
    },
  });
}

function userScroll(element: HTMLElement, top: number) {
  element.scrollTop = top;
  // A scroll event has no source information by itself. The component uses a
  // preceding wheel/touch/key gesture to distinguish user movement from a
  // measurement/virtualizer correction.
  fireEvent.wheel(element);
  fireEvent.scroll(element);
}

function programmaticScroll(element: HTMLElement) {
  fireEvent.scroll(element);
}

function pointerMove(element: HTMLElement, pointerType: 'mouse' | 'pen', buttons: number) {
  const event = new Event('pointermove', { bubbles: true });
  Object.defineProperties(event, {
    pointerType: { configurable: true, value: pointerType },
    buttons: { configurable: true, value: buttons },
  });
  element.dispatchEvent(event);
}

const chatMessagesSource = readFileSync(
  resolve(process.cwd(), 'src/components/chat/ChatMessages.tsx'),
  'utf8',
);

const msgs = (n: number, prefix = 'm') =>
  Array.from({ length: n }, (_, i) => ({
    role: (i % 2 === 0 ? 'user' : 'assistant') as 'user' | 'assistant',
    content: `${prefix}-${i}`,
  }));

let rafId = 0;
let mockClientHeight = 400;
let resizeObserverCallback: ResizeObserverCallback | null = null;

class TestResizeObserver {
  constructor(callback: ResizeObserverCallback) {
    resizeObserverCallback = callback;
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  mockClientHeight = 400;
  resizeObserverCallback = null;
  vi.stubGlobal('ResizeObserver', TestResizeObserver);
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
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  cleanup();
});

it('groups only adjacent thinking blocks and keeps semantic boundaries', () => {
  const messages: Message[] = [
    { role: 'thinking', content: 'thought 1' },
    { role: 'thinking', content: 'thought 2' },
    { role: 'assistant', content: 'visible answer' },
    { role: 'thinking', content: 'thought 3' },
    { role: 'tool', content: 'Run({})' },
    { role: 'thinking', content: 'thought 4' },
    { role: 'user', content: 'next request' },
  ];
  const grouped = groupMessages(messages);

  expect(grouped.map((item) => 'type' in item ? item.type : item.role)).toEqual([
    'thinking_group', 'assistant', 'thinking_group', 'tool_group', 'thinking_group', 'user',
  ]);
  expect(grouped.map((item) => getItemRole(item))).toEqual([
    'thinking', 'assistant', 'thinking', 'tool', 'thinking', 'user',
  ]);
  expect(grouped[0]).toMatchObject({ type: 'thinking_group', items: messages.slice(0, 2) });
});

it('merges each adjacent tool/thinking run only when the preference is enabled', () => {
  const messages: Message[] = [
    { role: 'thinking', content: 'thought 1' },
    { role: 'thinking', content: 'thought 2' },
    { role: 'tool', content: 'Run({})' },
    { role: 'tool', content: 'Read({})' },
    { role: 'assistant', content: 'visible answer' },
    { role: 'tool', content: 'Write({})' },
    { role: 'thinking', content: 'thought 3' },
    { role: 'system', content: 'system notice' },
    { role: 'thinking', content: 'thought 4' },
  ];

  const merged = groupMessages(messages, true);
  expect(merged.map((item) => 'type' in item ? item.type : item.role)).toEqual([
    'non_body_group', 'assistant', 'non_body_group', 'system', 'non_body_group',
  ]);
  expect(merged[0]).toMatchObject({ type: 'non_body_group', items: messages.slice(0, 4) });
  expect(merged[2]).toMatchObject({ type: 'non_body_group', items: messages.slice(5, 7) });
  expect(merged[0] && 'type' in merged[0] ? merged[0].items.map((item) => item.role) : []).toEqual([
    'thinking', 'thinking', 'tool', 'tool',
  ]);
  expect(merged.map((item) => getItemRole(item))).toEqual([
    'tool', 'assistant', 'thinking', 'system', 'thinking',
  ]);

  // Default and explicit off mode retain the established independent groups.
  expect(groupMessages(messages).map((item) => 'type' in item ? item.type : item.role))
    .toEqual(groupMessages(messages, false).map((item) => 'type' in item ? item.type : item.role));
  expect(groupMessages(messages, false).map((item) => 'type' in item ? item.type : item.role)).toEqual([
    'thinking_group', 'tool_group', 'assistant', 'tool_group', 'thinking_group', 'system', 'thinking_group',
  ]);
  expect(groupMessages(messages, false).filter((item) => 'type' in item && item.type === 'tool_group')[0])
    .toMatchObject({ items: messages.slice(2, 4) });
});

describe('worker report message treatment', () => {
  it('labels only task-agent reports in the message body', () => {
    const messages = [
      { role: 'assistant' as const, content: '@@@@by agent : ses_1 | Worker\nfinished' },
      { role: 'assistant' as const, content: '////by agent : ses_2 | Meta\nplan' },
      { role: 'assistant' as const, content: '@@@@by qq : user:1 | Nick\nhello' },
      { role: 'assistant' as const, content: 'ordinary reply' },
    ];
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: messages });
    m.setTotalSize(400);
    m.setVirtualItems(messages.map((_, index) => ({ index, start: index * 100, size: 100 })));

    const { container } = render(<ChatMessages />);

    expect(container.querySelectorAll('.worker-report-label')).toHaveLength(1);
    expect(container.querySelector('.message-row-worker-report')?.textContent).toContain('finished');
    expect(container.querySelector('.message-row-worker-report')?.textContent).toContain('Worker report');
  });
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

  it('binds user scroll tracking when async history mounts the first virtual rows', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: [] });
    const { container } = render(<ChatMessages />);
    expect(container.querySelector('.overflow-auto')).toBeNull();

    m.setTotalSize(2000);
    act(() => {
      useSessionStore.setState({ currentMessages: msgs(4) });
    });
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    userScroll(scrollEl, 500);
    m.setTotalSize(2600);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(3), { role: 'assistant', content: 'streamed after user scroll' }] });
    });
    expect(scrollEl.scrollTop).toBe(500);
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

  it('keeps following through measurement scrolls, stream growth, final, result, and DONE', () => {
    const initial = msgs(4);
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: initial });
    m.setTotalSize(1400);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(1400);

    const applyStep = (totalSize: number, currentMessages: Message[]) => {
      // Simulate the browser/virtualizer emitting a layout scroll while the
      // old scrollTop is still at the previous bottom. There is no user
      // gesture in this sequence.
      m.setTotalSize(totalSize);
      programmaticScroll(scrollEl);
      act(() => {
        useSessionStore.setState({ currentMessages });
      });
      expect(scrollEl.scrollTop).toBe(totalSize);
    };

    applyStep(1800, [
      ...initial.slice(0, 3),
      { role: 'assistant', content: 'stream delta 1', nativeItemId: 'turn-1' },
    ]);
    applyStep(2200, [
      ...initial.slice(0, 3),
      {
        role: 'assistant',
        content: Array.from({ length: 40 }, (_, index) => `stream line ${index}`).join('\n'),
        nativeItemId: 'turn-1',
      },
    ]);
    applyStep(2400, [
      ...initial.slice(0, 3),
      { role: 'assistant', content: 'final answer', nativeItemId: 'turn-1' },
    ]);
    applyStep(2500, [
      ...initial.slice(0, 3),
      { role: 'assistant', content: 'final answer', messageId: 'canonical-1' },
      { role: 'system', content: '[DONE] Task completed', nativeItemId: 'done-1' },
    ]);
    applyStep(2600, [
      ...initial.slice(0, 3),
      { role: 'assistant', content: 'final answer refreshed', messageId: 'canonical-1' },
      { role: 'system', content: '[DONE] Task completed', nativeItemId: 'done-1' },
    ]);
  });

  it.each(['wheel', 'touchstart'] as const)(
    'keeps a %s gesture active across delayed multi-frame inertia scrolls',
    (inputType) => {
      vi.useFakeTimers();
      useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
      m.setTotalSize(2000);
      const { container } = render(<ChatMessages />);
      const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
      expect(scrollEl.scrollTop).toBe(2000);

      // The first input does not have to produce a scroll event immediately.
      // Inertia/smooth scrolling can deliver the actual movement several
      // animation frames later. The old one-rAF intent marker expires here.
      if (inputType === 'wheel') fireEvent.wheel(scrollEl, { deltaY: -700 });
      else fireEvent.touchStart(scrollEl);
      vi.advanceTimersByTime(64);

      scrollEl.scrollTop = 700;
      fireEvent.scroll(scrollEl);
      vi.advanceTimersByTime(48);
      scrollEl.scrollTop = 500;
      fireEvent.scroll(scrollEl);

      m.setTotalSize(2600);
      act(() => {
        useSessionStore.setState({
          currentMessages: [...msgs(4), { role: 'assistant', content: 'late stream delta' }],
        });
      });

      expect(scrollEl.scrollTop).toBe(500);
      expect(container.querySelector('[title="Scroll to bottom"]')).not.toBeNull();
    },
  );

  it('does not opt out of follow mode for a trusted scroll without user input intent', () => {
    // jsdom cannot manufacture a trusted Event: its isTrusted property is a
    // non-configurable UA-owned getter. Guard the stronger source contract
    // here, then exercise the same no-input programmatic-scroll path below.
    expect(chatMessagesSource).not.toMatch(/isTrusted/);
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    // A browser/virtualizer correction can deliver a trusted scroll while no
    // wheel, touch, pointer, or keyboard input preceded it.
    scrollEl.scrollTop = 700;
    programmaticScroll(scrollEl);
    m.setTotalSize(2400);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4)] });
    });

    expect(scrollEl.scrollTop).toBe(2400);
  });

  it('does not treat a pure mouse hover pointermove as scroll intent', () => {
    vi.useFakeTimers();
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    // Hover movement has no pressed button and must not open user-scroll
    // activity before a delayed virtualizer/measurement correction.
    pointerMove(scrollEl, 'mouse', 0);
    vi.advanceTimersByTime(64);
    scrollEl.scrollTop = 700;
    programmaticScroll(scrollEl);
    vi.advanceTimersByTime(64);

    m.setTotalSize(2400);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4)] });
    });

    expect(scrollEl.scrollTop).toBe(2400);
  });

  it('lets a pressed mouse pointer drag opt out of follow mode', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    fireEvent.pointerDown(scrollEl, { pointerType: 'mouse', buttons: 1 });
    pointerMove(scrollEl, 'mouse', 1);
    scrollEl.scrollTop = 600;
    fireEvent.scroll(scrollEl);

    m.setTotalSize(2400);
    act(() => {
      useSessionStore.setState({
        currentMessages: [...msgs(4), { role: 'assistant', content: 'dragged away' }],
      });
    });

    expect(scrollEl.scrollTop).toBe(600);
    expect(container.querySelector('[title="Scroll to bottom"]')).not.toBeNull();
  });

  it('lets a real user scroll opt out after the programmatic layout window ends', () => {
    vi.useFakeTimers();
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    // Finish a measurement/resize generation before the next user gesture.
    m.setTotalSize(2200);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4)] });
      vi.advanceTimersByTime(1000);
    });

    userScroll(scrollEl, 600);
    m.setTotalSize(2600);
    act(() => {
      useSessionStore.setState({
        currentMessages: [...msgs(4), { role: 'assistant', content: 'must not pull user down' }],
      });
    });

    expect(scrollEl.scrollTop).toBe(600);
  });

  it('keeps follow mode after button recovery and a later UA scroll correction', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    userScroll(scrollEl, 600);
    const button = container.querySelector('[title="Scroll to bottom"]') as HTMLButtonElement;
    expect(button).not.toBeNull();
    fireEvent.click(button);
    expect(scrollEl.scrollTop).toBe(2000);

    scrollEl.scrollTop = 1600;
    programmaticScroll(scrollEl);
    m.setTotalSize(2400);
    act(() => {
      useSessionStore.setState({
        currentMessages: [...msgs(4), { role: 'assistant', content: 'after correction' }],
      });
    });

    expect(scrollEl.scrollTop).toBe(2400);
  });

  it('keeps follow mode through a programmatic viewport resize', () => {
    vi.useFakeTimers();
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    mockClientHeight = 360;
    act(() => {
      resizeObserverCallback?.([], {} as ResizeObserver);
      vi.runOnlyPendingTimers();
    });
    expect(scrollEl.scrollTop).toBe(2000);

    m.setTotalSize(2400);
    act(() => {
      useSessionStore.setState({ currentMessages: msgs(4) });
    });
    act(() => {
      resizeObserverCallback?.([], {} as ResizeObserver);
      vi.runOnlyPendingTimers();
    });
    expect(scrollEl.scrollTop).toBe(2400);
  });

  it('hides the button and follows new messages within the bottom threshold', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);
    expect(container.querySelector('[title="Scroll to bottom"]')).toBeNull();

    // 2000 - (2000 - 400 - threshold) - 400 = threshold.
    userScroll(scrollEl, 2000 - 400 - SCROLL_BOTTOM_THRESHOLD);
    expect(container.querySelector('[title="Scroll to bottom"]')).toBeNull();

    m.setTotalSize(2200);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(1, 'new')] });
    });

    expect(scrollEl.scrollTop).toBe(2200);
    expect(container.querySelector('[title="Scroll to bottom"]')).toBeNull();
  });

  it('shows the button and does not follow when the user is beyond the threshold', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    // One pixel beyond the follow zone must opt out.
    userScroll(scrollEl, 2000 - 400 - SCROLL_BOTTOM_THRESHOLD - 1);
    expect(container.querySelector('[title="Scroll to bottom"]')).not.toBeNull();
    m.setTotalSize(2600);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(1, 'new')] });
    });

    expect(scrollEl.scrollTop).toBe(2000 - 400 - SCROLL_BOTTOM_THRESHOLD - 1);
    expect(container.querySelector('[title="Scroll to bottom"]')).not.toBeNull();
  });

  it('does not pull an away-from-bottom user down on measurement changes', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scrollEl.scrollTop).toBe(2000);

    // A real user movement is explicitly marked before the layout change.
    userScroll(scrollEl, 700);
    m.setTotalSize(2400);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4)] });
    });

    expect(scrollEl.scrollTop).toBe(700);
  });

  it('does NOT yank the user to the bottom when older messages are prepended while scrolled up', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    // Land at the bottom on entry, then the user scrolls up to the top.
    expect(scrollEl.scrollTop).toBe(2000);
    userScroll(scrollEl, 0);
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

    userScroll(scrollEl, 0); // user scrolls away → unpinned

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

    userScroll(scrollEl, 700);

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

  it('hides the button and resumes following after the user returns near the bottom', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4) });
    m.setTotalSize(2000);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    userScroll(scrollEl, 500);
    expect(container.querySelector('[title="Scroll to bottom"]')).not.toBeNull();
    m.setTotalSize(2200);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(1, 'paused')] });
    });
    expect(scrollEl.scrollTop).toBe(500);

    // Returning within the threshold re-enables follow mode and hides the
    // button before the next message arrives.
    userScroll(scrollEl, 2200 - 400 - SCROLL_BOTTOM_THRESHOLD);
    expect(container.querySelector('[title="Scroll to bottom"]')).toBeNull();
    m.setTotalSize(2800);
    act(() => {
      useSessionStore.setState({ currentMessages: [...msgs(4), ...msgs(2, 'follow')] });
    });
    expect(scrollEl.scrollTop).toBe(2800);
    expect(container.querySelector('[title="Scroll to bottom"]')).toBeNull();
  });

  it('resets follow mode when switching sessions and returning to the first session', () => {
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(4, 'A') });
    m.setTotalSize(1800);
    const { container } = render(<ChatMessages />);
    const scrollEl = container.querySelector('.overflow-auto') as HTMLElement;

    userScroll(scrollEl, 500);
    expect(container.querySelector('[title="Scroll to bottom"]')).not.toBeNull();

    m.setTotalSize(1200);
    act(() => {
      useSessionStore.setState({ currentSessionId: 's2', currentMessages: msgs(3, 'B') });
    });
    expect(scrollEl.scrollTop).toBe(1200);

    userScroll(scrollEl, 400);
    m.setTotalSize(2100);
    act(() => {
      useSessionStore.setState({ currentSessionId: 's1', currentMessages: msgs(6, 'A-return') });
    });
    expect(scrollEl.scrollTop).toBe(2100);
    expect(container.querySelector('[title="Scroll to bottom"]')).toBeNull();
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
    userScroll(scrollEl, 100);
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

  it('keeps the virtual key when a provisional tool delta changes role', () => {
    const provisional: Message = {
      role: 'assistant',
      content: 'running command',
      nativeItemId: 'tool-transition-1',
    };
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: [provisional] });
    m.setVirtualItems([{ index: 0, start: 0, size: 120 }]);
    const { container } = render(<ChatMessages />);

    const getItemKey = m.state.options?.getItemKey;
    expect(getItemKey).toBeTypeOf('function');
    const provisionalKey = getItemKey!(0);
    expect(groupMessages([provisional])[0]).toEqual(provisional);

    // appendEventToMessages preserves the Message identity when the provider
    // finalizes the same native item as a tool. Reusing the logical display
    // key prevents a virtual row remount during a neighboring delta resize.
    provisional.role = 'tool';
    act(() => {
      useSessionStore.setState({ currentMessages: [provisional] });
    });

    expect(getItemKey!(0)).toBe(provisionalKey);
    expect([...container.querySelectorAll('[data-index]')].map((row) => row.getAttribute('data-index')))
      .toEqual(['0']);
  });

  it('keeps an expanded thinking group and its virtual row while adjacent thinking streams in', () => {
    const first: Message = {
      role: 'thinking',
      content: 'streamed first thought',
      blockId: 'stream-group-first',
    };
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: [first] });
    m.setVirtualItems([{ index: 0, start: 0, size: 120 }]);
    const { container } = render(<ChatMessages />);
    const getItemKey = m.state.options?.getItemKey;
    const initialKey = getItemKey?.(0);
    const firstDisclosure = screen.getByRole('button', { name: 'thinking' });
    fireEvent.click(firstDisclosure);
    expect(firstDisclosure.getAttribute('aria-expanded')).toBe('true');

    act(() => {
      useSessionStore.setState({
        currentMessages: [
          first,
          { role: 'thinking', content: 'streamed second thought', blockId: 'stream-group-second' },
        ],
      });
    });

    const updatedDisclosure = screen.getByRole('button', { name: '2 thinking blocks' });
    expect(getItemKey?.(0)).toBe(initialKey);
    expect(updatedDisclosure.getAttribute('aria-expanded')).toBe('true');
    expect(container.textContent).toContain('streamed first thought');
    expect(container.textContent).toContain('streamed second thought');
  });

  it('scopes virtual rows and expanded thinking-group state to the selected Session', () => {
    const thinking: Message[] = [
      { role: 'thinking', content: 'session-scoped plan', messageId: 'same-message-id' },
      { role: 'thinking', content: 'session-scoped detail', blockId: 'same-thinking-block' },
    ];
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: thinking });
    m.setVirtualItems([{ index: 0, start: 0, size: 120 }]);
    const { container } = render(<ChatMessages />);

    const firstKey = m.state.options?.getItemKey?.(0);
    const disclosure = container.querySelector('.thinking button')!;
    fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('true');

    act(() => {
      useSessionStore.setState({ currentSessionId: 's2', currentMessages: thinking.map((item) => ({ ...item })) });
    });

    expect(m.state.options?.getItemKey?.(0)).not.toBe(firstKey);
    expect(container.querySelector('.thinking button')?.getAttribute('aria-expanded')).toBe('false');
  });

  it('keeps an expanded non-body parent across stream appends and resets it on Session change', () => {
    useAppSettingsStore.setState({ mergeConsecutiveNonBodyBlocks: true });
    const first: Message = {
      role: 'thinking',
      content: 'session-scoped plan '.repeat(1_300),
      blockId: 'non-body-stream-first',
    };
    const second: Message = {
      role: 'tool',
      content: 'Command({"command":"true"})',
      blockId: 'non-body-stream-second',
    };
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: [first] });
    m.setVirtualItems([{ index: 0, start: 0, size: 120 }]);
    const { container } = render(<ChatMessages />);

    const parent = screen.getByRole('button', { name: /1 non-body blocks/ });
    const firstKey = m.state.options?.getItemKey?.(0);
    fireEvent.click(parent);
    expect(parent.getAttribute('aria-expanded')).toBe('true');
    const thinkingDisclosure = screen.getByRole('button', { name: 'thinking' });
    fireEvent.click(thinkingDisclosure);
    expect(thinkingDisclosure.getAttribute('aria-expanded')).toBe('true');

    act(() => {
      useSessionStore.setState({ currentMessages: [first, second] });
    });
    const streamedParent = screen.getByRole('button', { name: /2 non-body blocks/ });
    expect(m.state.options?.getItemKey?.(0)).toBe(firstKey);
    expect(streamedParent.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('button', { name: 'thinking' }).getAttribute('aria-expanded')).toBe('true');
    expect(container.textContent).toContain('session-scoped plan');
    expect(screen.getByRole('button', { name: '1 tools' })).toBeTruthy();

    const streamedKey = m.state.options?.getItemKey?.(0);
    act(() => {
      useSessionStore.setState({
        currentSessionId: 's2',
        currentMessages: [first, second].map((item) => ({ ...item })),
      });
    });

    expect(m.state.options?.getItemKey?.(0)).not.toBe(streamedKey);
    expect(screen.getByRole('button', { name: /2 non-body blocks/ }).getAttribute('aria-expanded'))
      .toBe('false');
    expect(screen.queryByRole('button', { name: '1 tools' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /2 non-body blocks/ }));
    expect(screen.getByRole('button', { name: 'thinking' }).getAttribute('aria-expanded'))
      .toBe('false');
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
    userScroll(scrollEl, 500);
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

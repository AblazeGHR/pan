// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  hasChatScrollToBottomHandler,
  registerChatScrollToBottomHandler,
  requestChatScrollToBottom,
} from './chatScrollBridge';

let unregister: (() => void) | null = null;

afterEach(() => {
  unregister?.();
  unregister = null;
});

describe('chat scroll-to-bottom bridge', () => {
  it('reports and routes only for the mounted Session', () => {
    const scroll = vi.fn(() => true);
    unregister = registerChatScrollToBottomHandler('s1', scroll);

    expect(hasChatScrollToBottomHandler('s1')).toBe(true);
    expect(hasChatScrollToBottomHandler('s2')).toBe(false);
    expect(requestChatScrollToBottom('s2')).toBe(false);
    expect(scroll).not.toHaveBeenCalled();

    expect(requestChatScrollToBottom('s1')).toBe(true);
    expect(scroll).toHaveBeenCalledTimes(1);
  });

  it('returns false instead of throwing without a mounted chat', () => {
    expect(hasChatScrollToBottomHandler('s1')).toBe(false);
    expect(requestChatScrollToBottom('s1')).toBe(false);
  });

  it('drops the handler on unregister but never a newer registration', () => {
    const first = vi.fn(() => true);
    const staleCleanup = registerChatScrollToBottomHandler('s1', first);
    const second = vi.fn(() => true);
    unregister = registerChatScrollToBottomHandler('s2', second);

    // A Session switch mounts the successor before the predecessor's cleanup.
    staleCleanup();
    expect(hasChatScrollToBottomHandler('s2')).toBe(true);
    expect(requestChatScrollToBottom('s2')).toBe(true);
    expect(requestChatScrollToBottom('s1')).toBe(false);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);

    unregister();
    unregister = null;
    expect(hasChatScrollToBottomHandler('s2')).toBe(false);
  });
});

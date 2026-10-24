// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { jumpToMessage } from './jumpToMessage';
import { useSessionStore } from '@/stores/sessionStore';
import type { ChatMessagesHandle } from './ChatMessages';
import cases from './navigationUserCases.json';
import { getNavigationKind } from './messageFilter';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('agrees with backend on every shared provenance/header case', () => {
  for (const fixture of cases) expect(getNavigationKind(fixture.message) === 'user', JSON.stringify(fixture)).toBe(fixture.user);
});

it('uses the rail canonical pagination and real scroll call parameters, with mount retry', async () => {
  const message = { role: 'user', content: 'Older', messageId: 'msg_target' };
  const load = vi.fn().mockResolvedValue(message);
  vi.spyOn(useSessionStore, 'getState').mockReturnValue({ ensureMessageLoaded: load } as unknown as ReturnType<typeof useSessionStore.getState>);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  const scrollToMessage = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
  const ref = { current: { scrollToMessage } as unknown as ChatMessagesHandle };
  expect(await jumpToMessage(ref, { offset: 12, total: 1500, messageId: 'msg_target' }, () => true)).toBe(true);
  expect(load).toHaveBeenCalledWith(1487, 1500);
  expect(scrollToMessage).toHaveBeenNthCalledWith(1, message, 12);
  expect(scrollToMessage).toHaveBeenNthCalledWith(2, message, 12);
});

it('never scrolls a changed identity or abandoned Session', async () => {
  const load = vi.fn().mockResolvedValue({ messageId: 'different' });
  vi.spyOn(useSessionStore, 'getState').mockReturnValue({ ensureMessageLoaded: load } as unknown as ReturnType<typeof useSessionStore.getState>);
  const scrollToMessage = vi.fn();
  const ref = { current: { scrollToMessage } as unknown as ChatMessagesHandle };
  await expect(jumpToMessage(ref, { offset: 1, total: 10, messageId: 'original' }, () => true)).rejects.toThrow('替换');
  expect(await jumpToMessage(ref, { offset: 1, total: 10 }, () => false)).toBe(false);
  expect(scrollToMessage).not.toHaveBeenCalled();
});

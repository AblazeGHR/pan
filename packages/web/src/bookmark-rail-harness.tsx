/* eslint-disable react-refresh/only-export-components */
/* Production bookmark rail harness: mounts the REAL MessageNavigationRail
 * against fixture rows whose `bookmarked` flags come from the same shape the
 * backend projection emits.  This is verification scaffolding only — it is
 * not a production entry point and carries no mock decoration logic. */
import { useMemo } from 'react';
import { createRoot } from 'react-dom/client';
import { MessageNavigationRail } from './components/chat/MessageNavigationRail';
import { DEFAULT_SETTINGS, useAppSettingsStore } from './stores/appSettingsStore';
import { useSessionStore } from './stores/sessionStore';
import { useBookmarkStore } from './stores/bookmarkStore';
import { markDurableRow } from '@/stores/messageOrdering';
import type { Message } from './types';
import './index.css';

const sessionId = 'bookmark-rail-fixture';
const FIXTURE_ROWS: Message[] = [
  { role: 'user', content: '首条提问（已书签）', messageId: 'pan:00000000000000000000000000000001', bookmarked: true },
  { role: 'assistant', content: '普通回复一', messageId: 'pan:00000000000000000000000000000002' },
  { role: 'assistant', content: '被书签的普通回复', messageId: 'pan:00000000000000000000000000000003', bookmarked: true },
  { role: 'user', content: '第二条提问', messageId: 'pan:00000000000000000000000000000004' },
  { role: 'assistant', content: '末尾回复', messageId: 'pan:00000000000000000000000000000005' },
  { role: 'assistant', content: '再一条普通回复', messageId: 'pan:00000000000000000000000000000006' },
];
FIXTURE_ROWS.forEach((message, index) => markDurableRow(message, index));

useSessionStore.setState({
  currentSessionId: sessionId,
  currentMessages: FIXTURE_ROWS,
  historyLoadEnd: FIXTURE_ROWS.length,
  hasMoreMessages: false,
  sessions: [{ id: sessionId, historyTotal: FIXTURE_ROWS.length } as never],
  sessionTranscripts: {},
  ensureMessageLoaded: async (fromEnd: number) =>
    FIXTURE_ROWS[FIXTURE_ROWS.length - 1 - fromEnd] ?? null,
});
useBookmarkStore.setState({
  sessions: {
    [sessionId]: { revision: 1, marks: {}, loaded: true, pending: {}, error: null },
  },
});
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });

declare global {
  interface Window {
    /** Overlay hook: applies a mark exactly like an optimistic mutation or a
     *  dual-client broadcast would, so the e2e can drive the real store. */
    __markBookmark: (messageId: string, value: boolean) => void;
  }
}

window.__markBookmark = (messageId, value) => {
  const current = useBookmarkStore.getState().sessions[sessionId];
  useBookmarkStore.setState({
    sessions: {
      [sessionId]: {
        revision: (current?.revision ?? 0) + 1,
        marks: { ...(current?.marks ?? {}), [messageId]: value },
        loaded: true, pending: {}, error: null,
      },
    },
  });
};

function Harness() {
  const chatRef = useMemo(() => ({
    current: {
      scrollToMessage: () => true,
      // No viewport snapshot: this harness verifies decoration, not the
      // nearest-locate highlight (which its own e2e already covers), so no
      // marker carries is-viewport-target and colors compare cleanly.
      getViewportHistoryRange: () => null,
    },
  }), []);
  return (
    <div
      className="chat-view-stage"
      data-testid="bookmark-rail-stage"
      style={{ position: 'absolute', inset: 0, display: 'flex', justifyContent: 'flex-end' }}
    >
      <MessageNavigationRail chatRef={chatRef} expanded />
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<Harness />);

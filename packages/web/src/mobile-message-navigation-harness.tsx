/* eslint-disable react-refresh/only-export-components */
import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MessageNavigationDock } from './components/chat/MessageNavigationDock';
import type { ChatMessagesHandle } from './components/chat/ChatMessages';
import { DEFAULT_SETTINGS, useAppSettingsStore } from './stores/appSettingsStore';
import { useSessionStore } from './stores/sessionStore';
import type { Message } from './types';
import './index.css';

const sessionId = 'mobile-navigation-scrub-fixture';
const messages: Message[] = Array.from({ length: 48 }, (_, index) => ({
  role: 'user',
  content: `Chromium scrub preview ${String(index + 1).padStart(2, '0')} ${'preview detail '.repeat(4)}`,
}));

declare global {
  interface Window {
    __scrubJumpCalls: number;
    __scrubScrollCalls: number;
  }
}

window.__scrubJumpCalls = 0;
window.__scrubScrollCalls = 0;
useSessionStore.setState({
  currentSessionId: sessionId,
  currentMessages: messages,
  historyLoadEnd: 0,
  sessions: [{ id: sessionId, name: 'Mobile scrub fixture', historyTotal: messages.length } as never],
  ensureMessageLoaded: async (fromEnd: number) => {
    window.__scrubJumpCalls += 1;
    return messages[messages.length - 1 - fromEnd] ?? null;
  },
});
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, showMessageNavigationRail: true });

function Harness() {
  const [mobileExpanded, setMobileExpanded] = useState(true);
  const dockRef = useRef<HTMLDivElement | null>(null);
  const chatRef = useRef<ChatMessagesHandle>({
    scrollToMessage: () => {
      window.__scrubScrollCalls += 1;
      return true;
    },
  });

  return (
    <div
      className="chat-view-stage"
      data-testid="scrub-stage"
      style={{ position: 'absolute', top: 80, left: 0, right: 0, height: 360 }}
    >
      <MessageNavigationDock
        chatRef={chatRef}
        dockRef={dockRef}
        isMobile
        mobileExpanded={mobileExpanded}
        onMobileClose={() => setMobileExpanded(false)}
        onRestoreFocus={() => {}}
      />
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<Harness />);

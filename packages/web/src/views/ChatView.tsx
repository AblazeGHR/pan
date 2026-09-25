import { ChatLayout } from '@/components/layout/ChatLayout';
import { ChatMessages, type ChatMessagesHandle } from '@/components/chat/ChatMessages';
import { MessageNavigationDock, MESSAGE_NAVIGATION_PANEL_ID } from '@/components/chat/MessageNavigationDock';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import { InputRow } from '@/components/chat/InputRow';
import { ApprovalBanner } from '@/components/chat/ApprovalBanner';
import { UserInputBanner } from '@/components/chat/UserInputBanner';
import { ElicitationBanner } from '@/components/chat/ElicitationBanner';
import { TerminalInteractionBanner } from '@/components/chat/TerminalInteractionBanner';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { ChevronLeft, ChevronRight } from 'lucide-react';

export default function ChatView() {
  const chatRef = useRef<ChatMessagesHandle>(null);
  const chatStageRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  // Unmounting (rather than hiding) the rail is the point of the switch: the
  // dock (including its cached index) is removed when the master switch is off.
  const showMessageNavigationRail = useAppSettingsStore((s) => s.showMessageNavigationRail);
  const { isMobile } = useMediaQuery();
  const [mobileExpanded, setMobileExpanded] = useState(false);

  useEffect(() => {
    // Enabling the master switch and changing viewport modes both start folded.
    setMobileExpanded(false);
  }, [showMessageNavigationRail, isMobile]);

  const restoreChatFocus = useCallback(() => {
    chatStageRef.current?.focus();
  }, []);

  const closeMobileNavigation = useCallback(() => {
    if (dockRef.current?.contains(document.activeElement)) {
      mobileToggleRef.current?.focus();
    }
    setMobileExpanded(false);
  }, []);

  const toggleMobileNavigation = () => {
    if (mobileExpanded) {
      closeMobileNavigation();
      return;
    }
    setMobileExpanded(true);
  };

  const topBarRightAction = showMessageNavigationRail && isMobile ? (
    <button
      ref={mobileToggleRef}
      type="button"
      className="message-navigation-mobile-toggle"
      aria-label={mobileExpanded ? 'Close message navigation rail' : 'Open message navigation rail'}
      aria-expanded={mobileExpanded}
      aria-controls={MESSAGE_NAVIGATION_PANEL_ID}
      title={mobileExpanded ? 'Close message navigation rail' : 'Open message navigation rail'}
      data-testid="mobile-message-navigation-toggle"
      onClick={toggleMobileNavigation}
    >
      {mobileExpanded ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
    </button>
  ) : undefined;

  return (
    <ChatLayout topBarRightAction={topBarRightAction}>
      <div className="flex flex-col h-full min-h-0">
        <ApprovalBanner />
        <UserInputBanner />
        <ElicitationBanner />
        <TerminalInteractionBanner />
        <div ref={chatStageRef} className="chat-view-stage flex flex-1 min-h-0 min-w-0" tabIndex={-1}>
          <ChatMessages ref={chatRef} />
          {showMessageNavigationRail && (
            <MessageNavigationDock
              chatRef={chatRef}
              dockRef={dockRef}
              isMobile={isMobile}
              mobileExpanded={mobileExpanded}
              onMobileClose={closeMobileNavigation}
              onRestoreFocus={restoreChatFocus}
            />
          )}
        </div>
        <InputRow />
      </div>
    </ChatLayout>
  );
}

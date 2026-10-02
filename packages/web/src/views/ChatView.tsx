import { ChatLayout } from '@/components/layout/ChatLayout';
import { ChatMessages, type ChatMessagesHandle } from '@/components/chat/ChatMessages';
import { MessageNavigationDock, MESSAGE_NAVIGATION_PANEL_ID } from '@/components/chat/MessageNavigationDock';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useSessionStore } from '@/stores/sessionStore';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { InputRow } from '@/components/chat/InputRow';
import { ApprovalBanner } from '@/components/chat/ApprovalBanner';
import { UserInputBanner } from '@/components/chat/UserInputBanner';
import { ElicitationBanner } from '@/components/chat/ElicitationBanner';
import { TerminalInteractionBanner } from '@/components/chat/TerminalInteractionBanner';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { ChevronLeft, ChevronRight } from 'lucide-react';

const SessionHistorySearch = lazy(() =>
  import('@/components/chat/SessionHistorySearch').then((module) => ({
    default: module.SessionHistorySearch,
  })),
);

const GlobalHistorySearch = lazy(() =>
  import('@/components/chat/GlobalHistorySearch').then((module) => ({
    default: module.GlobalHistorySearch,
  })),
);

export default function ChatView() {
  const chatRef = useRef<ChatMessagesHandle>(null);
  const chatStageRef = useRef<HTMLDivElement>(null);
  const [popupContainer, setPopupContainer] = useState<HTMLDivElement | null>(null);
  const setChatStage = useCallback((element: HTMLDivElement | null) => {
    chatStageRef.current = element;
    setPopupContainer(element);
  }, []);
  const dockRef = useRef<HTMLDivElement>(null);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  // Unmounting (rather than hiding) the rail is the point of the switch: the
  // dock (including its cached index) is removed when the master switch is off.
  const showMessageNavigationRail = useAppSettingsStore((s) => s.showMessageNavigationRail);
  const showHistorySearch = useAppSettingsStore((s) => s.showHistorySearch);
  const { isMobile } = useMediaQuery();
  const [mobileExpanded, setMobileExpanded] = useState(false);
  const [activeHistorySearch, setActiveHistorySearch] = useState<'session' | 'global' | null>(null);
  const [searchTarget, setSearchTarget] = useState<{ sessionId: string; messageId: string; query?: string; occurrence?: number } | null>(null);

  useEffect(() => {
    // Enabling the master switch and changing viewport modes both start folded.
    setMobileExpanded(false);
  }, [showMessageNavigationRail, isMobile]);

  useEffect(() => {
    if (!showHistorySearch) {
      setActiveHistorySearch(null);
      setSearchTarget(null);
    }
  }, [showHistorySearch]);

  const handleSessionSearchOpenChange = useCallback((open: boolean) => {
    setActiveHistorySearch((active) => open ? 'session' : active === 'session' ? null : active);
  }, []);

  const handleGlobalSearchOpenChange = useCallback((open: boolean) => {
    setActiveHistorySearch((active) => open ? 'global' : active === 'global' ? null : active);
  }, []);

  const handleSessionHighlight = useCallback((messageId: string | null, query?: string, occurrence = 0) => {
    if (!messageId) {
      setSearchTarget(null);
      return;
    }
    const sessionId = useSessionStore.getState().currentSessionId;
    setSearchTarget(sessionId ? { sessionId, messageId, query, occurrence } : null);
  }, []);

  const handleGlobalHighlight = useCallback((sessionId: string | null, messageId: string | null, query?: string) => {
    setSearchTarget(sessionId && messageId ? { sessionId, messageId, query } : null);
  }, []);

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
        <div ref={setChatStage} className="chat-view-stage flex flex-1 min-h-0 min-w-0" tabIndex={-1}>
          <ChatMessages
            ref={chatRef}
            hideScrollToBottom={showMessageNavigationRail && isMobile && mobileExpanded}
            searchTarget={showHistorySearch ? searchTarget : null}
          />
          {(showMessageNavigationRail || showHistorySearch) && (
            <aside className={`chat-tools-sidebar${showMessageNavigationRail ? ' has-navigation' : ''}${isMobile ? ' is-mobile' : ''}`}
              data-testid="chat-tools-sidebar" aria-label="Chat search and navigation">
          {showHistorySearch && (
            <div className="chat-tools-sidebar__search">
              <Suspense fallback={null}>
                <SessionHistorySearch
                  chatRef={chatRef}
                  popupContainer={popupContainer}
                  isMobile={isMobile}
                  isOpen={activeHistorySearch === 'session'}
                  onOpenChange={handleSessionSearchOpenChange}
                  onHighlightMessage={handleSessionHighlight}
                />
              </Suspense>
              <Suspense fallback={null}>
                <GlobalHistorySearch
                  chatRef={chatRef}
                  popupContainer={popupContainer}
                  isMobile={isMobile}
                  open={activeHistorySearch === 'global'}
                  onOpenChange={handleGlobalSearchOpenChange}
                  onHighlightMessage={handleGlobalHighlight}
                />
              </Suspense>
            </div>
          )}
          {showMessageNavigationRail && (
            <div className="chat-tools-sidebar__navigation">
            <MessageNavigationDock
              chatRef={chatRef}
              dockRef={dockRef}
              isMobile={isMobile}
              mobileExpanded={mobileExpanded}
              onMobileClose={closeMobileNavigation}
              onRestoreFocus={restoreChatFocus}
            />
            </div>
          )}
            </aside>
          )}
        </div>
        <InputRow />
      </div>
    </ChatLayout>
  );
}

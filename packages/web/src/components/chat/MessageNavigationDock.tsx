import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
  type ReactNode,
} from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { MessageNavigationRail } from './MessageNavigationRail';
import type { ChatMessagesHandle } from './ChatMessages';
import { useSessionStore } from '@/stores/sessionStore';

export const MESSAGE_NAVIGATION_PANEL_ID = 'message-navigation-panel';

interface MessageNavigationDockProps {
  chatRef: RefObject<ChatMessagesHandle | null>;
  isMobile: boolean;
  mobileExpanded: boolean;
  onMobileClose: () => void;
  onRestoreFocus: () => void;
  dockRef: { current: HTMLDivElement | null };
  children?: ReactNode;
  showNavigation?: boolean;
}

const COLLAPSE_DELAY_MS = 90;

export function MessageNavigationDock({
  chatRef,
  isMobile,
  mobileExpanded,
  onMobileClose,
  onRestoreFocus,
  dockRef,
  children,
  showNavigation = true,
}: MessageNavigationDockProps) {
  const handleRef = useRef<HTMLButtonElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const collapseTimerRef = useRef<number | null>(null);
  const pointerInteractionRef = useRef(false);
  const setDockElement = useCallback((element: HTMLDivElement | null) => {
    rootRef.current = element;
    dockRef.current = element;
  }, [dockRef]);
  const [pointerWithin, setPointerWithin] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [keyboardDismissed, setKeyboardDismissed] = useState(false);
  // The rail portals its tag-filter menu to <body>, so the menu is physically
  // outside this dock: pointer leave would fold the panel out from under the
  // open menu. The rail reports that menu's lifetime; while it is open the
  // hover fold is held, and releasing it restores the plain pointer/focus
  // rules. This is the rail's own popover, not a global open-popover probe.
  const [filterPopoverOpen, setFilterPopoverOpen] = useState(false);
  const filterPopoverOpenRef = useRef(false);
  const pointerInsideRef = useRef(false);
  const focusWithinRef = useRef(focusWithin);
  focusWithinRef.current = focusWithin;
  const keyboardDismissedRef = useRef(keyboardDismissed);
  keyboardDismissedRef.current = keyboardDismissed;
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const [openedSession, setOpenedSession] = useState<{ id: string | null } | null>(null);
  // Folding keeps this Session's index warm. A different Session must not
  // start a complete history scan behind the folded dock on its first screen.
  const hasOpened = openedSession !== null && openedSession.id === currentSessionId;

  const expanded = isMobile
    ? mobileExpanded
    : pointerWithin || filterPopoverOpen || (focusWithin && !keyboardDismissed);
  const label = children ? 'chat tools sidebar' : 'message navigation rail';

  useEffect(() => {
    if (expanded) setOpenedSession({ id: currentSessionId });
  }, [expanded, currentSessionId]);

  useEffect(() => () => {
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
    }
  }, []);

  useLayoutEffect(() => () => {
    const dock = rootRef.current;
    if (dock?.contains(document.activeElement)) onRestoreFocus();
  }, [dockRef, onRestoreFocus]);

  const cancelPendingCollapse = useCallback(() => {
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = null;
    }
  }, []);

  const schedulePointerCollapse = useCallback(() => {
    cancelPendingCollapse();
    collapseTimerRef.current = window.setTimeout(() => {
      collapseTimerRef.current = null;
      // An open rail popover owns the panel: folding now would hide its anchor
      // while the menu is still on screen. The release path re-schedules.
      if (filterPopoverOpenRef.current) return;
      if (focusWithinRef.current && !keyboardDismissedRef.current) return;
      setPointerWithin(false);
      const focusedElement = document.activeElement;
      if (
        pointerInteractionRef.current &&
        focusedElement instanceof HTMLElement &&
        rootRef.current?.contains(focusedElement) &&
        focusedElement !== handleRef.current
      ) {
        handleRef.current?.focus();
      }
    }, COLLAPSE_DELAY_MS);
  }, [cancelPendingCollapse]);

  const handleFilterOpenChange = useCallback((open: boolean) => {
    filterPopoverOpenRef.current = open;
    setFilterPopoverOpen(open);
    if (open) {
      // A fold queued by leaving the dock for the just-opened menu must not
      // fire underneath it.
      cancelPendingCollapse();
      return;
    }
    // Closing the menu restores the original mouse-leave fold: only when the
    // pointer really is outside the dock.
    if (!isMobile && !pointerInsideRef.current) schedulePointerCollapse();
  }, [cancelPendingCollapse, isMobile, schedulePointerCollapse]);

  const isInsideDock = (target: EventTarget | null) => target instanceof Node && Boolean(rootRef.current?.contains(target));
  const handleFocus = (event: FocusEvent<HTMLDivElement>) => {
    if (!isInsideDock(event.target)) return;
    if (pointerInteractionRef.current) {
      setFocusWithin(false);
      return;
    }
    setKeyboardDismissed(false);
    setFocusWithin(true);
    setOpenedSession({ id: currentSessionId });
  };

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (!isInsideDock(event.target)) return;
    if (event.relatedTarget instanceof Node && rootRef.current?.contains(event.relatedTarget)) {
      return;
    }
    window.setTimeout(() => {
      if (!rootRef.current?.contains(document.activeElement)) {
        pointerInteractionRef.current = false;
        setFocusWithin(false);
        setKeyboardDismissed(false);
      }
    }, 0);
  };

  const handlePointerEnter = (event: PointerEvent<HTMLDivElement>) => {
    if (!isInsideDock(event.target)) return;
    if (isMobile || (event.pointerType && event.pointerType !== 'mouse' && event.pointerType !== 'pen')) {
      return;
    }
    cancelPendingCollapse();
    pointerInsideRef.current = true;
    setPointerWithin(true);
    setOpenedSession({ id: currentSessionId });
  };

  const handlePointerOut = (event: PointerEvent<HTMLDivElement>) => {
    if (!isInsideDock(event.target)) return;
    if (isMobile) return;
    if (event.relatedTarget instanceof Node && rootRef.current?.contains(event.relatedTarget)) {
      return;
    }
    pointerInsideRef.current = false;
    schedulePointerCollapse();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!isInsideDock(event.target)) return;
    pointerInteractionRef.current = false;
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    if (isMobile) {
      onMobileClose();
      return;
    }
    handleRef.current?.focus();
    setKeyboardDismissed(true);
    setPointerWithin(false);
    setFocusWithin(false);
  };

  const handleDesktopToggle = (event: React.MouseEvent<HTMLButtonElement>) => {
    if (expanded) {
      setKeyboardDismissed(true);
      setPointerWithin(false);
      setFocusWithin(false);
    } else {
      const keyboardActivation = event.detail === 0;
      setKeyboardDismissed(!keyboardActivation);
      setFocusWithin(keyboardActivation);
      setOpenedSession({ id: currentSessionId });
    }
  };

  return (
    <div
      ref={setDockElement}
      className={`message-navigation-dock${expanded ? ' is-open' : ''}${isMobile ? ' is-mobile' : ''}`}
      data-testid="message-navigation-dock"
      data-placement={isMobile ? 'viewport-end' : 'viewport-end-before-scrollbar'}
      data-expanded={expanded}
      onPointerEnter={handlePointerEnter}
      onPointerDown={(event) => {
        if (!isInsideDock(event.target)) return;
        if (!isMobile && (!event.pointerType || event.pointerType === 'mouse' || event.pointerType === 'pen')) {
          pointerInteractionRef.current = true;
          setKeyboardDismissed(true);
        }
      }}
      onPointerOut={handlePointerOut}
      onPointerLeave={(event) => {
        if (!isMobile && isInsideDock(event.target)) schedulePointerCollapse();
      }}
      onFocusCapture={handleFocus}
      onBlurCapture={handleBlur}
      onKeyDown={handleKeyDown}
    >
      {!isMobile && (
        <button
          ref={handleRef}
          type="button"
          className="message-navigation-dock__handle"
          aria-label={`${expanded ? 'Close' : 'Open'} ${label}`}
          aria-expanded={expanded}
          aria-controls={MESSAGE_NAVIGATION_PANEL_ID}
          title={`${expanded ? 'Close' : 'Open'} ${label}`}
          onClick={handleDesktopToggle}
        >
          {expanded ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      )}
      <div
        id={MESSAGE_NAVIGATION_PANEL_ID}
        className="message-navigation-dock__panel"
        aria-hidden={!expanded}
        inert={!expanded}
      >
        {children}
        {showNavigation && hasOpened && (
          <div className="chat-tools-sidebar__navigation">
            <MessageNavigationRail
              chatRef={chatRef}
              expanded={expanded}
              isMobile={isMobile}
              mobileExpanded={mobileExpanded}
              onFilterOpenChange={handleFilterOpenChange}
            />
          </div>
        )}
      </div>
    </div>
  );
}

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
} from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { MessageNavigationRail } from './MessageNavigationRail';
import type { ChatMessagesHandle } from './ChatMessages';

export const MESSAGE_NAVIGATION_PANEL_ID = 'message-navigation-panel';

interface MessageNavigationDockProps {
  chatRef: RefObject<ChatMessagesHandle | null>;
  isMobile: boolean;
  mobileExpanded: boolean;
  onMobileClose: () => void;
  onRestoreFocus: () => void;
  dockRef: { current: HTMLDivElement | null };
}

const COLLAPSE_DELAY_MS = 90;

export function MessageNavigationDock({
  chatRef,
  isMobile,
  mobileExpanded,
  onMobileClose,
  onRestoreFocus,
  dockRef,
}: MessageNavigationDockProps) {
  const handleRef = useRef<HTMLButtonElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const collapseTimerRef = useRef<number | null>(null);
  const setDockElement = useCallback((element: HTMLDivElement | null) => {
    rootRef.current = element;
    dockRef.current = element;
  }, [dockRef]);
  const [pointerWithin, setPointerWithin] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [keyboardDismissed, setKeyboardDismissed] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);

  const expanded = isMobile
    ? mobileExpanded
    : pointerWithin || (focusWithin && !keyboardDismissed);

  useEffect(() => {
    if (expanded) setHasOpened(true);
  }, [expanded]);

  useEffect(() => () => {
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
    }
  }, []);

  useLayoutEffect(() => () => {
    const dock = rootRef.current;
    if (dock?.contains(document.activeElement)) onRestoreFocus();
  }, [dockRef, onRestoreFocus]);

  const cancelPendingCollapse = () => {
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = null;
    }
  };

  const schedulePointerCollapse = () => {
    cancelPendingCollapse();
    collapseTimerRef.current = window.setTimeout(() => {
      collapseTimerRef.current = null;
      if (!rootRef.current?.contains(document.activeElement)) {
        setPointerWithin(false);
      }
    }, COLLAPSE_DELAY_MS);
  };

  const handleFocus = () => {
    setKeyboardDismissed(false);
    setFocusWithin(true);
    setHasOpened(true);
  };

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (event.relatedTarget instanceof Node && rootRef.current?.contains(event.relatedTarget)) {
      return;
    }
    window.setTimeout(() => {
      if (!rootRef.current?.contains(document.activeElement)) {
        setFocusWithin(false);
        setKeyboardDismissed(false);
      }
    }, 0);
  };

  const handlePointerEnter = (event: PointerEvent<HTMLDivElement>) => {
    if (isMobile || (event.pointerType && event.pointerType !== 'mouse' && event.pointerType !== 'pen')) {
      return;
    }
    cancelPendingCollapse();
    setPointerWithin(true);
    setHasOpened(true);
  };

  const handlePointerOut = (event: PointerEvent<HTMLDivElement>) => {
    if (isMobile) return;
    if (event.relatedTarget instanceof Node && rootRef.current?.contains(event.relatedTarget)) {
      return;
    }
    schedulePointerCollapse();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
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

  const handleDesktopToggle = () => {
    if (expanded) {
      setKeyboardDismissed(true);
      setPointerWithin(false);
      setFocusWithin(false);
    } else {
      setKeyboardDismissed(false);
      setFocusWithin(true);
      setHasOpened(true);
    }
  };

  return (
    <div
      ref={setDockElement}
      className={`message-navigation-dock${expanded ? ' is-open' : ''}${isMobile ? ' is-mobile' : ''}`}
      data-testid="message-navigation-dock"
      data-expanded={expanded}
      onPointerEnter={handlePointerEnter}
      onPointerOut={handlePointerOut}
      onPointerLeave={() => {
        if (!isMobile) schedulePointerCollapse();
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
          aria-label={expanded ? 'Close message navigation rail' : 'Open message navigation rail'}
          aria-expanded={expanded}
          aria-controls={MESSAGE_NAVIGATION_PANEL_ID}
          title={expanded ? 'Close message navigation rail' : 'Open message navigation rail'}
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
        {hasOpened && <MessageNavigationRail chatRef={chatRef} />}
      </div>
    </div>
  );
}

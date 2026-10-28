/**
 * Bridge between the shortcut dispatcher and the mounted chat message list.
 *
 * The virtualizer, follow-bottom flag, programmatic-scroll suppression and
 * scroll snapshots all live inside ChatMessages.  A shortcut must therefore
 * reuse the same explicit recovery the “Scroll to bottom” button runs instead
 * of assigning `scrollTop` from the registry or from a second document
 * listener.  The mounted Session is stored with the handler so a chord can
 * never scroll a list the reader already switched away from, and the
 * dispatcher can leave the key alone when no chat is visible.
 */

export type ChatScrollToBottomHandler = () => boolean;

interface ChatScrollRegistration {
  sessionId: string;
  scroll: ChatScrollToBottomHandler;
}

let registration: ChatScrollRegistration | null = null;

export function registerChatScrollToBottomHandler(
  sessionId: string,
  scroll: ChatScrollToBottomHandler,
): () => void {
  const entry: ChatScrollRegistration = { sessionId, scroll };
  registration = entry;
  return () => {
    if (registration === entry) registration = null;
  };
}

/** True only while the visible chat list belongs to that Session. */
export function hasChatScrollToBottomHandler(sessionId: string): boolean {
  return registration !== null && registration.sessionId === sessionId;
}

/** Returns true when the mounted list accepted the request for that Session. */
export function requestChatScrollToBottom(sessionId: string): boolean {
  if (!registration || registration.sessionId !== sessionId) return false;
  return registration.scroll();
}

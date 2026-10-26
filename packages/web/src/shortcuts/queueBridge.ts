/**
 * Bridge between the shortcut dispatcher's queue-selection actions and the
 * mounted send-queue panel.
 *
 * Editing a queued message is not a plain store write: the panel owns the
 * existing `requestEdit` transaction (agent/system confirmation, edit lease,
 * composer hand-off).  The registry therefore asks the mounted panel to start
 * it instead of duplicating that flow, exactly like `composerBridge` for the
 * locked-enqueue action.
 */

export type QueueSelectedEditHandler = (sessionId: string, itemId: string) => boolean;

let handler: QueueSelectedEditHandler | null = null;

export function registerQueueSelectedEditHandler(next: QueueSelectedEditHandler): () => void {
  handler = next;
  return () => {
    if (handler === next) handler = null;
  };
}

/** Returns true when a mounted panel accepted the request for that Session. */
export function requestQueueSelectedEdit(sessionId: string, itemId: string): boolean {
  return handler ? handler(sessionId, itemId) : false;
}

export function hasQueueSelectedEditHandler(): boolean {
  return handler !== null;
}

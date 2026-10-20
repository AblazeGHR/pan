/**
 * Bridge between the global shortcut dispatcher and the mounted chat composer.
 *
 * The composer owns the draft, attachment occurrences, URL nodes, idempotency
 * key and failure recovery.  A shortcut must therefore reuse its normal send
 * transaction instead of rebuilding a message elsewhere.  Exactly one composer
 * is mounted at a time; a shortcut for another Session gets `false` and never
 * retargets the successor Session.
 */

export type LockedEnqueueHandler = (sessionId: string) => boolean;

let handler: LockedEnqueueHandler | null = null;

export function registerLockedEnqueueHandler(next: LockedEnqueueHandler): () => void {
  handler = next;
  return () => {
    if (handler === next) handler = null;
  };
}

/** Returns true when a mounted composer accepted the request for that Session. */
export function requestLockedComposerEnqueue(sessionId: string): boolean {
  return handler ? handler(sessionId) : false;
}

export function hasLockedEnqueueHandler(): boolean {
  return handler !== null;
}

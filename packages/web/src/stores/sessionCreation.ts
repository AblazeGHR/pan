/** Client-only identities are never valid HTTP/WS targets, including late cleanup.
 * Register before publishing selection; keep provenance after settlement so an
 * outgoing component cannot persist to a retired placeholder. */
const localIds = new Set<string>();
const pending = new Set<string>();
const removedDuringCreation = new Set<string>();
export function beginSessionCreation(name: string): string {
  const base = `__pending_${name}`;
  let id = base;
  let suffix = 1;
  while (localIds.has(id)) id = `${base}_${suffix++}`;
  localIds.add(id);
  pending.add(id);
  return id;
}
export const isLocalSessionIdentity = (id: string) => localIds.has(id);
export const isSessionCreating = (id: string) => pending.has(id);
export function markSessionRemovedDuringCreation(id: string) {
  if (pending.size) removedDuringCreation.add(id);
}
export const wasSessionRemovedDuringCreation = (id: string) => removedDuringCreation.has(id);
export function settleSessionCreation(id: string) {
  pending.delete(id);
  if (!pending.size) removedDuringCreation.clear();
}

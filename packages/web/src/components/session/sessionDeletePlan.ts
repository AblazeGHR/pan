import type { Session } from '@/types';

/** A non-empty managed list is intentional: it makes a session special even
 * when an entry is stale or missing from the current list. */
export function hasManagedChildren(session: Session): boolean {
  return (session.managed?.length ?? 0) > 0;
}

export function collectDescendantIds(sessions: Session[], roots: string[]): string[] {
  const byId = new Map(sessions.map((s) => [s.id, s]));
  const visited = new Set<string>();
  const result: string[] = [];
  const visit = (id: string) => {
    if (visited.has(id)) return;
    const session = byId.get(id);
    if (!session) return;
    visited.add(id);
    for (const childId of session.managed ?? []) {
      visit(childId);
      if (byId.has(childId) && !roots.includes(childId) && !result.includes(childId)) {
        result.push(childId);
      }
    }
  };
  roots.forEach(visit);
  return result;
}

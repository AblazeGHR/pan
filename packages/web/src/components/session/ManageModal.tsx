import { useEffect, useMemo, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import {
  claimSession,
  unclaimSession,
  reportSubscribe,
  reportUnsubscribe,
  fetchSession,
} from '@/services/api';
import type { Session } from '@/types';
import { Search, Star, Check, Bell } from 'lucide-react';

const SHOW_LIMIT = 20;

interface ManageModalProps {
  open: boolean;
  onClose: () => void;
  /** Id of the managing session; its `managed` ids drive the checked state. */
  sessionId: string | null;
}

/**
 * Manage (claim / unclaim) pan_session relationships. The managing session
 * checks sessions to claim; unchecking unclaims. Both call the backend and
 * then reload the session list so `managed` / `managedBy` stay in sync.
 */
export function ManageModal({ open, onClose, sessionId }: ManageModalProps) {
  const sessions = useSessionStore((s) => s.sessions);
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const showToast = useUIStore((s) => s.showToast);

  const session = useSessionStore((s) =>
    sessionId ? s.sessions.find((x) => x.id === sessionId) ?? null : null,
  );

  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Full session fetched on open — the sidebar list is summary=1 driven and
  // does NOT carry `managed` / `reportSubscriptions`, so we pull them on demand
  // (and only for the session whose modal is open).
  const [detailSession, setDetailSession] = useState<Session | null>(null);

  // Reset on open + fetch the full session (managed / reportSubscriptions).
  useEffect(() => {
    if (open && sessionId) {
      setQuery('');
      setShowAll(false);
      setBusyId(null);
      setDetailSession(null);
      fetchSession(sessionId)
        .then((full) => setDetailSession(full))
        .catch(() => setDetailSession(null));
    }
  }, [open, sessionId]);

  const managerId = detailSession?.id ?? session?.id ?? null;

  // Live set of sessions this manager already claims.
  const managedIds = useMemo(
    () => new Set<string>(detailSession?.managed ?? []),
    [detailSession],
  );

  // Live set of sessions this manager subscribes to completion reports.
  // (Claim auto-subscribes on the backend, so these usually overlap with
  // `managed` — the subscribe checkbox independently controls them.)
  const subscribedIds = useMemo(
    () => new Set<string>(detailSession?.reportSubscriptions ?? []),
    [detailSession],
  );

  // Candidate sessions: everything except the manager itself, pending
  // placeholders, and sessions already managed by a *different* manager
  // (the backend refuses to claim those). Already-managed ones float first.
  const candidates = useMemo(() => {
    if (!managerId) return [];
    return sessions
      .filter((s) => {
        if (s.id === managerId) return false;
        if (s.id.startsWith('__pending_')) return false;
        if (s.managedBy && s.managedBy !== managerId) return false;
        return true;
      })
      .sort((a, b) => {
        const aManaged = managedIds.has(a.id) ? 0 : 1;
        const bManaged = managedIds.has(b.id) ? 0 : 1;
        if (aManaged !== bManaged) return aManaged - bManaged;
        return (a.name || '').localeCompare(b.name || '');
      });
  }, [sessions, managerId, managedIds]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return candidates;
    return candidates.filter(
      (s) =>
        s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q),
    );
  }, [candidates, query]);

  const visible = showAll ? filtered : filtered.slice(0, SHOW_LIMIT);

  const toggle = async (targetId: string, checked: boolean) => {
    if (!managerId || busyId) return;
    setBusyId(targetId);
    const target = sessions.find((s) => s.id === targetId);
    const label = target?.name || targetId;
    try {
      if (checked) {
        await claimSession(managerId, targetId);
        showToast(`Now managing "${label}"`);
      } else {
        await unclaimSession(managerId, targetId);
        showToast(`Stopped managing "${label}"`);
      }
      // Optimistic local update: managedIds/subscribedIds derive from the
      // detailSession snapshot fetched on open, and loadSessions is summary=1
      // (no managed/reportSubscriptions) — without this the buttons stay stale
      // even though the backend already applied the change. Claim auto-subscribes
      // and unclaim auto-unsubscribes (server-side), so both sets move together.
      setDetailSession((d) => {
        if (!d) return d;
        const managed = new Set(d.managed ?? []);
        const subs = new Set(d.reportSubscriptions ?? []);
        if (checked) {
          managed.add(targetId);
          subs.add(targetId);
        } else {
          managed.delete(targetId);
          subs.delete(targetId);
        }
        return {
          ...d,
          managed: [...managed],
          reportSubscriptions: [...subs],
        };
      });
      await loadSessions();
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Manage failed',
        'error',
      );
    } finally {
      setBusyId(null);
    }
  };

  const toggleSubscribe = async (targetId: string, checked: boolean) => {
    if (!managerId || busyId) return;
    setBusyId(targetId);
    const target = sessions.find((s) => s.id === targetId);
    const label = target?.name || targetId;
    try {
      if (checked) {
        await reportSubscribe(managerId, targetId);
        showToast(`Subscribed to "${label}" reports`);
      } else {
        await reportUnsubscribe(managerId, targetId);
        showToast(`Unsubscribed from "${label}" reports`);
      }
      // Optimistic local update so the button + header count reflect immediately.
      setDetailSession((d) => {
        if (!d) return d;
        const subs = new Set(d.reportSubscriptions ?? []);
        if (checked) subs.add(targetId);
        else subs.delete(targetId);
        return { ...d, reportSubscriptions: [...subs] };
      });
      await loadSessions();
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Subscribe failed',
        'error',
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Manage Sessions" size="lg">
      <div className="flex flex-col gap-3">
        {!managerId && (
          <div className="py-6 text-center text-sm text-text-tertiary">
            Session not found
          </div>
        )}

        {managerId && (
          <>
            <div className="text-xs text-text-secondary">
              {detailSession?.name || session?.name || 'Untitled'} manages the
              sessions checked below.
            </div>

            {/* Search */}
            <div className="relative">
              <Search
                size={12}
                className="absolute left-2 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none"
              />
              <input
                type="text"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setShowAll(false);
                }}
                placeholder="Search sessions by name or ID..."
                className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 pl-6 pr-2 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50"
              />
            </div>

            <div className="flex items-center justify-between text-[11px] text-text-tertiary">
              <span>
                {managedIds.size} managed &middot; {subscribedIds.size}{' '}
                subscribed &middot; {filtered.length} available
              </span>
            </div>

            {/* Candidate list */}
            <div className="max-h-72 overflow-y-auto space-y-0.5 rounded border border-border-muted bg-bg-primary p-1">
              {visible.length === 0 && (
                <div className="py-4 text-center text-sm text-text-tertiary">
                  No matching sessions
                </div>
              )}
              {visible.map((c) => {
                const isManaged = managedIds.has(c.id);
                const isSubscribed = subscribedIds.has(c.id);
                return (
                  <div
                    key={c.id}
                    className={`flex items-center gap-2 px-2.5 py-1.5 rounded transition-colors hover:bg-bg-tertiary ${
                      busyId !== null ? 'pointer-events-none opacity-70' : ''
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-text-primary truncate">
                        {c.name || 'Untitled'}
                      </div>
                      <div className="text-[11px] text-text-tertiary truncate">
                        {c.id}
                      </div>
                    </div>
                    {c.adapter && (
                      <span className="text-[10px] text-text-tertiary bg-bg-tertiary border border-border-default rounded px-1 py-px shrink-0">
                        {c.adapter}
                      </span>
                    )}
                    {/* Manage button: gray "Manage" → blue "Managed" when active */}
                    <button
                      type="button"
                      onClick={() => toggle(c.id, !isManaged)}
                      disabled={busyId !== null}
                      title={
                        isManaged
                          ? 'Click to stop managing'
                          : 'Click to manage (also subscribes to reports)'
                      }
                      className={`shrink-0 inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] font-medium transition-colors ${
                        isManaged
                          ? 'border-accent/50 bg-accent/10 text-accent'
                          : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                      }`}
                    >
                      {isManaged ? <Check size={12} /> : <Star size={12} />}
                      {isManaged ? 'Managed' : 'Manage'}
                    </button>
                    {/* Subscribe button: gray "Subscribe" → blue "Subscribed" */}
                    <button
                      type="button"
                      onClick={() => toggleSubscribe(c.id, !isSubscribed)}
                      disabled={busyId !== null}
                      title={
                        isSubscribed
                          ? 'Click to unsubscribe from reports'
                          : 'Click to subscribe to completion reports'
                      }
                      className={`shrink-0 inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] font-medium transition-colors ${
                        isSubscribed
                          ? 'border-accent/50 bg-accent/10 text-accent'
                          : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                      }`}
                    >
                      {isSubscribed ? (
                        <Check size={12} />
                      ) : (
                        <Bell size={12} />
                      )}
                      {isSubscribed ? 'Subscribed' : 'Subscribe'}
                    </button>
                  </div>
                );
              })}
            </div>

            {/* Show-all toggle */}
            {!showAll && filtered.length > SHOW_LIMIT && (
              <button
                onClick={() => setShowAll(true)}
                className="w-full text-xs text-accent hover:underline py-1 text-center transition-colors"
              >
                Show all ({filtered.length})
              </button>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

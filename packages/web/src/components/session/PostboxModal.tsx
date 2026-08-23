import { useEffect, useMemo, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { fetchQqContacts, qqSubscribe, qqUnsubscribe } from '@/services/api';
import type { QqContact } from '@/types';
import { Search } from 'lucide-react';

const SHOW_LIMIT = 20;

interface PostboxModalProps {
  open: boolean;
  onClose: () => void;
  /** Id of the session whose QQ subscriptions are edited. */
  sessionId: string | null;
}

/** Subscription key as stored on the session, e.g. "user:1470993983". */
function contactKey(c: QqContact): string {
  return `${c.chatType === 2 ? 'group' : 'user'}:${c.peerUin}`;
}

/**
 * QQ postbox: subscribe/unsubscribe the session to QQ conversation inbox
 * reminders. Contact list comes from GET /api/qq/contacts; subscriptions live
 * in the session's `qqSubscriptions` (strings like "user:<uin>").
 */
export function PostboxModal({ open, onClose, sessionId }: PostboxModalProps) {
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const showToast = useUIStore((s) => s.showToast);

  const session = useSessionStore((s) =>
    sessionId ? s.sessions.find((x) => x.id === sessionId) ?? null : null,
  );

  const [contacts, setContacts] = useState<QqContact[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  // Fetch QQ contacts + reset on open
  useEffect(() => {
    if (!open) return;
    setQuery('');
    setShowAll(false);
    setBusyKey(null);
    setLoadError(null);
    setLoading(true);
    fetchQqContacts()
      .then((list) => setContacts(list))
      .catch((e) =>
        setLoadError(
          e instanceof Error ? e.message : 'Failed to load QQ contacts',
        ),
      )
      .finally(() => setLoading(false));
  }, [open]);

  const sessionIdValue = session?.id ?? null;

  // Live set of subscription keys for this session.
  const subscribedKeys = useMemo(
    () => new Set<string>(session?.qqSubscriptions ?? []),
    [session],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return contacts;
    return contacts.filter(
      (c) =>
        c.peerName.toLowerCase().includes(q) || c.peerUin.includes(q),
    );
  }, [contacts, query]);

  const visible = showAll ? filtered : filtered.slice(0, SHOW_LIMIT);

  const toggle = async (c: QqContact, checked: boolean) => {
    if (!sessionIdValue || busyKey) return;
    const targetType: 'user' | 'group' = c.chatType === 2 ? 'group' : 'user';
    const key = contactKey(c);
    setBusyKey(key);
    try {
      if (checked) {
        await qqSubscribe(sessionIdValue, targetType, c.peerUin);
        showToast(`Subscribed to "${c.peerName}"`);
      } else {
        await qqUnsubscribe(sessionIdValue, targetType, c.peerUin);
        showToast(`Unsubscribed from "${c.peerName}"`);
      }
      await loadSessions();
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Postbox update failed',
        'error',
      );
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="QQ Postbox" size="lg">
      <div className="flex flex-col gap-3">
        {!sessionIdValue && (
          <div className="py-6 text-center text-sm text-text-tertiary">
            Session not found
          </div>
        )}

        {sessionIdValue && (
          <>
            <div className="text-xs text-text-secondary">
              Subscribe this session to QQ conversation inbox updates.
            </div>

            {/* Load error */}
            {loadError && (
              <div className="rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
                Failed to load QQ contacts: {loadError}
              </div>
            )}

            {/* Loading */}
            {loading && (
              <div className="py-6 text-center text-sm text-text-tertiary">
                Loading contacts...
              </div>
            )}

            {/* Empty (no error) */}
            {!loading && !loadError && contacts.length === 0 && (
              <div className="py-6 text-center text-sm text-text-tertiary">
                No QQ contacts available
              </div>
            )}

            {!loading && !loadError && contacts.length > 0 && (
              <>
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
                    placeholder="Search by name or QQ number..."
                    className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 pl-6 pr-2 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50"
                  />
                </div>

                <div className="flex items-center justify-between text-[11px] text-text-tertiary">
                  <span>
                    {subscribedKeys.size} subscribed &middot; {filtered.length}{' '}
                    contacts
                  </span>
                </div>

                {/* Contact list */}
                <div className="max-h-72 overflow-y-auto space-y-0.5 rounded border border-border-muted bg-bg-primary p-1">
                  {visible.length === 0 && (
                    <div className="py-4 text-center text-sm text-text-tertiary">
                      No matching contacts
                    </div>
                  )}
                  {visible.map((c) => {
                    const key = contactKey(c);
                    return (
                      <label
                        key={key}
                        className={`flex items-center gap-2 px-2.5 py-1.5 rounded cursor-pointer transition-colors hover:bg-bg-tertiary ${
                          busyKey !== null
                            ? 'pointer-events-none opacity-70'
                            : ''
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={subscribedKeys.has(key)}
                          disabled={busyKey !== null}
                          onChange={(e) => toggle(c, e.target.checked)}
                          className="accent-accent shrink-0"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-text-primary truncate">
                            {c.peerName || 'Unknown'}
                          </div>
                          <div className="text-[11px] text-text-tertiary truncate">
                            {c.peerUin}
                          </div>
                        </div>
                        <span className="text-[10px] text-text-tertiary bg-bg-tertiary border border-border-default rounded px-1 py-px shrink-0">
                          {c.chatType === 2 ? 'group' : 'user'}
                        </span>
                      </label>
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
          </>
        )}
      </div>
    </Modal>
  );
}

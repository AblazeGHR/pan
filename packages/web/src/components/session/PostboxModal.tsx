import { useEffect, useMemo, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import {
  fetchQqChannels,
  fetchQqContacts,
  qqSubscribe,
  qqUnsubscribe,
  fetchSession,
} from '@/services/api';
import type { QqChannelInfo, QqContact, Session } from '@/types';
import { Search, Bell, Check } from 'lucide-react';

const SHOW_LIMIT = 20;

interface PostboxModalProps {
  open: boolean;
  onClose: () => void;
  /** Id of the session whose QQ subscriptions are edited. */
  sessionId: string | null;
}

/** A contact row tagged with the bot account it came from (merge mode). */
interface BotContact {
  contact: QqContact;
  /** Bot QQ number this contact belongs to; '' = default channel (no bot). */
  botUin: string;
  /** Channel name, fallback label when botUin is empty. */
  botName: string;
}

/** Subscription base key as stored on the session, e.g. "user:1234567890". */
function contactKey(c: QqContact): string {
  return `${c.chatType === 2 ? 'group' : 'user'}:${c.peerUin}`;
}

/** Session subscription key: "user:123@3494144273"; bot '' → legacy key. */
function botKey(base: string, botUin: string): string {
  return botUin ? `${base}@${botUin}` : base;
}

/**
 * QQ postbox: subscribe/unsubscribe the session to QQ conversation inbox
 * reminders. Multi-account merge mode: contacts from EVERY connected bot are
 * merged into one list, each row tagged with its bot (bot_uin badge) so the
 * same person under different bots shows as separate rows and can be
 * subscribed independently. Subscriptions live in the session's
 * `qqSubscriptions` as either bot-scoped keys "user:<uin>@<bot_uin>" or
 * legacy bot-agnostic keys "user:<uin>" (matched for every bot).
 */
export function PostboxModal({ open, onClose, sessionId }: PostboxModalProps) {
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const showToast = useUIStore((s) => s.showToast);

  const session = useSessionStore((s) =>
    sessionId ? s.sessions.find((x) => x.id === sessionId) ?? null : null,
  );

  const [bots, setBots] = useState<QqChannelInfo[]>([]);
  const [items, setItems] = useState<BotContact[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // Full session fetched on open — the list is summary=1 driven and does not
  // carry `qqSubscriptions`.
  const [detailSession, setDetailSession] = useState<Session | null>(null);

  // Fetch bot channels + contacts (merged per-bot) + session detail on open
  useEffect(() => {
    if (!open) return;
    setQuery('');
    setShowAll(false);
    setBusyKey(null);
    setLoadError(null);
    setDetailSession(null);
    setItems([]);
    let cancelled = false;
    setLoading(true);

    const load = async () => {
      try {
        let botList: QqChannelInfo[] = [];
        try {
          botList = await fetchQqChannels();
        } catch {
          botList = [];
        }
        if (cancelled) return;
        setBots(botList);
        // Merge mode: pull contacts from EVERY bot, merged into one list. A
        // bot with no bot_uin (single-channel compat) hits the default
        // channel. A failing bot is skipped so one offline account doesn't
        // blank the whole list.
        const targets: QqChannelInfo[] =
          botList.length > 0
            ? botList
            : [{ name: '', bot_uin: '', connected: false }];
        // 分批加载：每个 bot 拉完立即追加显示，不阻塞等待全部完成（首次
        // 冷启动时先出第一批，避免一直卡 loading）。
        const accumulated: BotContact[] = [];
        await Promise.all(
          targets.map(async (b) => {
            try {
              const list = await fetchQqContacts(b.bot_uin || undefined);
              const rows = list.map((contact): BotContact => ({
                contact,
                botUin: b.bot_uin,
                botName: b.name,
              }));
              accumulated.push(...rows);
            } catch {
              // 单个 bot 失败跳过，不因离线账号清空整表
            }
            if (cancelled) return;
            setItems(accumulated.slice());
          }),
        );
        if (cancelled) return;
      } catch (e) {
        if (cancelled) return;
        setLoadError(
          e instanceof Error ? e.message : 'Failed to load QQ contacts',
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();

    if (sessionId) {
      fetchSession(sessionId)
        .then((full) => setDetailSession(full))
        .catch(() => setDetailSession(null));
    }
    return () => {
      cancelled = true;
    };
  }, [open, sessionId]);

  const sessionIdValue = session?.id ?? sessionId ?? null;

  const subscriptions = detailSession?.qqSubscriptions ?? [];

  // A row is subscribed if the bot-scoped exact key exists, or the legacy
  // bot-agnostic key exists (delivers reminders from ANY bot).
  const isSubscribed = (item: BotContact): boolean => {
    const base = contactKey(item.contact);
    return (
      subscriptions.includes(botKey(base, item.botUin)) ||
      subscriptions.includes(base)
    );
  };

  // 仅保留可订阅的条目：peerUin 非空非 "0"、chatType 为私聊(1)/群(2)。
  // 后端已清洗（合并 recent + friend/group 列表），此处双保险避免 Unknown/q号0
  // 条目进入搜索与展示。
  const validItems = useMemo(
    () =>
      items.filter(
        (it) =>
          it.contact.peerUin &&
          it.contact.peerUin !== '0' &&
          (it.contact.chatType === 1 || it.contact.chatType === 2),
      ),
    [items],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return validItems;
    return validItems.filter(
      (it) =>
        (it.contact.peerName || '').toLowerCase().includes(q) ||
        it.contact.peerUin.includes(q),
    );
  }, [validItems, query]);

  // 已订阅的置顶（保持其余相对顺序），然后截断到 SHOW_LIMIT。
  const visible = useMemo(() => {
    const sorted = filtered
      .slice()
      .sort((a, b) => Number(isSubscribed(b)) - Number(isSubscribed(a)));
    return showAll ? sorted : sorted.slice(0, SHOW_LIMIT);
  }, [filtered, showAll, subscriptions]);
  const subscribedCount = validItems.filter(isSubscribed).length;

  const toggle = async (item: BotContact, checked: boolean) => {
    if (!sessionIdValue || busyKey) return;
    const c = item.contact;
    const targetType: 'user' | 'group' = c.chatType === 2 ? 'group' : 'user';
    const base = contactKey(c);
    const exactKey = botKey(base, item.botUin);
    setBusyKey(exactKey);
    try {
      if (checked) {
        await qqSubscribe(sessionIdValue, targetType, c.peerUin, item.botUin || undefined);
        showToast(`Subscribed to "${c.peerName}"`);
      } else {
        // Remove the bot-scoped exact key first; if only the legacy
        // bot-agnostic key exists, remove that instead.
        const removeBot = subscriptions.includes(exactKey)
          ? item.botUin || undefined
          : undefined;
        await qqUnsubscribe(sessionIdValue, targetType, c.peerUin, removeBot);
        showToast(`Unsubscribed from "${c.peerName}"`);
      }
      // Optimistic local update: subscriptions derive from detailSession
      // (fetched once on open) — without this the checkbox/button would stay
      // stale until the modal is reopened.
      setDetailSession((d) => {
        if (!d) return d;
        const subs = new Set(d.qqSubscriptions ?? []);
        if (checked) subs.add(exactKey);
        else if (subscriptions.includes(exactKey)) subs.delete(exactKey);
        else subs.delete(base);
        return { ...d, qqSubscriptions: [...subs] };
      });
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
              {bots.length > 1 &&
                ` Merging ${bots.length} bot accounts — each contact is tagged with its bot and subscribable independently.`}
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
            {!loading && !loadError && validItems.length === 0 && (
              <div className="py-6 text-center text-sm text-text-tertiary">
                No QQ contacts available
              </div>
            )}

            {!loading && !loadError && validItems.length > 0 && (
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
                    {subscribedCount} subscribed &middot; {filtered.length}{' '}
                    contacts
                    {bots.length > 1 && ` &middot; ${bots.length} accounts`}
                  </span>
                </div>

                {/* Contact list */}
                <div className="max-h-72 overflow-y-auto space-y-0.5 rounded border border-border-muted bg-bg-primary p-1">
                  {visible.length === 0 && (
                    <div className="py-4 text-center text-sm text-text-tertiary">
                      No matching contacts
                    </div>
                  )}
                  {visible.map((it) => {
                    const c = it.contact;
                    const base = contactKey(c);
                    const exactKey = botKey(base, it.botUin);
                    const subscribed = isSubscribed(it);
                    const legacyOnly =
                      subscribed && !subscriptions.includes(exactKey);
                    return (
                      <div
                        key={exactKey}
                        className={`flex items-center gap-2 px-2.5 py-1.5 rounded transition-colors hover:bg-bg-tertiary ${
                          busyKey !== null
                            ? 'pointer-events-none opacity-70'
                            : ''
                        }`}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-text-primary truncate">
                            {c.peerName || 'Unknown'}
                          </div>
                          <div className="text-[11px] text-text-tertiary truncate flex items-center gap-1">
                            <span>{c.peerUin}</span>
                            {/* Source bot badge */}
                            {it.botUin ? (
                              <span className="shrink-0 rounded bg-bg-tertiary border border-border-default px-1 py-px text-[10px] leading-none">
                                {it.botUin}
                              </span>
                            ) : it.botName ? (
                              <span className="shrink-0 rounded bg-bg-tertiary border border-border-default px-1 py-px text-[10px] leading-none">
                                {it.botName}
                              </span>
                            ) : null}
                            {/* Legacy bot-agnostic subscription scope */}
                            {legacyOnly && (
                              <span className="shrink-0 rounded bg-accent/10 border border-accent/30 px-1 py-px text-[10px] leading-none text-accent">
                                any bot
                              </span>
                            )}
                          </div>
                        </div>
                        <span className="text-[10px] text-text-tertiary bg-bg-tertiary border border-border-default rounded px-1 py-px shrink-0">
                          {c.chatType === 2 ? 'group' : 'user'}
                        </span>
                        {/* Subscribe button: gray "Subscribe" → blue "Subscribed" */}
                        <button
                          type="button"
                          onClick={() => toggle(it, !subscribed)}
                          disabled={busyKey !== null}
                          title={
                            subscribed
                              ? 'Click to unsubscribe from inbox updates'
                              : 'Click to subscribe to inbox updates'
                          }
                          className={`shrink-0 inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] font-medium transition-colors ${
                            subscribed
                              ? 'border-accent/50 bg-accent/10 text-accent'
                              : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                          }`}
                        >
                          {subscribed ? (
                            <Check size={12} />
                          ) : (
                            <Bell size={12} />
                          )}
                          {subscribed ? 'Subscribed' : 'Subscribe'}
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
          </>
        )}
      </div>
    </Modal>
  );
}

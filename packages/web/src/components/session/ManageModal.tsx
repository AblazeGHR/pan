import { useEffect, useMemo, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import {
  claimSession,
  unclaimSession,
  reportSubscribe,
  reportUnsubscribe,
  setSessionReadonly,
  fetchSession,
  fetchMcpServers,
  patchSession,
} from '@/services/api';
import type { McpServerInfo, PanAccess, Session } from '@/types';
import { Search, Star, Check, Bell, Unlink, Lock, Unlock } from 'lucide-react';

const SHOW_LIMIT = 20;

const PAN_ACCESS_ROWS: {
  key: keyof PanAccess;
  label: string;
  hint: string;
  desc: string;
}[] = [
  {
    key: 'restrictToManaged',
    label: 'Restrict to managed',
    hint: 'restrict_to_managed',
    desc: 'Over MCP this agent may only operate on sessions it manages.',
  },
  {
    key: 'canClaimUnmanaged',
    label: 'Can claim unmanaged',
    hint: 'can_claim_unmanaged',
    desc: 'Over MCP this agent may claim sessions that have no manager yet.',
  },
  {
    key: 'autoClaimCreated',
    label: 'Auto-claim created',
    hint: 'auto_claim_created',
    desc: 'Over MCP sessions created by this agent are claimed automatically.',
  },
];

interface ManageModalProps {
  open: boolean;
  onClose: () => void;
  /** Id of the managing session; its `managed` ids drive the checked state. */
  sessionId: string | null;
}

interface ManageSessionsPanelProps {
  /** When true, per-open state is reset and the full session + MCP catalog are
   *  fetched. The modal passes its `open`; the full page always passes `true`. */
  open: boolean;
  /** Id of the managing session; its `managed` ids drive the checked state. */
  sessionId: string | null;
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="border-b border-border-muted pb-1">
      <div className="text-xs font-semibold text-text-primary">{title}</div>
      <div className="text-[11px] text-text-tertiary">{subtitle}</div>
    </div>
  );
}

function SwitchRow({
  label,
  hint,
  desc,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  desc: string;
  checked: boolean;
  disabled: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="w-full flex items-start justify-between gap-3 rounded px-2.5 py-2 text-left transition-colors hover:bg-bg-tertiary disabled:opacity-60 disabled:pointer-events-none"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">
          {label}
          <span className="ml-1.5 text-[10px] text-text-tertiary font-mono">{hint}</span>
        </span>
        <span className="block text-[11px] text-text-tertiary mt-0.5">{desc}</span>
      </span>
      <span
        className={`relative inline-flex w-8 h-[18px] shrink-0 rounded-full transition-colors ${
          checked ? 'bg-accent' : 'bg-bg-hover'
        }`}
      >
        <span
          className={`absolute top-[2px] left-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-[14px]' : 'translate-x-0'
          }`}
        />
      </span>
    </button>
  );
}

/**
 * Session relationship + capability panel, split into three sections:
 *   1. "Managed by"  — who manages this session (and how to break the link).
 *   2. "Manages"     — claim / unclaim + report subscriptions of other sessions.
 *   3. "Pan Access"  — MCP-only capability flags (persisted via PATCH).
 * All mutations hit the backend and then reload the session list so
 * `managed` / `managedBy` stay in sync.
 *
 * Shared by the desktop ManageModal (popup) and the mobile full-page
 * ManageView so both stay visually identical.
 */
export function ManageSessionsPanel({ open, sessionId }: ManageSessionsPanelProps) {
  const sessions = useSessionStore((s) => s.sessions);
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const showToast = useUIStore((s) => s.showToast);

  const session = useSessionStore((s) =>
    sessionId ? (s.sessions.find((x) => x.id === sessionId) ?? null) : null,
  );

  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [savingFlag, setSavingFlag] = useState<keyof PanAccess | null>(null);
  // Catalog of all manifest-declared MCP servers (for the multi-select list).
  const [mcpServers, setMcpServers] = useState<McpServerInfo[]>([]);
  // True when the manifest catalog could not be loaded (empty + loaded:false).
  const [mcpCatalogLoaded, setMcpCatalogLoaded] = useState(false);
  // Busy flag scoped to the MCP section's save calls.
  const [savingMcp, setSavingMcp] = useState(false);
  // Force-release of a "never" template lock after user confirmation. Local
  // to this modal-open only — reopening the modal re-arms the template lock.
  const [mcpForced, setMcpForced] = useState(false);
  // Full session fetched on open — the sidebar list is summary=1 driven and
  // does NOT carry `managed` / `reportSubscriptions` / `panAccess`, so we pull
  // them on demand (and only for the session whose modal is open).
  const [detailSession, setDetailSession] = useState<Session | null>(null);

  // Reset on open + fetch the full session (managed / reportSubscriptions /
  // managedBy / panAccess).
  useEffect(() => {
    if (open && sessionId) {
      setQuery('');
      setShowAll(false);
      setBusyId(null);
      setCancelBusy(false);
      setSavingFlag(null);
      setDetailSession(null);
      setMcpServers([]);
      setMcpCatalogLoaded(false);
      setSavingMcp(false);
      setMcpForced(false);
      fetchSession(sessionId)
        .then((full) => setDetailSession(full))
        .catch(() => setDetailSession(null));
      // Pull the full MCP server catalog (independent of the session fetch).
      fetchMcpServers()
        .then((list) => {
          setMcpServers(list);
          setMcpCatalogLoaded(true);
        })
        .catch(() => {
          setMcpServers([]);
          setMcpCatalogLoaded(false);
        });
    }
  }, [open, sessionId]);

  const managerId = detailSession?.id ?? session?.id ?? null;

  // The detail snapshot is the live source once fetched (we patch it locally
  // after each mutation); before that fall back to the summary list entry.
  const managedBy = detailSession
    ? (detailSession.managedBy ?? null)
    : (session?.managedBy ?? null);
  const managedByLabel = useMemo(() => {
    if (!managedBy) return null;
    const m = sessions.find((s) => s.id === managedBy);
    return m ? m.name || 'Untitled' : null;
  }, [managedBy, sessions]);

  const panAccess: PanAccess = detailSession?.panAccess ?? {};

  // Live set of sessions this manager already claims.
  const managedIds = useMemo(() => new Set<string>(detailSession?.managed ?? []), [detailSession]);

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
      (s) => s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q),
    );
  }, [candidates, query]);

  const visible = showAll ? filtered : filtered.slice(0, SHOW_LIMIT);

  // Break the incoming manage link. The backend only checks that the passed
  // managerId matches this session's current manager — it does not require the
  // manager itself to be the caller, so the managed session can detach.
  const cancelManagedBy = async () => {
    if (!managerId || !managedBy || cancelBusy) return;
    setCancelBusy(true);
    try {
      await unclaimSession(managedBy, managerId);
      setDetailSession((d) => (d ? { ...d, managedBy: null } : d));
      showToast(`No longer managed by "${managedByLabel || managedBy}"`);
      await loadSessions();
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Unclaim failed', 'error');
    } finally {
      setCancelBusy(false);
    }
  };

  const togglePanAccess = async (key: keyof PanAccess, next: boolean) => {
    if (!managerId || savingFlag) return;
    setSavingFlag(key);
    // Send only the toggled flag — the backend patches it in place and leaves
    // the other two capability flags untouched.
    const patch: PanAccess = {};
    patch[key] = next;
    try {
      const updated = await patchSession(managerId, { panAccess: patch });
      setDetailSession((d) => {
        if (!d) return d;
        const merged: PanAccess = { ...(d.panAccess ?? {}), ...patch };
        return { ...d, panAccess: updated.panAccess ?? merged };
      });
      const label = PAN_ACCESS_ROWS.find((r) => r.key === key)?.label ?? key;
      showToast(`${label} ${next ? 'enabled' : 'disabled'}`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Update failed', 'error');
    } finally {
      setSavingFlag(null);
    }
  };

  // Live set of MCP server names currently enabled for this session.
  const enabledMcp = useMemo(
    () => new Set<string>(detailSession?.mcpServers ?? []),
    [detailSession],
  );

  // Template lock state: mcpLockReason tells always ("locked ON") from never
  // ("locked OFF"); null/undefined = no lock info. A "never" lock can be
  // force-released after confirmation (mcpForced); while a template lock
  // exists every MCP patch must carry forceMcp so the backend skips its
  // always/never check.
  const mcpLockReason = detailSession?.mcpLockReason ?? null;
  const mcpLocked = detailSession?.mcpLocked === true;
  const mcpForceUnlocked = mcpLocked && mcpLockReason === 'never' && mcpForced;
  // An `always` template locks MCP on, but the server membership remains
  // editable. Keep the catalog visible and prevent removing the final server.
  // `never` remains locked until the explicit force-enable flow is confirmed.
  const mcpSelectionLocked =
    mcpLocked && mcpLockReason !== 'always' && !mcpForceUnlocked;
  const mcpEditable = !mcpSelectionLocked;

  // Toggle one MCP server in/out of the session's enabled set and persist the
  // full name list. Empty list clears them (backend supports [] / null).
  const toggleMcpServer = async (name: string, checked: boolean) => {
    if (!managerId || savingMcp || !mcpEditable) return;
    // Keep the backend's always-on invariant intact even during a render
    // transition or if an event is triggered programmatically.
    if (mcpLockReason === 'always' && !checked && enabledMcp.size <= 1) return;
    const next = new Set(enabledMcp);
    if (checked) next.add(name);
    else next.delete(name);
    const names = [...next];
    setSavingMcp(true);
    try {
      const updated = await patchSession(
        managerId,
        mcpLocked ? { mcpServers: names, forceMcp: true } : { mcpServers: names },
      );
      setDetailSession((d) => {
        if (!d) return d;
        return { ...d, mcpServers: updated.mcpServers ?? names };
      });
      showToast(checked ? `Enabled MCP server "${name}"` : `Disabled MCP server "${name}"`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Update failed', 'error');
    } finally {
      setSavingMcp(false);
    }
  };

  // Force-enable entry for a "never" template lock: confirm, then reveal the
  // server list with patches carrying forceMcp (the lock itself stays armed).
  const forceEnableMcp = () => {
    if (!confirm('This template locks MCP off. Force-enable anyway?')) return;
    setMcpForced(true);
  };

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
      showToast(e instanceof Error ? e.message : 'Manage failed', 'error');
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
      showToast(e instanceof Error ? e.message : 'Subscribe failed', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const toggleReadonly = async (targetId: string, enabled: boolean) => {
    if (!managerId || busyId || !managedIds.has(targetId)) return;
    setBusyId(targetId);
    const target = sessions.find((s) => s.id === targetId);
    const label = target?.name || targetId;
    try {
      await setSessionReadonly(managerId, targetId, enabled);
      useSessionStore.getState().updateSession(targetId, { readonlySession: enabled });
      showToast(`${enabled ? 'Readonly enabled' : 'Readonly disabled'} for "${label}"`);
      await loadSessions();
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Readonly update failed', 'error');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      {!managerId && (
        <div className="py-6 text-center text-sm text-text-tertiary">Session not found</div>
      )}

      {managerId && (
        <>
          {/* ── Section 1: Managed by ── */}
          <section className="flex flex-col gap-2">
            <SectionHeader
              title="Managed by / 被谁管理"
              subtitle="The manager (parent) session that claimed this session."
            />
            <div className="flex items-center gap-2 rounded border border-border-muted bg-bg-primary px-2.5 py-2">
              <div className="flex-1 min-w-0">
                {managedBy ? (
                  <>
                    <div className="text-sm text-text-primary truncate">
                      {managedByLabel || managedBy}
                    </div>
                    <div className="text-[11px] text-text-tertiary truncate">{managedBy}</div>
                  </>
                ) : (
                  <div className="text-sm text-text-tertiary">Unmanaged / 未托管</div>
                )}
              </div>
              {managedBy && (
                <button
                  type="button"
                  onClick={cancelManagedBy}
                  disabled={cancelBusy}
                  title="Break the manage link (this session becomes unmanaged)"
                  className="shrink-0 inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] font-medium text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-60 disabled:pointer-events-none"
                >
                  <Unlink size={12} />
                  取消被管理 / Cancel manage by
                </button>
              )}
            </div>
          </section>

          {/* ── Section 2: Manages ── */}
          <section className="flex flex-col gap-2">
            <SectionHeader
              title="Manages / 管理谁"
              subtitle={`${
                detailSession?.name || session?.name || 'Untitled'
              } manages the sessions marked below; Subscribe controls completion reports.`}
            />

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
                {managedIds.size} managed &middot; {subscribedIds.size} subscribed &middot;{' '}
                {filtered.length} available
              </span>
            </div>

            {/* Candidate list — vertical scroll for length, horizontal scroll on
                narrow screens so the action buttons are never crushed (the name
                column keeps a min-width and truncates instead). */}
            <div className="max-h-56 overflow-y-auto overflow-x-auto space-y-0.5 rounded border border-border-muted bg-bg-primary p-1">
              {visible.length === 0 && (
                <div className="py-4 text-center text-sm text-text-tertiary">
                  No matching sessions
                </div>
              )}
              {visible.map((c) => {
                const isManaged = managedIds.has(c.id);
                const isSubscribed = subscribedIds.has(c.id);
                const isReadonly = c.readonlySession === true;
                return (
                  <div
                    key={c.id}
                    className={`flex items-center gap-1.5 sm:gap-2 whitespace-nowrap px-2.5 py-1.5 rounded transition-colors hover:bg-bg-tertiary ${
                      busyId !== null ? 'pointer-events-none opacity-70' : ''
                    }`}
                  >
                    {/* min-w-32 keeps the name readable on narrow screens; flex
                        otherwise collapses it to ~0 next to the shrink-0 buttons.
                        truncate + title preserve the full name for long labels. */}
                    <div className="flex-1 min-w-32">
                      <div className="text-sm text-text-primary truncate" title={c.name || 'Untitled'}>
                        {c.name || 'Untitled'}
                      </div>
                      <div className="text-[11px] text-text-tertiary truncate">{c.id}</div>
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
                      className={`shrink-0 inline-flex items-center whitespace-nowrap gap-0.5 sm:gap-1 rounded border px-1.5 sm:px-2 py-1 text-[10px] sm:text-[11px] font-medium transition-colors ${
                        isManaged
                          ? 'border-accent/50 bg-accent/10 text-accent'
                          : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                      }`}
                    >
                      {isManaged ? <Check size={12} className="h-2.5 w-2.5 sm:h-3 sm:w-3" /> : <Star size={12} className="h-2.5 w-2.5 sm:h-3 sm:w-3" />}
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
                      className={`shrink-0 inline-flex items-center whitespace-nowrap gap-0.5 sm:gap-1 rounded border px-1.5 sm:px-2 py-1 text-[10px] sm:text-[11px] font-medium transition-colors ${
                        isSubscribed
                          ? 'border-accent/50 bg-accent/10 text-accent'
                          : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                      }`}
                    >
                      {isSubscribed ? <Check size={12} className="h-2.5 w-2.5 sm:h-3 sm:w-3" /> : <Bell size={12} className="h-2.5 w-2.5 sm:h-3 sm:w-3" />}
                      {isSubscribed ? 'Subscribed' : 'Subscribe'}
                    </button>
                    {/* Readonly is available only for sessions this manager currently manages. */}
                    <button
                      type="button"
                      onClick={() => toggleReadonly(c.id, !isReadonly)}
                      disabled={busyId !== null || !isManaged}
                      aria-pressed={isReadonly}
                      title={!isManaged
                        ? 'Manage this session first'
                        : isReadonly
                          ? 'Click to allow messages, tasks, and notifications'
                          : 'Click to block manager messages, tasks, and notifications'}
                      className={`shrink-0 inline-flex items-center whitespace-nowrap gap-0.5 sm:gap-1 rounded border px-1.5 sm:px-2 py-1 text-[10px] sm:text-[11px] font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none ${
                        isReadonly
                          ? 'border-amber-500/50 bg-amber-500/10 text-amber-400'
                          : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                      }`}
                    >
                      {isReadonly ? <Lock size={12} className="h-2.5 w-2.5 sm:h-3 sm:w-3" /> : <Unlock size={12} className="h-2.5 w-2.5 sm:h-3 sm:w-3" />}
                      {isReadonly ? 'Readonly' : 'Readonly'}
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
          </section>

          {/* ── Section 3: Pan Access ── */}
          <section className="flex flex-col gap-2">
            <SectionHeader
              title="Pan Access / MCP 权限"
              subtitle="Capability flags for the MCP path only — manage actions from this UI are never restricted."
            />
            <div className="rounded border border-border-muted bg-bg-primary p-1 divide-y divide-border-muted">
              {PAN_ACCESS_ROWS.map((row) => (
                <SwitchRow
                  key={row.key}
                  label={row.label}
                  hint={row.hint}
                  desc={row.desc}
                  checked={Boolean(panAccess[row.key])}
                  disabled={savingFlag !== null || detailSession === null}
                  onChange={(v) => togglePanAccess(row.key, v)}
                />
              ))}
            </div>
          </section>

          {/* ── Section 4: MCP Server ── */}
          <section className="flex flex-col gap-2">
            <SectionHeader
              title="MCP Server / MCP 服务"
              subtitle="Select MCP servers from the manifest for this session; worker restarts with the change applied."
            />
            {mcpSelectionLocked ? (
              <div className="rounded border border-border-muted bg-bg-primary px-2.5 py-2 text-[11px] text-text-tertiary flex items-center justify-between gap-2">
                <span>
                  {mcpLockReason === 'never'
                    ? 'MCP is locked OFF by the session template — selection disabled.'
                    : 'MCP is locked by the session template — selection disabled.'}
                </span>
                {mcpLockReason === 'never' && (
                  <button
                    type="button"
                    onClick={forceEnableMcp}
                    disabled={savingMcp}
                    title="Bypass the template lock after confirmation"
                    className="shrink-0 inline-flex items-center rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] font-medium text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-60 disabled:pointer-events-none"
                  >
                    Force enable
                  </button>
                )}
              </div>
            ) : (
              <>
                {mcpLocked && mcpLockReason === 'always' && (
                  <div className="rounded border border-border-muted bg-bg-primary px-2.5 py-2 text-[11px] text-text-tertiary">
                    MCP is locked ON by the session template — at least one server must remain enabled.
                  </div>
                )}
                <div className="rounded border border-border-muted bg-bg-primary p-1 space-y-0.5">
                  {!mcpCatalogLoaded && mcpServers.length === 0 && (
                    <div className="py-3 text-center text-[11px] text-text-tertiary">
                      Loading MCP servers…
                    </div>
                  )}
                  {mcpCatalogLoaded && mcpServers.length === 0 && (
                    <div className="py-4 text-center text-sm text-text-tertiary">
                      No MCP servers available (manifest not loaded)
                    </div>
                  )}
                  {mcpServers.map((srv) => {
                    const checked = enabledMcp.has(srv.name);
                    return (
                      <label
                        key={srv.name}
                        className={`flex items-start gap-2 px-2.5 py-1.5 rounded transition-colors hover:bg-bg-tertiary ${
                          savingMcp ? 'pointer-events-none opacity-70' : ''
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5 shrink-0 accent-accent"
                          checked={checked}
                          disabled={
                            savingMcp ||
                            detailSession === null ||
                            (mcpLockReason === 'always' && checked && enabledMcp.size <= 1)
                          }
                          title={
                            mcpLockReason === 'always' && checked && enabledMcp.size <= 1
                              ? 'At least one MCP server must remain enabled'
                              : undefined
                          }
                          onChange={(e) => toggleMcpServer(srv.name, e.target.checked)}
                        />
                        <span className="min-w-0">
                          <span className="block text-sm text-text-primary">{srv.name}</span>
                          {srv.command && (
                            <span className="block text-[11px] text-text-tertiary font-mono truncate">
                              {srv.command}
                              {srv.cwd ? ` · cwd: ${srv.cwd}` : ''}
                            </span>
                          )}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </>
            )}
          </section>
        </>
      )}
    </div>
  );
}

export function ManageModal({ open, onClose, sessionId }: ManageModalProps) {
  return (
    <Modal open={open} onClose={onClose} title="Manage Sessions" size="xl">
      <ManageSessionsPanel open={open} sessionId={sessionId} />
    </Modal>
  );
}

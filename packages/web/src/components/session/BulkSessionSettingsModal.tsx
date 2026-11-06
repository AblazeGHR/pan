import { useCallback, useEffect, useMemo, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import {
  claimSession,
  fetchMcpServers,
  fetchSession,
  patchSession,
  setReportsToManager,
  setSessionReadonly,
  unclaimSession,
} from '@/services/api';
import type { McpServerInfo, PanAccess, PluginBinding, Session } from '@/types';
import { effectiveWorkspaceIds } from '@/utils/sessionFilters';
import { confirmWorkspaceManagerChange } from '@/utils/workspaceMoveConfirmation';
import { runBulkWithRollback, type BulkTarget } from '@/utils/bulkSessionSettings';
import { buildManagerEdges, collectDescendants } from './sessionDrag';
import { PAN_ACCESS_ROWS } from '@/utils/panAccessRows';

interface BulkSessionSettingsModalProps {
  open: boolean;
  onClose: () => void;
  /** Stable-order snapshot captured when the Actions menu opened. */
  targets: BulkTarget[];
  onApplied?: () => void;
}

interface BulkDraft {
  /** null = top-level root. Never a manages-session list. */
  managerId: string | null;
  panAccess: PanAccess;
  mcp: Set<string>;
  readonly: boolean;
  reportsToManager: boolean;
}

const EMPTY_DRAFT: BulkDraft = {
  managerId: null,
  panAccess: {},
  mcp: new Set(),
  readonly: false,
  reportsToManager: false,
};

function draftFrom(session: Session): BulkDraft {
  return {
    managerId: session.managedBy ?? null,
    panAccess: { ...(session.panAccess ?? {}) },
    mcp: new Set(session.mcpServers ?? []),
    readonly: session.readonlySession === true,
    reportsToManager: session.reportsToManager === true,
  };
}

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  return a.size === b.size && [...a].every((item) => b.has(item));
}

function normalizeBindings(session: Session): PluginBinding[] {
  return session.pluginBindings && session.pluginBindings.length > 0
    ? session.pluginBindings
    : (session.mcpServers ?? []).map((plugin_id) => ({
        plugin_id,
        enabled: true,
        mode: 'standalone' as const,
      }));
}

/**
 * Bulk Manage for the select-mode Actions bar.
 *
 * Reads the representative (first stable-ordered selected Session) state and
 * applies the same non-manages-session fields to every selected Session after
 * an explicit confirmation. The "manages" relationship (the sessions this
 * Session manages) is never displayed and never written: only the Session's
 * own manager, Pan access flags, MCP/plugin selection, readonly and report
 * subscription are patched, one Session at a time through the existing
 * single-Session APIs. A mid-run failure rolls the applied members back.
 */
export function BulkSessionSettingsModal({
  open,
  onClose,
  targets,
  onApplied,
}: BulkSessionSettingsModalProps) {
  const sessions = useSessionStore((state) => state.sessions);
  const showToast = useUIStore((state) => state.showToast);
  const [representative, setRepresentative] = useState<Session | null>(null);
  const [catalog, setCatalog] = useState<McpServerInfo[]>([]);
  const [draft, setDraft] = useState<BulkDraft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    const first = targets[0];

    setDraft(EMPTY_DRAFT);
    setRepresentative(null);
    setError('');
    setBusy(false);
    if (!first) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetchSession(first.id),
      fetchMcpServers().catch(() => [] as McpServerInfo[]),
    ])
      .then(([session, list]) => {
        if (cancelled) return;
        setRepresentative(session);
        setDraft(draftFrom(session));
        setCatalog(list);
      })
      .catch((failure) => {
        if (cancelled) return;
        setError(failure instanceof Error ? failure.message : 'Session metadata unavailable');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // `targets` is the caller's captured snapshot; its identity is stable for
    // as long as the modal stays open, so it cannot reset the draft mid-edit.
  }, [open, targets]);

  const selectedIds = useMemo(() => new Set(targets.map((target) => target.id)), [targets]);
  /** The session's own manager choices; selected Sessions and their own
   *  descendants are excluded so a bulk write can never form a cycle. */
  const managerOptions = useMemo(() => {
    const edges = buildManagerEdges(sessions);
    const blocked = new Set(selectedIds);
    for (const id of selectedIds) {
      for (const descendant of collectDescendants(edges, id)) blocked.add(descendant);
    }
    const options = sessions.filter(
      (session) => !session.id.startsWith('__pending_') && !blocked.has(session.id),
    );
    const current = representative?.managedBy
      ? sessions.find((session) => session.id === representative.managedBy)
      : undefined;
    if (current && !options.some((session) => session.id === current.id)) {
      options.unshift(current);
    }
    return options;
  }, [sessions, selectedIds, representative]);

  const togglePan = (key: keyof PanAccess, value: boolean) => {
    setDraft((current) => ({ ...current, panAccess: { ...current.panAccess, [key]: value } }));
  };
  const toggleMcp = (name: string, value: boolean) => {
    setDraft((current) => {
      const next = new Set(current.mcp);
      if (value) next.add(name);
      else next.delete(name);
      return { ...current, mcp: next };
    });
  };

  const apply = useCallback(async () => {
    if (busy || !representative) return;
    setBusy(true);
    setError('');
    try {
      // Fresh per-member details: CAS values and rollback targets must come
      // from the member itself, never from the representative snapshot.
      const details = await Promise.all(targets.map((target) => fetchSession(target.id)));
      const detailById = new Map(details.map((session) => [session.id, session]));
      const tree = sessions.map((session) => {
        const detail = detailById.get(session.id);
        return detail ? { ...session, managedBy: detail.managedBy } : session;
      });
      const edges = buildManagerEdges(tree);
      const nextManager = draft.managerId;

      // Preflight: management cycles, template MCP locks and workspace
      // confirmation all happen before the first write.
      if (nextManager) {
        for (const target of targets) {
          // The chosen manager itself keeps its own manager; only descendants
          // of another selected member are an illegal cycle.
          if (target.id === nextManager) continue;
          if (collectDescendants(edges, target.id).has(nextManager)) {
            throw new Error(
              `禁止：不能把「${target.name}」移入自己或其下级的组内（会形成递归管理）`,
            );
          }
        }
      }
      for (const detail of details) {
        const mcpChanged = !sameSet(new Set(detail.mcpServers ?? []), draft.mcp);
        if (!mcpChanged) continue;
        if (detail.mcpLocked && detail.mcpLockReason === 'never' && draft.mcp.size > 0) {
          throw new Error(`「${detail.name || detail.id}」的模板禁止 MCP；批量设置不能绕过该限制`);
        }
        if (detail.mcpLocked && detail.mcpLockReason === 'always' && draft.mcp.size === 0) {
          throw new Error(`「${detail.name || detail.id}」的模板要求至少保留一个 MCP server`);
        }
      }
      const managerSession = nextManager
        ? sessions.find((session) => session.id === nextManager)
        : undefined;
      const workspaceChanges = managerSession
        ? details.filter(
            (detail) =>
              detail.id !== nextManager &&
              (detail.managedBy ?? null) !== nextManager &&
              effectiveWorkspaceIds(detail, sessions)[0] !==
                effectiveWorkspaceIds(managerSession, sessions)[0],
          )
        : [];
      if (
        workspaceChanges.length > 0 &&
        useAppSettingsStore.getState().notifications.confirmCrossWorkspaceManagement
      ) {
        const manager = sessions.find((session) => session.id === nextManager);
        const destinationWorkspace =
          (manager && effectiveWorkspaceIds(manager, sessions)[0]) ?? null;
        const destinationName =
          useWorkspaceStore.getState().workspaces.find((workspace) => workspace.id === destinationWorkspace)
            ?.name ?? '未分组';
        const accepted = await confirmWorkspaceManagerChange({
          changeType: 'attach',
          sessionName: `${representative.name || representative.id} 等 ${targets.length} 个会话`,
          subtreeCount: targets.length,
          managerName: manager?.name || nextManager || '',
          targetWorkspaceName: destinationName,
        });
        if (!accepted) {
          setBusy(false);
          return;
        }
      }

      const result = await runBulkWithRollback(targets, async (target, registerUndo) => {
        const previous = detailById.get(target.id);
        if (!previous) throw new Error('Session metadata unavailable');
        // When the chosen manager is itself selected, it keeps its own manager
        // and manager-scoped flags: applying "managed by itself" is never valid.
        const isChosenManager = nextManager !== null && target.id === nextManager;

        // 1) Own manager only — never the `managed` children set.
        if (!isChosenManager && (previous.managedBy ?? null) !== nextManager) {
          if (previous.managedBy) {
            const oldManager = previous.managedBy;
            await unclaimSession(oldManager, target.id);
            registerUndo(async () => {
              await claimSession(oldManager, target.id);
            });
          }
          if (nextManager) {
            await claimSession(nextManager, target.id);
            registerUndo(async () => {
              await unclaimSession(nextManager, target.id);
            });
          }
        }

        // 2) Manager-scoped flags reuse the single-Session endpoints (and
        //    their expected-current checks) against the member's new manager.
        if (!isChosenManager && nextManager && (previous.readonlySession === true) !== draft.readonly) {
          await setSessionReadonly(nextManager, target.id, draft.readonly, previous.readonlySession);
          registerUndo(async () => {
            await setSessionReadonly(
              nextManager,
              target.id,
              previous.readonlySession === true,
              draft.readonly,
            );
          });
        }
        if (!isChosenManager && nextManager && (previous.reportsToManager === true) !== draft.reportsToManager) {
          await setReportsToManager(target.id, nextManager, draft.reportsToManager);
          registerUndo(async () => {
            await setReportsToManager(
              target.id,
              nextManager,
              previous.reportsToManager === true,
            );
          });
        }

        // 3) Pan access: send only the flags that differ.
        const accessPatch: PanAccess = {};
        for (const row of PAN_ACCESS_ROWS) {
          if (Boolean(previous.panAccess?.[row.key]) !== Boolean(draft.panAccess[row.key])) {
            accessPatch[row.key] = Boolean(draft.panAccess[row.key]);
          }
        }
        if (Object.keys(accessPatch).length > 0) {
          // Only keys that previously carried an explicit value need an undo
          // patch; an absent flag defaults to false, so re-sending false would
          // be a no-op rather than a restoration.
          const undoPatch: PanAccess = {};
          for (const key of Object.keys(accessPatch) as (keyof PanAccess)[]) {
            if (previous.panAccess?.[key] !== undefined) {
              undoPatch[key] = Boolean(previous.panAccess[key]);
            }
          }
          await patchSession(target.id, { panAccess: accessPatch });
          if (Object.keys(undoPatch).length > 0) {
            registerUndo(async () => {
              await patchSession(target.id, { panAccess: undoPatch });
            });
          }
        }

        // 4) MCP / plugin selection, keeping the member's existing binding
        //    modes and the template lock contract inherited from ManageModal.
        if (!sameSet(new Set(previous.mcpServers ?? []), draft.mcp)) {
          const useBindings = catalog.some((server) => server.supportedModes);
          const forceMcp = previous.mcpLocked ? { forceMcp: true } : {};
          if (useBindings) {
            const nextNames = [...draft.mcp];
            const nextBindings = nextNames.map((name) => {
              const existing = normalizeBindings(previous).find((binding) => binding.plugin_id === name);
              return existing ?? {
                plugin_id: name,
                enabled: true,
                mode: catalog.find((server) => server.name === name)?.defaultMode ?? 'standalone',
              };
            });
            const updated = await patchSession(target.id, {
              pluginBindings: nextBindings,
              expectedPluginRevision: previous.pluginSelectionRevision ?? 0,
              ...forceMcp,
            });
            const undoRevision = updated.pluginSelectionRevision ?? 0;
            const previousBindings = normalizeBindings(previous);
            registerUndo(async () => {
              if (previousBindings.length > 0) {
                await patchSession(target.id, {
                  pluginBindings: previousBindings,
                  expectedPluginRevision: undoRevision,
                  ...forceMcp,
                });
              } else {
                await patchSession(target.id, { mcpServers: [], ...forceMcp });
              }
            });
          } else {
            const previousNames = [...(previous.mcpServers ?? [])];
            await patchSession(target.id, { mcpServers: [...draft.mcp], ...forceMcp });
            registerUndo(async () => {
              await patchSession(target.id, { mcpServers: previousNames, ...forceMcp });
            });
          }
        }
      });

      if (result.error) {
        const rollbackNote = `${result.rolledBack} applied session(s) rolled back`;
        setError(
          `Bulk update stopped at ${result.failedName}: ${result.error}. ${rollbackNote}.${
            result.rollbackFailures.length > 0
              ? ` Rollback failed for ${result.rollbackFailures.join('; ')}.`
              : ''
          }`,
        );
        showToast(
          `Bulk Manage stopped at ${result.failedName}: ${result.error}. ${rollbackNote}.`,
          'error',
        );
      } else {
        showToast(`Updated ${targets.length} selected Sessions`);
        onApplied?.();
        onClose();
      }
      try {
        await useSessionStore.getState().loadSessions({ throwOnError: true });
      } catch {
        showToast('Settings settled, but the Session list refresh failed', 'error');
      }
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : String(failure);
      setError(message);
      showToast(message, 'error');
    } finally {
      setBusy(false);
    }
  }, [
    busy,
    representative,
    targets,
    sessions,
    draft,
    catalog,
    showToast,
    onApplied,
    onClose,
  ]);

  return (
    <Modal
      open={open}
      onClose={() => { if (!busy) onClose(); }}
      title="Manage selected Sessions"
      size="lg"
      mobileFullscreen
    >
      <div data-testid="bulk-manage-modal" className="flex flex-col gap-3">
        <p className="text-xs text-text-secondary">
          Representative: {representative?.name || '…'} · {targets.length} selected Sessions.
          Settings below start from the first selected Session in list order and apply to every
          selected Session; the sessions it manages are never shown or changed.
        </p>
        {loading && <p role="status" className="text-xs text-text-tertiary">Loading representative settings…</p>}
        {error && (
          <p role="alert" className="rounded border border-danger/30 bg-danger/10 px-2 py-1 text-xs text-danger">
            {error}
          </p>
        )}

        <section className="flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-xs text-text-secondary">
            Managed by
            <select
              aria-label="Managed by"
              value={draft.managerId ?? ''}
              disabled={busy || loading}
              onChange={(event) => {
                const value = event.target.value;
                setDraft((current) => ({ ...current, managerId: value || null }));
              }}
              className="rounded border border-border-default bg-bg-primary px-2 py-1 text-xs text-text-primary"
            >
              <option value="">No manager</option>
              {managerOptions.map((session) => (
                <option key={session.id} value={session.id}>
                  {session.name || session.id}
                </option>
              ))}
            </select>
          </label>
        </section>

        <section className="flex flex-col gap-2">
          <div className="border-b border-border-muted pb-1 text-xs font-semibold text-text-primary">
            Pan Access
          </div>
          {PAN_ACCESS_ROWS.map((row) => (
            <label key={row.key} className="flex items-center gap-2 text-xs text-text-primary">
              <input
                type="checkbox"
                aria-label={row.label}
                checked={Boolean(draft.panAccess[row.key])}
                disabled={busy || loading}
                onChange={(event) => togglePan(row.key, event.target.checked)}
              />
              {row.label}
            </label>
          ))}
        </section>

        <section className="flex flex-col gap-2">
          <div className="border-b border-border-muted pb-1 text-xs font-semibold text-text-primary">
            MCP and Plugins
          </div>
          {catalog.length === 0 && (
            <p className="text-[11px] text-text-tertiary">MCP catalog unavailable — selection unchanged.</p>
          )}
          {catalog.map((server) => (
            <label key={server.name} className="flex items-center gap-2 text-xs text-text-primary">
              <input
                type="checkbox"
                aria-label={`MCP: ${server.name}`}
                checked={draft.mcp.has(server.name)}
                disabled={busy || loading}
                onChange={(event) => toggleMcp(server.name, event.target.checked)}
              />
              {server.displayName || server.name}
            </label>
          ))}
        </section>

        <section className="flex flex-col gap-2">
          <label className="flex items-center gap-2 text-xs text-text-primary">
            <input
              type="checkbox"
              aria-label="Readonly"
              checked={draft.readonly}
              disabled={busy || loading || !draft.managerId}
              onChange={(event) => setDraft((current) => ({ ...current, readonly: event.target.checked }))}
            />
            Readonly (requires a manager)
          </label>
          <label className="flex items-center gap-2 text-xs text-text-primary">
            <input
              type="checkbox"
              aria-label="Reports to manager"
              checked={draft.reportsToManager}
              disabled={busy || loading || !draft.managerId}
              onChange={(event) =>
                setDraft((current) => ({ ...current, reportsToManager: event.target.checked }))}
            />
            Reports to manager (requires a manager)
          </label>
        </section>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" size="sm" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="bulk-apply"
            disabled={busy || loading || !representative}
            onClick={() => void apply()}
          >
            Apply to all selected sessions
          </Button>
        </div>
      </div>
    </Modal>
  );
}

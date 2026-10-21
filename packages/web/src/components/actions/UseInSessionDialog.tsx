import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { actionRequest } from '@/services/api';
import { useUIStore } from '@/stores/uiStore';
import {
  useActionCatalogStore,
  useActionRegistryStore,
  useActionSessionDetailStore,
  useActionSessionsStore,
} from '@/stores/actionRegistryStore';
import type { ActionGroup } from '@/types/actions';
import type { PluginBinding, PluginMode, Session } from '@/types';
import { MODE_DESCRIPTIONS, MODE_LABELS, shortVersion } from './actionsModel';
import { Badge, Modal, cx, inputClass } from './ActionsUi';
import {
  AlertTriangle,
  Check,
  Info,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldCheck,
  Waves,
} from 'lucide-react';

const MODES: PluginMode[] = ['standalone', 'pan_native', 'gateway'];

export function UseInSessionDialog({
  group,
  onClose,
}: {
  group: ActionGroup;
  onClose: () => void;
}) {
  const build = group.build;
  const [query, setQuery] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [binding, setBinding] = useState<PluginBinding | null>(null);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');
  const [refreshingSessions, setRefreshingSessions] = useState(false);

  const sessions = useActionSessionsStore((state) => state.sessions);
  const sessionsStatus = useActionSessionsStore((state) => state.status);
  const sessionsError = useActionSessionsStore((state) => state.error);
  const servers = useActionCatalogStore((state) => state.servers);
  const catalogStatus = useActionCatalogStore((state) => state.status);
  const catalogError = useActionCatalogStore((state) => state.error);
  const entry = useActionSessionDetailStore((state) =>
    sessionId ? state.entries[sessionId] : undefined,
  );
  const loadSession = useActionSessionDetailStore((state) => state.loadSession);
  const mergeSession = useActionSessionDetailStore((state) => state.mergeSession);
  const showToast = useUIStore((state) => state.showToast);

  const filtered = useMemo(() => {
    const value = query.trim().toLowerCase();
    if (!value) return sessions;
    return sessions.filter(
      (session) =>
        session.name.toLowerCase().includes(value) ||
        session.id.toLowerCase().includes(value) ||
        (session.adapter ?? '').toLowerCase().includes(value),
    );
  }, [query, sessions]);

  const selected = sessions.find((session) => session.id === sessionId) ?? null;
  const detail = entry?.data ?? null;
  const server = build ? servers.find((item) => item.name === build.plugin_id) : undefined;
  const modes = useMemo(() => server?.supportedModes ?? [], [server]);
  const catalogLoading = catalogStatus === 'loading';
  const catalogProblem = catalogStatus === 'error' || (catalogStatus === 'ready' && !server);
  const catalogMessage =
    catalogStatus === 'error'
      ? catalogError || 'The plugin catalog could not be loaded.'
      : `The built plugin ${build?.plugin_id ?? ''} is not present in the catalog. Refresh the catalog or rebuild the Group.`;

  useEffect(() => {
    void useActionSessionsStore.getState().ensureSessions();
    void useActionCatalogStore.getState().ensureCatalog();
  }, []);

  useEffect(() => {
    if (!sessionId || binding || !build) return;
    if (!detail) return;
    const existing = detail.pluginBindings?.find((item) => item.plugin_id === build?.plugin_id);
    if (existing) {
      setBinding({ ...existing });
      return;
    }
    const fallbackMode = server?.defaultMode ?? 'pan_native';
    const mode = modes.includes(fallbackMode) ? fallbackMode : (modes[0] ?? 'pan_native');
    setBinding(build ? { plugin_id: build.plugin_id, enabled: true, mode } : null);
  }, [binding, build, detail, modes, server?.defaultMode, sessionId]);

  useEffect(() => {
    if (!binding || !server || !modes.length) return;
    if (modes.includes(binding.mode)) return;
    const fallback = server.defaultMode && modes.includes(server.defaultMode)
      ? server.defaultMode
      : modes[0]!;
    setBinding({ ...binding, mode: fallback });
  }, [binding, modes, server]);

  if (!build) return null;

  async function selectSession(id: string) {
    setSessionId(id);
    setBinding(null);
    setError('');
    await loadSession(id);
  }

  async function apply() {
    if (!build || !selected || !detail || !binding || !server) return;
    if (!modes.includes(binding.mode)) {
      setError('This plugin does not support the selected mode.');
      return;
    }
    setApplying(true);
    setError('');
    try {
      const bindings = [
        ...(detail.pluginBindings ?? []).filter((item) => item.plugin_id !== binding.plugin_id),
        binding,
      ];
      const result = await actionRequest<Session>(
        `sessions/${encodeURIComponent(selected.id)}`,
        'PATCH',
        {
          pluginBindings: bindings,
          expectedPluginRevision: detail.pluginSelectionRevision ?? 0,
        },
      );
      if (result && 'error' in result && (result as Session & { error?: string }).error) {
        throw new Error((result as Session & { error?: string }).error);
      }
      mergeSession(result);
      useActionRegistryStore.getState().applyBinding(build.plugin_id, result, binding);
      // The binding changed how the registry projects this Group's Session
      // state (including running blockers). Revalidate that derived projection
      // in the background; the modal already reflects the authoritative PATCH
      // response, so a failed refresh cannot undo the applied binding locally.
      void useActionRegistryStore.getState().refreshRegistry();
      const applyStatus = result.pluginApplyStatus;
      showToast(
        binding.enabled
          ? `Binding updated${applyStatus === 'next_start' ? ' — applies after the Worker becomes idle' : ''}`
          : 'Binding disabled',
        'info',
      );
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setApplying(false);
    }
  }

  const loadedVersion = group.sessions.find((item) => item.id === sessionId)?.loadedVersion;
  const running = selected?.workerStatus === 'running';
  const detailLoading = Boolean(sessionId) && entry?.status === 'loading' && !detail;
  const detailError = entry?.status === 'error' ? entry.error : '';

  return (
    <Modal
      title="Use in Session"
      description={
        <>
          Bind <span className="font-medium text-text-primary">{group.name}</span> to one Session. The
          built release stays pinned until its Worker restarts.
        </>
      }
      width="max-w-2xl"
      onClose={onClose}
      footer={
        <>
          <Button type="button" size="sm" onClick={onClose} disabled={applying}>
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            variant="primary"
            disabled={!selected || !binding || !server || modes.length === 0 || applying || Boolean(detailError)}
            onClick={() => void apply()}
          >
            {applying ? <LoaderCircle size={13} className="animate-spin" /> : <Check size={13} />}
            {applying ? 'Applying…' : 'Apply to Session'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-md border border-border-default bg-bg-secondary/50 px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="success" dot>
              Registered
            </Badge>
            <span className="ml-auto font-mono text-[10px] text-text-tertiary" title={build.version}>
              v {shortVersion(build.version)}
            </span>
            <Badge tone="neutral">{build.action_ids.length} Actions</Badge>
          </div>
          <p className="mt-1 break-all font-mono text-[10px] leading-relaxed text-text-tertiary">
            {build.plugin_id}
          </p>
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">Session</h3>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-text-tertiary">{sessions.length} available</span>
              <button
                type="button"
                aria-label="Refresh Sessions"
                title="Refresh Sessions"
                className="rounded p-1 text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary"
                onClick={() => {
                  setRefreshingSessions(true);
                  void useActionSessionsStore.getState().refreshSessions().finally(() => setRefreshingSessions(false));
                }}
              >
                <RefreshCw size={12} className={refreshingSessions ? 'animate-spin' : undefined} />
              </button>
            </div>
          </div>
          <div className="relative mb-2">
            <Search
              size={13}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-tertiary"
            />
            <input
              className={cx(inputClass, 'pl-8 text-xs')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by name, ID or adapter"
              aria-label="Search Sessions"
            />
          </div>
          {sessionsError && (
            <div className="mb-2 flex items-center gap-2 rounded border border-warning/30 bg-warning/5 px-2 py-1.5 text-[11px] text-warning">
              <AlertTriangle size={12} className="shrink-0" />
              <span className="min-w-0 flex-1 break-words">
                {sessions.length ? 'Could not refresh Sessions. Showing cached results. ' : ''}
                {sessionsError}
              </span>
              <button
                type="button"
                className="shrink-0 underline"
                onClick={() => void useActionSessionsStore.getState().refreshSessions()}
              >
                Retry
              </button>
            </div>
          )}
          <div
            role="radiogroup"
            aria-label="Session"
            className="max-h-56 overflow-auto rounded-md border border-border-default"
          >
            {sessionsStatus === 'loading' && !sessions.length ? (
              <div className="flex items-center gap-2 px-3 py-6 text-xs text-text-tertiary">
                <LoaderCircle size={13} className="animate-spin" /> Loading Sessions…
              </div>
            ) : filtered.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-text-tertiary">
                {sessions.length ? 'No Sessions match this search.' : 'No Sessions available. Create one from the sidebar, then Refresh.'}
              </p>
            ) : (
              filtered.map((session) => {
                const active = session.id === sessionId;
                const existing = session.pluginBindings?.find((item) => item.plugin_id === build.plugin_id);
                return (
                  <label
                    key={session.id}
                    className={cx(
                      'flex cursor-pointer items-center gap-2.5 border-b border-border-muted px-3 py-2.5 transition-colors last:border-0',
                      active ? 'bg-accent/5' : 'hover:bg-bg-tertiary/50',
                    )}
                  >
                    <input
                      type="radio"
                      name="actions-session"
                      className="accent-accent"
                      checked={active}
                      onChange={() => void selectSession(session.id)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="truncate text-xs font-medium text-text-primary">{session.name}</span>
                        {session.adapter && <Badge tone="neutral">{session.adapter}</Badge>}
                        {existing && <Badge tone="accent">bound</Badge>}
                      </span>
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-text-tertiary">
                        {session.id}
                        {session.workdir ? ` · ${session.workdir}` : ''}
                      </span>
                    </span>
                    <Badge tone={session.workerStatus === 'running' ? 'warning' : 'success'} dot>
                      {session.workerStatus === 'running' ? 'Running' : 'Idle'}
                    </Badge>
                  </label>
                );
              })
            )}
          </div>
        </div>

        {!sessionId ? (
          <div className="flex items-center gap-2 rounded-md border border-dashed border-border-default px-3 py-4 text-xs text-text-tertiary">
            <Info size={14} className="shrink-0" />
            Select a Session to load its current binding and choose a mode.
          </div>
        ) : detailLoading ? (
          <div className="flex items-center gap-2 rounded-md border border-border-default px-3 py-4 text-xs text-text-tertiary">
            <LoaderCircle size={13} className="animate-spin" /> Loading Session settings…
          </div>
        ) : detailError ? (
          <div className="flex items-center gap-2 rounded-md border border-danger/30 bg-danger/5 px-3 py-3 text-xs text-danger">
            <AlertTriangle size={13} className="shrink-0" />
            <span className="min-w-0 flex-1 break-words">{detailError}</span>
            <button
              type="button"
              className="shrink-0 underline"
              onClick={() => void loadSession(sessionId, { force: true })}
            >
              Retry
            </button>
          </div>
        ) : detail && binding ? (
          <div className="space-y-3 rounded-md border border-border-default p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="text-xs font-semibold text-text-primary">Plugin binding</p>
                <p className="mt-0.5 truncate font-mono text-[10px] text-text-tertiary">{build.plugin_id}</p>
              </div>
              <label className="flex items-center gap-2 text-xs text-text-secondary">
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={binding.enabled}
                  onChange={(event) =>
                    setBinding((current) =>
                      current ? { ...current, enabled: event.target.checked } : current,
                    )
                  }
                />
                Enabled for this Session
              </label>
            </div>

            {catalogProblem ? (
              <div className="flex items-start gap-2 rounded border border-danger/30 bg-danger/5 p-2 text-[11px] leading-relaxed text-danger">
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                <span className="min-w-0 flex-1">{catalogMessage}</span>
                <button
                  type="button"
                  className="shrink-0 underline"
                  onClick={() => void useActionCatalogStore.getState().ensureCatalog({ force: true })}
                >
                  Retry
                </button>
              </div>
            ) : catalogLoading && !server ? (
              <div className="flex items-center gap-2 text-[11px] text-text-tertiary">
                <LoaderCircle size={12} className="animate-spin" /> Loading plugin modes…
              </div>
            ) : (
              <div role="radiogroup" aria-label="Plugin mode" className="grid gap-2 sm:grid-cols-3">
                {MODES.map((mode) => {
                  const supported = modes.includes(mode);
                  const active = binding.mode === mode;
                  return (
                    <label
                      key={mode}
                      className={cx(
                        'flex cursor-pointer flex-col gap-1 rounded-md border p-2.5 transition-colors',
                        active
                          ? 'border-accent/60 bg-accent/10 ring-1 ring-accent/20'
                          : 'border-border-default hover:border-text-tertiary/40',
                        (!supported || !binding.enabled) && 'cursor-not-allowed opacity-50',
                      )}
                    >
                      <span className="flex items-center gap-1.5 text-xs font-medium text-text-primary">
                        <input
                          type="radio"
                          name="actions-plugin-mode"
                          className="accent-accent"
                          disabled={!supported || !binding.enabled}
                          checked={active}
                          onChange={() =>
                            setBinding((current) => (current ? { ...current, mode } : current))
                          }
                        />
                        {MODE_LABELS[mode]}
                      </span>
                      <span className="text-[10px] leading-relaxed text-text-tertiary">
                        {MODE_DESCRIPTIONS[mode]}
                      </span>
                      {!supported && <span className="text-[10px] text-warning">Unsupported by this plugin</span>}
                    </label>
                  );
                })}
              </div>
            )}

            {!binding.enabled && (
              <p className="text-[10px] leading-relaxed text-warning">
                Enable the binding to choose one of the three modes.
              </p>
            )}

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border-default pt-2.5 text-[11px] text-text-tertiary">
              {running ? (
                <>
                  <Waves size={13} className="text-warning" />
                  Running Worker: the change is stored now and applies after it becomes idle.
                </>
              ) : (
                <>
                  <ShieldCheck size={13} className="text-success" />
                  Idle Session: the binding can be applied on the next connection.
                </>
              )}
              <span className="ml-auto">
                Loaded: <span className="font-mono">{loadedVersion ? shortVersion(loadedVersion) : 'next start'}</span>
              </span>
            </div>
            {error && (
              <p role="alert" className="break-words rounded border border-danger/30 bg-danger/5 px-2 py-1.5 text-[11px] text-danger">
                {error}
              </p>
            )}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/Button';
import type { ActionGroup } from '@/types/actions';
import type { McpServerInfo, PluginBinding, PluginMode } from '@/types';
import { MODE_DESCRIPTIONS, MODE_LABELS, type MockSession } from './fixtures';
import { Badge, Modal, cx, inputClass } from './ui';
import { Check, Info, Search, ShieldCheck, Waves } from 'lucide-react';

const MODES: PluginMode[] = ['standalone', 'pan_native', 'gateway'];

export function UseInSessionDialog({
  group,
  sessions,
  servers,
  onClose,
  onApply,
}: {
  group: ActionGroup;
  sessions: MockSession[];
  servers: McpServerInfo[];
  onClose: () => void;
  onApply: (sessionId: string, binding: PluginBinding) => void;
}) {
  const build = group.build;
  const [query, setQuery] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [binding, setBinding] = useState<PluginBinding | null>(null);
  const filtered = useMemo(() => {
    const value = query.trim().toLowerCase();
    if (!value) return sessions;
    return sessions.filter(
      (session) =>
        session.name.toLowerCase().includes(value) ||
        session.id.toLowerCase().includes(value) ||
        session.adapter.includes(value),
    );
  }, [query, sessions]);
  const selected = sessions.find((session) => session.id === sessionId) ?? null;
  const server = servers.find((item) => item.name === build?.plugin_id);
  const modes = server?.supportedModes ?? MODES;

  useEffect(() => {
    if (!selected || !build) {
      setBinding(null);
      return;
    }
    const existing = selected.pluginBindings.find((item) => item.plugin_id === build.plugin_id);
    setBinding(
      existing
        ? { ...existing }
        : { plugin_id: build.plugin_id, enabled: true, mode: server?.defaultMode ?? 'pan_native' },
    );
  }, [selected, build, server]);

  if (!build) return null;

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
          <span className="mr-auto hidden text-[10px] text-text-tertiary sm:block">
            Mock only — no Session is modified.
          </span>
          <Button type="button" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            variant="primary"
            disabled={!selected || !binding}
            onClick={() => {
              if (selected && binding) onApply(selected.id, binding);
            }}
            data-autofocus
          >
            <Check size={13} /> Apply to Session
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
              v {build.version.slice(0, 8)}…
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
            <span className="text-[11px] text-text-tertiary">{sessions.length} available</span>
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
          <div
            role="radiogroup"
            aria-label="Session"
            className="max-h-56 overflow-auto rounded-md border border-border-default"
          >
            {filtered.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-text-tertiary">No Sessions match this search.</p>
            ) : (
              filtered.map((session) => {
                const active = session.id === sessionId;
                const existing = session.pluginBindings.find((item) => item.plugin_id === build.plugin_id);
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
                      name="session"
                      className="accent-accent"
                      checked={active}
                      onChange={() => setSessionId(session.id)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="truncate text-xs font-medium text-text-primary">{session.name}</span>
                        <Badge tone="neutral">{session.adapter}</Badge>
                        {existing && <Badge tone="accent">bound</Badge>}
                      </span>
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-text-tertiary">
                        {session.id} · {session.workdir}
                      </span>
                    </span>
                    <Badge tone={session.status === 'running' ? 'warning' : 'success'} dot>
                      {session.status === 'running' ? 'Running' : 'Idle'}
                    </Badge>
                  </label>
                );
              })
            )}
          </div>
        </div>

        {!selected ? (
          <div className="flex items-center gap-2 rounded-md border border-dashed border-border-default px-3 py-4 text-xs text-text-tertiary">
            <Info size={14} className="shrink-0" />
            Select a Session to load its current binding and choose a mode.
          </div>
        ) : (
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
                  checked={binding?.enabled ?? false}
                  onChange={(event) =>
                    setBinding((current) =>
                      current ? { ...current, enabled: event.target.checked } : current,
                    )
                  }
                />
                Enabled for this Session
              </label>
            </div>

            {!binding?.enabled && (
              <p className="text-[10px] leading-relaxed text-warning">
                Enable the binding to choose one of the three modes.
              </p>
            )}
            <div role="radiogroup" aria-label="Plugin mode" className="grid gap-2 sm:grid-cols-3">
              {MODES.map((mode) => {
                const supported = modes.includes(mode);
                const active = binding?.mode === mode;
                return (
                  <label
                    key={mode}
                    className={cx(
                      'flex cursor-pointer flex-col gap-1 rounded-md border p-2.5 transition-colors',
                      active
                        ? 'border-accent/60 bg-accent/10 ring-1 ring-accent/20'
                        : 'border-border-default hover:border-text-tertiary/40',
                      (!supported || !binding?.enabled) && 'cursor-not-allowed opacity-50',
                    )}
                  >
                    <span className="flex items-center gap-1.5 text-xs font-medium text-text-primary">
                      <input
                        type="radio"
                        name="plugin-mode"
                        className="accent-accent"
                        disabled={!supported || !binding?.enabled}
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

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border-default pt-2.5 text-[11px] text-text-tertiary">
              {selected.status === 'running' ? (
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
                Loaded:{' '}
                <span className="font-mono">
                  {group.sessions.find((item) => item.id === selected.id)?.loadedVersion?.slice(0, 8) ?? 'next start'}
                </span>
              </span>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

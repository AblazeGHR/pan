import { lazy, Suspense, useEffect, useRef, useState, type ComponentType } from 'react';
import {
  Bell,
  BellOff,
  CheckCheck,
  ChevronDown,
  Folder,
  RotateCw,
  Send,
  Timer,
  XOctagon,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { createJob } from '@/services/api';
import { effectiveWorkspaceIds } from '@/utils/sessionFilters';
import type { JobCreateInput } from '@/types/jobs';
import {
  describeActionResults,
  runSelectedSessionAction,
  snapshotSelectedSessions,
  type SelectedSessionAction,
  type SelectedSessionTarget,
} from '@/utils/selectedSessionActions';

const NewJobForm = lazy(() =>
  import('@/components/jobs/NewJobForm').then((module) => ({ default: module.NewJobForm })),
);

interface MenuItem {
  key: SelectedSessionAction | 'workspace' | 'broadcast';
  label: string;
  Icon: ComponentType<{ size?: number }>;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  danger?: boolean;
}

export function SelectedSessionActions({
  onBusyChange,
}: {
  onBusyChange?: (busy: boolean) => void;
}) {
  const selectedIds = useSessionStore((state) => state.selectedIds);
  const workspaces = useWorkspaceStore((state) => state.workspaces);
  const delayedRestarts = useWorkerStore((state) => state.delayedRestarts);
  const [targets, setTargets] = useState<SelectedSessionTarget[] | null>(null);
  const [panel, setPanel] = useState<'menu' | 'workspace' | 'broadcast' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const busyRef = useRef(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const toast = useUIStore((state) => state.showToast);
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
  const workspaceError = useWorkspaceStore((state) => state.error);
  const workspaceLoading = useWorkspaceStore((state) => state.loading);
  const close = () => {
    if (!busyRef.current) {
      setPanel(null);
      setTargets(null);
      setError('');
    }
  };

  useEffect(() => {
    if (panel !== 'menu') return;
    const outside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setPanel(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPanel(null);
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [panel]);

  const run = async (action: SelectedSessionAction) => {
    if (!targets || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setPanel(null);
    try {
      const results = await runSelectedSessionAction(targets, action);
      toast(
        describeActionResults(results),
        results.some((result) => result.error) ? 'error' : 'info',
      );
      if (action !== 'read') {
        try {
          await useSessionStore.getState().loadSessions({ throwOnError: true });
        } catch {
          toast('Actions settled, but Session list refresh failed', 'error');
        }
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  const move = async (workspaceId: string | null) => {
    if (!targets || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      const changed = await useWorkspaceStore.getState().moveSessions(
        targets.map((target) => target.id),
        workspaceId,
      );
      toast(
        changed.length
          ? `Moved ${changed.length} Sessions (including managed descendants)`
          : 'No Sessions moved (unchanged or cancelled)',
      );
      setPanel(null);
    } catch (failure) {
      const sessions = useSessionStore.getState().sessions;
      const matched = targets.filter((target) => {
        const session = sessions.find((item) => item.id === target.id);
        if (!session) return false;
        const ids = effectiveWorkspaceIds(session, sessions);
        return workspaceId ? ids.length === 1 && ids[0] === workspaceId : ids.length === 0;
      });
      setError(
        `Move failed: ${failure instanceof Error ? failure.message : String(failure)}. Currently in target: ${matched.length}/${targets.length}${matched.length ? ' (' + matched.map((target) => target.name).join(', ') + ')' : ''}. Review membership before retrying.`,
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  const broadcast = async (input: JobCreateInput) => {
    if (!targets || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      if (input.kind !== 'session-broadcast') throw new Error('Expected a Session broadcast Job');
      const job = await createJob({
        ...input,
        target: { sessionId: targets[0]!.id, sessionIds: targets.map((target) => target.id) },
      });
      toast(`Broadcast Job created: ${job.jobId}`);
      setPanel(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  // Lifecycle availability mirrors the single-Session Worker menu: Kill needs a
  // live worker on at least one target, and a delayed restart already pending
  // on any target must not be re-requested from here.
  const killAvailable = Boolean(targets?.some((target) => target.hasWorker));
  const delayedPending = Boolean(
    targets?.some((target) => {
      const status = delayedRestarts[target.id]?.status;
      return status === 'pending' || status === 'restarting';
    }),
  );
  const sessionMenuItems: MenuItem[] = [
    {
      key: 'workspace',
      label: 'Workspace',
      Icon: Folder,
      onClick: () => {
        setPanel('workspace');
        if (!useWorkspaceStore.getState().loaded)
          void useWorkspaceStore.getState().loadWorkspaces();
      },
    },
    { key: 'read', label: 'Read all', Icon: CheckCheck, onClick: () => void run('read') },
    { key: 'broadcast', label: '群发', Icon: Send, onClick: () => setPanel('broadcast') },
    { key: 'bell-on', label: 'Bell on', Icon: Bell, onClick: () => void run('bell-on') },
    { key: 'bell-off', label: 'Bell off', Icon: BellOff, onClick: () => void run('bell-off') },
  ];
  const lifecycleMenuItems: MenuItem[] = [
    {
      key: 'restart',
      label: 'Restart',
      Icon: RotateCw,
      onClick: () => void run('restart'),
      title: 'Restart each live worker, or start one when offline.',
    },
    {
      key: 'delayed-restart',
      label: 'Delayed restart',
      Icon: Timer,
      onClick: () => void run('delayed-restart'),
      disabled: delayedPending,
      title: delayedPending
        ? 'A selected Session already has a delayed restart pending'
        : 'Finish the current task, then restart before queued work. Starts a worker if offline.',
    },
    {
      key: 'kill',
      label: 'Kill',
      Icon: XOctagon,
      danger: true,
      onClick: () => {
        if (!confirm(`Kill workers for ${targets?.length ?? 0} selected session(s)?`)) return;
        void run('kill');
      },
      disabled: !killAvailable,
      title: killAvailable
        ? 'Kill the live workers of the selected Sessions'
        : 'No selected Session has a live worker to kill',
    },
  ];
  const renderMenuItem = ({ key, label, Icon, onClick, disabled, title, danger }: MenuItem) => (
    <button
      key={key}
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`flex w-full items-center gap-2 rounded px-3 py-2 text-left text-xs transition-colors hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent ${
        danger ? 'text-danger' : 'text-text-primary'
      }`}
    >
      <Icon size={14} />
      {label}
    </button>
  );

  return (
    <div ref={menuRef} className="relative">
      <Button
        size="sm"
        variant="secondary"
        disabled={!selectedIds.size || busy}
        aria-haspopup="menu"
        aria-expanded={panel === 'menu'}
        onClick={() => {
          if (panel === 'menu') {
            close();
            return;
          }
          setTargets(snapshotSelectedSessions(selectedIds, useSessionStore.getState().sessions));
          setPanel('menu');
          setError('');
        }}
      >
        Actions <ChevronDown size={12} />
      </Button>
      {panel === 'menu' && (
        <div
          role="menu"
          aria-label="Selected Session actions"
          className="absolute right-0 top-full z-50 mt-1 w-44 rounded-md border border-border-default bg-bg-primary p-1 shadow-xl"
        >
          <p
            className="px-3 py-1 text-[10px] text-text-tertiary"
            title={targets?.map((target) => target.name).join(', ')}
          >
            {targets?.length} selected targets
          </p>
          {sessionMenuItems.map(renderMenuItem)}
          <div className="border-t border-border-muted my-1" role="separator" />
          {lifecycleMenuItems.map(renderMenuItem)}
        </div>
      )}
      {(panel === 'workspace' || panel === 'broadcast') && targets && (
        <Modal
          open
          onClose={close}
          title={panel === 'workspace' ? 'Move selected Sessions' : '群发消息'}
          size="sm"
        >
          <p className="mb-3 break-words text-xs text-text-secondary">
            Targets: {targets.map((target) => target.name).join(', ')}
          </p>
          {panel === 'workspace' ? (
            <div className="flex flex-col gap-1">
              {workspaceLoading && <p role="status">Loading workspaces…</p>}
              {workspaceError && (
                <p role="alert" className="text-xs text-danger">
                  {workspaceError}
                </p>
              )}
              <p className="mb-2 text-xs text-text-tertiary">
                Managed descendants move together. Existing cross-workspace confirmation applies.
              </p>
              {[{ id: '', name: '未分组（移出工作区）' }, ...workspaces].map((workspace) => (
                <Button
                  key={workspace.id}
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  onClick={() => void move(workspace.id || null)}
                >
                  {workspace.name}
                </Button>
              ))}
            </div>
          ) : (
            <Suspense fallback={<p role="status">Loading message form…</p>}>
              <NewJobForm
                broadcastTargets={targets.map((target) => target.id)}
                submitting={busy}
                onCreate={(input) => void broadcast(input)}
              />
            </Suspense>
          )}
          {error && (
            <p role="alert" className="mt-3 text-xs text-danger">
              {error}
            </p>
          )}
          <Button className="mt-3" variant="ghost" size="sm" disabled={busy} onClick={close}>
            Cancel
          </Button>
        </Modal>
      )}
    </div>
  );
}

import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Bell, BellOff, CheckCheck, ChevronDown, Folder, Send } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useUIStore } from '@/stores/uiStore';
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

export function SelectedSessionActions({
  onBusyChange,
}: {
  onBusyChange?: (busy: boolean) => void;
}) {
  const selectedIds = useSessionStore((state) => state.selectedIds);
  const workspaces = useWorkspaceStore((state) => state.workspaces);
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
          {(
            [
              [
                'Workspace',
                Folder,
                () => {
                  setPanel('workspace');
                  if (!useWorkspaceStore.getState().loaded)
                    void useWorkspaceStore.getState().loadWorkspaces();
                },
              ],
              ['Read all', CheckCheck, () => void run('read')],
              ['群发', Send, () => setPanel('broadcast')],
              ['Bell on', Bell, () => void run('bell-on')],
              ['Bell off', BellOff, () => void run('bell-off')],
            ] as const
          ).map(([label, Icon, onClick]) => (
            <button
              key={label}
              role="menuitem"
              onClick={onClick}
              className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-xs text-text-primary hover:bg-bg-hover"
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
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

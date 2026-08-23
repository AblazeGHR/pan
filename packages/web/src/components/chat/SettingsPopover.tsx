import { useCallback, useEffect, useState } from 'react';
import { useCurrentSession, useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { Button } from '@/components/ui/Button';
import { fetchSession } from '@/services/api';
import type { AdapterConfig, PermissionMode, Session } from '@/types';

interface SettingsPopoverProps {
  open: boolean;
  onClose: () => void;
}

function supportsSetting(
  config: AdapterConfig | null,
  name: string,
): boolean {
  if (!config?.supportedSettings) return false;
  return config.supportedSettings.includes(name);
}

/**
 * Upward-expanding settings popover anchored to the toolbar's gear button
 * (rendered `absolute bottom-full`, so it appears above the input row and
 * never covers the textarea). Compact replacement for the old right-side
 * SettingsPanel: model / permission mode / thinking+effort / worker actions.
 */
export function SettingsPopover({ open, onClose }: SettingsPopoverProps) {
  const session = useCurrentSession();
  const currentWorker = useWorkerStore((s) => s.currentWorker);
  const showToast = useUIStore((s) => s.showToast);
  const { restart, startWorker, killCurrent, interrupt, takeover } =
    useWorkerStore();
  const config = useAdapterStore((s) => s.getConfig());
  const applySettings = useAdapterStore((s) => s.applySettings);
  const { loadSessions } = useSessionStore();

  // The sidebar list is summary=1 driven and does NOT carry model /
  // permissionMode / alwaysThinkingEnabled / effort — fetch the full session
  // on open so the editor shows the real values (on-demand detail, same as
  // Manage/Postbox). The settings fields are also merged into the store so the
  // toolbar pills / effort select reflect them too.
  const [detailSession, setDetailSession] = useState<Session | null>(null);
  useEffect(() => {
    if (!open || !session?.id) return;
    setDetailSession(null);
    fetchSession(session.id)
      .then((full) => {
        setDetailSession(full);
        useSessionStore.getState().updateSession(full.id, {
          model: full.model ?? undefined,
          permissionMode: full.permissionMode ?? undefined,
          alwaysThinkingEnabled: full.alwaysThinkingEnabled,
          effort: full.effort,
          workdir: full.workdir,
        });
      })
      .catch(() => setDetailSession(null));
  }, [open, session?.id]);

  // Same per-session effective-worker logic as TopBar/SettingsPanel.
  const effectiveWorkerId =
    session?.workerId ||
    (currentWorker && currentWorker.sessionId === session?.id
      ? currentWorker.id
      : null) ||
    null;

  const applySetting = useCallback(
    async (key: string, value: unknown) => {
      if (!session) return;
      try {
        await applySettings(session.id, effectiveWorkerId || undefined, {
          [key]: value,
        });
        // Reflect the change locally so the select/checkbox stays in sync.
        setDetailSession((d) => (d ? { ...d, [key]: value } : d));
        await loadSessions();
      } catch (e) {
        showToast((e as Error).message || 'Failed', 'error');
      }
    },
    [session, effectiveWorkerId, applySettings, loadSessions, showToast],
  );

  // Close on outside click (the gear button lives under [data-settings-popover]).
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-settings-popover]')) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open, onClose]);

  if (!open || !session || !config) return null;

  // Prefer the on-demand full session when loaded; fall back to the store's
  // (summary) session for id / workerStatus etc.
  const s = detailSession ?? session;

  const models = config.models || [];
  const currentModel = s.model || config.defaultModel;
  const modelOptions = models.includes(currentModel)
    ? models
    : [currentModel, ...models];
  const modes = config.permissionModes || [];
  const showMode = supportsSetting(config, 'permissionMode');
  const showThinking = supportsSetting(config, 'thinking');
  const showEffort =
    showThinking &&
    supportsSetting(config, 'effort') &&
    !!s.alwaysThinkingEnabled;
  const effortValues = config.effortValues || [];

  return (
    <div
      data-settings-popover
      className="absolute bottom-full mb-1 left-0 z-30 w-72 max-h-[60vh] overflow-y-auto rounded-md border border-border-default bg-bg-primary shadow-xl p-3 space-y-3"
    >
      {/* Model */}
      <div>
        <label className="block text-xs text-text-secondary mb-1">Model</label>
        <select
          value={currentModel}
          onChange={(e) => applySetting('model', e.target.value)}
          className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
        >
          {modelOptions.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      {/* Permission Mode */}
      {showMode && (
        <div>
          <label className="block text-xs text-text-secondary mb-1">
            Permission Mode
          </label>
          <select
            value={s.permissionMode || config.defaultPermissionMode}
            onChange={(e) => applySetting('permissionMode', e.target.value)}
            className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
          >
            {modes.map((p: PermissionMode) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Thinking + Effort */}
      {showThinking && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={!!s.alwaysThinkingEnabled}
              onChange={(e) =>
                applySetting('alwaysThinkingEnabled', e.target.checked)
              }
              className="rounded border-border-default bg-bg-tertiary"
            />
            Always Thinking
          </label>
          {showEffort && effortValues.length > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              <span className="whitespace-nowrap">Effort</span>
              <select
                value={s.effort || effortValues[1] || effortValues[0] || ''}
                onChange={(e) => applySetting('effort', e.target.value)}
                className="rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
              >
                {effortValues.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}

      <div className="border-t border-border-muted" />

      {/* Worker actions */}
      <div>
        <h4 className="text-xs font-semibold text-text-secondary mb-2">
          Worker
        </h4>
        <div className="flex flex-col gap-1.5">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              const workerId = effectiveWorkerId;
              if (workerId) {
                restart(workerId).catch((e) => showToast(e.message, 'error'));
              } else if (session?.id) {
                // No worker yet — spawn one (mirrors TopBar "Start").
                startWorker(session.id).catch((e) =>
                  showToast(e.message, 'error'),
                );
              }
            }}
          >
            ⟳ Restart
          </Button>
          {effectiveWorkerId && (
            <>
              <Button
                variant="secondary"
                size="sm"
                onClick={() =>
                  interrupt(effectiveWorkerId).catch((e) =>
                    showToast(e.message, 'error'),
                  )
                }
              >
                ⊘ Interrupt
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() =>
                  takeover(effectiveWorkerId)
                    .then(() =>
                      showToast('PowerShell opened for takeover'),
                    )
                    .catch((e) => showToast(e.message, 'error'))
                }
              >
                ⤓ Takeover
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => {
                  if (!confirm(`Kill worker ${effectiveWorkerId}?`)) return;
                  killCurrent(effectiveWorkerId).catch((e) =>
                    showToast(e.message, 'error'),
                  );
                }}
              >
                ✕ Kill
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useState } from 'react';
import { useCurrentSession, useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { Button } from '@/components/ui/Button';
import { ModelSelect } from '@/components/ui/ModelSelect';
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
      const patch: Record<string, unknown> = { [key]: value };
      // Codex exposes model-specific reasoning levels. Clear an effort that
      // the newly selected model cannot accept; empty means native default.
      if (key === 'model' && config?.modelEfforts) {
        const nextEfforts = config.modelEfforts[String(value)];
        const currentEffort = (detailSession ?? session).effort || '';
        if (nextEfforts && currentEffort && !nextEfforts.includes(currentEffort)) {
          patch.effort = '';
        }
      }
      try {
        const res = await applySettings(
          session.id,
          effectiveWorkerId || undefined,
          patch,
        );
        // Reflect the change locally so the select/checkbox stays in sync.
        setDetailSession((d) => (d ? { ...d, ...patch } : d));
        await loadSessions();
        // Process-affecting settings (output_mode / model / mcp …) require a
        // worker restart to take effect. When a worker is NOT running the
        // backend flags `requireRestart`; surface it so the user knows the
        // change applies on next spawn / when the worker goes idle.
        if ((res as { requireRestart?: boolean }).requireRestart) {
          showToast(
            '配置已保存，worker 将在下次空闲时重启以生效（或手动重启）',
            'info',
          );
        }
      } catch (e) {
        showToast((e as Error).message || 'Failed', 'error');
      }
    },
    [session, detailSession, config, effectiveWorkerId, applySettings, loadSessions, showToast],
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
    supportsSetting(config, 'effort') &&
    (!showThinking || !!s.alwaysThinkingEnabled);
  const modelEfforts = config.modelEfforts?.[currentModel];
  const effortValues = modelEfforts ? ['', ...modelEfforts] : config.effortValues || [];
  // opencode's effort list starts with "" (unset sentinel); filter it out so
  // the dropdown never renders a blank <option>, and surface it as a clear
  // "默认" placeholder instead.
  const validEffortValues = effortValues.filter((v) => v && String(v).trim() !== '');
  const hadEmpty = effortValues.length !== validEffortValues.length;
  const currentEffort =
    s.effort && validEffortValues.includes(s.effort.trim())
      ? s.effort
      : hadEmpty
        ? ''
        : validEffortValues[0] ?? '';
  // Output Mode selector is shown only when the adapter exposes more than one
  // execution mode (e.g. cbc: ["stream","oneshot"]). Single-mode adapters
  // (kimi/opencode: ["stream"]) never render it — they cannot switch.
  const execModes = config.executionModes || ['stream'];
  const showOutputMode = execModes.length > 1;
  const currentOutputMode =
    s.outputMode ?? (execModes.includes('stream') ? 'stream' : execModes[0]);

  return (
    <div
      data-settings-popover
      className="absolute bottom-full mb-1 left-0 z-30 w-72 max-h-[60vh] overflow-y-auto rounded-md border border-border-default bg-bg-primary shadow-xl p-3 space-y-3"
    >
      {/* Model — ModelSelect 支持关键字过滤（opencode 几十上百个模型时可快速检索） */}
      <div>
        <label className="block text-xs text-text-secondary mb-1">Model</label>
        <ModelSelect
          value={currentModel}
          options={modelOptions}
          onChange={(v) => applySetting('model', v)}
        />
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
          {showEffort && validEffortValues.length > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              <span className="whitespace-nowrap">Effort</span>
              <select
                value={currentEffort}
                onChange={(e) => applySetting('effort', e.target.value)}
                className="rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
              >
                {hadEmpty && <option value="">默认</option>}
                {validEffortValues.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}

      {/* Output Mode (execution mode): only adapters with >1 mode offer it */}
      {showOutputMode && (
        <div>
          <label className="block text-xs text-text-secondary mb-1">
            Output Mode
          </label>
          <select
            value={currentOutputMode}
            onChange={(e) => applySetting('outputMode', e.target.value)}
            className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
          >
            {execModes.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
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
                restart(workerId)
                  .then(() => showToast('Restarted worker'))
                  .catch((e) => showToast(e.message, 'error'));
              } else if (session?.id) {
                // No worker yet — spawn one (mirrors TopBar "Start").
                startWorker(session.id)
                  .then(() => showToast('Worker started'))
                  .catch((e) => showToast(e.message, 'error'));
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
                  interrupt(effectiveWorkerId)
                    .then(() => showToast('Interrupt sent'))
                    .catch((e) => showToast(e.message, 'error'))
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
                  killCurrent(effectiveWorkerId)
                    .then(() => showToast('Kill sent'))
                    .catch((e) => showToast(e.message, 'error'));
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

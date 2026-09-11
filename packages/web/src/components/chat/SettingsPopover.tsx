import { createPortal } from 'react-dom';
import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from 'react';
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
  anchorRef?: RefObject<HTMLElement | null>;
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
export function SettingsPopover({ open, onClose, anchorRef }: SettingsPopoverProps) {
  const session = useCurrentSession();
  const currentWorker = useWorkerStore((s) => s.currentWorker);
  const showToast = useUIStore((s) => s.showToast);
  const { restart, killCurrent, interrupt, takeover } =
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
  const [moreOpen, setMoreOpen] = useState(false);
  const [contextWindowInput, setContextWindowInput] = useState('');
  const [autoCompactInput, setAutoCompactInput] = useState('');
  const [contextWindowError, setContextWindowError] = useState('');
  const [autoCompactError, setAutoCompactError] = useState('');
  const [restoringCodexDefaults, setRestoringCodexDefaults] = useState(false);
  const [popoverPosition, setPopoverPosition] = useState<{ left: number; bottom: number } | null>(null);
  const updatePopoverPosition = () => {
    const rect = anchorRef?.current?.getBoundingClientRect();
    if (!rect) return;
    setPopoverPosition({ left: rect.left, bottom: window.innerHeight - rect.top + 4 });
  };

  useLayoutEffect(() => {
    if (!open) {
      setPopoverPosition(null);
      return;
    }
    updatePopoverPosition();
  }, [open, anchorRef]);

  useEffect(() => {
    if (!open) return;
    const update = () => updatePopoverPosition();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [open, anchorRef]);
  useEffect(() => {
    if (!open || !session?.id) return;
    setDetailSession(null);
    setMoreOpen(false);
    setContextWindowInput(session.modelContextWindow?.toString() ?? '');
    setAutoCompactInput(session.modelAutoCompactTokenLimit?.toString() ?? '');
    setContextWindowError('');
    setAutoCompactError('');
    fetchSession(session.id)
      .then((full) => {
        setDetailSession(full);
        setContextWindowInput(full.modelContextWindow?.toString() ?? '');
        setAutoCompactInput(full.modelAutoCompactTokenLimit?.toString() ?? '');
        useSessionStore.getState().updateSession(full.id, {
          model: full.model ?? undefined,
          permissionMode: full.permissionMode ?? undefined,
          alwaysThinkingEnabled: full.alwaysThinkingEnabled,
          effort: full.effort,
          modelContextWindow: full.modelContextWindow,
          modelAutoCompactTokenLimit: full.modelAutoCompactTokenLimit,
          workdir: full.workdir,
        });
      })
      .catch(() => setDetailSession(null));
  }, [open, session?.id]);

  // Same per-session effective-worker logic as TopBar/SettingsPanel.
  const effectiveWorkerId =
    (session?.workerId && session.workerStatus ? session.workerId : null) ||
    (currentWorker && currentWorker.sessionId === session?.id
      ? currentWorker.id
      : null) ||
    null;

  const applySetting = useCallback(
    async (key: string, value: unknown): Promise<boolean> => {
      if (!session) return false;
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
            '配置已保存，Worker 将自动 respawn 后生效；当前 turn 结束后切换',
            'info',
          );
        }
        return true;
      } catch (e) {
        showToast((e as Error).message || 'Failed', 'error');
        return false;
      }
    },
    [session, detailSession, config, effectiveWorkerId, applySettings, loadSessions, showToast],
  );

  const updateCodexNumber = async (
    key: 'modelContextWindow' | 'modelAutoCompactTokenLimit',
    raw: string,
    setError: (value: string) => void,
  ) => {
    if (!/^[1-9]\d*$/.test(raw)) {
      setError('请输入正整数；如需交给 Codex 默认值，请使用“恢复默认”');
      return;
    }
    const parsed = Number(raw);
    if (!Number.isSafeInteger(parsed) || parsed <= 0) {
      setError('请输入 JavaScript 安全范围内的正整数');
      return;
    }
    setError('');
    await applySetting(key, parsed);
  };

  const restoreCodexDefaults = async () => {
    if (!session || restoringCodexDefaults) return;
    setRestoringCodexDefaults(true);
    try {
      const ok = await applySettings(session.id, {
        modelContextWindow: null,
        modelAutoCompactTokenLimit: null,
      });
      setDetailSession((d) => (d ? {
        ...d,
        modelContextWindow: null,
        modelAutoCompactTokenLimit: null,
      } : d));
      await loadSessions();
      if (!(ok as { error?: string }).error) {
        setContextWindowInput('');
        setAutoCompactInput('');
        setContextWindowError('');
        setAutoCompactError('');
        showToast('已清除 Codex 上下文覆盖，Worker 将自动 respawn 后使用模型默认值', 'info');
      }
    } catch (e) {
      showToast((e as Error).message || 'Failed', 'error');
    } finally {
      setRestoringCodexDefaults(false);
    }
  };

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
  const showCodexContext =
    s.adapter === 'codex' &&
    supportsSetting(config, 'modelContextWindow') &&
    supportsSetting(config, 'modelAutoCompactTokenLimit');
  const hasCodexOverrides =
    s.modelContextWindow != null || s.modelAutoCompactTokenLimit != null;

  if (!popoverPosition) return null;

  return createPortal(
    <div
      data-settings-popover
      style={{ position: 'fixed', left: popoverPosition.left, bottom: popoverPosition.bottom }}
      className="z-[60] mb-1 w-72 max-w-[calc(100vw-1rem)] max-h-[60vh] overflow-y-auto rounded-md border border-border-default bg-bg-primary shadow-xl p-3 space-y-3"
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

      {/* Codex context controls stay behind a compact More disclosure. */}
      {showCodexContext && (
        <div className="border-t border-border-muted pt-2">
          <button
            type="button"
            className="flex w-full items-center justify-between text-left text-xs font-semibold text-text-secondary"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen((value) => !value)}
          >
            <span>More</span>
            <span aria-hidden="true" className="text-text-muted">{moreOpen ? '▾' : '▸'}</span>
          </button>
          {moreOpen && (
            <div className="mt-2 space-y-2">
              <p className="text-[11px] leading-4 text-text-muted">
                修改后需 Worker 自动 respawn/restart 后生效；当前 turn 不热更新。
              </p>
              <label className="block text-xs text-text-secondary">
                <span className="mb-1 block">Context window</span>
                <input
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={contextWindowInput}
                  aria-label="model_context_window"
                  onChange={(e) => {
                    setContextWindowInput(e.target.value);
                    if (e.target.value === '' || /^[1-9]\d*$/.test(e.target.value)) {
                      setContextWindowError('');
                    } else {
                      setContextWindowError('请输入正整数');
                    }
                  }}
                  onBlur={() => updateCodexNumber(
                    'modelContextWindow',
                    contextWindowInput,
                    setContextWindowError,
                  )}
                  className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
                />
                {contextWindowError && <span className="mt-1 block text-[11px] text-danger">{contextWindowError}</span>}
              </label>
              <label className="block text-xs text-text-secondary">
                <span className="mb-1 block">Auto-compact token limit</span>
                <input
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={autoCompactInput}
                  aria-label="model_auto_compact_token_limit"
                  onChange={(e) => {
                    setAutoCompactInput(e.target.value);
                    if (e.target.value === '' || /^[1-9]\d*$/.test(e.target.value)) {
                      setAutoCompactError('');
                    } else {
                      setAutoCompactError('请输入正整数');
                    }
                  }}
                  onBlur={() => updateCodexNumber(
                    'modelAutoCompactTokenLimit',
                    autoCompactInput,
                    setAutoCompactError,
                  )}
                  className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
                />
                {autoCompactError && <span className="mt-1 block text-[11px] text-danger">{autoCompactError}</span>}
              </label>
              <Button
                variant="secondary"
                size="sm"
                disabled={!hasCodexOverrides || restoringCodexDefaults}
                onClick={restoreCodexDefaults}
              >
                {restoringCodexDefaults ? 'Restoring…' : 'Restore defaults'}
              </Button>
            </div>
          )}
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
              if (!session?.id) return;
              // The session-level endpoint decides atomically whether this is
              // a restart or a start; effectiveWorkerId may be stale after a
              // watchdog destroy and must not be used for control routing.
              restart(session.id)
                .then(() => showToast('Worker restarted or started'))
                .catch((e) => showToast(e.message, 'error'));
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
                  interrupt(session.id)
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
                  takeover(session.id)
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
                  killCurrent(session.id)
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
    </div>,
    document.body,
  );
}

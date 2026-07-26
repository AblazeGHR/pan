import { useEffect, useState, useCallback } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { Button } from '@/components/ui/Button';
import type { SyncedSettings, SettingsBody, PermissionMode } from '@/types';

export function SettingsPanel() {
  const settingsOpen = useUIStore((s) => s.settingsOpen);
  const toggleSettings = useUIStore((s) => s.toggleSettings);
  const showToast = useUIStore((s) => s.showToast);
  const session = useSessionStore((s) => s.currentSession);
  const currentWorkerId = useWorkerStore((s) => s.currentWorkerId);

  const config = useAdapterStore((s) => s.getConfig());
  const configReady = useAdapterStore((s) => s.configReady);
  const currentAdapter = useAdapterStore((s) => s.currentAdapter);
  const loadConfig = useAdapterStore((s) => s.loadConfig);
  const applySettings = useAdapterStore((s) => s.applySettings);
  const updateSyncedSettings = useAdapterStore((s) => s.updateSyncedSettings);
  const lastSyncedSettings = useAdapterStore((s) => s.lastSyncedSettings);
  const { loadSessions } = useSessionStore();

  // Local form state
  const [model, setModel] = useState('');
  const [isCustomModel, setIsCustomModel] = useState(false);
  const [customModel, setCustomModel] = useState('');
  const [permissionMode, setPermissionMode] = useState('');
  const [alwaysThinking, setAlwaysThinking] = useState(false);
  const [effort, setEffort] = useState('');
  const [hasChanges, setHasChanges] = useState(false);

  // Load adapter config when panel opens or session changes
  useEffect(() => {
    if (!settingsOpen || !session) return;
    const adapter = session.adapter || 'cbc';
    if (!configReady || adapter !== currentAdapter) {
      loadConfig(adapter);
    }
  }, [settingsOpen, session, configReady, currentAdapter, loadConfig]);

  // Sync form from session when config is ready
  useEffect(() => {
    if (!settingsOpen || !session || !config || !configReady) return;

    const defModel = session.model || config.defaultModel;
    const models = config.models || [];
    if (models.includes(defModel)) {
      setModel(defModel);
      setIsCustomModel(false);
    } else {
      setModel('__custom__');
      setIsCustomModel(true);
      setCustomModel(defModel);
    }

    if (supportsSetting(config, 'permissionMode')) {
      setPermissionMode(session.permissionMode || config.defaultPermissionMode);
    }
    if (supportsSetting(config, 'thinking')) {
      setAlwaysThinking(session.alwaysThinkingEnabled);
    }
    if (supportsSetting(config, 'effort')) {
      const effValues = config.effortValues || [];
      const eff = session.effort || effValues[1] || effValues[0] || '';
      setEffort(effValues.includes(session.effort || '') ? session.effort || '' : eff);
    }

    // Record baseline
    const baseline: SyncedSettings = {
      model: getEffectiveModel(),
      permissionMode: supportsSetting(config, 'permissionMode')
        ? (session.permissionMode || config.defaultPermissionMode)
        : '',
      alwaysThinkingEnabled: supportsSetting(config, 'thinking')
        ? session.alwaysThinkingEnabled
        : false,
      effort: supportsSetting(config, 'effort') ? (session.effort || '') : '',
    };
    updateSyncedSettings(baseline);
    setHasChanges(false);
  }, [settingsOpen, session, config, configReady, updateSyncedSettings]);

  const getEffectiveModel = useCallback((): string => {
    if (isCustomModel) return customModel || config?.defaultModel || '';
    return model || config?.defaultModel || '';
  }, [isCustomModel, customModel, model, config]);

  // Track changes
  useEffect(() => {
    if (!lastSyncedSettings || !config) return;
    const eff = getEffectiveModel();
    if (eff !== lastSyncedSettings.model) { setHasChanges(true); return; }
    if (supportsSetting(config, 'permissionMode') &&
        permissionMode !== lastSyncedSettings.permissionMode) { setHasChanges(true); return; }
    if (supportsSetting(config, 'thinking') &&
        alwaysThinking !== lastSyncedSettings.alwaysThinkingEnabled) { setHasChanges(true); return; }
    if (supportsSetting(config, 'effort') &&
        effort !== lastSyncedSettings.effort) { setHasChanges(true); return; }
    setHasChanges(false);
  }, [model, isCustomModel, customModel, permissionMode, alwaysThinking, effort, lastSyncedSettings, config, getEffectiveModel]);

  const handleApply = async () => {
    if (!session) return;
    const body: SettingsBody = {};
    if (supportsSetting(config, 'model')) body.model = getEffectiveModel();
    if (supportsSetting(config, 'permissionMode') && permissionMode) body.permissionMode = permissionMode;
    if (supportsSetting(config, 'thinking')) body.alwaysThinkingEnabled = alwaysThinking;
    if (supportsSetting(config, 'effort')) body.effort = effort;

    const busy = session.workerStatus === 'running' || session.workerStatus === 'held';
    if (busy) {
      showToast('Cannot change settings while worker is busy. Use Restart instead.');
      return;
    }

    try {
      await applySettings(session.id, currentWorkerId || undefined, body);
      const newBaseline: SyncedSettings = {
        model: getEffectiveModel(),
        permissionMode: supportsSetting(config, 'permissionMode') ? permissionMode : '',
        alwaysThinkingEnabled: supportsSetting(config, 'thinking') ? alwaysThinking : false,
        effort: supportsSetting(config, 'effort') ? effort : '',
      };
      updateSyncedSettings(newBaseline);
      setHasChanges(false);
      // Refresh session data
      await loadSessions();
    } catch (e) {
      showToast((e as Error).message || 'Failed to apply settings', 'error');
    }
  };

  if (!settingsOpen || !session) return null;

  const models = config?.models || [];
  const modes = config?.permissionModes || [];
  const effortValues = config?.effortValues || [];
  const showMode = supportsSetting(config, 'permissionMode');
  const showThinking = supportsSetting(config, 'thinking');
  const showEffort = showThinking && supportsSetting(config, 'effort') && alwaysThinking;

  return (
    <div className="border-t border-border-default bg-bg-secondary p-4" role="region">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-text-primary">Settings</h3>
        <Button variant="ghost" size="sm" onClick={toggleSettings}>
          ✕
        </Button>
      </div>

      {!configReady ? (
        <p className="text-xs text-text-tertiary">Loading settings...</p>
      ) : (
        <div className="space-y-3">
          {/* Model */}
          <div>
            <label className="block text-xs text-text-secondary mb-1">Model</label>
            <select
              value={isCustomModel ? '__custom__' : model}
              onChange={(e) => {
                const v = e.target.value;
                if (v === '__custom__') {
                  setIsCustomModel(true);
                  setCustomModel(model);
                } else {
                  setIsCustomModel(false);
                  setModel(v);
                }
              }}
              className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
            >
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
              <option value="__custom__">✎ custom...</option>
            </select>
            {isCustomModel && (
              <input
                type="text"
                value={customModel}
                onChange={(e) => setCustomModel(e.target.value)}
                placeholder={config?.defaultModel}
                className="mt-1 w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
              />
            )}
          </div>

          {/* Permission Mode */}
          {showMode && (
            <div>
              <label className="block text-xs text-text-secondary mb-1">Permission Mode</label>
              <select
                value={permissionMode}
                onChange={(e) => setPermissionMode(e.target.value)}
                className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
              >
                {modes.map((p: PermissionMode) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
          )}

          {/* Thinking */}
          {showThinking && (
            <div>
              <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
                <input
                  type="checkbox"
                  checked={alwaysThinking}
                  onChange={(e) => setAlwaysThinking(e.target.checked)}
                  className="rounded border-border-default bg-bg-tertiary"
                />
                Always Thinking
              </label>
            </div>
          )}

          {/* Effort */}
          {showEffort && effortValues.length > 0 && (
            <div>
              <label className="block text-xs text-text-secondary mb-1">Effort</label>
              <select
                value={effort}
                onChange={(e) => setEffort(e.target.value)}
                className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
              >
                {effortValues.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
            </div>
          )}

          {/* Apply button */}
          {hasChanges && (
            <div className="pt-1">
              <Button variant="primary" size="sm" onClick={handleApply}>
                Apply Settings
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function supportsSetting(
  config: { supportedSettings?: string[] } | null,
  name: string,
): boolean {
  if (!config?.supportedSettings) return false;
  return config.supportedSettings.includes(name);
}

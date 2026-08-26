import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { useUIStore } from '@/stores/uiStore';
import { fetchSessionTemplates } from '@/services/api';
import type { SessionTemplate, AdapterConfig } from '@/types';

interface NewSessionModalProps {
  open: boolean;
  onClose: () => void;
}

/** Readable manifest location for a template: prefer the backend-computed
 *  short label (e.g. "packages/mcp/manifest.json"); fall back to the last
 *  directory of the full path + "/manifest.json" when the label is missing. */
function manifestLabel(t: SessionTemplate): string {
  if (t.sourceManifestLabel) return t.sourceManifestLabel;
  if (t.sourceManifest) {
    const parts = t.sourceManifest.replace(/\\/g, '/').split('/').filter(Boolean);
    return (parts[parts.length - 1] || '') + '/manifest.json';
  }
  return 'manifest.json';
}

/** True when the selected adapter exposes a given setting (per-adapter
 *  capability, consumed from /api/adapter/config). */
function supportsSetting(config: AdapterConfig | null, name: string): boolean {
  return !!config?.supportedSettings?.includes(name);
}

export function NewSessionModal({ open, onClose }: NewSessionModalProps) {
  const [name, setName] = useState('');
  const [workdir, setWorkdir] = useState('');
  const [adapter, setAdapter] = useState('cbc');
  // Linked settings that follow the selected adapter's config.
  const [model, setModel] = useState('');
  const [permissionMode, setPermissionMode] = useState('');
  const [alwaysThinkingEnabled, setAlwaysThinkingEnabled] = useState(false);
  const [effort, setEffort] = useState('');
  const [outputMode, setOutputMode] = useState('');
  const [sessionTemplate, setSessionTemplate] = useState('');
  const [templates, setTemplates] = useState<SessionTemplate[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  const adapters = useAdapterStore((s) => s.adapters);
  const loadAdapterList = useAdapterStore((s) => s.loadAdapterList);
  const loadConfig = useAdapterStore((s) => s.loadConfig);
  // Config for the *currently selected* adapter (keyed by local state), so the
  // model/permission/effort selects render based on the chosen adapter.
  const config = useAdapterStore((s) => s.adapterConfigs[adapter] ?? null);
  const createNewSession = useSessionStore((s) => s.createNewSession);
  const sessions = useSessionStore((s) => s.sessions);
  const showToast = useUIStore((s) => s.showToast);

  // Load adapter list + default config + session templates when modal opens.
  useEffect(() => {
    if (open) {
      loadAdapterList();
      setName('');
      setWorkdir('');
      setAdapter('cbc');
      setModel('');
      setPermissionMode('');
      setAlwaysThinkingEnabled(false);
      setEffort('');
      setOutputMode('');
      setSessionTemplate('');
      setSubmitting(false);
      loadConfig('cbc');
      fetchSessionTemplates()
        .then(setTemplates)
        .catch(() => setTemplates([]));
      // Focus name input after render
      requestAnimationFrame(() => nameRef.current?.focus());
    }
  }, [open, loadAdapterList, loadConfig]);

  // When the selected adapter's config loads (including right after switching),
  // seed the linked fields with that adapter's defaults so the selects follow
  // the adapter switch. User edits that happen before the config arrives are
  // overwritten, which is acceptable — the config drives the canonical options.
  useEffect(() => {
    if (!config) return;
    setModel(config.defaultModel || '');
    setPermissionMode(config.defaultPermissionMode || '');
    setAlwaysThinkingEnabled(false);
    setEffort(config.effortValues?.[1] || config.effortValues?.[0] || '');
    // Only pre-select an Output Mode when the adapter exposes multiple modes;
    // single-mode adapters (kimi/opencode) never offer the switch.
    const execModes = config.executionModes || ['stream'];
    setOutputMode(execModes.length > 1 ? (execModes[0] || 'stream') : '');
  }, [config]);

  const handleAdapterChange = (next: string) => {
    setAdapter(next);
    // Fetch + cache this adapter's config so model/permission/effort update.
    void loadConfig(next);
  };

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (submitting) return;
    setSubmitting(true);

    const finalName =
      name.trim() || `session-${sessions.length + 1}`;

    try {
      await createNewSession(
        finalName,
        workdir.trim() || null,
        adapter,
        sessionTemplate || undefined,
        {
          model: model || undefined,
          permissionMode: permissionMode || undefined,
          alwaysThinkingEnabled,
          effort: effort || undefined,
          outputMode: outputMode || undefined,
        },
      );
      onClose();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to create session';
      showToast(message, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  // Model select options: ensure the current value is always present.
  const models = config?.models || [];
  const currentModel = model || config?.defaultModel || '';
  const modelOptions = models.includes(currentModel)
    ? models
    : [currentModel, ...models];
  const modes = config?.permissionModes || [];
  const effortValues = config?.effortValues || [];
  const execModes = config?.executionModes || ['stream'];
  const showOutputMode = execModes.length > 1;

  return (
    <Modal open={open} onClose={onClose} title="New Session" size="lg">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* Adapter select — dynamically rendered from /api/adapters */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">
            Adapter
          </span>
          <select
            value={adapter}
            onChange={(e) => handleAdapterChange(e.target.value)}
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
          >
            {adapters.length > 0 ? (
              adapters.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name}
                </option>
              ))
            ) : (
              <option value="cbc">cbc</option>
            )}
          </select>
        </label>

        {/* Model — follows the selected adapter's config (supportedSettings) */}
        {supportsSetting(config, 'model') && (
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">
              Model
            </span>
            <select
              value={currentModel}
              onChange={(e) => setModel(e.target.value)}
              className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
            >
              {modelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        )}

        {/* Permission Mode — follows the selected adapter's config */}
        {supportsSetting(config, 'permissionMode') && (
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">
              Permission Mode
            </span>
            <select
              value={permissionMode || config?.defaultPermissionMode || ''}
              onChange={(e) => setPermissionMode(e.target.value)}
              className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
            >
              {modes.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        )}

        {/* Thinking + Effort — follows the selected adapter's config */}
        {supportsSetting(config, 'thinking') && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={alwaysThinkingEnabled}
                onChange={(e) => setAlwaysThinkingEnabled(e.target.checked)}
                className="rounded border-border-default bg-bg-tertiary"
              />
              Always Thinking
            </label>
            {supportsSetting(config, 'effort') &&
              effortValues.length > 0 &&
              alwaysThinkingEnabled && (
                <label className="flex items-center gap-1.5 text-xs text-text-secondary">
                  <span className="whitespace-nowrap">Effort</span>
                  <select
                    value={
                      effort ||
                      effortValues[1] ||
                      effortValues[0] ||
                      ''
                    }
                    onChange={(e) => setEffort(e.target.value)}
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

        {/* Output Mode — only adapters with >1 execution mode offer the switch */}
        {showOutputMode && (
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">
              Output Mode
            </span>
            <select
              value={outputMode || execModes[0] || 'stream'}
              onChange={(e) => setOutputMode(e.target.value)}
              className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
            >
              {execModes.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        )}

        {/* Session template select */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">
            Session Template{' '}
            <span className="font-normal text-text-tertiary">
              (optional)
            </span>
          </span>
          <select
            value={sessionTemplate}
            onChange={(e) => setSessionTemplate(e.target.value)}
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
          >
            <option value="">None</option>
            {templates.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name} ({t.model || '?'})
                {t.mcpServers && t.mcpServers.length > 0 ? ' [MCP]' : ''} (
                {manifestLabel(t)})
              </option>
            ))}
          </select>
        </label>

        {/* Session name */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">
            Session Name
          </span>
          <input
            ref={nameRef}
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={`session-${sessions.length + 1}`}
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent"
          />
        </label>

        {/* Workdir */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">
            Working Directory{' '}
            <span className="font-normal text-text-tertiary">
              (optional)
            </span>
          </span>
          <input
            type="text"
            value={workdir}
            onChange={(e) => setWorkdir(e.target.value)}
            placeholder="/path/to/project"
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent"
          />
        </label>

        {/* Actions */}
        <div className="flex justify-end gap-2 pt-2">
          <Button
            type="button"
            variant="ghost"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={submitting}
          >
            {submitting ? 'Creating...' : 'Create'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

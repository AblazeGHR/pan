import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { useUIStore } from '@/stores/uiStore';
import { nextSessionDefaultName } from '@/utils/sessionName';
import { fetchSessionTemplates, pickDirectory } from '@/services/api';
import type { SessionTemplate } from '@/types';
import { FolderPlus } from 'lucide-react';

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


export function NewSessionModal({ open, onClose }: NewSessionModalProps) {
  const [name, setName] = useState('');
  const [workdir, setWorkdir] = useState('');
  const [adapter, setAdapter] = useState('cbc');
  // Output mode follows the selected adapter's config.
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

  // A template may pin its own adapter (manifest `adapter` field). When the
  // selected template carries an adapter, the adapter selector is locked to it.
  const selectedTemplate = templates.find((t) => t.name === sessionTemplate);
  const lockedAdapter = selectedTemplate?.adapter || null;

  // Load adapter list + default config + session templates when modal opens.
  useEffect(() => {
    if (open) {
      loadAdapterList();
      setName('');
      setWorkdir('');
      setAdapter('cbc');
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
    // Only pre-select an Output Mode when the adapter exposes multiple modes;
    // single-mode adapters (kimi/opencode) never offer the switch.
    const execModes = config.executionModes || ['stream'];
    setOutputMode(execModes.length > 1 ? (execModes[0] || 'stream') : '');
  }, [config]);

  const handleAdapterChange = (next: string) => {
    setAdapter(next);
    // Fetch + cache this adapter's config so the Output Mode options update.
    void loadConfig(next);
  };

  // When the user picks a template that pins an adapter, lock the adapter
  // selector to that adapter and surface a toast. Picking a template without
  // an adapter (or "None") releases the lock and the selector becomes editable.
  const handleTemplateChange = (value: string) => {
    setSessionTemplate(value);
    const tpl = templates.find((t) => t.name === value);
    if (tpl?.adapter) {
      setAdapter(tpl.adapter);
      showToast(`已选择带 adapter 的 template（${tpl.adapter}），adapter 已锁定`, 'info');
    }
  };

  const handlePickDirectory = async () => {
    try {
      const result = await pickDirectory(workdir.trim() || undefined);
      if (result.path) {
        setWorkdir(result.path);
      } else if (!result.supported) {
        showToast(
          result.reason || 'Folder selection is only supported on Windows.',
          'info',
        );
      }
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to open folder picker';
      showToast(message, 'error');
    }
  };

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (submitting) return;
    setSubmitting(true);

    const finalName =
      name.trim() || nextSessionDefaultName(sessions);

    try {
      await createNewSession(
        finalName,
        workdir.trim() || null,
        adapter,
        sessionTemplate || undefined,
        {
          // model / permissionMode / alwaysThinkingEnabled / effort are no
          // longer exposed at creation time — the backend applies its defaults
          // and the user can change them later via the session settings.
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
            disabled={!!lockedAdapter}
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-60"
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
            {lockedAdapter &&
              !adapters.some((a) => a.name === lockedAdapter) && (
                <option key={lockedAdapter} value={lockedAdapter}>
                  {lockedAdapter}
                </option>
              )}
          </select>
        </label>

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
            onChange={(e) => handleTemplateChange(e.target.value)}
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

        {adapter === 'kimi' && (
          <p className="-mt-2 text-[11px] leading-snug text-text-tertiary">
            kimi 的 MCP 通过隔离目录 data/kimi-homes 自动加载（KIMI_CODE_HOME），无需信任文件夹。
          </p>
        )}

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
            placeholder={nextSessionDefaultName(sessions)}
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
          <div className="flex gap-2">
            <input
              type="text"
              value={workdir}
              onChange={(e) => setWorkdir(e.target.value)}
              placeholder="/path/to/project"
              className="min-w-0 flex-1 rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent"
            />
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => void handlePickDirectory()}
              title="Choose a folder"
              aria-label="Add folder"
            >
              <FolderPlus size={14} />
              添加文件夹
            </Button>
          </div>
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

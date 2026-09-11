import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { DirectoryInput } from '@/components/session/DirectoryInput';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useSessionStore } from '@/stores/sessionStore';
import { getAvailableCliAdapters, useAdapterStore } from '@/stores/adapterStore';
import { useUIStore } from '@/stores/uiStore';
import { nextSessionDefaultName } from '@/utils/sessionName';
import { createDirectory, fetchDirectories, fetchSessionTemplates } from '@/services/api';
import { isMissingDirectoryError, parseDirectoryInput } from '@/utils/directoryInput';
import type { SessionTemplate } from '@/types';
import { ArrowLeft } from 'lucide-react';

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
  const [adapter, setAdapter] = useState('');
  // Output mode follows the selected adapter's config.
  const [outputMode, setOutputMode] = useState('');
  const [sessionTemplate, setSessionTemplate] = useState('');
  const [templates, setTemplates] = useState<SessionTemplate[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [directoryCreationPath, setDirectoryCreationPath] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const cliStatus = useAdapterStore((s) => s.cliStatus);
  const cliStatusLoading = useAdapterStore((s) => s.cliStatusLoading);
  const cliStatusError = useAdapterStore((s) => s.cliStatusError);
  const loadCliStatus = useAdapterStore((s) => s.loadCliStatus);
  const loadConfig = useAdapterStore((s) => s.loadConfig);
  // Config for the *currently selected* adapter (keyed by local state), so the
  // model/permission/effort selects render based on the chosen adapter.
  const config = useAdapterStore((s) => s.adapterConfigs[adapter] ?? null);
  const createNewSession = useSessionStore((s) => s.createNewSession);
  const sessions = useSessionStore((s) => s.sessions);
  const showToast = useUIStore((s) => s.showToast);
  const { isMobile } = useMediaQuery();

  // A template may pin its own adapter (manifest `adapter` field). When the
  // selected template carries an adapter, the adapter selector is locked to it.
  const selectedTemplate = templates.find((t) => t.name === sessionTemplate);
  const lockedAdapter = selectedTemplate?.adapter || null;
  const availableAdapters = useMemo(
    () => getAvailableCliAdapters(cliStatus),
    [cliStatus],
  );
  const availableAdapterNames = useMemo(
    () => new Set(availableAdapters.map((a) => a.name)),
    [availableAdapters],
  );
  const hasAvailableAdapter = availableAdapters.length > 0;
  const selectedAdapterAvailable = availableAdapterNames.has(adapter);
  const lockedAdapterUnavailable =
    !!lockedAdapter && !!cliStatus && !availableAdapterNames.has(lockedAdapter);

  // Load CLI availability and session templates when the modal opens.
  useEffect(() => {
    if (open) {
      loadCliStatus();
      setName('');
      setWorkdir('');
      setAdapter('');
      setOutputMode('');
      setSessionTemplate('');
      setSubmitting(false);
      setDirectoryCreationPath(null);
      fetchSessionTemplates()
        .then(setTemplates)
        .catch(() => setTemplates([]));
      // Focus name input after render
      requestAnimationFrame(() => nameRef.current?.focus());
    }
  }, [open, loadCliStatus]);

  // Full-screen mobile page closes on Escape too (parity with <Modal>).
  useEffect(() => {
    if (!open || !isMobile) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, isMobile, onClose]);

  // Choose cbc when it is available, otherwise the first available adapter.
  // If a template pins an unavailable adapter, leave the selection empty so
  // submission cannot silently send an invalid adapter to the backend.
  useEffect(() => {
    if (!open || cliStatusLoading || !cliStatus) return;
    setAdapter((current) => {
      if (lockedAdapter) {
        return availableAdapterNames.has(lockedAdapter) ? lockedAdapter : '';
      }
      if (availableAdapterNames.has(current)) return current;
      return availableAdapters.find((a) => a.name === 'cbc')?.name
        ?? availableAdapters[0]?.name
        ?? '';
    });
  }, [
    open,
    cliStatusLoading,
    cliStatus,
    lockedAdapter,
    availableAdapters,
    availableAdapterNames,
  ]);

  // Config is only fetched for an adapter that the CLI preflight marked
  // available. This avoids showing settings for a selection that cannot run.
  useEffect(() => {
    if (open && selectedAdapterAvailable) void loadConfig(adapter);
  }, [open, adapter, selectedAdapterAvailable, loadConfig]);

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
      if (!cliStatus || cliStatusLoading) {
        setAdapter('');
      } else if (availableAdapterNames.has(tpl.adapter)) {
        setAdapter(tpl.adapter);
        showToast(`已选择带 adapter 的 template（${tpl.adapter}），adapter 已锁定`, 'info');
      } else {
        setAdapter('');
        showToast(`模板要求的 adapter ${tpl.adapter} 当前不可用，无法创建此 session`, 'error');
      }
    } else if (value === '') {
      setAdapter(
        availableAdapters.find((a) => a.name === 'cbc')?.name
          ?? availableAdapters[0]?.name
          ?? '',
      );
    }
  };

  const createSession = async (requestedWorkdir: string | null) => {
    const finalName = name.trim() || nextSessionDefaultName(sessions);
    await createNewSession(
      finalName,
      requestedWorkdir,
      adapter,
      sessionTemplate || undefined,
      { outputMode: outputMode || undefined },
    );
    onClose();
  };

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (submitting) return;
    if (cliStatusLoading) {
      showToast('正在检测 Agent CLI 可用性，请稍候', 'error');
      return;
    }
    if (cliStatusError) {
      showToast(`无法检测 Agent CLI 可用性：${cliStatusError}`, 'error');
      return;
    }
    if (!hasAvailableAdapter) {
      showToast('当前没有可用的 Agent CLI，无法创建 session', 'error');
      return;
    }
    if (lockedAdapterUnavailable) {
      showToast(`模板要求的 adapter ${lockedAdapter} 当前不可用，请更换模板`, 'error');
      return;
    }
    if (!selectedAdapterAvailable) {
      showToast('请选择一个当前可用的 adapter', 'error');
      return;
    }
    setSubmitting(true);

    const requestedWorkdir = workdir.trim() ? parseDirectoryInput(workdir).candidate : null;

    try {
      if (requestedWorkdir) {
        try {
          await fetchDirectories(requestedWorkdir);
        } catch (error: unknown) {
          if (!isMissingDirectoryError(error)) throw error;
          setDirectoryCreationPath(requestedWorkdir);
          return;
        }
      }
      await createSession(requestedWorkdir);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to create session';
      showToast(message, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDirectoryCreation = async () => {
    const path = directoryCreationPath;
    if (!path || path !== (workdir.trim() ? parseDirectoryInput(workdir).candidate : '')) {
      setDirectoryCreationPath(null);
      return;
    }
    setDirectoryCreationPath(null);
    setSubmitting(true);
    try {
      await createDirectory(path);
      await createSession(path);
    } catch (err: unknown) {
      showToast(err instanceof Error ? err.message : '目录创建失败', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  // Closed → render nothing. The Sidebar keeps this component always mounted
  // and toggles `open`; without this guard the mobile branch below would
  // portal the full-screen page even while the creation flow is closed (the
  // desktop path is safe because <Modal> already returns null when closed).
  // Placed after every hook call and before any render branch, so hook order
  // stays unconditional.
  if (!open) return null;

  const execModes = config?.executionModes || ['stream'];
  const showOutputMode = execModes.length > 1;
  const createDisabled =
    submitting ||
    cliStatusLoading ||
    !!cliStatusError ||
    !hasAvailableAdapter ||
    !selectedAdapterAvailable ||
    lockedAdapterUnavailable;

  const directoryConfirmation = directoryCreationPath && (
    <Modal open title="创建工作目录" onClose={() => setDirectoryCreationPath(null)} size="sm">
      <div className="flex flex-col gap-4">
        <p className="break-all text-sm text-text-primary">
          目录不存在，是否创建？<br />{directoryCreationPath}
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => setDirectoryCreationPath(null)}>取消</Button>
          <Button type="button" variant="primary" onClick={() => void confirmDirectoryCreation()}>创建目录</Button>
        </div>
      </div>
    </Modal>
  );

  const formBody = (
    <form id="new-session-form" onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* Adapter select — availability comes from /api/cli/status. */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">
            Adapter
          </span>
          <select
            value={selectedAdapterAvailable ? adapter : ''}
            onChange={(e) => handleAdapterChange(e.target.value)}
            disabled={!!lockedAdapter || cliStatusLoading || !!cliStatusError || !hasAvailableAdapter}
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-60"
          >
            {cliStatusLoading ? (
              <option value="">检测 CLI 可用性中…</option>
            ) : cliStatusError ? (
              <option value="">无法加载可用 adapter</option>
            ) : hasAvailableAdapter ? (
              availableAdapters.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name}
                </option>
              ))
            ) : (
              <option value="">没有可用 adapter</option>
            )}
          </select>
        </label>

        {cliStatusError && (
          <p className="-mt-2 text-[11px] leading-snug text-danger">
            无法检测当前可用 adapter：{cliStatusError}。请检查 Pan 后端连接后重试。
          </p>
        )}
        {!cliStatusLoading && !cliStatusError && !hasAvailableAdapter && (
          <p className="-mt-2 text-[11px] leading-snug text-danger">
            当前没有可用的 Agent CLI。请安装对应 CLI 后重试；不会自动使用不可用的 cbc。
          </p>
        )}
        {lockedAdapterUnavailable && (
          <p className="-mt-2 text-[11px] leading-snug text-danger">
            当前模板要求 adapter <code>{lockedAdapter}</code>，但它不可用。请更换模板后再创建。
          </p>
        )}

        {/* Output Mode — only adapters with >1 execution mode offer the switch */}
        {showOutputMode && selectedAdapterAvailable && (
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

        {/* Workdir and its search text share one input. */}
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">
            Working Directory{' '}
            <span className="font-normal text-text-tertiary">
              (optional)
            </span>
          </span>
          <DirectoryInput
            value={workdir}
            onChange={setWorkdir}
            onSelect={setWorkdir}
            selectDirectories
            inputTestId="new-session-workdir-input"
          />
        </div>

        {/* Actions — desktop keeps them inside the dialog. On mobile they
            move to the fixed full-screen footer; the submit button there is
            associated with the form via the HTML `form` attribute. */}
        {!isMobile && (
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
              disabled={createDisabled}
            >
              {submitting ? 'Creating...' : 'Create'}
            </Button>
          </div>
        )}
      </form>
  );

  // Mobile: the create-session settings page renders as a full-screen page
  // (not a desktop-style centered dialog). Portal to <body> for the same
  // reason as <Modal>: the mobile sidebar container is transformed, which
  // would clamp position:fixed descendants. Safe-area insets keep the header
  // clear of notches and the footer above the home indicator; the middle
  // section scrolls independently.
  if (isMobile) {
    return createPortal(
      <>
        <div
          data-testid="new-session-fullscreen"
          role="dialog"
          aria-modal="true"
          aria-label="New Session"
          className="fixed inset-0 z-40 flex flex-col bg-bg-primary"
        >
        <header className="flex shrink-0 items-center gap-2 border-b border-border-muted px-3 pb-2 pt-[calc(env(safe-area-inset-top)+0.5rem)]">
          <button
            type="button"
            onClick={onClose}
            aria-label="Back"
            className="rounded p-1.5 text-text-tertiary transition-colors hover:bg-bg-tertiary hover:text-text-primary"
          >
            <ArrowLeft size={18} />
          </button>
          <h2 className="text-base font-semibold text-text-primary">New Session</h2>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{formBody}</div>
        <footer className="flex shrink-0 justify-end gap-2 border-t border-border-muted bg-bg-primary px-4 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" form="new-session-form" variant="primary" disabled={createDisabled}>
            {submitting ? 'Creating...' : 'Create'}
          </Button>
        </footer>
        </div>
        {directoryConfirmation}
      </>,
      document.body,
    );
  }

  return (
    <>
      <Modal open={open} onClose={onClose} title="New Session" size="lg">
        {formBody}
      </Modal>
      {directoryConfirmation}
    </>
  );
}

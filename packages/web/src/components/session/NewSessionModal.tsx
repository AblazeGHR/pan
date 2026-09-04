import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useSessionStore } from '@/stores/sessionStore';
import { getAvailableCliAdapters, useAdapterStore } from '@/stores/adapterStore';
import { useUIStore } from '@/stores/uiStore';
import { nextSessionDefaultName } from '@/utils/sessionName';
import { fetchSessionTemplates, fetchDirectories, type DirectoryListResponse } from '@/services/api';
import type { SessionTemplate } from '@/types';
import { ArrowLeft, ChevronUp, Folder, FolderOpen, FolderPlus, Loader2 } from 'lucide-react';

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

interface DirectoryBrowserProps {
  path: string;
  onPathChange: (path: string) => void;
  onSelect: (path: string) => void;
  onCancel: () => void;
}

function DirectoryBrowser({ path, onPathChange, onSelect, onCancel }: DirectoryBrowserProps) {
  const cacheRef = useRef(new Map<string, DirectoryListResponse>());
  const requestIdRef = useRef(0);
  const [data, setData] = useState<DirectoryListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const cached = cacheRef.current.get(path);
    if (cached) {
      setData(cached);
      setError(null);
      return;
    }
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    fetchDirectories(path || undefined)
      .then((result) => {
        if (requestId !== requestIdRef.current) return;
        cacheRef.current.set(path, result);
        setData(result);
      })
      .catch((err: unknown) => {
        if (requestId !== requestIdRef.current) return;
        setData(null);
        setError(err instanceof Error ? err.message : '无法读取目录');
      })
      .finally(() => {
        if (requestId === requestIdRef.current) setLoading(false);
      });
    return () => { requestIdRef.current += 1; };
  }, [path]);

  const goTo = (nextPath: string) => {
    requestIdRef.current += 1;
    onPathChange(nextPath);
  };

  return (
    <div className="flex flex-col gap-3" data-testid="directory-browser">
      {/* Breadcrumb: the first crumb is the roots level (Windows drive list /
          POSIX roots). The backend reports parent=null for a drive root (e.g.
          "D:\"), so without this crumb there is no way back to the level where
          no drive is selected yet. The current path is shown by the readout
          box below, so the crumb stays link-only. */}
      {path !== '' && (
        <div className="flex items-center gap-1 text-xs" data-testid="directory-breadcrumb">
          <button
            type="button"
            className="text-accent hover:underline"
            onClick={() => goTo('')}
          >
            盘符列表
          </button>
          <span className="text-text-tertiary">/</span>
        </div>
      )}
      <div className="rounded border border-border-muted bg-bg-primary px-3 py-2 text-xs text-text-secondary break-all">
        {data?.current || path || '服务器文件系统根位置'}
      </div>
      <div className="flex items-center gap-2">
        {/* 上一级: a drive root (or filesystem root) reports parent=null from
            the backend; going up from there means returning to the
            drive/roots list (path=''), so the button stays enabled. */}
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={!path || loading}
          onClick={() => goTo(data?.parent ?? '')}
        >
          <ChevronUp size={14} /> 上一级
        </Button>
        <span className="text-xs text-text-tertiary">仅按需加载当前层目录</span>
      </div>
      {/* Fixed-height scroll window with an always-styled scrollbar, so a
          large directory can never overflow the screen. */}
      <div
        data-testid="directory-entries"
        className="dir-scroll h-64 overflow-y-auto rounded border border-border-muted bg-bg-primary"
      >
        {loading && <div className="flex items-center gap-2 p-4 text-sm text-text-secondary"><Loader2 size={15} className="animate-spin" />加载中…</div>}
        {!loading && error && <div className="p-4 text-sm text-danger">加载失败：{error}</div>}
        {!loading && !error && data && data.entries.length === 0 && <div className="p-4 text-sm text-text-tertiary">空目录</div>}
        {!error && data?.entries.map((entry) => (
          <button key={entry.path} type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-tertiary" onClick={() => goTo(entry.path)}>
            <Folder size={15} className="shrink-0 text-text-tertiary" />
            <span className="truncate">{entry.name}</span>
          </button>
        ))}
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" onClick={onCancel}>取消</Button>
        <Button type="button" variant="primary" disabled={!data?.current || loading || !!error} onClick={() => data?.current && onSelect(data.current)}>
          选择当前目录
        </Button>
      </div>
    </div>
  );
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
  const [directoryBrowserOpen, setDirectoryBrowserOpen] = useState(false);
  const [browserPath, setBrowserPath] = useState('');
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
      setDirectoryBrowserOpen(false);
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
              onClick={() => {
                setBrowserPath(workdir.trim());
                setDirectoryBrowserOpen(true);
              }}
              title="Choose a folder"
              aria-label="Add folder"
            >
              <FolderPlus size={14} />
              添加文件夹
            </Button>
          </div>
        </label>

        {directoryBrowserOpen && (
          <div className="rounded-lg border border-border-default bg-bg-secondary p-4" aria-label="Directory browser panel">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-text-primary">
              <FolderOpen size={16} /> 选择服务端目录
            </div>
            <DirectoryBrowser
              path={browserPath}
              onPathChange={setBrowserPath}
              onSelect={(selectedPath) => {
                setWorkdir(selectedPath);
                setDirectoryBrowserOpen(false);
              }}
              onCancel={() => setDirectoryBrowserOpen(false)}
            />
          </div>
        )}

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
      </div>,
      document.body,
    );
  }

  return (
    <Modal open={open} onClose={onClose} title="New Session" size="lg">
      {formBody}
    </Modal>
  );
}

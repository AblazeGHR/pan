import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { DirectoryInput } from '@/components/session/DirectoryInput';
import { SessionTemplateSelect } from '@/components/session/SessionTemplateSelect';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useSessionStore } from '@/stores/sessionStore';
import { getAvailableCliAdapters, useAdapterStore } from '@/stores/adapterStore';
import { useUIStore } from '@/stores/uiStore';
import { nextSessionDefaultName } from '@/utils/sessionName';
import { getCreationWorkspaceIds } from '@/utils/creationWorkspace';
import {
  createDirectory,
  fetchDirectories,
  fetchNewSessionDefaults,
  fetchMcpServers,
  fetchSessionTemplates,
  fetchSessionTemplateTargets,
  saveNewSessionDefaults,
  saveSessionTemplate,
} from '@/services/api';
import { isMissingDirectoryError, parseDirectoryInput } from '@/utils/directoryInput';
import type { McpServerInfo, SessionTemplate, SessionTemplateManifestTarget, SessionTemplateSaveInput } from '@/types';
import { ArrowLeft } from 'lucide-react';

interface NewSessionModalProps {
  open: boolean;
  onClose: () => void;
}

export function NewSessionModal({ open, onClose }: NewSessionModalProps) {
  const [name, setName] = useState('');
  const [workdir, setWorkdir] = useState('');
  const [adapter, setAdapter] = useState('');
  const [adapterExplicit, setAdapterExplicit] = useState(false);
  // Output mode follows the selected adapter's config.
  const [outputMode, setOutputMode] = useState('');
  const [sessionTemplate, setSessionTemplate] = useState('');
  const [saveAsDefault, setSaveAsDefault] = useState(false);
  const [defaultsReady, setDefaultsReady] = useState(false);
  const [templates, setTemplates] = useState<SessionTemplate[]>([]);
  const [manifestTargets, setManifestTargets] = useState<SessionTemplateManifestTarget[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerInfo[]>([]);
  const [activeTab, setActiveTab] = useState<'basic' | 'advanced'>('basic');
  const [model, setModel] = useState('');
  const [permissionMode, setPermissionMode] = useState('');
  const [effort, setEffort] = useState('');
  const [modelContextWindow, setModelContextWindow] = useState('');
  const [modelAutoCompactTokenLimit, setModelAutoCompactTokenLimit] = useState('');
  const [thinking, setThinking] = useState<boolean | undefined>(undefined);
  const [systemPromptOverride, setSystemPromptOverride] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [mcpServerOverride, setMcpServerOverride] = useState<string[] | null>(null);
  const [mcpMode, setMcpMode] = useState<'' | 'always' | 'optional' | 'never'>('optional');
  const [panAccess, setPanAccess] = useState<{
    restrictToManaged?: boolean;
    canClaimUnmanaged?: boolean;
    autoClaimCreated?: boolean;
  }>({});
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false);
  const [saveTemplateName, setSaveTemplateName] = useState('');
  const [saveTemplateTarget, setSaveTemplateTarget] = useState('');
  const [saveTemplateError, setSaveTemplateError] = useState('');
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [targetsLoadError, setTargetsLoadError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [directoryCreationPath, setDirectoryCreationPath] = useState<string | null>(null);
  const directoryCreationWorkspaceIds = useRef<string[] | null>(null);
  const directoryCreationIncludesAdvanced = useRef(false);
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
      setAdapterExplicit(false);
      setOutputMode('');
      setSessionTemplate('');
      setActiveTab('basic');
      setModel('');
      setPermissionMode('');
      setEffort('');
      setModelContextWindow('');
      setModelAutoCompactTokenLimit('');
      setThinking(undefined);
      setSystemPromptOverride(false);
      setSystemPrompt('');
      setMcpServerOverride(null);
      setMcpMode('optional');
      setPanAccess({});
      setSaveTemplateOpen(false);
      setSaveTemplateName('');
      setSaveTemplateTarget('');
      setSaveTemplateError('');
      setTargetsLoadError('');
      setSaveAsDefault(false);
      setDefaultsReady(false);
      setSubmitting(false);
      setDirectoryCreationPath(null);
      directoryCreationWorkspaceIds.current = null;
      directoryCreationIncludesAdvanced.current = false;
      let cancelled = false;
      Promise.all([
        fetchSessionTemplates().catch(() => [] as SessionTemplate[]),
        fetchSessionTemplateTargets().catch((error: unknown) => {
          if (!cancelled) setTargetsLoadError(error instanceof Error ? error.message : 'Unknown error');
          if (!cancelled) showToast(`Failed to load template targets: ${error instanceof Error ? error.message : 'Unknown error'}`, 'error');
          return [] as SessionTemplateManifestTarget[];
        }),
        fetchMcpServers().catch(() => [] as McpServerInfo[]),
        fetchNewSessionDefaults().catch((error: unknown) => {
          if (!cancelled) showToast(
            `Failed to load New Session defaults: ${error instanceof Error ? error.message : 'Unknown error'}`,
            'error',
          );
          return null;
        }),
      ]).then(([loadedTemplates, loadedTargets, loadedMcpServers, defaults]) => {
        if (cancelled) return;
        setTemplates(loadedTemplates);
        setManifestTargets(loadedTargets);
        setMcpServers(loadedMcpServers);
        const savedTemplate = defaults?.sessionTemplate ?? '';
        const templateExists = !savedTemplate || loadedTemplates.some((t) => t.name === savedTemplate);
        setSessionTemplate(templateExists ? savedTemplate : '');
        setMcpMode(templateExists && savedTemplate ? '' : 'optional');
        if (savedTemplate && !templateExists) {
          showToast(`Saved Session Template “${savedTemplate}” is unavailable. The prefilled value has been cleared.`, 'error');
        }
        setAdapter(defaults?.adapter ?? '');
        setOutputMode(defaults?.outputMode ?? '');
        setWorkdir(defaults?.workdir ?? '');
        setDefaultsReady(true);
      });
      // Focus name input after render
      requestAnimationFrame(() => nameRef.current?.focus());
      return () => { cancelled = true; };
    }
  }, [open, loadCliStatus, showToast]);

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
    if (!open || !defaultsReady || cliStatusLoading || !cliStatus) return;
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
    defaultsReady,
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
    setOutputMode((current) => execModes.length > 1
      ? (current && execModes.includes(current) ? current : (execModes[0] || 'stream'))
      : '');
  }, [config]);

  const handleAdapterChange = (next: string) => {
    setAdapter(next);
    setAdapterExplicit(true);
    // Fetch + cache this adapter's config so the Output Mode options update.
    void loadConfig(next);
  };

  // When the user picks a template that pins an adapter, lock the adapter
  // selector to that adapter and surface a toast. Picking a template without
  // an adapter (or "None") releases the lock and the selector becomes editable.
  const handleTemplateChange = (value: string) => {
    setSessionTemplate(value);
    setMcpMode(value ? '' : 'optional');
    setAdapterExplicit(false);
    setModel('');
    setPermissionMode('');
    setEffort('');
    setModelContextWindow('');
    setModelAutoCompactTokenLimit('');
    setThinking(undefined);
    setSystemPromptOverride(false);
    setSystemPrompt('');
    setMcpServerOverride(null);
    setPanAccess({});
    const tpl = templates.find((t) => t.name === value);
    if (tpl?.adapter) {
      if (!cliStatus || cliStatusLoading) {
        setAdapter('');
      } else if (availableAdapterNames.has(tpl.adapter)) {
        setAdapter(tpl.adapter);
        showToast(`The selected template uses adapter ${tpl.adapter}; the adapter is now locked.`, 'info');
      } else {
        setAdapter('');
        showToast(`The template requires adapter ${tpl.adapter}, which is unavailable. This Session cannot be created.`, 'error');
      }
    } else if (value === '') {
      setAdapter(
        availableAdapters.find((a) => a.name === 'cbc')?.name
          ?? availableAdapters[0]?.name
          ?? '',
      );
    }
  };

  const createSession = async (
    requestedWorkdir: string | null,
    workspaceIds: string[],
    includeAdvancedSettings: boolean,
  ) => {
    const finalName = name.trim() || nextSessionDefaultName(sessions);
    const createSettings = {
      outputMode: outputMode || undefined,
      workspaceIds,
      ...(includeAdvancedSettings ? {
        model: model || undefined,
        permissionMode: permissionMode || undefined,
        effort: effort || undefined,
        modelContextWindow: modelContextWindow ? Number(modelContextWindow) : undefined,
        modelAutoCompactTokenLimit: modelAutoCompactTokenLimit ? Number(modelAutoCompactTokenLimit) : undefined,
        alwaysThinkingEnabled: thinking,
        systemPrompt: systemPromptOverride ? systemPrompt : undefined,
        mcpServers: mcpServerOverride === null ? undefined : mcpServerOverride,
        panAccess: Object.keys(panAccess).length ? panAccess : undefined,
      } : {}),
    };
    await createNewSession(
      finalName,
      requestedWorkdir,
      adapter,
      sessionTemplate || undefined,
      createSettings,
    );
    if (saveAsDefault) {
      try {
        await saveNewSessionDefaults({
          adapter,
          outputMode,
          sessionTemplate: sessionTemplate || '',
          workdir: requestedWorkdir || '',
        });
        showToast('Session created and defaults saved.', 'info');
      } catch (error: unknown) {
        showToast(
          `Session created, but defaults were not saved: ${error instanceof Error ? error.message : 'Unknown error'}`,
          'error',
        );
      }
    }
    onClose();
  };

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (submitting) return;
    if (cliStatusLoading) {
      showToast('Checking Agent CLI availability. Please wait.', 'error');
      return;
    }
    if (cliStatusError) {
      showToast(`Unable to check Agent CLI availability: ${cliStatusError}`, 'error');
      return;
    }
    if (!hasAvailableAdapter) {
      showToast('No Agent CLI is available. A Session cannot be created.', 'error');
      return;
    }
    if (lockedAdapterUnavailable) {
      showToast(`The template requires adapter ${lockedAdapter}, which is unavailable. Choose another template.`, 'error');
      return;
    }
    if (!selectedAdapterAvailable) {
      showToast('Select an available adapter.', 'error');
      return;
    }
    // Preserve the Workspace active when the user submits, even if a directory
    // check or the missing-directory confirmation takes time.
    const activeWorkspaceId = useUIStore.getState().activeWorkspaceId;
    const includeAdvancedSettings = activeTab === 'advanced';
    setSubmitting(true);

    const requestedWorkdir = workdir.trim() ? parseDirectoryInput(workdir).candidate : null;

    try {
      const workspaceIds = await getCreationWorkspaceIds(activeWorkspaceId);
      if (requestedWorkdir) {
        try {
          await fetchDirectories(requestedWorkdir);
        } catch (error: unknown) {
          if (!isMissingDirectoryError(error)) throw error;
          directoryCreationWorkspaceIds.current = workspaceIds;
          directoryCreationIncludesAdvanced.current = includeAdvancedSettings;
          setDirectoryCreationPath(requestedWorkdir);
          return;
        }
      }
      await createSession(requestedWorkdir, workspaceIds, includeAdvancedSettings);
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
      directoryCreationWorkspaceIds.current = null;
      setDirectoryCreationPath(null);
      return;
    }
    const activeWorkspaceId = useUIStore.getState().activeWorkspaceId;
    const workspaceIds = directoryCreationWorkspaceIds.current
      ?? await getCreationWorkspaceIds(activeWorkspaceId);
    const includeAdvancedSettings = directoryCreationIncludesAdvanced.current;
    directoryCreationWorkspaceIds.current = null;
    directoryCreationIncludesAdvanced.current = false;
    setDirectoryCreationPath(null);
    setSubmitting(true);
    try {
      await createDirectory(path);
      await createSession(path, workspaceIds, includeAdvancedSettings);
    } catch (err: unknown) {
      showToast(err instanceof Error ? err.message : 'Failed to create directory.', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const openSaveTemplate = () => {
    setSaveTemplateError('');
    setSaveTemplateName('');
    setSaveTemplateTarget(manifestTargets.find((target) => target.writable)?.id ?? '');
    setSaveTemplateOpen(true);
  };

  const handleSaveTemplate = async (event: FormEvent) => {
    event.preventDefault();
    if (savingTemplate) return;
    const cleanName = saveTemplateName.trim();
    const target = manifestTargets.find((item) => item.id === saveTemplateTarget);
    if (!cleanName) {
      setSaveTemplateError('Enter a template name.');
      return;
    }
    if (!target) {
      setSaveTemplateError('Select a loaded manifest target.');
      return;
    }
    if (!target.writable) {
      setSaveTemplateError(target.reason || 'This manifest is not writable.');
      return;
    }

    const payload: SessionTemplateSaveInput = {
      manifestId: target.id,
      name: cleanName,
      ...(sessionTemplate ? { baseTemplate: sessionTemplate } : {}),
    };
    if (!sessionTemplate || adapterExplicit) payload.adapter = adapter || null;
    if (mcpMode) payload.mcp_mode = mcpMode;
    else if (!sessionTemplate) payload.mcp_mode = 'optional';
    if (model) payload.model = model;
    if (permissionMode) payload.permission_mode = permissionMode;
    if (systemPromptOverride) payload.system_prompt = systemPrompt;
    if (mcpServerOverride !== null) payload.mcp_servers = mcpServerOverride;
    const definedPanAccess: NonNullable<SessionTemplateSaveInput['pan_access']> = {};
    if (panAccess.restrictToManaged !== undefined) definedPanAccess.restrict_to_managed = panAccess.restrictToManaged;
    if (panAccess.canClaimUnmanaged !== undefined) definedPanAccess.can_claim_unmanaged = panAccess.canClaimUnmanaged;
    if (panAccess.autoClaimCreated !== undefined) definedPanAccess.auto_claim_created = panAccess.autoClaimCreated;
    if (Object.keys(definedPanAccess).length) payload.pan_access = definedPanAccess;

    setSavingTemplate(true);
    setSaveTemplateError('');
    try {
      await saveSessionTemplate(payload);
      try {
        const refreshed = await fetchSessionTemplates();
        if (!refreshed.some((item) => item.name === cleanName)) {
          throw new Error('The template was saved, but it was not found after refreshing the list.');
        }
        setTemplates(refreshed);
        setSaveTemplateOpen(false);
        showToast(`Session Template “${cleanName}” saved.`, 'info');
      } catch (refreshError) {
        setSaveTemplateOpen(false);
        showToast(`Template saved, but the list could not be refreshed. The new template is not available yet: ${refreshError instanceof Error ? refreshError.message : 'Unknown error'}`, 'error');
      }
    } catch (error) {
      setSaveTemplateError(error instanceof Error ? error.message : 'Failed to save Session Template.');
    } finally {
      setSavingTemplate(false);
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
  const supportedSettings = config?.supportedSettings ?? [];
  const showModel = supportedSettings.includes('model') && (config?.models.length ?? 0) > 0;
  const showPermission = supportedSettings.includes('permissionMode') && (config?.permissionModes.length ?? 0) > 0;
  const showEffort = supportedSettings.includes('effort') && (config?.effortValues.length ?? 0) > 0;
  const showThinking = supportedSettings.includes('thinking');
  const showContextWindow = supportedSettings.includes('modelContextWindow');
  const showAutoCompactLimit = supportedSettings.includes('modelAutoCompactTokenLimit');
  const createDisabled =
    submitting ||
    !defaultsReady ||
    cliStatusLoading ||
    !!cliStatusError ||
    !hasAvailableAdapter ||
    !selectedAdapterAvailable ||
    lockedAdapterUnavailable;

  const directoryConfirmation = directoryCreationPath && (
    <Modal open title="Create Working Directory" onClose={() => setDirectoryCreationPath(null)} size="sm">
      <div className="flex flex-col gap-4">
        <p className="break-all text-sm text-text-primary">
          This directory does not exist. Create it?<br />{directoryCreationPath}
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => setDirectoryCreationPath(null)}>Cancel</Button>
          <Button type="button" variant="primary" onClick={() => void confirmDirectoryCreation()}>Create Directory</Button>
        </div>
      </div>
    </Modal>
  );

  const saveTemplateDialog = (
    <Modal open={saveTemplateOpen} title="Save as Template" onClose={() => setSaveTemplateOpen(false)} size="md">
      <form onSubmit={handleSaveTemplate} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">Template Name <span className="text-danger">*</span></span>
          <input autoFocus required value={saveTemplateName} onChange={(event) => setSaveTemplateName(event.target.value)} className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">Target manifest <span className="text-danger">*</span></span>
          <select value={saveTemplateTarget} onChange={(event) => setSaveTemplateTarget(event.target.value)} disabled={manifestTargets.length === 0} className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent disabled:opacity-60">
            <option value="">{targetsLoadError ? 'Unable to load targets' : 'Select a manifest'}</option>
            {manifestTargets.map((target) => <option key={target.id} value={target.id}>{target.label} — {target.writable ? 'Writable' : 'Not writable'}</option>)}
          </select>
        </label>
        {saveTemplateTarget && (() => {
          const target = manifestTargets.find((item) => item.id === saveTemplateTarget);
          if (!target) return null;
          return <p className={`text-xs ${target.writable ? 'text-text-tertiary' : 'text-danger'}`}>{target.writable ? 'Writable' : 'Not writable'}{target.reason ? `: ${target.reason}` : ''}</p>;
        })()}
        {targetsLoadError && <p className="text-xs text-danger">Failed to load targets: {targetsLoadError}</p>}
        {saveTemplateError && <p role="alert" className="text-sm text-danger">{saveTemplateError}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => setSaveTemplateOpen(false)} disabled={savingTemplate}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={savingTemplate || !saveTemplateTarget || !manifestTargets.find((item) => item.id === saveTemplateTarget)?.writable}>{savingTemplate ? 'Saving...' : 'Save Template'}</Button>
        </div>
      </form>
    </Modal>
  );

  const formBody = (
      <form id="new-session-form" onSubmit={handleSubmit} className="flex flex-col gap-4">
        <fieldset disabled={!defaultsReady} className="contents">
        <div role="tablist" aria-label="New Session settings" className="flex border-b border-border-muted">
          <button type="button" role="tab" aria-selected={activeTab === 'basic'} onClick={() => setActiveTab('basic')} className={`border-b-2 px-3 py-2 text-sm ${activeTab === 'basic' ? 'border-accent text-accent' : 'border-transparent text-text-secondary'}`}>Basic</button>
          <button type="button" role="tab" aria-selected={activeTab === 'advanced'} onClick={() => setActiveTab('advanced')} className={`border-b-2 px-3 py-2 text-sm ${activeTab === 'advanced' ? 'border-accent text-accent' : 'border-transparent text-text-secondary'}`}>Advanced</button>
        </div>
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
              <option value="">Checking CLI availability…</option>
            ) : cliStatusError ? (
              <option value="">Unable to load available adapters</option>
            ) : hasAvailableAdapter ? (
              availableAdapters.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name}
                </option>
              ))
            ) : (
              <option value="">No adapters available</option>
            )}
          </select>
        </label>

        {cliStatusError && (
          <p className="-mt-2 text-[11px] leading-snug text-danger">
            Unable to check adapter availability: {cliStatusError}. Check the Pan backend connection and try again.
          </p>
        )}
        {!cliStatusLoading && !cliStatusError && !hasAvailableAdapter && (
          <p className="-mt-2 text-[11px] leading-snug text-danger">
            No Agent CLI is available. Install an adapter CLI and try again; an unavailable cbc will not be selected automatically.
          </p>
        )}
        {lockedAdapterUnavailable && (
          <p className="-mt-2 text-[11px] leading-snug text-danger">
            The selected template requires adapter <code>{lockedAdapter}</code>, which is unavailable. Choose another template to continue.
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
        <div className="flex min-w-0 flex-col gap-1">
          <span id="new-session-template-label" className="text-xs font-medium text-text-secondary">
            Session Template{' '}
            <span className="font-normal text-text-tertiary">
              (optional)
            </span>
          </span>
          <SessionTemplateSelect
            templates={templates}
            value={sessionTemplate}
            onChange={handleTemplateChange}
            labelId="new-session-template-label"
            disabled={!defaultsReady}
          />
        </div>

        {adapter === 'kimi' && (
          <p className="-mt-2 text-[11px] leading-snug text-text-tertiary">
            Kimi loads MCP servers automatically from an isolated data/kimi-homes directory (KIMI_CODE_HOME); no trusted folder is needed.
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

        <label className="flex items-center gap-2 text-sm text-text-secondary">
          <input
            type="checkbox"
            checked={saveAsDefault}
            onChange={(e) => setSaveAsDefault(e.target.checked)}
            className="accent-accent"
          />
          Save these settings as the default (Session Name is excluded)
        </label>
        {activeTab === 'advanced' && <div className="flex flex-col gap-4">
          <p className="text-xs text-text-tertiary">Leave a field blank or choose “Inherit” to use the selected template or backend default. Explicit values apply only to this Session.</p>
          {showModel && <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Model</span>
            <select value={model} onChange={(event) => { setModel(event.target.value); setEffort(''); }} className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent">
              <option value="">Inherit template / default</option>{config!.models.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>}
          {showPermission && <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Permission Mode</span>
            <select value={permissionMode} onChange={(event) => setPermissionMode(event.target.value)} className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent">
              <option value="">Inherit template / default</option>{config!.permissionModes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>}
          {showEffort && <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Effort / Thinking Level</span>
            <select value={effort} onChange={(event) => setEffort(event.target.value)} className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent">
              <option value="">Inherit template / default</option>{(config!.modelEfforts?.[model] ?? config!.effortValues).map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>}
          {showThinking && <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Always Thinking</span>
            <select value={thinking === undefined ? '' : String(thinking)} onChange={(event) => setThinking(event.target.value === '' ? undefined : event.target.value === 'true')} className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent">
              <option value="">Inherit template / default</option><option value="true">On</option><option value="false">Off</option>
            </select>
          </label>}
          {showContextWindow && <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Model Context Window</span>
            <input type="number" min={1} value={modelContextWindow} onChange={(event) => setModelContextWindow(event.target.value)} placeholder="Inherit default" className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent" />
          </label>}
          {showAutoCompactLimit && <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Auto Compact Token Limit</span>
            <input type="number" min={1} value={modelAutoCompactTokenLimit} onChange={(event) => setModelAutoCompactTokenLimit(event.target.value)} placeholder="Inherit default" className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent" />
          </label>}
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" checked={systemPromptOverride} onChange={(event) => setSystemPromptOverride(event.target.checked)} className="accent-accent" />
            Override System Prompt (an empty value is submitted as an empty string)
          </label>
          {systemPromptOverride && <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">System Prompt</span>
            <textarea value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} rows={5} className="rounded border border-border-muted bg-bg-primary px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
          </label>}
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">MCP Servers</span>
            <select value={mcpServerOverride === null ? 'inherit' : 'custom'} onChange={(event) => setMcpServerOverride(event.target.value === 'inherit' ? null : [])} className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent">
              <option value="inherit">Inherit template / default MCP settings</option><option value="custom">Choose servers explicitly</option>
            </select>
          </label>
          {mcpServerOverride !== null && <div className="flex flex-col gap-2 rounded border border-border-muted p-3">
            {mcpServers.length === 0 ? <p className="text-xs text-text-tertiary">No MCP servers are loaded. Submitting an empty list will explicitly disable MCP.</p> : mcpServers.map((server) => <label key={server.name} className="flex items-center gap-2 text-sm text-text-secondary">
              <input type="checkbox" checked={mcpServerOverride.includes(server.name)} onChange={(event) => setMcpServerOverride((current) => {
                const list = current ?? [];
                return event.target.checked ? [...list, server.name] : list.filter((item) => item !== server.name);
              })} className="accent-accent" />{server.name}
            </label>)}
          </div>}
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Saved template MCP policy</span>
            <select value={mcpMode} onChange={(event) => setMcpMode(event.target.value as '' | 'always' | 'optional' | 'never')} className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent">
              {sessionTemplate && <option value="">Inherit from template{selectedTemplate?.mcpMode ? ` (${selectedTemplate.mcpMode})` : ''}</option>}
              <option value="optional">Optional</option><option value="always">Always — lock selected servers</option><option value="never">Never — lock MCP off</option>
            </select>
            <span className="text-xs text-text-tertiary">{sessionTemplate ? 'The template policy is inherited unless you choose another option.' : 'When no template is selected, optional is saved by default.'}</span>
          </label>
          {selectedTemplate && <p className="text-xs text-text-tertiary">If the template locks MCP to always or never, the backend validates the explicit server list and reports any conflicts.</p>}
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Pan access</span>
            {([
              ['restrictToManaged', 'Only operate on managed Sessions'],
              ['canClaimUnmanaged', 'Allow claiming unmanaged Sessions'],
              ['autoClaimCreated', 'Automatically claim new Sessions'],
            ] as const).map(([key, label]) => <label key={key} className="flex items-center justify-between gap-3 text-sm text-text-secondary">
              <span>{label}</span>
              <select value={panAccess[key] === undefined ? '' : String(panAccess[key])} onChange={(event) => setPanAccess((current) => {
                if (event.target.value === '') {
                  const next = { ...current };
                  delete next[key];
                  return next;
                }
                return { ...current, [key]: event.target.value === 'true' };
              })} className="rounded border border-border-muted bg-bg-primary px-2 py-1 text-xs text-text-primary">
                <option value="">Inherit</option><option value="true">On</option><option value="false">Off</option>
              </select>
            </label>)}
          </div>
        </div>}

        {/* Actions — desktop keeps them inside the dialog. On mobile they
            move to the fixed full-screen footer; the submit button there is
            associated with the form via the HTML `form` attribute. */}
        {!isMobile && (
          <div className="flex justify-end gap-2 pt-2">
            {activeTab === 'advanced' && <Button type="button" variant="ghost" onClick={openSaveTemplate} disabled={!defaultsReady || submitting}>Save as Template</Button>}
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
        </fieldset>
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
          {activeTab === 'advanced' && <Button type="button" variant="ghost" onClick={openSaveTemplate} disabled={!defaultsReady || submitting}>Save as Template</Button>}
          <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" form="new-session-form" variant="primary" disabled={createDisabled}>
            {submitting ? 'Creating...' : 'Create'}
          </Button>
        </footer>
        </div>
        {directoryConfirmation}
        {saveTemplateDialog}
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
      {saveTemplateDialog}
    </>
  );
}

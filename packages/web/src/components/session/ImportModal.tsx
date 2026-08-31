import { useCallback, useEffect, useMemo, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { getAvailableCliAdapters, useAdapterStore } from '@/stores/adapterStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type {
  CbcProject,
  CbcSessionItem,
  KimiWorkspace,
  KimiSessionItem,
  OpencodeSessionItem,
  CodexSessionItem,
} from '@/types';
import {
  fetchCbcProjects,
  fetchCbcSessions,
  importCbcSession,
  fetchKimiWorkspaces,
  fetchKimiSessions,
  importKimiSession,
  fetchOpencodeSessions,
  importOpencodeSession,
  fetchCodexSessions,
  importCodexSession,
} from '@/services/api';

interface ImportModalProps {
  open: boolean;
  onClose: () => void;
  initialAdapter?: Adapter;
}

type Adapter = 'cbc' | 'kimi' | 'opencode' | 'codex';

const IMPORT_ADAPTERS: Array<{ name: Adapter; label: string }> = [
  { name: 'cbc', label: 'cbc' },
  { name: 'kimi', label: 'kimi' },
  { name: 'opencode', label: 'opencode' },
  { name: 'codex', label: 'codex' },
];

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return ts;
  }
}

export function ImportModal({ open, onClose, initialAdapter = 'cbc' }: ImportModalProps) {
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const selectSession = useSessionStore((s) => s.selectSession);
  const showToast = useUIStore((s) => s.showToast);
  const cliStatus = useAdapterStore((s) => s.cliStatus);
  const cliStatusLoading = useAdapterStore((s) => s.cliStatusLoading);
  const cliStatusError = useAdapterStore((s) => s.cliStatusError);
  const loadCliStatus = useAdapterStore((s) => s.loadCliStatus);

  const [adapter, setAdapter] = useState<Adapter | null>(null);

  // ── CBC state ──
  const [projects, setProjects] = useState<CbcProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [selectedDrive, setSelectedDrive] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [cbcSessions, setCbcSessions] = useState<CbcSessionItem[]>([]);
  const [cbcLoading, setCbcLoading] = useState(false);

  // ── Kimi state ──
  const [workspaces, setWorkspaces] = useState<KimiWorkspace[]>([]);
  const [workspacesLoading, setWorkspacesLoading] = useState(false);
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null);
  const [kimiSessions, setKimiSessions] = useState<KimiSessionItem[]>([]);
  const [kimiLoading, setKimiLoading] = useState(false);

  // ── OpenCode state ──
  const [opencodeCwd, setOpencodeCwd] = useState('');
  const [opencodeSessions, setOpencodeSessions] = useState<OpencodeSessionItem[]>([]);
  const [opencodeLoading, setOpencodeLoading] = useState(false);

  // ── Codex state ──
  const [codexCwd, setCodexCwd] = useState('');
  const [codexSessions, setCodexSessions] = useState<CodexSessionItem[]>([]);
  const [codexLoading, setCodexLoading] = useState(false);

  // ── Import state ──
  const [importingId, setImportingId] = useState<string | null>(null);

  // ── Reset on open/close ──
  useEffect(() => {
    if (!open) return;
    loadCliStatus();
    setAdapter(null);
    setProjects([]);
    setProjectsLoading(false);
    setSelectedDrive(null);
    setSelectedProject(null);
    setCbcSessions([]);
    setCbcLoading(false);
    setWorkspaces([]);
    setWorkspacesLoading(false);
    setSelectedWorkspace(null);
    setKimiSessions([]);
    setKimiLoading(false);
    setOpencodeCwd('');
    setOpencodeSessions([]);
    setOpencodeLoading(false);
    setCodexCwd('');
    setCodexSessions([]);
    setCodexLoading(false);
    setImportingId(null);
  }, [open, loadCliStatus]);

  const availableCliAdapters = useMemo(
    () => getAvailableCliAdapters(cliStatus),
    [cliStatus],
  );
  const availableAdapterNames = useMemo(
    () => new Set(availableCliAdapters.map((item) => item.name)),
    [availableCliAdapters],
  );
  const availableImportAdapters = useMemo(
    () => IMPORT_ADAPTERS.filter((item) => availableAdapterNames.has(item.name)),
    [availableAdapterNames],
  );
  const initialAdapterUnavailable =
    !cliStatusLoading &&
    !!cliStatus &&
    !availableAdapterNames.has(initialAdapter);

  // Keep an explicitly requested tab when it is available; otherwise choose
  // the first supported available adapter. A null selection is intentional
  // for loading, error, and empty states so no import request can be issued.
  useEffect(() => {
    if (!open || cliStatusLoading || !cliStatus) return;
    setAdapter((current) => {
      if (current && availableAdapterNames.has(current)) return current;
      if (availableAdapterNames.has(initialAdapter)) return initialAdapter;
      return availableImportAdapters[0]?.name ?? null;
    });
  }, [
    open,
    cliStatusLoading,
    cliStatus,
    initialAdapter,
    availableAdapterNames,
    availableImportAdapters,
  ]);

  const handleAdapterChange = (next: Adapter) => {
    setAdapter(next);
    if (next === 'cbc') {
      setSelectedDrive(null);
      setSelectedProject(null);
      setCbcSessions([]);
    } else if (next === 'kimi') {
      setSelectedWorkspace(null);
      setKimiSessions([]);
    } else if (next === 'opencode') {
      setOpencodeSessions([]);
      setOpencodeLoading(false);
    } else if (next === 'codex') {
      setCodexSessions([]);
      setCodexLoading(false);
    }
  };

  // ── Load CBC projects ──
  useEffect(() => {
    if (!open || adapter !== 'cbc') return;
    setProjectsLoading(true);
    fetchCbcProjects()
      .then((list) => setProjects(list))
      .catch((e) => {
        showToast(
          e instanceof Error ? e.message : 'Failed to load projects',
          'error',
        );
      })
      .finally(() => setProjectsLoading(false));
  }, [open, adapter, showToast]);

  // ── Load CBC sessions when project changes ──
  useEffect(() => {
    if (!open || adapter !== 'cbc' || !selectedProject) return;
    setCbcLoading(true);
    setCbcSessions([]);
    fetchCbcSessions(selectedProject)
      .then((list) => setCbcSessions(list))
      .catch((e) => {
        showToast(
          e instanceof Error ? e.message : 'Failed to load sessions',
          'error',
        );
      })
      .finally(() => setCbcLoading(false));
  }, [open, adapter, selectedProject, showToast]);

  // ── Load Kimi workspaces ──
  useEffect(() => {
    if (!open || adapter !== 'kimi') return;
    setWorkspacesLoading(true);
    fetchKimiWorkspaces()
      .then((list) => setWorkspaces(list))
      .catch((e) => {
        showToast(
          e instanceof Error ? e.message : 'Failed to load workspaces',
          'error',
        );
      })
      .finally(() => setWorkspacesLoading(false));
  }, [open, adapter, showToast]);

  // ── Load Kimi sessions when workspace changes ──
  useEffect(() => {
    if (!open || adapter !== 'kimi' || !selectedWorkspace) return;
    setKimiLoading(true);
    setKimiSessions([]);
    fetchKimiSessions(selectedWorkspace)
      .then((list) => setKimiSessions(list))
      .catch((e) => {
        showToast(
          e instanceof Error ? e.message : 'Failed to load sessions',
          'error',
        );
      })
      .finally(() => setKimiLoading(false));
  }, [open, adapter, selectedWorkspace, showToast]);

  // ── Load OpenCode sessions (filtered by optional cwd) ──
  const loadOpencode = useCallback(
    (cwd: string) => {
      setOpencodeLoading(true);
      fetchOpencodeSessions(cwd)
        .then((list) => setOpencodeSessions(list))
        .catch((e) => {
          showToast(
            e instanceof Error ? e.message : 'Failed to load opencode sessions',
            'error',
          );
        })
        .finally(() => setOpencodeLoading(false));
    },
    [showToast],
  );

  // Auto-load once when the opencode section opens (cwd=''). The "Load"
  // button re-triggers with the typed cwd; we intentionally don't depend on
  // opencodeCwd here so typing doesn't refetch on every keystroke.
  useEffect(() => {
    if (open && adapter === 'opencode') loadOpencode(opencodeCwd);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, adapter]);

  // ── Load Codex sessions (filtered by optional cwd) ──
  const loadCodex = useCallback(
    (cwd: string) => {
      setCodexLoading(true);
      fetchCodexSessions(cwd)
        .then((list) => setCodexSessions(list))
        .catch((e) => {
          showToast(
            e instanceof Error ? e.message : 'Failed to load Codex sessions',
            'error',
          );
        })
        .finally(() => setCodexLoading(false));
    },
    [showToast],
  );

  useEffect(() => {
    if (open && adapter === 'codex') loadCodex(codexCwd);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, adapter]);

  // ── Import CBC session ──
  const handleImportCbc = async (item: CbcSessionItem) => {
    if (importingId) return;
    setImportingId(item.session_id);
    try {
      const result = await importCbcSession(item.session_id, item.project_dir);
      onClose();
      await loadSessions();
      selectSession(result.id);
      showToast('Session imported');
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Import failed',
        'error',
      );
    } finally {
      setImportingId(null);
    }
  };

  // ── Import Kimi session ──
  const handleImportKimi = async (item: KimiSessionItem) => {
    if (importingId) return;
    setImportingId(item.session_id);
    try {
      const result = await importKimiSession(item.session_id, item.workDir);
      onClose();
      await loadSessions();
      selectSession(result.id);
      showToast('Session imported');
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Import failed',
        'error',
      );
    } finally {
      setImportingId(null);
    }
  };

  // ── Import OpenCode session ──
  const handleImportOpencode = async (item: OpencodeSessionItem) => {
    if (importingId) return;
    setImportingId(item.session_id);
    try {
      const result = await importOpencodeSession(item.session_id, item.workDir);
      onClose();
      await loadSessions();
      selectSession(result.id);
      showToast('Session imported');
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Import failed',
        'error',
      );
    } finally {
      setImportingId(null);
    }
  };

  // ── Import Codex session ──
  const handleImportCodex = async (item: CodexSessionItem) => {
    if (importingId) return;
    setImportingId(item.session_id);
    try {
      const result = await importCodexSession(item.session_id, item.workDir);
      onClose();
      await loadSessions();
      selectSession(result.id);
      showToast('Session imported');
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Import failed',
        'error',
      );
    } finally {
      setImportingId(null);
    }
  };

  // ── CBC helpers ──
  const drives = [...new Set(projects.map((p) => p.drive))].sort();
  const filteredProjects = projects
    .filter((p) => !selectedDrive || p.drive === selectedDrive)
    .sort((a, b) => a.short_label.localeCompare(b.short_label));

  const driveSessionCount = (drive: string): number =>
    projects
      .filter((p) => p.drive === drive)
      .reduce((sum, p) => sum + p.session_count, 0);

  // ── Render ──
  const noImportAdapterMessage = cliStatus?.hasAvailable
    ? '当前没有可导入的可用 adapter。请安装对应 CLI 后重试。'
    : '当前没有可用的 Agent CLI。请安装对应 CLI 后重试。';

  return (
    <Modal open={open} onClose={onClose} title="Import Session" size="lg">
      <div className="flex flex-col gap-4">
        {/* Adapter tabs — only adapters marked available by /api/cli/status. */}
        {cliStatusLoading && (
          <div className="rounded border border-border-muted bg-bg-tertiary px-3 py-2 text-sm text-text-secondary">
            正在检测 Agent CLI 可用性…
          </div>
        )}
        {cliStatusError && (
          <div role="alert" className="rounded border border-danger/50 bg-danger/10 px-3 py-2 text-sm text-danger">
            无法检测 Agent CLI 可用性：{cliStatusError}。请检查 Pan 后端连接后重试。
          </div>
        )}
        {!cliStatusLoading && !cliStatusError && availableImportAdapters.length === 0 && (
          <div role="alert" className="rounded border border-warning/50 bg-warning/10 px-3 py-2 text-sm text-text-secondary">
            {noImportAdapterMessage}
          </div>
        )}
        {!cliStatusLoading && !cliStatusError && availableImportAdapters.length > 0 && (
          <div className="flex gap-1 rounded border border-border-muted bg-bg-tertiary p-0.5">
            {availableImportAdapters.map((item) => (
              <button
                key={item.name}
                type="button"
                onClick={() => handleAdapterChange(item.name)}
                className={`flex-1 rounded px-3 py-1 text-xs font-medium transition-colors ${
                  adapter === item.name
                    ? 'bg-accent text-white'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        )}

        {initialAdapterUnavailable && availableImportAdapters.length > 0 && (
          <p className="-mt-2 text-xs text-text-secondary">
            请求的 adapter <code>{initialAdapter}</code> 当前不可用，已切换到可用的 <code>{adapter}</code>。
          </p>
        )}

        {adapter && availableImportAdapters.some((item) => item.name === adapter) && (
          <hr className="border-border-muted" />
        )}

        {/* cbc import */}
        {adapter === 'cbc' && (
          <CbcSection
            projectsLoading={projectsLoading}
            drives={drives}
            selectedDrive={selectedDrive}
            onDriveChange={(d) => {
              setSelectedDrive(d);
              setSelectedProject(null);
              setCbcSessions([]);
            }}
            driveSessionCount={driveSessionCount}
            filteredProjects={filteredProjects}
            selectedProject={selectedProject}
            onProjectChange={(p) => setSelectedProject(p)}
            cbcSessions={cbcSessions}
            cbcLoading={cbcLoading}
            importingId={importingId}
            onImport={handleImportCbc}
          />
        )}

        {/* kimi import */}
        {adapter === 'kimi' && (
          <KimiSection
            workspacesLoading={workspacesLoading}
            workspaces={workspaces}
            selectedWorkspace={selectedWorkspace}
            onWorkspaceChange={(w) => {
              setSelectedWorkspace(w);
              setKimiSessions([]);
            }}
            kimiSessions={kimiSessions}
            kimiLoading={kimiLoading}
            importingId={importingId}
            onImport={handleImportKimi}
          />
        )}

        {/* opencode import */}
        {adapter === 'opencode' && (
          <OpencodeSection
            cwd={opencodeCwd}
            onCwdChange={setOpencodeCwd}
            sessions={opencodeSessions}
            loading={opencodeLoading}
            importingId={importingId}
            onLoad={() => loadOpencode(opencodeCwd)}
            onImport={handleImportOpencode}
          />
        )}

        {/* codex import */}
        {adapter === 'codex' && (
          <CodexSection
            cwd={codexCwd}
            onCwdChange={setCodexCwd}
            sessions={codexSessions}
            loading={codexLoading}
            importingId={importingId}
            onLoad={() => loadCodex(codexCwd)}
            onImport={handleImportCodex}
          />
        )}

        {/* Footer */}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose} disabled={!!importingId}>
            Cancel
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ──────────────────────────────────────────
// CBC Section
// ──────────────────────────────────────────

interface CbcSectionProps {
  projectsLoading: boolean;
  drives: string[];
  selectedDrive: string | null;
  onDriveChange: (drive: string | null) => void;
  driveSessionCount: (drive: string) => number;
  filteredProjects: CbcProject[];
  selectedProject: string | null;
  onProjectChange: (dir: string) => void;
  cbcSessions: CbcSessionItem[];
  cbcLoading: boolean;
  importingId: string | null;
  onImport: (item: CbcSessionItem) => void;
}

function CbcSection({
  projectsLoading,
  drives,
  selectedDrive,
  onDriveChange,
  driveSessionCount,
  filteredProjects,
  selectedProject,
  onProjectChange,
  cbcSessions,
  cbcLoading,
  importingId,
  onImport,
}: CbcSectionProps) {
  // Front-end path filter: narrow the session list by the session's
  // project_dir (cbc sessions are scoped to a project, so this is the path).
  const [pathFilter, setPathFilter] = useState('');
  const pathFilterTrim = pathFilter.trim();
  const visibleSessions = pathFilterTrim
    ? cbcSessions.filter((s) =>
        s.project_dir.toLowerCase().includes(pathFilterTrim.toLowerCase()),
      )
    : cbcSessions;

  return (
    <>
      {/* Loading */}
      {projectsLoading && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          Loading projects...
        </div>
      )}

      {/* Empty projects */}
      {!projectsLoading && drives.length === 0 && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          No projects found
        </div>
      )}

      {/* Drive select */}
      {!projectsLoading && drives.length > 0 && (
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">Drive</span>
          <select
            value={selectedDrive ?? ''}
            onChange={(e) =>
              onDriveChange(e.target.value || null)
            }
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
          >
            <option value="">All drives</option>
            {drives.map((d) => (
              <option key={d} value={d}>
                {d} ({driveSessionCount(d)})
              </option>
            ))}
          </select>
        </label>
      )}

      {/* Project select */}
      {!projectsLoading && drives.length > 0 && (
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">
            Project
          </span>
          <select
            value={selectedProject ?? ''}
            onChange={(e) => onProjectChange(e.target.value)}
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
          >
            <option value="">Select a project</option>
            {filteredProjects.map((p) => (
              <option key={p.project_dir} value={p.project_dir}>
                {p.short_label}{' '}
                {p.session_count > 0 && `(${p.session_count})`}
                {p.resumable_count !== undefined &&
                  p.resumable_count > 0 &&
                  ` — ${p.resumable_count} resumable`}
              </option>
            ))}
          </select>
        </label>
      )}

      {/* No project selected hint */}
      {!projectsLoading &&
        drives.length > 0 &&
        !selectedProject && (
          <div className="py-2 text-center text-sm text-text-tertiary">
            Select a project
          </div>
        )}

      {/* Loading sessions */}
      {selectedProject && cbcLoading && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          Loading...
        </div>
      )}

      {/* Empty sessions */}
      {selectedProject &&
        !cbcLoading &&
        cbcSessions.length === 0 && (
          <div className="py-2 text-center text-sm text-text-tertiary">
            No sessions in this project
          </div>
        )}

      {/* Front-end path filter */}
      {selectedProject && !cbcLoading && cbcSessions.length > 0 && (
        <input
          type="text"
          value={pathFilter}
          onChange={(e) => setPathFilter(e.target.value)}
          placeholder="Filter by path (project_dir)…"
          className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent"
        />
      )}

      {/* No sessions match the path filter */}
      {selectedProject &&
        !cbcLoading &&
        cbcSessions.length > 0 &&
        visibleSessions.length === 0 && (
          <div className="py-2 text-center text-sm text-text-tertiary">
            No sessions match “{pathFilterTrim}”
          </div>
        )}

      {/* Session list */}
      {selectedProject &&
        !cbcLoading &&
        visibleSessions.length > 0 && (
          <div className="max-h-64 overflow-y-auto space-y-1 rounded border border-border-muted bg-bg-primary p-1">
            {visibleSessions.map((item) => (
              <CbcSessionItemRow
                key={item.session_id}
                item={item}
                isImporting={importingId === item.session_id}
                onClick={() => onImport(item)}
              />
            ))}
          </div>
        )}
    </>
  );
}

// ──────────────────────────────────────────
// CBC Session Item
// ──────────────────────────────────────────

function CbcSessionItemRow({
  item,
  isImporting,
  onClick,
}: {
  item: CbcSessionItem;
  isImporting: boolean;
  onClick: () => void;
}) {
  const ts = item.last_timestamp || item.first_timestamp || '';
  const isFork = !!item.forked_from;

  return (
    <div
      onClick={onClick}
      className={`rounded px-2.5 py-2 cursor-pointer transition-colors hover:bg-bg-tertiary ${
        isImporting ? 'opacity-50 pointer-events-none' : ''
      }`}
      title={
        isFork
          ? `Forked from: ${item.forked_from}`
          : undefined
      }
    >
      <div className="flex items-center gap-1.5">
        <span className="text-sm text-text-primary truncate">
          {item.title}
        </span>
        {isFork && (
          <span className="shrink-0 rounded bg-warning/20 px-1 text-[10px] font-medium text-warning">
            fork
          </span>
        )}
      </div>
      <div className="text-xs text-text-tertiary mt-0.5">
        {item.message_count} msgs &middot; {item.model} &middot;{' '}
        {ts ? formatTime(ts) : '—'}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// Kimi Section
// ──────────────────────────────────────────

interface KimiSectionProps {
  workspacesLoading: boolean;
  workspaces: KimiWorkspace[];
  selectedWorkspace: string | null;
  onWorkspaceChange: (id: string) => void;
  kimiSessions: KimiSessionItem[];
  kimiLoading: boolean;
  importingId: string | null;
  onImport: (item: KimiSessionItem) => void;
}

function KimiSection({
  workspacesLoading,
  workspaces,
  selectedWorkspace,
  onWorkspaceChange,
  kimiSessions,
  kimiLoading,
  importingId,
  onImport,
}: KimiSectionProps) {
  // Front-end path filter: narrow the session list by the session's workDir.
  const [pathFilter, setPathFilter] = useState('');
  const pathFilterTrim = pathFilter.trim();
  const visibleSessions = pathFilterTrim
    ? kimiSessions.filter((s) =>
        s.workDir.toLowerCase().includes(pathFilterTrim.toLowerCase()),
      )
    : kimiSessions;

  return (
    <>
      {/* Loading */}
      {workspacesLoading && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          Loading workspaces...
        </div>
      )}

      {/* Empty workspaces */}
      {!workspacesLoading && workspaces.length === 0 && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          No workspaces found
        </div>
      )}

      {/* Workspace select */}
      {!workspacesLoading && workspaces.length > 0 && (
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-text-secondary">
            Workspace
          </span>
          <select
            value={selectedWorkspace ?? ''}
            onChange={(e) => onWorkspaceChange(e.target.value)}
            className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
          >
            <option value="">Select a workspace</option>
            {workspaces.map((w) => (
              <option key={w.workspace_id} value={w.root}>
                {w.name} ({w.session_count})
              </option>
            ))}
          </select>
        </label>
      )}

      {/* No workspace selected hint */}
      {!workspacesLoading &&
        workspaces.length > 0 &&
        !selectedWorkspace && (
          <div className="py-2 text-center text-sm text-text-tertiary">
            Select a workspace
          </div>
        )}

      {/* Loading sessions */}
      {selectedWorkspace && kimiLoading && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          Loading...
        </div>
      )}

      {/* Empty sessions */}
      {selectedWorkspace &&
        !kimiLoading &&
        kimiSessions.length === 0 && (
          <div className="py-2 text-center text-sm text-text-tertiary">
            No sessions in this workspace
          </div>
        )}

      {/* Front-end path filter */}
      {selectedWorkspace && !kimiLoading && kimiSessions.length > 0 && (
        <input
          type="text"
          value={pathFilter}
          onChange={(e) => setPathFilter(e.target.value)}
          placeholder="Filter by path (workDir)…"
          className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent"
        />
      )}

      {/* No sessions match the path filter */}
      {selectedWorkspace &&
        !kimiLoading &&
        kimiSessions.length > 0 &&
        visibleSessions.length === 0 && (
          <div className="py-2 text-center text-sm text-text-tertiary">
            No sessions match “{pathFilterTrim}”
          </div>
        )}

      {/* Session list */}
      {selectedWorkspace &&
        !kimiLoading &&
        visibleSessions.length > 0 && (
          <div className="max-h-64 overflow-y-auto space-y-1 rounded border border-border-muted bg-bg-primary p-1">
            {visibleSessions.map((item) => (
              <KimiSessionItemRow
                key={item.session_id}
                item={item}
                isImporting={importingId === item.session_id}
                onClick={() => onImport(item)}
              />
            ))}
          </div>
        )}
    </>
  );
}

// ──────────────────────────────────────────
// Kimi Session Item
// ──────────────────────────────────────────

function KimiSessionItemRow({
  item,
  isImporting,
  onClick,
}: {
  item: KimiSessionItem;
  isImporting: boolean;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={`rounded px-2.5 py-2 cursor-pointer transition-colors hover:bg-bg-tertiary ${
        isImporting ? 'opacity-50 pointer-events-none' : ''
      }`}
    >
      <div className="text-sm text-text-primary truncate">{item.title}</div>
      <div className="text-xs text-text-tertiary mt-0.5">
        {item.message_count} msgs &middot; {item.model} &middot;{' '}
        {item.updatedAt ? formatTime(item.updatedAt) : '—'}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// OpenCode Section
// ──────────────────────────────────────────

interface OpencodeSectionProps {
  cwd: string;
  onCwdChange: (cwd: string) => void;
  sessions: OpencodeSessionItem[];
  loading: boolean;
  importingId: string | null;
  onLoad: () => void;
  onImport: (item: OpencodeSessionItem) => void;
}

function OpencodeSection({
  cwd,
  onCwdChange,
  sessions,
  loading,
  importingId,
  onLoad,
  onImport,
}: OpencodeSectionProps) {
  return (
    <>
      {/* Working-directory filter (opencode has no workspace list) */}
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-text-secondary">
          Working Directory{' '}
          <span className="font-normal text-text-tertiary">
            (filter, optional)
          </span>
        </span>
        <div className="flex gap-2">
          <input
            type="text"
            value={cwd}
            onChange={(e) => onCwdChange(e.target.value)}
            placeholder="/path/to/opencode/project"
            className="flex-1 rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent"
          />
          <Button variant="secondary" size="sm" onClick={onLoad}>
            Load
          </Button>
        </div>
      </label>

      {/* Loading */}
      {loading && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          Loading opencode sessions...
        </div>
      )}

      {/* Empty */}
      {!loading && sessions.length === 0 && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          No opencode sessions found
        </div>
      )}

      {/* Session list */}
      {!loading && sessions.length > 0 && (
        <div className="max-h-64 overflow-y-auto space-y-1 rounded border border-border-muted bg-bg-primary p-1">
          {sessions.map((item) => (
            <OpencodeSessionItemRow
              key={item.session_id}
              item={item}
              isImporting={importingId === item.session_id}
              onClick={() => onImport(item)}
            />
          ))}
        </div>
      )}
    </>
  );
}

// ──────────────────────────────────────────
// OpenCode Session Item
// ──────────────────────────────────────────

function OpencodeSessionItemRow({
  item,
  isImporting,
  onClick,
}: {
  item: OpencodeSessionItem;
  isImporting: boolean;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={`rounded px-2.5 py-2 cursor-pointer transition-colors hover:bg-bg-tertiary ${
        isImporting ? 'opacity-50 pointer-events-none' : ''
      }`}
    >
      <div className="text-sm text-text-primary truncate">
        {item.title || 'Untitled'}
      </div>
      <div className="text-xs text-text-tertiary mt-0.5">
        {item.message_count} msgs &middot; {item.model || '?'} &middot;{' '}
        {item.updatedAt ? formatTime(item.updatedAt) : '—'}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// Codex Section
// ──────────────────────────────────────────

interface CodexSectionProps {
  cwd: string;
  onCwdChange: (cwd: string) => void;
  sessions: CodexSessionItem[];
  loading: boolean;
  importingId: string | null;
  onLoad: () => void;
  onImport: (item: CodexSessionItem) => void;
}

function CodexSection({
  cwd,
  onCwdChange,
  sessions,
  loading,
  importingId,
  onLoad,
  onImport,
}: CodexSectionProps) {
  return (
    <>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-text-secondary">
          Working Directory{' '}
          <span className="font-normal text-text-tertiary">
            (filter, optional)
          </span>
        </span>
        <div className="flex gap-2">
          <input
            type="text"
            value={cwd}
            onChange={(e) => onCwdChange(e.target.value)}
            placeholder="/path/to/project"
            className="flex-1 rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent"
          />
          <Button variant="secondary" size="sm" onClick={onLoad}>
            Load
          </Button>
        </div>
      </label>

      {loading && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          Loading Codex sessions...
        </div>
      )}

      {!loading && sessions.length === 0 && (
        <div className="py-4 text-center text-sm text-text-tertiary">
          No Codex sessions found
        </div>
      )}

      {!loading && sessions.length > 0 && (
        <div className="max-h-64 overflow-y-auto space-y-1 rounded border border-border-muted bg-bg-primary p-1">
          {sessions.map((item) => (
            <CodexSessionItemRow
              key={item.session_id}
              item={item}
              isImporting={importingId === item.session_id}
              onClick={() => onImport(item)}
            />
          ))}
        </div>
      )}
    </>
  );
}

function CodexSessionItemRow({
  item,
  isImporting,
  onClick,
}: {
  item: CodexSessionItem;
  isImporting: boolean;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={`rounded px-2.5 py-2 cursor-pointer transition-colors hover:bg-bg-tertiary ${
        isImporting ? 'opacity-50 pointer-events-none' : ''
      }`}
    >
      <div className="text-sm text-text-primary truncate">
        {item.title || 'Untitled'}
      </div>
      <div className="text-xs text-text-tertiary mt-0.5">
        {item.message_count} msgs &middot; {item.model || '?'} &middot;{' '}
        {item.workDir || '—'} &middot;{' '}
        {item.updatedAt ? formatTime(item.updatedAt) : '—'}
      </div>
    </div>
  );
}

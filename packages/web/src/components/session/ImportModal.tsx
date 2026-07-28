import { useEffect, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type {
  CbcProject,
  CbcSessionItem,
  KimiWorkspace,
  KimiSessionItem,
} from '@/types';
import {
  fetchCbcProjects,
  fetchCbcSessions,
  importCbcSession,
  fetchKimiWorkspaces,
  fetchKimiSessions,
  importKimiSession,
} from '@/services/api';

interface ImportModalProps {
  open: boolean;
  onClose: () => void;
}

type Adapter = 'cbc' | 'kimi';

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

export function ImportModal({ open, onClose }: ImportModalProps) {
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const selectSession = useSessionStore((s) => s.selectSession);
  const showToast = useUIStore((s) => s.showToast);

  const [adapter, setAdapter] = useState<Adapter>('cbc');

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

  // ── Import state ──
  const [importingId, setImportingId] = useState<string | null>(null);

  // ── Reset on open/close ──
  useEffect(() => {
    if (!open) return;
    setAdapter('cbc');
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
    setImportingId(null);
  }, [open]);

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
  return (
    <Modal open={open} onClose={onClose} title="Import Session" size="lg">
      <div className="flex flex-col gap-4">
        {/* Adapter selector */}
        <div className="flex gap-1 rounded border border-border-muted bg-bg-tertiary p-0.5">
          <button
            type="button"
            onClick={() => {
              setAdapter('cbc');
              setSelectedDrive(null);
              setSelectedProject(null);
              setCbcSessions([]);
            }}
            className={`flex-1 rounded px-3 py-1 text-xs font-medium transition-colors ${
              adapter === 'cbc'
                ? 'bg-accent text-white'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            cbc
          </button>
          <button
            type="button"
            onClick={() => {
              setAdapter('kimi');
              setSelectedWorkspace(null);
              setKimiSessions([]);
            }}
            className={`flex-1 rounded px-3 py-1 text-xs font-medium transition-colors ${
              adapter === 'kimi'
                ? 'bg-accent text-white'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            kimi
          </button>
        </div>

        <hr className="border-border-muted" />

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

      {/* Session list */}
      {selectedProject &&
        !cbcLoading &&
        cbcSessions.length > 0 && (
          <div className="max-h-64 overflow-y-auto space-y-1 rounded border border-border-muted bg-bg-primary p-1">
            {cbcSessions.map((item) => (
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

      {/* Session list */}
      {selectedWorkspace &&
        !kimiLoading &&
        kimiSessions.length > 0 && (
          <div className="max-h-64 overflow-y-auto space-y-1 rounded border border-border-muted bg-bg-primary p-1">
            {kimiSessions.map((item) => (
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

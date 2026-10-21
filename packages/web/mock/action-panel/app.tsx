import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { SearchInput } from '@/components/ui/SearchInput';
import type { Action, ActionGroup, ActionRegistry } from '@/types/actions';
import type { McpServerInfo, PluginBinding, PluginMode } from '@/types';
import {
  buildBlockers,
  fixtureSessions,
  fixtureServers,
  initialRegistry,
  mockActionId,
  mockGroupId,
  mockVersion,
  type MockSession,
} from './fixtures';
import {
  EFFECT_LABELS,
  actionRoute,
  effectTone,
  emptyActionDraft,
  executionLabel,
  formatTime,
  groupEditorState,
  isEditorDirty,
  pluginIdFor,
  shortVersion,
  usedByGroups,
  actionEditorState,
  type EditorState,
} from './model';
import { ActionEditorPage, GroupEditorPage } from './editors';
import { UseInSessionDialog } from './use-in-session';
import {
  Badge,
  ConfirmDialog,
  IconButton,
  IconTile,
  ListEmpty,
  LoadError,
  Segmented,
  SkeletonList,
  StaleBanner,
  cx,
} from './ui';
import {
  AlertTriangle,
  ArrowUpDown,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  FlaskConical,
  Folder,
  Hammer,
  Import,
  Info,
  Braces,
  Layers,
  Lock,
  Menu,
  Moon,
  MoreHorizontal,
  PackageOpen,
  PanelLeftClose,
  PanelsTopLeft,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  SlidersHorizontal,
  Sun,
  TerminalSquare,
  Trash2,
  Waves,
  X,
} from 'lucide-react';

/* ── Preview bootstrap ─────────────────────────────────────────────────── */

const BOOTSTRAP = readParams();

function readParams() {
  if (typeof window === 'undefined') {
    return { view: '', id: '', state: 'ready', theme: 'dark', ribbon: true, controls: false };
  }
  const params = new URLSearchParams(window.location.search);
  const view = params.get('view') ?? '';
  const state = params.get('state') ?? 'ready';
  return {
    view,
    id: params.get('id') ?? '',
    state: ['ready', 'loading', 'error', 'empty', 'stale'].includes(state) ? state : 'ready',
    theme: params.get('theme') === 'light' ? 'light' : 'dark',
    ribbon: params.get('ribbon') !== '0',
    controls: params.get('controls') === '1',
    hideControls: params.get('controls') === '0',
  };
}

type PreviewState = 'ready' | 'loading' | 'error' | 'empty' | 'stale';
type Tab = 'actions' | 'groups';
type ToastTone = 'success' | 'danger' | 'warning' | 'neutral';
type Toast = { id: number; title: string; detail?: string; tone: ToastTone };
type ConfirmState =
  | { kind: 'discard' }
  | { kind: 'delete-action'; action: Action }
  | { kind: 'delete-group'; group: ActionGroup };

function cloneRegistry(source: ActionRegistry): ActionRegistry {
  return structuredClone(source);
}

function bootstrapEditor(): EditorState | null {
  if (BOOTSTRAP.view === 'new-action') return actionEditorState(emptyActionDraft(), 'new');
  if (BOOTSTRAP.view === 'edit-action') {
    const action = initialRegistry.actions.find((item) => item.id === BOOTSTRAP.id) ?? initialRegistry.actions[0];
    return action ? actionEditorState(action, 'edit') : null;
  }
  if (BOOTSTRAP.view === 'edit-group') {
    const group = initialRegistry.groups.find((item) => item.id === BOOTSTRAP.id) ?? initialRegistry.groups[0];
    return group ? groupEditorState(group, 'edit') : null;
  }
  return null;
}

function bootstrapTab(): Tab {
  return BOOTSTRAP.view === 'groups' || BOOTSTRAP.view === 'edit-group' || BOOTSTRAP.view === 'use'
    ? 'groups'
    : 'actions';
}

function bootstrapUseInSession(): string | null {
  if (BOOTSTRAP.view !== 'use') return null;
  const group = initialRegistry.groups.find((item) => item.id === BOOTSTRAP.id) ?? initialRegistry.groups[0];
  return group?.build ? group.id : null;
}

/* ── App ────────────────────────────────────────────────────────────────── */

export function ActionPanelMockApp() {
  const mobile = useIsMobile();
  const [theme, setTheme] = useState<'dark' | 'light'>(BOOTSTRAP.theme as 'dark' | 'light');
  const [registry, setRegistry] = useState<ActionRegistry>(() => cloneRegistry(initialRegistry));
  const [sessions, setSessions] = useState<MockSession[]>(() => structuredClone(fixtureSessions));
  const servers: McpServerInfo[] = fixtureServers;
  const [tab, setTab] = useState<Tab>(bootstrapTab);
  const [previewState, setPreviewState] = useState<PreviewState>(BOOTSTRAP.state as PreviewState);
  const [editor, setEditor] = useState<EditorState | null>(bootstrapEditor);
  const [useInSessionGroupId, setUseInSessionGroupId] = useState<string | null>(bootstrapUseInSession);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [lastLoaded, setLastLoaded] = useState(() => new Date());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [controlOpen, setControlOpen] = useState(BOOTSTRAP.controls);
  const [toast, setToast] = useState<Toast | null>(null);
  const toastId = useRef(0);

  const notify = useCallback((title: string, detail?: string, tone: ToastTone = 'success') => {
    toastId.current += 1;
    setToast({ id: toastId.current, title, detail, tone });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (!mobile) setSidebarOpen(false);
  }, [mobile]);

  useEffect(() => {
    if (!editor || !isEditorDirty(editor)) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [editor]);

  const notifyMockOnly = useCallback(
    (what: string) => notify('Preview only', `${what} is disabled in this interactive mock.`, 'neutral'),
    [notify],
  );

  function copyText(value: string, label: string) {
    void navigator.clipboard
      ?.writeText(value)
      .then(() => notify('Copied', `${label} copied to clipboard.`, 'neutral'))
      .catch(() => notify('Copy unavailable', 'Select the value and copy it manually.', 'warning'));
  }

  function openAction(action: Action) {
    setPreviewState('ready');
    setEditor(actionEditorState(action, 'edit'));
  }

  function newAction() {
    setPreviewState('ready');
    setTab('actions');
    setEditor(actionEditorState(emptyActionDraft(), 'new'));
  }

  function openGroup(group: ActionGroup) {
    setPreviewState('ready');
    setEditor(groupEditorState({ id: group.id, name: group.name, action_ids: [...group.action_ids] }, 'edit'));
  }

  function newGroup() {
    setPreviewState('ready');
    setTab('groups');
    setEditor(groupEditorState({ id: '', name: '', action_ids: [] }, 'new'));
  }

  function requestCloseEditor() {
    if (!editor) return;
    if (isEditorDirty(editor)) {
      setConfirm({ kind: 'discard' });
      return;
    }
    closeEditor();
  }

  function closeEditor() {
    const kind = editor?.kind;
    setEditor(null);
    if (kind === 'group') setTab('groups');
    if (kind === 'action') setTab('actions');
  }

  function saveAction() {
    if (!editor || editor.kind !== 'action') return;
    const { draft, schemaError } = editor;
    if (!draft.name.trim() || !draft.description.trim() || schemaError) {
      notify('Can’t save this Action', 'Name, description and a valid input schema are required.', 'danger');
      return;
    }
    setBusy(true);
    window.setTimeout(() => {
      if (editor.mode === 'new') {
        const item: Action = { ...structuredClone(draft), id: mockActionId() };
        setRegistry((previous) => ({
          ...previous,
          revision: previous.revision + 1,
          actions: [...previous.actions, item],
        }));
        notify('Action created', `${item.name} · ${item.id}`, 'success');
      } else {
        const item: Action = { ...structuredClone(draft), id: editor.originalId ?? draft.id };
        setRegistry((previous) => ({
          ...previous,
          revision: previous.revision + 1,
          actions: previous.actions.map((action) => (action.id === item.id ? item : action)),
        }));
        notify('Action saved', `${item.name} · revision ${registry.revision + 1}`, 'success');
      }
      setLastLoaded(new Date());
      setBusy(false);
      closeEditor();
    }, 220);
  }

  function saveGroup() {
    if (!editor || editor.kind !== 'group') return;
    if (!editor.draft.name.trim()) {
      notify('Can’t save this Group', 'A Group name is required.', 'danger');
      return;
    }
    setBusy(true);
    window.setTimeout(() => {
      if (editor.mode === 'new') {
        const item: ActionGroup = {
          id: mockGroupId(),
          name: editor.draft.name.trim(),
          action_ids: [...editor.draft.action_ids],
          blockers: [],
          sessions: [],
        };
        setRegistry((previous) => ({
          ...previous,
          revision: previous.revision + 1,
          groups: [...previous.groups, item],
        }));
        notify('Group created', `${item.name} · ${item.id}`, 'success');
      } else {
        const id = editor.originalId ?? editor.draft.id;
        setRegistry((previous) => ({
          ...previous,
          revision: previous.revision + 1,
          groups: previous.groups.map((group) =>
            group.id === id
              ? { ...group, name: editor.draft.name.trim(), action_ids: [...editor.draft.action_ids] }
              : group,
          ),
        }));
        notify('Group saved', `${editor.draft.name.trim()} · revision ${registry.revision + 1}`, 'success');
      }
      setLastLoaded(new Date());
      setBusy(false);
      closeEditor();
    }, 220);
  }

  function buildGroup(groupId: string) {
    const group = registry.groups.find((item) => item.id === groupId);
    if (!group) return;
    const gate = buildBlockers(group.action_ids, registry.actions);
    if (gate) {
      notify('Build blocked', gate.message, 'danger');
      return;
    }
    setBusy(true);
    window.setTimeout(() => {
      const plugin_id = pluginIdFor(group.id);
      const version = mockVersion();
      setRegistry((previous) => ({
        ...previous,
        revision: previous.revision + 1,
        groups: previous.groups.map((item) =>
          item.id === groupId
            ? {
                ...item,
                build: { plugin_id, version, action_ids: [...item.action_ids] },
                blockers: [],
              }
            : item,
        ),
      }));
      setLastLoaded(new Date());
      setBusy(false);
      notify('Group built', `${group.name} · ${plugin_id}`, 'success');
    }, 260);
  }

  function unbuildGroup(groupId: string) {
    const group = registry.groups.find((item) => item.id === groupId);
    if (!group) return;
    if (!group.build) {
      notify('Not built', 'This Group has no registered release to remove.', 'warning');
      return;
    }
    if (group.blockers.length) {
      notify(
        'Unbuild blocked',
        `${group.blockers.length} running ${group.blockers.length === 1 ? 'Session has' : 'Sessions have'} this Group enabled.`,
        'danger',
      );
      return;
    }
    setBusy(true);
    window.setTimeout(() => {
      setRegistry((previous) => ({
        ...previous,
        revision: previous.revision + 1,
        groups: previous.groups.map((item) =>
          item.id === groupId ? { ...item, build: undefined, sessions: [], blockers: [] } : item,
        ),
      }));
      setSessions((previous) =>
        previous.map((session) => ({
          ...session,
          pluginBindings: session.pluginBindings.filter(
            (binding) => binding.plugin_id !== pluginIdFor(groupId),
          ),
        })),
      );
      setLastLoaded(new Date());
      setBusy(false);
      notify('Group unbuilt', `${group.name} · Session bindings removed (mock)`, 'success');
    }, 240);
  }

  function requestDeleteAction(action: Action) {
    const usage = usedByGroups(registry, action.id);
    if (usage.length) {
      notify(
        'Action is in use',
        `Remove it from ${usage.map((group) => group.name).join(', ')} before deleting.`,
        'danger',
      );
      return;
    }
    setConfirm({ kind: 'delete-action', action });
  }

  function requestDeleteGroup(group: ActionGroup) {
    if (group.build) {
      notify('Group is registered', 'Unbuild the Group before deleting it.', 'danger');
      return;
    }
    setConfirm({ kind: 'delete-group', group });
  }

  function refresh() {
    setRefreshing(true);
    window.setTimeout(() => {
      setRefreshing(false);
      setPreviewState('ready');
      setLastLoaded(new Date());
      notify('Registry refreshed', `Mock refresh · revision ${registry.revision}`, 'success');
    }, 650);
  }

  function applyBinding(sessionId: string, binding: PluginBinding) {
    const groupId = useInSessionGroupId;
    if (!groupId) return;
    const group = registry.groups.find((item) => item.id === groupId);
    const session = sessions.find((item) => item.id === sessionId);
    if (!group?.build || !session) return;
    const updatedSessions = sessions.map((item) =>
      item.id === sessionId
        ? {
            ...item,
            pluginBindings: [
              ...item.pluginBindings.filter((entry) => entry.plugin_id !== binding.plugin_id),
              { ...binding },
            ],
            pluginSelectionRevision: item.pluginSelectionRevision + 1,
            pluginApplyStatus: item.status === 'running' ? ('next_start' as const) : ('applied' as const),
          }
        : item,
    );
    setSessions(updatedSessions);
    setRegistry((previous) => ({
      ...previous,
      groups: previous.groups.map((item) => {
        if (item.id !== groupId) return item;
        const existing = item.sessions.find((entry) => entry.id === sessionId);
        const nextSessions = [
          ...item.sessions.filter((entry) => entry.id !== sessionId),
          {
            id: session.id,
            name: session.name,
            loadedVersion: existing?.loadedVersion ?? null,
            ...binding,
          },
        ];
        const blockers = nextSessions
          .filter(
            (entry) =>
              entry.enabled &&
              updatedSessions.find((candidate) => candidate.id === entry.id)?.status === 'running',
          )
          .map((entry) => ({ id: entry.id, name: entry.name }));
        return { ...item, sessions: nextSessions, blockers };
      }),
    }));
    setUseInSessionGroupId(null);
    notify(
      binding.enabled ? 'Binding saved (mock)' : 'Binding disabled (mock)',
      `${session.name} · ${MODE_LABEL[binding.mode]}${session.status === 'running' ? ' · applies after idle' : ''}`,
      'success',
    );
  }

  function resetFixtures() {
    setRegistry(cloneRegistry(initialRegistry));
    setSessions(structuredClone(fixtureSessions));
    setPreviewState('ready');
    setEditor(null);
    setConfirm(null);
    setUseInSessionGroupId(null);
    setTab('actions');
    setLastLoaded(new Date());
    setBusy(false);
    notify('Preview reset', 'Local fixtures restored. No external state changed.', 'neutral');
  }

  // Keyboard: Ctrl/Cmd+S saves, Escape backs out. Dialogs own Escape while open.
  useEffect(() => {
    if (!editor || confirm || useInSessionGroupId) return;
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        if (editor.kind === 'action') saveAction();
        else saveGroup();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        requestCloseEditor();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });

  const actionCount = previewState === 'empty' ? 0 : registry.actions.length;
  const groupCount = previewState === 'empty' ? 0 : registry.groups.length;
  const useGroup = registry.groups.find((group) => group.id === useInSessionGroupId) ?? null;
  const liveGroup =
    editor?.kind === 'group' && editor.originalId
      ? (registry.groups.find((group) => group.id === editor.originalId) ?? null)
      : null;
  const shellHeight = BOOTSTRAP.ribbon ? 'calc(100dvh - 28px)' : '100dvh';

  return (
    <div className="flex flex-col overflow-hidden bg-bg-primary text-text-primary" style={{ height: '100dvh' }}>
      {BOOTSTRAP.ribbon && <PreviewRibbon />}
      <div className="relative flex min-h-0 flex-1 overflow-hidden" style={{ height: shellHeight }}>
        {mobile && sidebarOpen && (
          <button
            type="button"
            aria-label="Close sidebar overlay"
            className="fixed inset-x-0 bottom-0 z-30 cursor-default bg-black/50"
            style={{ top: BOOTSTRAP.ribbon ? 28 : 0 }}
            onClick={() => setSidebarOpen(false)}
          />
        )}
        <PanSidebar
          mobile={mobile}
          open={sidebarOpen}
          ribbon={BOOTSTRAP.ribbon}
          theme={theme}
          onToggleTheme={() => setTheme((value) => (value === 'dark' ? 'light' : 'dark'))}
          onClose={() => setSidebarOpen(false)}
          onMockAction={notifyMockOnly}
        />
        <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
          {mobile && !sidebarOpen && !editor && (
            <button
              type="button"
              aria-label="Open sidebar"
              onClick={() => setSidebarOpen(true)}
              className="fixed left-1 z-40 rounded border border-border-default bg-bg-tertiary p-1.5 text-text-primary shadow-sm"
              style={{ top: BOOTSTRAP.ribbon ? 36 : 8 }}
            >
              <Menu size={16} />
            </button>
          )}
          {editor ? (
            editor.kind === 'action' ? (
              <ActionEditorPage
                state={editor}
                groups={registry.groups}
                busy={busy}
                onChange={setEditor}
                onSave={saveAction}
                onCancel={requestCloseEditor}
                onDelete={() => requestDeleteAction(editor.draft)}
              />
            ) : (
              <GroupEditorPage
                state={editor}
                liveGroup={liveGroup}
                actions={registry.actions}
                busy={busy}
                onChange={setEditor}
                onSave={saveGroup}
                onCancel={requestCloseEditor}
                onDelete={() => liveGroup && requestDeleteGroup(liveGroup)}
                onBuild={() => liveGroup && buildGroup(liveGroup.id)}
                onUnbuild={() => liveGroup && unbuildGroup(liveGroup.id)}
                onUseInSession={() => liveGroup && setUseInSessionGroupId(liveGroup.id)}
              />
            )
          ) : (
            <RegistryPage
              tab={tab}
              onTab={setTab}
              registry={registry}
              previewState={previewState}
              refreshing={refreshing}
              lastLoaded={lastLoaded}
              mobile={mobile}
              sidebarOpen={sidebarOpen}
              onRefresh={refresh}
              onNewAction={newAction}
              onNewGroup={newGroup}
              onEditAction={openAction}
              onEditGroup={openGroup}
              onDeleteAction={requestDeleteAction}
              onDeleteGroup={requestDeleteGroup}
              onBuild={buildGroup}
              onUnbuild={unbuildGroup}
              onUseInSession={(group) => setUseInSessionGroupId(group.id)}
              onCopy={copyText}
              onRetry={() => {
                setPreviewState('ready');
                notify('Retry complete (mock)', 'The fixture registry is available again.', 'success');
              }}
              counts={{ actions: actionCount, groups: groupCount }}
            />
          )}
        </main>
      </div>

      {confirm?.kind === 'discard' && (
        <ConfirmDialog
          title="Discard unsaved changes?"
          message={
            <>
              Your draft has unsaved changes. Leaving now discards them and returns to the list.
            </>
          }
          confirmLabel="Discard draft"
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            closeEditor();
            notify('Draft discarded', 'No changes were saved.', 'neutral');
          }}
        />
      )}
      {confirm?.kind === 'delete-action' && (
        <ConfirmDialog
          title={`Delete “${confirm.action.name}”?`}
          message={
            <>
              This mock removes the Action from the local registry. Real Pan blocks deleting an Action
              that is still referenced by a Group; this fixture checks the same rule first.
            </>
          }
          confirmLabel="Delete Action"
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            const action = confirm.action;
            setRegistry((previous) => ({
              ...previous,
              revision: previous.revision + 1,
              actions: previous.actions.filter((item) => item.id !== action.id),
            }));
            setConfirm(null);
            setEditor(null);
            setTab('actions');
            setLastLoaded(new Date());
            notify('Action deleted (mock)', action.name, 'neutral');
          }}
        />
      )}
      {confirm?.kind === 'delete-group' && (
        <ConfirmDialog
          title={`Delete “${confirm.group.name}”?`}
          message={
            <>
              This mock removes the Group from the local registry. Built Groups must be unbuilt first;
              this fixture checks the same gate.
            </>
          }
          confirmLabel="Delete Group"
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            const group = confirm.group;
            setRegistry((previous) => ({
              ...previous,
              revision: previous.revision + 1,
              groups: previous.groups.filter((item) => item.id !== group.id),
            }));
            setConfirm(null);
            setEditor(null);
            setTab('groups');
            setLastLoaded(new Date());
            notify('Group deleted (mock)', group.name, 'neutral');
          }}
        />
      )}

      {useGroup && (
        <UseInSessionDialog
          group={useGroup}
          sessions={sessions}
          servers={servers}
          onClose={() => setUseInSessionGroupId(null)}
          onApply={applyBinding}
        />
      )}

      {toast && <ToastCard toast={toast} onClose={() => setToast(null)} />}

      {!editor && !BOOTSTRAP.hideControls && (
        <PreviewControls
          open={controlOpen}
          onOpenChange={setControlOpen}
          state={previewState}
          onState={(value) => {
            setPreviewState(value);
            if (value === 'ready') setLastLoaded(new Date());
          }}
          theme={theme}
          onTheme={setTheme}
          onReset={resetFixtures}
        />
      )}
    </div>
  );
}

/* ── Sidebar shell ─────────────────────────────────────────────────────── */

function PanSidebar({
  mobile,
  open,
  ribbon,
  theme,
  onToggleTheme,
  onClose,
  onMockAction,
}: {
  mobile: boolean;
  open: boolean;
  ribbon: boolean;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
  onClose: () => void;
  onMockAction: (what: string) => void;
}) {
  const [sessionQuery, setSessionQuery] = useState('');
  const filtered = sessionsForSidebar.filter((session) => {
    const value = sessionQuery.trim().toLowerCase();
    return !value || session.name.toLowerCase().includes(value) || session.workdir.toLowerCase().includes(value);
  });
  return (
    <aside
      aria-label="Pan sidebar"
      inert={mobile && !open ? true : undefined}
      className={cx(
        'flex shrink-0 flex-col border-r border-border-default bg-bg-secondary',
        mobile
          ? cx(
              'fixed bottom-0 left-0 z-40 h-auto w-[min(280px,86vw)] transition-transform duration-200',
              open ? 'translate-x-0' : '-translate-x-full',
            )
          : 'relative h-full w-[280px]',
      )}
      style={mobile ? { top: ribbon ? 28 : 0 } : undefined}
    >
      <div className="shrink-0 border-b border-border-muted px-3 py-2">
        <div className="mb-2 flex items-center justify-between">
          <h1 className="text-lg font-bold text-text-primary">Pan</h1>
          <div className="flex items-center gap-0.5">
            <IconButton label="App settings (preview)" className="p-0.5" onClick={() => onMockAction('App settings')}>
              <Settings size={16} />
            </IconButton>
            <IconButton label="Toggle theme" className="p-0.5" onClick={onToggleTheme}>
              {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </IconButton>
            <IconButton
              label={mobile ? 'Close sidebar' : 'Collapse sidebar (preview)'}
              className="p-0.5"
              onClick={mobile ? onClose : () => onMockAction('Sidebar collapse')}
            >
              <PanelLeftClose size={16} />
            </IconButton>
          </div>
        </div>

        <div className="mb-2 flex min-w-0 items-center gap-1">
          <button
            type="button"
            aria-label="Previous view entries"
            disabled
            className="flex w-4 shrink-0 justify-center text-text-tertiary disabled:opacity-30"
          >
            <ChevronLeft size={14} />
          </button>
          <div className="grid min-w-0 flex-1 grid-cols-3">
            <SidebarViewEntry icon={<TerminalSquare size={12} />} label="Terminal" onClick={() => onMockAction('Terminal navigation')} />
            <span className="flex flex-col items-center justify-center gap-0.5 rounded bg-accent/20 py-1 text-xs font-medium text-accent">
              <span className="flex items-center gap-1">
                <PanelsTopLeft size={12} /> Actions
              </span>
            </span>
            <span aria-hidden="true" />
          </div>
          <button
            type="button"
            aria-label="Next view entries"
            disabled
            className="flex w-4 shrink-0 justify-center text-text-tertiary disabled:opacity-30"
          >
            <ChevronRight size={14} />
          </button>
        </div>

        <div className="flex gap-1">
          <div className="flex min-w-0 flex-[3] overflow-hidden rounded border border-transparent bg-accent">
            <Button
              type="button"
              variant="primary"
              size="sm"
              className="min-w-0 flex-1 rounded-none border-0"
              onClick={() => onMockAction('Session creation')}
            >
              <Plus size={14} /> New
            </Button>
            <span aria-hidden="true" className="w-px shrink-0 bg-white/30" />
            <Button
              type="button"
              variant="primary"
              size="sm"
              className="rounded-none border-0"
              title="New with settings (preview)"
              onClick={() => onMockAction('Session creation')}
            >
              <Settings size={14} />
            </Button>
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="flex-1"
            onClick={() => onMockAction('Session import')}
          >
            <Import size={14} /> Import
          </Button>
        </div>

        <div className="mt-2 flex items-center gap-1">
          <SearchInput value={sessionQuery} onChange={setSessionQuery} placeholder="Filter…" label="session" />
          <IconButton label="Filter Sessions (preview)" onClick={() => onMockAction('Session filters')}>
            <SlidersHorizontal size={13} />
          </IconButton>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[10px] text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary"
            onClick={() => onMockAction('Sort order')}
          >
            <ArrowUpDown size={11} /> recent <ChevronDown size={11} />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {filtered.map((session, index) => (
          <button
            type="button"
            key={session.id}
            onClick={() => onMockAction('Session selection')}
            className={cx(
              'w-full border-b border-border-default border-l-[3px] px-3 py-2 text-left transition-colors',
              index === 0
                ? 'border-l-accent bg-bg-tertiary/70'
                : 'border-l-transparent hover:bg-bg-tertiary/50',
            )}
          >
            <span className="flex items-center gap-1.5">
              <span
                className={cx(
                  'size-1.5 shrink-0 rounded-full',
                  session.status === 'running' ? 'bg-warning' : 'bg-success/70',
                )}
              />
              <span className="truncate text-sm font-medium text-text-primary">{session.name}</span>
              <span className="ml-auto shrink-0 rounded border border-border-default bg-bg-tertiary px-1 py-px text-[9px] text-text-tertiary">
                {session.adapter}
              </span>
            </span>
            <span className="mt-0.5 flex items-center gap-1 pl-3 text-[11px] text-text-tertiary">
              <Folder size={10} className="shrink-0" />
              <span className="truncate" title={session.workdir}>
                …/{session.workdir.split('/').slice(-2).join('/')}
              </span>
            </span>
            <span className="mt-1 flex items-center gap-2 pl-3 text-[10px] text-text-tertiary">
              <span className="flex items-center gap-1">
                <Clock size={10} /> {session.status === 'running' ? 'running' : 'idle'}
              </span>
              <span aria-hidden="true">·</span>
              <span className="flex items-center gap-1">
                <Waves size={10} /> {session.pluginBindings.length}
              </span>
              <span className="ml-auto rounded p-0.5">
                <Settings size={11} />
              </span>
            </span>
          </button>
        ))}
        {filtered.length === 0 && (
          <p className="px-3 py-6 text-center text-[11px] text-text-tertiary">No Sessions match this filter.</p>
        )}
      </div>

      <div className="shrink-0 border-t border-border-default px-3 py-2 text-[10px] leading-relaxed text-text-tertiary">
        <span className="rounded bg-warning/15 px-1 py-0.5 font-semibold uppercase tracking-wide text-warning">
          Mock preview
        </span>
        <span className="ml-1">Local fixtures. Session actions are disabled.</span>
      </div>
    </aside>
  );
}

function SidebarViewEntry({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex min-w-0 flex-col items-center justify-center gap-0.5 rounded py-1 text-xs text-text-tertiary transition-colors hover:bg-bg-hover hover:text-text-secondary"
    >
      <span className="flex items-center gap-1">
        {icon}
        <span className="truncate">{label}</span>
      </span>
    </button>
  );
}

const sessionsForSidebar = [
  ...fixtureSessions,
  {
    id: 'ses_9e4c17d0b283',
    name: 'Terminal reliability',
    adapter: 'codex' as const,
    status: 'idle' as const,
    workdir: 'D:/project/Pan/.validation/terminal',
    pluginBindings: [],
    pluginSelectionRevision: 1,
  },
];

/* ── Registry page ─────────────────────────────────────────────────────── */

function RegistryPage({
  tab,
  onTab,
  registry,
  previewState,
  refreshing,
  lastLoaded,
  mobile,
  sidebarOpen,
  onRefresh,
  onNewAction,
  onNewGroup,
  onEditAction,
  onEditGroup,
  onDeleteAction,
  onDeleteGroup,
  onBuild,
  onUnbuild,
  onUseInSession,
  onCopy,
  onRetry,
  counts,
}: {
  tab: Tab;
  onTab: (tab: Tab) => void;
  registry: ActionRegistry;
  previewState: PreviewState;
  refreshing: boolean;
  lastLoaded: Date;
  mobile: boolean;
  sidebarOpen: boolean;
  onRefresh: () => void;
  onNewAction: () => void;
  onNewGroup: () => void;
  onEditAction: (action: Action) => void;
  onEditGroup: (group: ActionGroup) => void;
  onDeleteAction: (action: Action) => void;
  onDeleteGroup: (group: ActionGroup) => void;
  onBuild: (groupId: string) => void;
  onUnbuild: (groupId: string) => void;
  onUseInSession: (group: ActionGroup) => void;
  onCopy: (value: string, label: string) => void;
  onRetry: () => void;
  counts: { actions: number; groups: number };
}) {
  const [actionQuery, setActionQuery] = useState('');
  const [groupQuery, setGroupQuery] = useState('');
  const [actionFilter, setActionFilter] = useState<'all' | 'enabled' | 'draft'>('all');
  const [groupFilter, setGroupFilter] = useState<'all' | 'built' | 'unbuilt'>('all');

  const filteredActions = registry.actions.filter((action) => {
    const value = actionQuery.trim().toLowerCase();
    const matches =
      !value ||
      action.name.toLowerCase().includes(value) ||
      action.description.toLowerCase().includes(value) ||
      action.id.toLowerCase().includes(value) ||
      actionRoute(action).toLowerCase().includes(value);
    if (!matches) return false;
    if (actionFilter === 'enabled') return action.enabled;
    if (actionFilter === 'draft') return !action.enabled;
    return true;
  });
  const filteredGroups = registry.groups.filter((group) => {
    const value = groupQuery.trim().toLowerCase();
    const actionNames = group.action_ids
      .map((id) => registry.actions.find((action) => action.id === id)?.name ?? '')
      .join(' ');
    const matches =
      !value ||
      group.name.toLowerCase().includes(value) ||
      group.id.toLowerCase().includes(value) ||
      actionNames.toLowerCase().includes(value);
    if (!matches) return false;
    if (groupFilter === 'built') return Boolean(group.build);
    if (groupFilter === 'unbuilt') return !group.build;
    return true;
  });

  const loading = previewState === 'loading';
  const empty = previewState === 'empty';
  const error = previewState === 'error';
  const query = tab === 'actions' ? actionQuery : groupQuery;
  const setQuery = tab === 'actions' ? setActionQuery : setGroupQuery;
  const filter = tab === 'actions' ? actionFilter : groupFilter;
  const setFilter =
    tab === 'actions'
      ? (value: string) => setActionFilter(value as typeof actionFilter)
      : (value: string) => setGroupFilter(value as typeof groupFilter);
  const visibleCount = empty ? 0 : tab === 'actions' ? filteredActions.length : filteredGroups.length;
  const totalCount = tab === 'actions' ? counts.actions : counts.groups;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-border-default bg-bg-primary">
        <div className="flex flex-wrap items-start justify-between gap-3 px-4 pb-3 pt-4 sm:px-6 sm:pt-5">
          <div className={cx('min-w-0', mobile && !sidebarOpen && 'pl-9')}>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold tracking-tight text-text-primary sm:text-2xl">
                Actions &amp; Groups
              </h1>
            </div>
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-text-secondary sm:text-sm">
              {tab === 'actions'
                ? 'Define an Action once. Share it across Groups, then build a Group to use it in a Session.'
                : 'Select Actions, build a release, then bind it to a Session in one of three modes.'}
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden flex-col items-end text-[10px] leading-tight text-text-tertiary md:flex">
              <span>Updated {formatTime(lastLoaded)}</span>
              <span className="font-mono">revision {empty ? 0 : registry.revision}</span>
            </span>
            <Button
              type="button"
              size="sm"
              onClick={onRefresh}
              disabled={refreshing || loading}
              title="Mock refresh — reads local fixtures only"
            >
              <RefreshCw size={13} className={refreshing ? 'animate-spin' : undefined} />
              <span>Refresh</span>
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 px-4 pb-3 sm:px-6">
          <Segmented
            label="Registry view"
            value={tab}
            options={[
              { value: 'actions' as const, label: 'Actions', count: loading ? undefined : counts.actions },
              { value: 'groups' as const, label: 'Groups', count: loading ? undefined : counts.groups },
            ]}
            onChange={onTab}
          />
          <span className="hidden text-[11px] text-text-tertiary lg:inline">
            {tab === 'actions'
              ? 'Actions are reusable definitions.'
              : 'Groups become MCP plugins when built.'}
          </span>
          <Button
            type="button"
            variant="primary"
            size="sm"
            className="ml-auto max-sm:w-full max-sm:justify-center"
            onClick={tab === 'actions' ? onNewAction : onNewGroup}
          >
            <Plus size={13} /> {tab === 'actions' ? 'New Action' : 'New Group'}
          </Button>
        </div>
      </header>

      <div className="mock-main min-h-0 flex-1 overflow-auto">
        <div className="sticky top-0 z-10 border-b border-border-muted bg-bg-primary/95 px-4 py-2.5 backdrop-blur sm:px-6">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-0 flex-1 basis-full sm:basis-56">
              <Search
                size={13}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-tertiary"
              />
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label={tab === 'actions' ? 'Search Actions' : 'Search Groups'}
                placeholder={tab === 'actions' ? 'Search Actions' : 'Search Groups'}
                title={tab === 'actions' ? 'Search by name, description, ID or route' : 'Search by Group name, ID or Action name'}
                className="w-full rounded-md border border-border-default bg-bg-tertiary py-1.5 pl-8 pr-8 text-xs text-text-primary placeholder:text-text-tertiary focus:border-accent/60 focus:outline-none"
              />
              {query && (
                <button
                  type="button"
                  aria-label="Clear search"
                  onClick={() => setQuery('')}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-tertiary hover:text-text-primary"
                >
                  <X size={13} />
                </button>
              )}
            </div>
            <Segmented
              label={tab === 'actions' ? 'Filter Actions' : 'Filter Groups'}
              value={filter}
              disabled={loading || error}
              options={
                tab === 'actions'
                  ? [
                      { value: 'all', label: 'All' },
                      { value: 'enabled', label: 'Enabled' },
                      { value: 'draft', label: 'Draft' },
                    ]
                  : [
                      { value: 'all', label: 'All' },
                      { value: 'built', label: 'Registered' },
                      { value: 'unbuilt', label: 'Unbuilt' },
                    ]
              }
              onChange={setFilter}
            />
            <span
              aria-live="polite"
              className="tabular ml-auto shrink-0 text-[11px] text-text-tertiary"
            >
              {loading
                ? 'Loading…'
                : error
                  ? 'Unavailable'
                  : `${visibleCount} of ${totalCount} ${tab === 'actions' ? 'Actions' : 'Groups'}`}
            </span>
          </div>
        </div>

        <div className="px-3 pb-24 pt-3 sm:px-6 sm:pb-10">
          {previewState === 'stale' && <StaleBanner onRetry={onRetry} />}
          {error ? (
            <LoadError
              message="The Action registry could not be reached. No cached configuration is available for this preview state."
              onRetry={onRetry}
            />
          ) : loading ? (
            <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
              <SkeletonList rows={8} kind={tab === 'actions' ? 'action' : 'group'} />
            </div>
          ) : empty ? (
            <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
              {tab === 'actions' ? (
                <ListEmpty
                  icon={<Layers size={18} />}
                  title="No Actions yet"
                  description="Create an internal API or command Action, then add it to a Group."
                  action={
                    <Button type="button" size="sm" variant="primary" onClick={onNewAction}>
                      <Plus size={13} /> New Action
                    </Button>
                  }
                />
              ) : (
                <ListEmpty
                  icon={<Layers size={18} />}
                  title="No Groups yet"
                  description="Group shared Actions to make them available as a Session plugin."
                  action={
                    <Button type="button" size="sm" variant="primary" onClick={onNewGroup}>
                      <Plus size={13} /> New Group
                    </Button>
                  }
                />
              )}
            </div>
          ) : tab === 'actions' ? (
            filteredActions.length === 0 ? (
              <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
                <ListEmpty
                  icon={<Search size={18} />}
                  title="No Actions match this view"
                  description="Clear the search or switch the filter to see the rest of the registry."
                  action={
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => {
                        setActionQuery('');
                        setActionFilter('all');
                      }}
                    >
                      <RotateCcw size={13} /> Clear filters
                    </Button>
                  }
                />
              </div>
            ) : (
              <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
                <div className="action-grid list-header border-b border-border-default px-3 py-1.5 text-[10px] font-medium uppercase tracking-wide text-text-tertiary">
                  <span>Name</span>
                  <span>Type</span>
                  <span className="action-col-effect">Effect</span>
                  <span className="action-col-status">Status</span>
                  <span className="action-col-usage">Used in</span>
                  <span className="text-right">Actions</span>
                </div>
                <ul aria-label="Actions">
                  {filteredActions.map((action) => (
                    <ActionRow
                      key={action.id}
                      action={action}
                      groups={usedByGroups(registry, action.id)}
                      onEdit={() => onEditAction(action)}
                      onDelete={() => onDeleteAction(action)}
                      onCopy={() => onCopy(action.id, 'Action ID')}
                    />
                  ))}
                </ul>
              </div>
            )
          ) : filteredGroups.length === 0 ? (
            <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
              <ListEmpty
                icon={<Search size={18} />}
                title="No Groups match this view"
                description="Clear the search or switch the filter to see the rest of the registry."
                action={
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => {
                      setGroupQuery('');
                      setGroupFilter('all');
                    }}
                  >
                    <RotateCcw size={13} /> Clear filters
                  </Button>
                }
              />
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
              <div className="group-grid list-header border-b border-border-default px-3 py-1.5 text-[10px] font-medium uppercase tracking-wide text-text-tertiary">
                <span>Name</span>
                <span className="group-col-release">Release</span>
                <span className="group-col-composition">Composition</span>
                <span className="group-col-status">Status</span>
                <span className="text-right">Actions</span>
              </div>
              <ul aria-label="Groups">
                {filteredGroups.map((group) => (
                  <GroupRow
                    key={group.id}
                    group={group}
                    actions={registry.actions}
                    onEdit={() => onEditGroup(group)}
                    onDelete={() => onDeleteGroup(group)}
                    onBuild={() => onBuild(group.id)}
                    onUnbuild={() => onUnbuild(group.id)}
                    onUseInSession={() => onUseInSession(group)}
                    onCopy={() => onCopy(pluginIdFor(group.id), 'Plugin ID')}
                  />
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Rows ──────────────────────────────────────────────────────────────── */

function ActionRow({
  action,
  groups,
  onEdit,
  onDelete,
  onCopy,
}: {
  action: Action;
  groups: ActionGroup[];
  onEdit: () => void;
  onDelete: () => void;
  onCopy: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const groupedNames = groups.map((group) => group.name).join(', ');
  return (
    <li className="border-b border-border-muted last:border-0">
      <div className="action-grid action-row px-3 py-2.5 transition-colors hover:bg-bg-tertiary/35 focus-within:bg-bg-tertiary/35">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <IconTile tone={action.execution.kind === 'api' ? 'accent' : 'neutral'}>
            {action.execution.kind === 'api' ? <Braces size={14} /> : <TerminalSquare size={14} />}
          </IconTile>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={onEdit}
                className="truncate text-left text-sm font-medium text-text-primary transition-colors hover:text-accent"
                title={`Edit ${action.name}`}
              >
                {action.name}
              </button>
            </div>
            <p className="mt-0.5 line-clamp-1 text-xs text-text-secondary" title={action.description}>
              {action.description}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] text-text-tertiary md:hidden">
              <span className={action.enabled ? 'text-success' : 'text-text-tertiary'}>
                {action.enabled ? 'Enabled' : 'Draft'}
              </span>
              <span>{action.execution.kind === 'api' ? 'API' : 'Command'}</span>
              <span>{EFFECT_LABELS[action.effect]}</span>
              <span>{groups.length} {groups.length === 1 ? 'Group' : 'Groups'}</span>
            </div>
          </div>
        </div>
        <div className="action-col-type min-w-0">
          <p className="text-xs text-text-secondary">{executionLabel(action)}</p>
          <p className="truncate font-mono text-[10px] text-text-tertiary" title={actionRoute(action)}>
            {actionRoute(action)}
          </p>
        </div>
        <div className="action-col-effect">
          <Badge tone={effectTone(action.effect)}>{EFFECT_LABELS[action.effect]}</Badge>
        </div>
        <div className="action-col-status">
          <Badge tone={action.enabled ? 'success' : 'muted'} dot>
            {action.enabled ? 'Enabled' : 'Draft'}
          </Badge>
        </div>
        <div className="action-col-usage text-xs text-text-secondary" title={groupedNames || 'Not used by any Group'}>
          {groups.length ? `${groups.length} ${groups.length === 1 ? 'Group' : 'Groups'}` : '—'}
        </div>
        <div className="flex shrink-0 items-center justify-end gap-1">
          <IconButton label={`Edit ${action.name}`} className="max-sm:hidden text-text-secondary" onClick={onEdit}>
            <Pencil size={14} />
          </IconButton>
          <div className="relative">
            <IconButton
              label={`More actions for ${action.name}`}
              aria-haspopup="menu"
              aria-expanded={menu}
              onClick={() => setMenu((value) => !value)}
            >
              <MoreHorizontal size={15} />
            </IconButton>
            {menu && (
              <>
                <button
                  type="button"
                  aria-hidden="true"
                  tabIndex={-1}
                  className="fixed inset-0 z-20 cursor-default"
                  onClick={() => setMenu(false)}
                />
                <div
                  role="menu"
                  className="absolute right-0 top-full z-30 mt-1 w-52 rounded-md border border-border-default bg-bg-primary p-1 shadow-dropdown"
                >
                  <MenuItem
                    onClick={() => {
                      setMenu(false);
                      onEdit();
                    }}
                  >
                    <Pencil size={13} /> Edit Action
                  </MenuItem>
                  <MenuItem
                    onClick={() => {
                      setMenu(false);
                      onCopy();
                    }}
                  >
                    <Copy size={13} /> Copy Action ID
                  </MenuItem>
                  <MenuItem
                    disabled={groups.length > 0}
                    title={
                      groups.length
                        ? `In use by ${groupedNames}. Remove it from those Groups first.`
                        : 'Delete this Action'
                    }
                    onClick={() => {
                      setMenu(false);
                      onDelete();
                    }}
                  >
                    <Trash2 size={13} /> Delete Action
                  </MenuItem>
                  {groups.length > 0 && (
                    <p className="px-2 py-1 text-[10px] leading-relaxed text-text-tertiary">
                      In use by {groups.length} {groups.length === 1 ? 'Group' : 'Groups'}; deletion is blocked.
                    </p>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

function GroupRow({
  group,
  actions,
  onEdit,
  onDelete,
  onBuild,
  onUnbuild,
  onUseInSession,
  onCopy,
}: {
  group: ActionGroup;
  actions: Action[];
  onEdit: () => void;
  onDelete: () => void;
  onBuild: () => void;
  onUnbuild: () => void;
  onUseInSession: () => void;
  onCopy: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const gate = buildBlockers(group.action_ids, actions);
  const actionNames = group.action_ids
    .map((id) => actions.find((action) => action.id === id)?.name ?? '')
    .filter(Boolean);
  const built = Boolean(group.build);
  return (
    <li className="border-b border-border-muted last:border-0">
      <div className="group-grid group-row px-3 py-2.5 transition-colors hover:bg-bg-tertiary/35 focus-within:bg-bg-tertiary/35">
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              onClick={onEdit}
              className="truncate text-left text-sm font-medium text-text-primary transition-colors hover:text-accent"
              title={`Edit ${group.name}`}
            >
              {group.name}
            </button>
          </div>
          <p className="mt-0.5 line-clamp-1 text-xs text-text-secondary" title={actionNames.join(' · ')}>
            {actionNames.join(' · ') || 'No Actions selected'}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] text-text-tertiary md:hidden">
            <span className={built ? 'text-success' : 'text-text-tertiary'}>{built ? 'Built' : 'Unbuilt'}</span>
            <span>{group.action_ids.length} Actions</span>
            <span>{group.sessions.length} Sessions</span>
            {built && group.blockers.length > 0 && (
              <span className="text-warning">{group.blockers.length} running</span>
            )}
          </div>
        </div>
        <div className="group-col-release min-w-0" title={group.build?.version ?? group.id}>
          {built ? (
            <>
              <p className="truncate font-mono text-[11px] text-text-secondary">
                v {shortVersion(group.build?.version ?? '')}
              </p>
              <p className="mt-0.5 truncate text-[10px] text-text-tertiary">Immutable release</p>
            </>
          ) : (
            <>
              <p className="text-xs text-text-secondary">Not built</p>
              <p className={cx('mt-0.5 truncate text-[10px]', gate ? 'text-warning' : 'text-text-tertiary')}>
                {gate ? 'Resolve build gates' : 'Ready to build'}
              </p>
            </>
          )}
        </div>
        <div className="group-col-composition min-w-0" title={group.id}>
          <p className="text-xs text-text-secondary">
            {group.action_ids.length} Actions · {group.sessions.length} Sessions
          </p>
        </div>
        <div className="group-col-status">
          <Badge tone={built ? 'success' : 'muted'} dot>
            {built ? 'Registered' : 'Unbuilt'}
          </Badge>
          {built && group.blockers.length > 0 && (
            <p className="mt-1 text-[10px] text-warning" title={group.blockers.map((item) => item.name).join(', ')}>
              {group.blockers.length} running
            </p>
          )}
          {!built && gate && <p className="mt-1 text-[10px] text-warning">Build blocked</p>}
        </div>
        <div className="flex shrink-0 items-center justify-end gap-0.5">
          {built ? (
            <button
              type="button"
              onClick={onUseInSession}
              title="Use in Session"
              className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-accent/25 bg-accent/10 px-2 py-1 text-xs font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/15"
            >
              <Play size={12} />
              <span className="hidden min-[1440px]:inline">Use in Session</span>
              <span className="min-[1440px]:hidden">Use</span>
            </button>
          ) : gate ? (
            <span
              title={gate.message}
              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-warning/25 bg-warning/10 px-2 py-1 text-xs font-medium text-warning"
            >
              <Lock size={12} /> Blocked
            </span>
          ) : (
            <button
              type="button"
              title="Build this Group"
              onClick={onBuild}
              className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-accent/25 bg-accent/10 px-2 py-1 text-xs font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/15"
            >
              <Hammer size={12} /> Build
            </button>
          )}
          <IconButton label={`Edit ${group.name}`} className="max-sm:hidden text-text-secondary" onClick={onEdit}>
            <Pencil size={14} />
          </IconButton>
          <div className="relative">
            <IconButton
              label={`More actions for ${group.name}`}
              aria-haspopup="menu"
              aria-expanded={menu}
              onClick={() => setMenu((value) => !value)}
            >
              <MoreHorizontal size={15} />
            </IconButton>
            {menu && (
              <>
                <button
                  type="button"
                  aria-hidden="true"
                  tabIndex={-1}
                  className="fixed inset-0 z-20 cursor-default"
                  onClick={() => setMenu(false)}
                />
                <div
                  role="menu"
                  className="absolute right-0 top-full z-30 mt-1 w-56 rounded-md border border-border-default bg-bg-primary p-1 shadow-dropdown"
                >
                  <MenuItem
                    onClick={() => {
                      setMenu(false);
                      onEdit();
                    }}
                  >
                    <Pencil size={13} /> Edit Group
                  </MenuItem>
                  <MenuItem
                    disabled={Boolean(gate)}
                    title={gate?.message ?? (built ? 'Rebuild this Group' : 'Build this Group')}
                    onClick={() => {
                      setMenu(false);
                      onBuild();
                    }}
                  >
                    <Hammer size={13} /> {built ? 'Rebuild Group' : 'Build Group'}
                  </MenuItem>
                  <MenuItem
                    disabled={!built || group.blockers.length > 0}
                    title={
                      !built
                        ? 'The Group is not built.'
                        : group.blockers.length
                          ? `Blocked by ${group.blockers.map((item) => item.name).join(', ')}.`
                          : 'Unbuild this Group'
                    }
                    onClick={() => {
                      setMenu(false);
                      onUnbuild();
                    }}
                  >
                    <PackageOpen size={13} /> Unbuild Group
                  </MenuItem>
                  <MenuItem
                    disabled={!built}
                    onClick={() => {
                      setMenu(false);
                      onCopy();
                    }}
                  >
                    <Copy size={13} /> Copy Plugin ID
                  </MenuItem>
                  <MenuItem
                    disabled={built}
                    title={built ? 'Unbuild the Group before deleting it.' : 'Delete this Group'}
                    onClick={() => {
                      setMenu(false);
                      onDelete();
                    }}
                  >
                    <Trash2 size={13} /> Delete Group
                  </MenuItem>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

function MenuItem({
  children,
  onClick,
  disabled = false,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      title={title}
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-text-secondary"
    >
      {children}
    </button>
  );
}

/* ── Preview chrome ────────────────────────────────────────────────────── */

function PreviewRibbon() {
  return (
    <div className="mock-ribbon flex shrink-0 items-center justify-between gap-2 border-b border-border-default bg-bg-secondary px-3 text-[11px] text-text-secondary">
      <span className="flex min-w-0 items-center gap-2">
        <span className="shrink-0 rounded bg-warning/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warning">
          Mock preview
        </span>
        <span className="truncate text-text-primary">Action Panel redesign · interactive prototype</span>
      </span>
      <span className="hidden shrink-0 sm:inline">
        Local fixtures · no API, Session or plugin changes
      </span>
    </div>
  );
}

function ToastCard({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const toneClass: Record<ToastTone, string> = {
    success: 'border-success/35',
    danger: 'border-danger/40',
    warning: 'border-warning/40',
    neutral: 'border-border-default',
  };
  return (
    <div
      role={toast.tone === 'danger' ? 'alert' : 'status'}
      className={cx(
        'fixed bottom-3 left-3 z-[60] w-[min(400px,calc(100vw-24px))] rounded-lg border bg-bg-primary p-3 shadow-dropdown max-sm:bottom-20',
        toneClass[toast.tone],
      )}
    >
      <div className="flex items-start gap-2.5">
        <span
          className={cx(
            'mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full',
            toast.tone === 'success' && 'bg-success/15 text-success',
            toast.tone === 'danger' && 'bg-danger/15 text-danger',
            toast.tone === 'warning' && 'bg-warning/15 text-warning',
            toast.tone === 'neutral' && 'bg-bg-tertiary text-text-secondary',
          )}
        >
          {toast.tone === 'success' ? <Check size={12} /> : toast.tone === 'danger' ? <AlertTriangle size={12} /> : <Info size={12} />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-text-primary">{toast.title}</p>
          {toast.detail && <p className="mt-0.5 break-words text-[11px] leading-relaxed text-text-secondary">{toast.detail}</p>}
          <p className="mt-1 text-[10px] text-text-tertiary">Mock only — nothing was sent to Pan.</p>
        </div>
        <button
          type="button"
          aria-label="Dismiss notification"
          onClick={onClose}
          className="rounded p-0.5 text-text-tertiary hover:text-text-primary"
        >
          <X size={13} />
        </button>
      </div>
    </div>
  );
}

const PREVIEW_STATES: Array<{ value: PreviewState; label: string; hint: string }> = [
  { value: 'ready', label: 'Ready', hint: 'Cached registry, successful refresh' },
  { value: 'loading', label: 'First load', hint: 'No cache yet → skeleton rows' },
  { value: 'error', label: 'First load failed', hint: 'Blocking error with retry' },
  { value: 'empty', label: 'Empty registry', hint: 'Valid empty state, not an error' },
  { value: 'stale', label: 'Refresh failed', hint: 'Cached list stays visible' },
];

function PreviewControls({
  open,
  onOpenChange,
  state,
  onState,
  theme,
  onTheme,
  onReset,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  state: PreviewState;
  onState: (state: PreviewState) => void;
  theme: 'dark' | 'light';
  onTheme: (theme: 'dark' | 'light') => void;
  onReset: () => void;
}) {
  return (
    <div className="fixed bottom-3 right-3 z-40 flex max-w-[calc(100vw-24px)] flex-col items-end gap-2">
      {open && (
        <div className="w-[min(320px,calc(100vw-24px))] rounded-lg border border-border-default bg-bg-primary p-3 shadow-dropdown">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div>
              <p className="flex items-center gap-1.5 text-xs font-semibold text-text-primary">
                <FlaskConical size={13} className="text-warning" /> Preview states
              </p>
              <p className="mt-0.5 text-[10px] text-text-tertiary">Switch UI states without network requests.</p>
            </div>
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="rounded p-0.5 text-text-tertiary hover:text-text-primary"
              aria-label="Close preview controls"
            >
              <X size={13} />
            </button>
          </div>
          <div role="radiogroup" aria-label="Preview data state" className="space-y-0.5">
            {PREVIEW_STATES.map((item) => (
              <button
                key={item.value}
                type="button"
                role="radio"
                aria-checked={state === item.value}
                onClick={() => onState(item.value)}
                className={cx(
                  'flex w-full items-start gap-2 rounded px-2 py-1.5 text-left transition-colors',
                  state === item.value ? 'bg-accent/10' : 'hover:bg-bg-tertiary',
                )}
              >
                <span
                  className={cx(
                    'mt-1 size-1.5 shrink-0 rounded-full',
                    state === item.value ? 'bg-accent' : 'bg-text-tertiary/60',
                  )}
                />
                <span className="min-w-0">
                  <span className="block text-xs text-text-primary">{item.label}</span>
                  <span className="block text-[10px] leading-relaxed text-text-tertiary">{item.hint}</span>
                </span>
              </button>
            ))}
          </div>
          <div className="mt-2 flex items-center justify-between gap-2 border-t border-border-default pt-2">
            <span className="text-[10px] text-text-tertiary">Theme</span>
            <Segmented
              label="Preview theme"
              value={theme}
              options={[
                { value: 'dark' as const, label: 'Dark' },
                { value: 'light' as const, label: 'Light' },
              ]}
              onChange={onTheme}
            />
          </div>
          <button
            type="button"
            onClick={onReset}
            className="mt-2 flex w-full items-center justify-center gap-1.5 rounded border border-border-default px-2 py-1.5 text-[11px] text-text-secondary hover:bg-bg-tertiary hover:text-text-primary"
          >
            <RotateCcw size={12} /> Reset local fixtures
          </button>
          <p className="mt-2 text-[10px] leading-relaxed text-text-tertiary">
            Visual review only. Nothing is loaded from or written to a real Pan server, Session or Group.
          </p>
        </div>
      )}
      <button
        type="button"
        aria-expanded={open}
        aria-label={open ? 'Hide preview controls' : 'Show preview controls'}
        onClick={() => onOpenChange(!open)}
        className="flex items-center gap-1.5 rounded-full border border-border-default bg-bg-secondary px-3 py-1.5 text-[11px] font-medium text-text-secondary shadow-panel transition-colors hover:bg-bg-tertiary hover:text-text-primary"
      >
        <FlaskConical size={13} className="text-warning" />
        {open ? 'Hide preview states' : 'Preview states'}
      </button>
    </div>
  );
}

const MODE_LABEL: Record<PluginMode, string> = {
  standalone: 'Standalone MCP',
  pan_native: 'Pan native',
  gateway: 'Discover / call',
};

function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia('(max-width: 767px)').matches,
  );
  useEffect(() => {
    const query = window.matchMedia('(max-width: 767px)');
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return mobile;
}

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { actionRequest } from '@/services/api';
import { useUIStore } from '@/stores/uiStore';
import {
  useActionCatalogStore,
  useActionRegistryStore,
  useActionSessionDetailStore,
} from '@/stores/actionRegistryStore';
import type { Action, ActionGroup, ActionRegistry } from '@/types/actions';
import {
  EFFECT_LABELS,
  actionEditorState,
  actionRoute,
  buildGate,
  effectTone,
  emptyActionDraft,
  executionLabel,
  formatTime,
  groupEditorState,
  isEditorDirty,
  sessionOperationError,
  pluginIdFor,
  shortVersion,
  usedByGroups,
  type EditorState,
} from '@/components/actions/actionsModel';
import { ActionEditorPage, GroupEditorPage } from '@/components/actions/ActionGroupEditors';
import { UseInSessionDialog } from '@/components/actions/UseInSessionDialog';
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
} from '@/components/actions/ActionsUi';
import {
  Braces,
  Copy,
  Hammer,
  Layers,
  Lock,
  MoreHorizontal,
  CopyPlus,
  PackageOpen,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  TerminalSquare,
  Trash2,
  X,
} from 'lucide-react';
import './actions.css';

type Tab = 'actions' | 'groups';
type ConfirmState =
  | { kind: 'discard' }
  | { kind: 'delete-action'; action: Action }
  | { kind: 'delete-group'; group: ActionGroup };
type ActionRegistryWithItem = import('@/types/actions').ActionRegistry & {
  item?: Action | ActionGroup;
};

export default function ActionsView() {
  const registry = useActionRegistryStore((state) => state.registry);
  const hasLoaded = useActionRegistryStore((state) => state.hasLoaded);
  const status = useActionRegistryStore((state) => state.status);
  const loadError = useActionRegistryStore((state) => state.error);
  const refreshError = useActionRegistryStore((state) => state.refreshError);
  const lastLoadedAt = useActionRegistryStore((state) => state.lastLoadedAt);
  const refreshing = useActionRegistryStore((state) => state.refreshing);
  const mutationPending = useActionRegistryStore((state) => state.mutationPending);
  const showToast = useUIStore((state) => state.showToast);

  const [tab, setTab] = useState<Tab>('actions');
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [editorError, setEditorError] = useState('');
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [useGroupId, setUseGroupId] = useState<string | null>(null);
  const [actionQuery, setActionQuery] = useState('');
  const [groupQuery, setGroupQuery] = useState('');
  const [actionFilter, setActionFilter] = useState<'all' | 'enabled' | 'draft'>('all');
  const [groupFilter, setGroupFilter] = useState<'all' | 'built' | 'unbuilt'>('all');
  const [duplicating, setDuplicating] = useState(false);
  const duplicateInFlight = useRef(false);

  useEffect(() => {
    void useActionRegistryStore.getState().ensureRegistry();
    const onFocus = () => useActionRegistryStore.getState().maybeRevalidate();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, []);

  useEffect(() => {
    if (!editor || !isEditorDirty(editor)) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [editor]);

  useEffect(() => {
    if (!editor || confirm || useGroupId) return;
    const handler = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        requestCloseEditor();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });

  function notify(message: string, type: 'error' | 'info' | 'warning' = 'info') {
    showToast(message, type);
  }

  function updateEditor(next: EditorState) {
    setEditor(next);
    setEditorError('');
  }

  function openAction(action: Action) {
    setEditor(actionEditorState(action, 'edit'));
    setEditorError('');
  }

  function newAction() {
    setTab('actions');
    setEditor(actionEditorState(emptyActionDraft(), 'new'));
    setEditorError('');
  }

  function openGroup(group: ActionGroup) {
    setEditor(
      groupEditorState(
        { id: group.id, name: group.name, action_ids: [...group.action_ids] },
        'edit',
      ),
    );
    setEditorError('');
  }

  function newGroup() {
    setTab('groups');
    setEditor(groupEditorState({ id: '', name: '', action_ids: [] }, 'new'));
    setEditorError('');
  }

  function closeEditor() {
    const kind = editor?.kind;
    setEditor(null);
    setEditorError('');
    if (kind === 'group') setTab('groups');
    if (kind === 'action') setTab('actions');
  }

  function requestCloseEditor() {
    if (!editor) return;
    if (isEditorDirty(editor)) {
      setConfirm({ kind: 'discard' });
      return;
    }
    closeEditor();
  }

  async function saveAction() {
    if (!editor || editor.kind !== 'action') return;
    const { draft, schemaError } = editor;
    if (sessionOperationError(draft)) { setEditorError(sessionOperationError(draft)); return; }
    if (!draft.name.trim() || !draft.description.trim() || schemaError) {
      setEditorError('Name, description and a valid input schema are required.');
      return;
    }
    setEditorError('');
    try {
      const result = await useActionRegistryStore.getState().runMutation((expectedRevision) => {
        if (editor.mode === 'new') {
          const { id: _ignored, ...item } = draft;
          void _ignored;
          return actionRequest<ActionRegistryWithItem>('actions/actions', 'POST', {
            item,
            expectedRevision,
          });
        }
        return actionRequest<ActionRegistryWithItem>(
          `actions/actions/${encodeURIComponent(draft.id)}`,
          'PUT',
          { item: draft, expectedRevision },
        );
      });
      const item = result.item as Action | undefined;
      closeEditor();
      notify(
        editor.mode === 'new'
          ? `Action created${item ? ` · ${item.id}` : ''}`
          : 'Action saved',
        'info',
      );
    } catch (caught) {
      const message = messageOf(caught);
      setEditorError(message);
      notify(`Couldn’t save Action: ${message}`, 'error');
      if (/revision|changed|conflict/i.test(message)) {
        void useActionRegistryStore.getState().ensureRegistry({ force: true });
      }
    }
  }

  async function saveGroup() {
    if (!editor || editor.kind !== 'group') return;
    if (!editor.draft.name.trim()) {
      setEditorError('A Group name is required.');
      return;
    }
    setEditorError('');
    try {
      const result = await useActionRegistryStore.getState().runMutation((expectedRevision) => {
        const item =
          editor.mode === 'new'
            ? { name: editor.draft.name.trim(), action_ids: [...editor.draft.action_ids] }
            : {
                id: editor.draft.id,
                name: editor.draft.name.trim(),
                action_ids: [...editor.draft.action_ids],
              };
        return actionRequest<ActionRegistryWithItem>(
          editor.mode === 'new'
            ? 'actions/groups'
            : `actions/groups/${encodeURIComponent(editor.draft.id)}`,
          editor.mode === 'new' ? 'POST' : 'PUT',
          { item, expectedRevision },
        );
      });
      const item = result.item as ActionGroup | undefined;
      closeEditor();
      notify(
        editor.mode === 'new'
          ? `Group created${item ? ` · ${item.id}` : ''}`
          : 'Group saved',
        'info',
      );
    } catch (caught) {
      const message = messageOf(caught);
      setEditorError(message);
      notify(`Couldn’t save Group: ${message}`, 'error');
      if (/revision|changed|conflict/i.test(message)) {
        void useActionRegistryStore.getState().ensureRegistry({ force: true });
      }
    }
  }

  function invalidateReleaseDetails(group: ActionGroup | undefined) {
    useActionCatalogStore.getState().invalidateCatalog();
    if (!group) return;
    for (const session of group.sessions) {
      useActionSessionDetailStore.getState().invalidateSession(session.id);
    }
  }

  async function buildGroup(groupId: string) {
    const group = registry.groups.find((item) => item.id === groupId);
    if (!group) return;
    const gate = buildGate(group.action_ids, registry.actions);
    if (gate) {
      notify(gate.message, 'error');
      return;
    }
    try {
      await useActionRegistryStore.getState().runMutation((expectedRevision) =>
        actionRequest<ActionRegistry>(`actions/groups/${encodeURIComponent(groupId)}/build`, 'POST', {
          expectedRevision,
        }),
      );
      invalidateReleaseDetails(group);
      notify(`Group built · ${pluginIdFor(group.id)}`, 'info');
    } catch (caught) {
      notify(`Couldn’t build Group: ${messageOf(caught)}`, 'error');
    }
  }

  async function unbuildGroup(groupId: string) {
    const group = registry.groups.find((item) => item.id === groupId);
    if (!group) return;
    if (!group.build) {
      notify('This Group has no registered release.', 'warning');
      return;
    }
    if (group.blockers.length) {
      notify(
        `Unbuild blocked by ${group.blockers.length} running ${
          group.blockers.length === 1 ? 'Session' : 'Sessions'
        }.`,
        'error',
      );
      return;
    }
    try {
      await useActionRegistryStore.getState().runMutation((expectedRevision) =>
        actionRequest<ActionRegistry>(`actions/groups/${encodeURIComponent(groupId)}/unbuild`, 'POST', {
          expectedRevision,
        }),
      );
      invalidateReleaseDetails(group);
      notify('Group unbuilt · Session bindings removed', 'info');
    } catch (caught) {
      notify(`Couldn’t unbuild Group: ${messageOf(caught)}`, 'error');
    }
  }

  function requestDeleteAction(action: Action) {
    const usage = usedByGroups(registry, action.id);
    if (usage.length) {
      notify(
        `Action is used by ${usage.map((group) => group.name).join(', ')}. Remove it from those Groups first.`,
        'error',
      );
      return;
    }
    setConfirm({ kind: 'delete-action', action });
  }

  async function duplicateAction(action: Action) {
    if (duplicateInFlight.current || mutationPending) return;
    duplicateInFlight.current = true;
    setDuplicating(true);
    const existingIds = new Set(registry.actions.map((item) => item.id));
    const copyName = duplicateName(action.name, registry.actions.map((item) => item.name));
    try {
      const { id: _ignored, ...copy } = structuredClone(action);
      void _ignored;
      const result = await useActionRegistryStore.getState().runMutation((expectedRevision) =>
        actionRequest<ActionRegistryWithItem>('actions/actions', 'POST', {
          item: { ...copy, name: copyName },
          expectedRevision,
        }),
      );
      const created =
        (result.item as Action | undefined) ??
        result.actions.find((item) => !existingIds.has(item.id) && item.name === copyName);
      if (!created) {
        notify('Action was duplicated, but the new item was not returned. Refresh and open it from the list.', 'warning');
        return;
      }
      setEditor(actionEditorState(created, 'edit'));
      setEditorError('');
      notify(`Action duplicated · ${created.id}`, 'info');
    } catch (caught) {
      notify(`Couldn’t duplicate Action: ${messageOf(caught)}`, 'error');
    } finally {
      duplicateInFlight.current = false;
      setDuplicating(false);
    }
  }

  function requestDeleteGroup(group: ActionGroup) {
    if (group.build) {
      notify('Unbuild the Group before deleting it.', 'error');
      return;
    }
    setConfirm({ kind: 'delete-group', group });
  }

  async function deleteAction(action: Action) {
    try {
      await useActionRegistryStore.getState().runMutation((expectedRevision) =>
        actionRequest<ActionRegistry>(
          `actions/actions/${encodeURIComponent(action.id)}?expectedRevision=${expectedRevision}`,
          'DELETE',
        ),
      );
      setConfirm(null);
      closeEditor();
      notify('Action deleted', 'info');
    } catch (caught) {
      setConfirm(null);
      setEditorError(messageOf(caught));
      notify(`Couldn’t delete Action: ${messageOf(caught)}`, 'error');
    }
  }

  async function deleteGroup(group: ActionGroup) {
    try {
      await useActionRegistryStore.getState().runMutation((expectedRevision) =>
        actionRequest<ActionRegistry>(
          `actions/groups/${encodeURIComponent(group.id)}?expectedRevision=${expectedRevision}`,
          'DELETE',
        ),
      );
      setConfirm(null);
      closeEditor();
      notify('Group deleted', 'info');
    } catch (caught) {
      setConfirm(null);
      setEditorError(messageOf(caught));
      notify(`Couldn’t delete Group: ${messageOf(caught)}`, 'error');
    }
  }

  function copyText(value: string, label: string) {
    void navigator.clipboard
      ?.writeText(value)
      .then(() => notify(`${label} copied`, 'info'))
      .catch(() => notify('Clipboard unavailable; select the value to copy it.', 'warning'));
  }

  async function refresh() {
    const ok = await useActionRegistryStore.getState().refreshRegistry();
    notify(
      ok ? 'Action registry refreshed' : 'Refresh failed — showing the last loaded registry.',
      ok ? 'info' : 'error',
    );
  }

  const actionCount = hasLoaded ? registry.actions.length : 0;
  const groupCount = hasLoaded ? registry.groups.length : 0;
  const useGroup = registry.groups.find((group) => group.id === useGroupId) ?? null;
  const liveGroup =
    editor?.kind === 'group' && editor.originalId
      ? (registry.groups.find((group) => group.id === editor.originalId) ?? null)
      : null;
  const loadingFirst = status === 'loading' && !hasLoaded;
  const blockingError = status === 'error' && !hasLoaded;

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

  const query = tab === 'actions' ? actionQuery : groupQuery;
  const setQuery = tab === 'actions' ? setActionQuery : setGroupQuery;
  const filter = tab === 'actions' ? actionFilter : groupFilter;
  const setFilter =
    tab === 'actions'
      ? (value: string) => setActionFilter(value as typeof actionFilter)
      : (value: string) => setGroupFilter(value as typeof groupFilter);
  const visibleCount = tab === 'actions' ? filteredActions.length : filteredGroups.length;
  const totalCount = tab === 'actions' ? actionCount : groupCount;

  return (
    <main aria-label="Actions Panel" className="flex h-full min-h-0 flex-col bg-bg-primary text-text-primary">
      {editor ? (
        editor.kind === 'action' ? (
          <ActionEditorPage
            state={editor}
            groups={registry.groups}
            busy={mutationPending}
            error={editorError}
            onChange={updateEditor}
            onSave={() => void saveAction()}
            onCancel={requestCloseEditor}
            onDelete={() => requestDeleteAction(editor.draft)}
          />
        ) : (
          <GroupEditorPage
            state={editor}
            liveGroup={liveGroup}
            actions={registry.actions}
            busy={mutationPending}
            error={editorError}
            onChange={updateEditor}
            onSave={() => void saveGroup()}
            onCancel={requestCloseEditor}
            onDelete={() => liveGroup && requestDeleteGroup(liveGroup)}
            onBuild={() => liveGroup && void buildGroup(liveGroup.id)}
            onUnbuild={() => liveGroup && void unbuildGroup(liveGroup.id)}
            onUseInSession={() => {
              if (!liveGroup) return;
              setUseGroupId(liveGroup.id);
            }}
          />
        )
      ) : (
        <div className="flex h-full min-h-0 flex-col">
          <header className="shrink-0 border-b border-border-default bg-bg-primary">
            <div className="flex flex-wrap items-start justify-between gap-3 px-4 pb-3 pt-4 sm:px-6 sm:pt-5">
              <div className="min-w-0 pl-10 sm:pl-0">
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
                  {lastLoadedAt ? (
                    <>
                      <span>Updated {formatTime(lastLoadedAt)}</span>
                      <span className="font-mono">revision {registry.revision}</span>
                    </>
                  ) : (
                    <span>{loadingFirst ? 'Loading…' : 'Not loaded'}</span>
                  )}
                </span>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => void refresh()}
                  disabled={refreshing || loadingFirst}
                  title="Refresh the Action registry"
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
                  { value: 'actions' as const, label: 'Actions', count: hasLoaded ? actionCount : undefined },
                  { value: 'groups' as const, label: 'Groups', count: hasLoaded ? groupCount : undefined },
                ]}
                onChange={setTab}
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
                onClick={tab === 'actions' ? newAction : newGroup}
              >
                <Plus size={13} /> {tab === 'actions' ? 'New Action' : 'New Group'}
              </Button>
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-auto">
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
                    title={
                      tab === 'actions'
                        ? 'Search by name, description, ID or route'
                        : 'Search by Group name, ID or Action name'
                    }
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
                  disabled={loadingFirst || blockingError}
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
                <span aria-live="polite" className="tabular ml-auto shrink-0 text-[11px] text-text-tertiary">
                  {loadingFirst
                    ? 'Loading…'
                    : blockingError
                      ? 'Unavailable'
                      : `${visibleCount} of ${totalCount} ${tab === 'actions' ? 'Actions' : 'Groups'}`}
                </span>
              </div>
            </div>

            <div className="px-3 pb-24 pt-3 sm:px-6 sm:pb-10">
              {refreshError && hasLoaded && (
                <StaleBanner message={refreshError} onRetry={() => void refresh()} />
              )}
              {blockingError ? (
                <LoadError
                  message={loadError || 'The Action registry could not be reached.'}
                  onRetry={() => void useActionRegistryStore.getState().ensureRegistry({ force: true })}
                />
              ) : loadingFirst ? (
                <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
                  <SkeletonList rows={8} kind={tab === 'actions' ? 'action' : 'group'} />
                </div>
              ) : tab === 'actions' ? (
                filteredActions.length === 0 ? (
                  <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
                    {registry.actions.length === 0 ? (
                      <ListEmpty
                        icon={<Layers size={18} />}
                        title="No Actions yet"
                        description="Create an internal API or command Action, then add it to a Group."
                        action={
                          <Button type="button" size="sm" variant="primary" onClick={newAction}>
                            <Plus size={13} /> New Action
                          </Button>
                        }
                      />
                    ) : (
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
                    )}
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
                          onEdit={() => openAction(action)}
                          onDelete={() => requestDeleteAction(action)}
                          onCopy={() => copyText(action.id, 'Action ID')}
                          onDuplicate={() => void duplicateAction(action)}
                          duplicateDisabled={duplicating || mutationPending}
                        />
                      ))}
                    </ul>
                  </div>
                )
              ) : filteredGroups.length === 0 ? (
                <div className="overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20">
                  {registry.groups.length === 0 ? (
                    <ListEmpty
                      icon={<Layers size={18} />}
                      title="No Groups yet"
                      description="Group shared Actions to make them available as a Session plugin."
                      action={
                        <Button type="button" size="sm" variant="primary" onClick={newGroup}>
                          <Plus size={13} /> New Group
                        </Button>
                      }
                    />
                  ) : (
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
                  )}
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
                        onEdit={() => openGroup(group)}
                        onDelete={() => requestDeleteGroup(group)}
                        onBuild={() => void buildGroup(group.id)}
                        onUnbuild={() => void unbuildGroup(group.id)}
                        onUseInSession={() => setUseGroupId(group.id)}
                        onCopy={() => copyText(pluginIdFor(group.id), 'Plugin ID')}
                      />
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {confirm?.kind === 'discard' && (
        <ConfirmDialog
          title="Discard unsaved changes?"
          message="Your draft has unsaved changes. Leaving now discards them and returns to the list."
          confirmLabel="Discard draft"
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            closeEditor();
            notify('Draft discarded', 'info');
          }}
        />
      )}
      {confirm?.kind === 'delete-action' && (
        <ConfirmDialog
          title={`Delete “${confirm.action.name}”?`}
          message="This removes the Action from the registry. Actions referenced by a Group must be removed from those Groups first."
          confirmLabel="Delete Action"
          onCancel={() => setConfirm(null)}
          onConfirm={() => void deleteAction(confirm.action)}
        />
      )}
      {confirm?.kind === 'delete-group' && (
        <ConfirmDialog
          title={`Delete “${confirm.group.name}”?`}
          message="This removes the Group from the registry. Built Groups must be unbuilt first."
          confirmLabel="Delete Group"
          onCancel={() => setConfirm(null)}
          onConfirm={() => void deleteGroup(confirm.group)}
        />
      )}

      {useGroup && <UseInSessionDialog group={useGroup} onClose={() => setUseGroupId(null)} />}
    </main>
  );
}

function duplicateName(name: string, existingNames: string[]): string {
  const used = new Set(existingNames.map((value) => value.trim().toLowerCase()));
  const base = `${name.trim() || 'Untitled Action'} copy`;
  let candidate = base;
  let index = 2;
  while (used.has(candidate.toLowerCase())) {
    candidate = `${base} ${index}`;
    index += 1;
  }
  return candidate;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/* ── Rows ──────────────────────────────────────────────────────────────── */

function ActionRow({
  action,
  groups,
  onEdit,
  onDelete,
  onCopy,
  onDuplicate,
  duplicateDisabled,
}: {
  action: Action;
  groups: ActionGroup[];
  onEdit: () => void;
  onDelete: () => void;
  onCopy: () => void;
  onDuplicate: () => void;
  duplicateDisabled: boolean;
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
              <span>{executionLabel(action)}</span>
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
                    disabled={duplicateDisabled}
                    title={duplicateDisabled ? 'A duplicate is already being created.' : 'Create an independent copy and open it in the editor'}
                    onClick={() => {
                      setMenu(false);
                      onDuplicate();
                    }}
                  >
                    <CopyPlus size={13} /> Duplicate Action
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
  const gate = buildGate(group.action_ids, actions);
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

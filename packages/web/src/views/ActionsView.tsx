import { useOperationIdentity, definitelyRejected } from '@/components/actions/useOperationIdentity';
import { canonicalJobs } from '@/services/canonicalJobs';
import { useAutomationAuth } from '@/stores/automationStore';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LegacyRecordPanel } from '@/components/actions/LegacyRecordPanel';
import { Button } from '@/components/ui/Button';
import { actionRequest, canonicalRequest } from '@/services/api';
import { useUIStore } from '@/stores/uiStore';
import {
  useActionCatalogStore,
  useActionRegistryStore,
  useActionSessionDetailStore,
  useActionSessionsStore,
} from '@/stores/actionRegistryStore';
import type { Action, ActionGroup, ActionRegistry, CanonicalLegacyAction } from '@/types/actions';
import { CanonicalDefinitionEditor } from '@/components/actions/CanonicalDefinitionEditor';
import { actionEditorState, actionRoute, buildGate, emptyActionDraft, formatTime, groupEditorState, isEditorDirty, sessionOperationError, pluginIdFor, usedByGroups, type EditorState } from '@/components/actions/actionsModel';
import { ActionEditorPage, GroupEditorPage } from '@/components/actions/ActionGroupEditors';
import { MappedActionPanel } from '@/components/actions/MappedActionPanel';
import { UseInSessionDialog } from '@/components/actions/UseInSessionDialog';
import { CanonicalActionPanel } from '@/components/actions/CanonicalActionPanel';
import { CanonicalActionRow, CanonicalGroupRow } from '@/components/actions/CanonicalRows';
import { CanonicalGroupPanel } from '@/components/actions/CanonicalGroupPanel';
import {
  buildUnifiedActions,
  buildUnifiedGroups,
  obj,
  rows,
  text,
  withVersionIndex,
} from '@/components/actions/unifiedModel';
import type { CanonicalReceipt, JsonObject } from '@/types/canonicalJobs';
import { ConfirmDialog, ListEmpty, LoadError, Segmented, SkeletonList, StaleBanner } from '@/components/actions/ActionsUi';
import { Braces, Layers, Plus, RefreshCw, RotateCcw, Search, X } from 'lucide-react';
import './actions.css';
export { LegacyActionsView as default };

type Tab = 'actions' | 'groups';
type ConfirmState =
  | { kind: 'discard' }
  | { kind: 'delete-action'; action: Action }
  | { kind: 'delete-group'; group: ActionGroup };
type ActionRegistryWithItem = import('@/types/actions').ActionRegistry & {
  item?: Action | ActionGroup;
};

export function LegacyActionsView() {
  const authorizationToken = useAutomationAuth((state) => state.token || state.authenticated);
  const registry = useActionRegistryStore((state) => state.registry);
  const hasLoaded = useActionRegistryStore((state) => state.hasLoaded);
  const status = useActionRegistryStore((state) => state.status);
  const loadError = useActionRegistryStore((state) => state.error);
  const refreshError = useActionRegistryStore((state) => state.refreshError);
  const lastLoadedAt = useActionRegistryStore((state) => state.lastLoadedAt);
  const refreshing = useActionRegistryStore((state) => state.refreshing);
  const mutationPending = useActionRegistryStore((state) => state.mutationPending);
  const showToast = useUIStore((state) => state.showToast);

  const [quickPending, setQuickPending] = useOperationIdentity('designed-form');
  const quickLock = useRef(false);
  const [quickBusy, setQuickBusy] = useState(false);
  const [tab, setTab] = useState<Tab>('actions');
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [canonicalEditor, setCanonicalEditor] = useState<(CanonicalLegacyAction & { legacyId: string }) | null>(null);
  const [canonicalNew, setCanonicalNew] = useState(false);
  const [canonicalDuplicateIntent, setCanonicalDuplicateIntent] = useState(false);
  const [canonicalDirty, setCanonicalDirty] = useState(false);
  const [editorError, setEditorError] = useState('');
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [useGroupId, setUseGroupId] = useState<string | null>(null);
  const [actionQuery, setActionQuery] = useState('');
  const [groupQuery, setGroupQuery] = useState('');
  const [actionFilter, setActionFilter] = useState<'all' | 'enabled' | 'draft'>('all');
  const [groupFilter, setGroupFilter] = useState<'all' | 'built' | 'unbuilt'>('all');
  const [canonicalPage, setCanonicalPage] = useState<JsonObject>({});
  const [canonicalGroupPage, setCanonicalGroupPage] = useState<JsonObject[]>([]);
  const [canonicalAliases, setCanonicalAliases] = useState<Record<string, string>>({});
  const [canonicalTarget, setCanonicalTarget] = useState<
    { kind: 'action' | 'group'; key: string } | null
  >(null);
  // A canonical definition opened through the designed form keeps its own
  // write authority (PUT jobs/actions) and observed revision.
  const [canonicalEdit, setCanonicalEdit] = useState<{ id: string; revision: number } | null>(null);
  const sessions = useActionSessionsStore((state) => state.sessions);

  const loadCanonical = useCallback(async () => {
    try {
      const [actions, groups] = await Promise.all([
        canonicalRequest<JsonObject>('jobs/actions'),
        canonicalRequest<JsonObject>('action-groups'),
      ]);
      setCanonicalPage(actions);
      setCanonicalGroupPage(rows(groups.items));
    } catch {
      // The canonical catalog is additive; the designed legacy list still works.
    }
    try {
      const bindings = await canonicalRequest<JsonObject>(
        'action-groups/legacy-action-bindings?limit=100',
      );
      const next: Record<string, string> = {};
      for (const item of rows(bindings.items)) {
        if (item.deleted === true) continue;
        const legacyId = text(item.legacyId);
        const actionId = text(item.actionId);
        if (legacyId && actionId) next[legacyId] = actionId;
      }
      setCanonicalAliases(next);
    } catch {
      setCanonicalAliases({});
    }
  }, []);

  useEffect(() => {
    void loadCanonical();
  }, [loadCanonical]);

  const canonicalCatalog = useMemo(
    () =>
      withVersionIndex(
        buildUnifiedActions({
          catalog: rows(canonicalPage?.items),
          registry,
          groups: canonicalGroupPage,
          aliases: canonicalAliases,
        }),
        canonicalPage,
      ),
    [canonicalPage, registry, canonicalGroupPage, canonicalAliases],
  );
  const canonicalGroupRows = useMemo(
    () => buildUnifiedGroups({ canonical: canonicalGroupPage, registry, catalog: canonicalCatalog }),
    [canonicalGroupPage, registry, canonicalCatalog],
  );
  const targetAction =
    canonicalTarget?.kind === 'action'
      ? canonicalCatalog.find((row) => row.key === canonicalTarget.key)
      : undefined;
  const targetGroup =
    canonicalTarget?.kind === 'group'
      ? canonicalGroupRows.find((row) => row.key === canonicalTarget.key)
      : undefined;

  useEffect(() => {
    void useActionRegistryStore.getState().ensureRegistry();
    const onFocus = () => useActionRegistryStore.getState().maybeRevalidate();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [authorizationToken]);

  useEffect(() => {
    if (!canonicalDirty && (!editor || !isEditorDirty(editor))) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [editor, canonicalDirty]);

  useEffect(() => {
    if ((!editor && !canonicalEditor && !canonicalNew) || confirm || useGroupId) return;
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
    if (!registry.canonicalActions?.[action.id]) { setCanonicalTarget({kind: 'action', key: `legacy:${action.id}`}); return; }
    if (action.system) { notify('System Actions are maintained by the product. Use a Job or Template to supply declared parameters.', 'info'); return; }
    const mapped = registry.canonicalActions?.[action.id];
    if (mapped) {
      // Keep the observed revision while editing. A concurrent refresh must
      // not silently attach an old draft body to a newer CAS revision.
      setCanonicalEditor({ ...structuredClone(mapped), legacyId: action.id });
      setCanonicalDuplicateIntent(false);
      setCanonicalDirty(false);
      return;
    }
    const preserved = registry.preservedActions?.find((item) => item.id === action.id);
    if (preserved) {
      notify(
        `This Action is preserved because its definition cannot be represented by the old editor: ${preserved.reason}. Editing is blocked; use the unified catalog.`,
        'warning',
      );
      return;
    }
    setEditor(actionEditorState(action, 'edit'));
    setEditorError('');
  }

  function newAction() {
    setTab('actions');
    setCanonicalNew(false);
    setEditor(actionEditorState(emptyActionDraft(), 'new'));
    setEditorError('');
  }

  function newAdvancedAction() {
    setTab('actions');
    setEditor(null);
    setCanonicalEditor(null);
    setCanonicalDuplicateIntent(false);
    setCanonicalNew(true);
    setCanonicalDirty(false);
    setEditorError('');
  }

  function openGroup(group: ActionGroup) {
    setCanonicalTarget({kind: 'group', key: `legacy:${group.id}`}); return;
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
    setCanonicalEdit(null);
    setCanonicalEditor(null);
    setCanonicalNew(false);
    setCanonicalDuplicateIntent(false);
    setCanonicalDirty(false);
    setEditorError('');
    if (kind === 'group') setTab('groups');
    if (kind === 'action') setTab('actions');
  }

  function requestCloseEditor() {
    if (canonicalNew) {
      if (canonicalDirty) setConfirm({ kind: 'discard' });
      else closeEditor();
      return;
    }
    if (canonicalEditor) {
      if (canonicalDirty) setConfirm({ kind: 'discard' });
      else closeEditor();
      return;
    }
    if (!editor) return;
    if (isEditorDirty(editor)) {
      setConfirm({ kind: 'discard' });
      return;
    }
    closeEditor();
  }

  async function trackedWrite(path: string, method: string, body: JsonObject, operation: string) {
    const requestKey = crypto.randomUUID();
    setQuickPending({operation, requestKey});
    try {
      const result = await canonicalRequest<CanonicalReceipt>(path, method, {...body, requestKey});
      if (!['completed', 'rejected', 'cancelled'].includes(result.status)) throw new Error('Result unconfirmed. Query the original operation.');
      setQuickPending(null);
      if (result.status !== 'completed') throw new Error('Request rejected. Review the original receipt.');
      return result;
    } catch (cause) { if (definitelyRejected(cause)) setQuickPending(null); throw cause; }
  }
  async function saveAction() {
    if (quickLock.current || quickPending) return;
    quickLock.current = true; setQuickBusy(true);
    try { await saveActionRequest(); } finally { quickLock.current = false; setQuickBusy(false); }
  }
  async function saveGroup() {
    if (quickLock.current || quickPending) return;
    quickLock.current = true; setQuickBusy(true);
    try { await saveGroupRequest(); } finally { quickLock.current = false; setQuickBusy(false); }
  }
  async function saveActionRequest() {
    if (!editor || editor.kind !== 'action') return;
    const { draft, schemaError } = editor;
    if (editor.executionError || sessionOperationError(draft)) { setEditorError(editor.executionError || sessionOperationError(draft)); return; }
    if (!draft.name.trim() || !draft.description.trim() || schemaError) {
      setEditorError('Name, description and a valid input schema are required.');
      return;
    }
    setEditorError('');
    if (canonicalEdit) {
      try {
        const { id: _ignored, ...legacyDraft } = draft;
        void _ignored;
        const preview = await canonicalRequest<{ definition: JsonObject }>(
          'jobs/actions/definition-preview',
          'POST',
          { draft: legacyDraft },
        );
        await trackedWrite(
          `jobs/actions/${encodeURIComponent(canonicalEdit.id)}`,
          'PUT',
          {
            definition: preview.definition,
            expectedRevision: canonicalEdit.revision,
            requestKey: crypto.randomUUID(),
          }, 'update',
        );
        await loadCanonical();
        closeEditor();
        notify('Action saved as a new draft version', 'info');
      } catch (caught) {
        const message = messageOf(caught);
        setEditorError(message);
        notify(`Couldn’t save Action: ${message}`, 'error');
      }
      return;
    }
    if (editor.mode === 'new') {
      // The designed quick-create form posts a legacy-shaped draft. The
      // read-only definition adapter maps it with the approved adoption
      // mapping, so creation lands in the canonical single authority instead
      // of an invisible legacy-only record.
      try {
        const { id: _ignored, ...legacyDraft } = draft;
        void _ignored;
        const preview = await canonicalRequest<{ definition: JsonObject }>(
          'jobs/actions/definition-preview',
          'POST',
          { draft: legacyDraft },
        );
        const created = await trackedWrite('jobs/actions', 'POST', {
          definition: preview.definition,
          expectedRevision: 0,
          requestKey: crypto.randomUUID(),
        }, 'create');
        const object = obj(created.result?.object);
        if (draft.enabled && object.id) {
          await trackedWrite(
            `jobs/actions/${encodeURIComponent(String(object.id))}/operations`,
            'POST',
            {
              operation: 'publish',
              expectedRevision: object.revision,
              expectedStateRevision: object.stateRevision,
              parameters: {},
              requestKey: crypto.randomUUID(),
            }, 'publish',
          );
        }
        await useActionRegistryStore.getState().ensureRegistry({ force: true });
        await loadCanonical();
        closeEditor();
        notify(
          `Action created · ${String(object.id ?? '')}${draft.enabled ? ' · published' : ' · draft'}`,
          'info',
        );
      } catch (caught) {
        const message = messageOf(caught);
        setEditorError(message);
        notify(`Couldn’t create Action: ${message}`, 'error');
      }
      return;
    }
    try {
      const result = await useActionRegistryStore.getState().runMutation((expectedRevision) =>
        actionRequest<ActionRegistryWithItem>(
          `actions/actions/${encodeURIComponent(draft.id)}`,
          'PUT',
          { item: draft, expectedRevision },
        ),
      );
      const item = result.item as Action | undefined;
      closeEditor();
      notify(`Action saved${item ? ` · ${item.id}` : ''}`, 'info');
    } catch (caught) {
      const message = messageOf(caught);
      setEditorError(message);
      notify(`Couldn’t save Action: ${message}`, 'error');
      if (/revision|changed|conflict/i.test(message)) {
        void useActionRegistryStore.getState().ensureRegistry({ force: true });
      }
    }
  }

  async function saveGroupRequest() {
    if (!editor || editor.kind !== 'group') return;
    if (!editor.draft.name.trim()) {
      setEditorError('A Group name is required.');
      return;
    }
    setEditorError('');
    try {
      if (editor.mode !== 'new') throw new Error('Retired Group is read-only. Create a new Group.');
      const references = obj(canonicalPage.references);
      const tools = editor.draft.action_ids.map((id) => {
        const row = canonicalCatalog.find((candidate) => candidate.canonicalId === id || candidate.legacyId === id);
        const ref = obj(references[row?.canonicalId ?? '']);
        if (!row?.canonicalId || !ref.digest) throw new Error('Select a published, enabled Action version.');
        return { alias: id, actionRef: ref };
      });
      const id = 'group_' + crypto.randomUUID().replaceAll('-', '').slice(0, 24);
      const created = await trackedWrite(`action-groups/${id}`, 'PUT', {
        group: { id, name: editor.draft.name.trim(), tools }, expectedRevision: 0,
      }, 'update');
      if (created.status !== 'completed') throw new Error('Creation is unconfirmed. Query the original operation before trying again.');
      const item = obj(created.result?.group);
      await loadCanonical();
      closeEditor();
      notify(
        editor.mode === 'new'
          ? `Group created · ${String(item.id ?? '')}`
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

  async function syncCanonicalEditor(legacyId: string) {
    await useActionRegistryStore.getState().ensureRegistry({ force: true });
    const next = useActionRegistryStore.getState().registry.canonicalActions?.[legacyId];
    if (next) setCanonicalEditor({ ...structuredClone(next), legacyId });
  }

  function openCanonicalCopy(object: Record<string, unknown>, definition: Record<string, unknown>) {
    setCanonicalEditor({
      object,
      definition,
      reason: 'Canonical copy created from the old entry; it is not bound to a legacy alias.',
      legacyId: String(object.id),
    });
    setCanonicalDuplicateIntent(false);
    setCanonicalDirty(false);
    setEditorError('');
    notify(
      `Canonical Action duplicated · ${String(object.id)}. Manage it in the unified catalog.`,
      'info',
    );
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
    if (action.system) { notify('System Actions cannot be deleted.', 'error'); return; }
    if (
      !registry.canonicalActions?.[action.id] &&
      registry.preservedActions?.some((item) => item.id === action.id)
    ) {
      notify('This Action is preserved; deletion through the old catalog is blocked.', 'error');
      return;
    }
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



  async function refresh() {
    const ok = await useActionRegistryStore.getState().refreshRegistry();
    notify(
      ok ? 'Action registry refreshed' : 'Refresh failed — showing the last loaded registry.',
      ok ? 'info' : 'error',
    );
  }

  const canonicalOnlyTotal = canonicalCatalog.filter((row) => row.adoption === 'canonical').length;
  const canonicalGroupOnlyTotal = canonicalGroupRows.filter(
    (row) => row.adoption === 'canonical',
  ).length;
  const actionCount = hasLoaded ? registry.actions.length + canonicalOnlyTotal : 0;
  const groupCount = hasLoaded ? registry.groups.length + canonicalGroupOnlyTotal : 0;
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
  const canonicalOnlyActions = canonicalCatalog.filter((row) => {
    if (row.adoption !== 'canonical') return false;
    const value = actionQuery.trim().toLowerCase();
    const matches =
      !value ||
      row.name.toLowerCase().includes(value) ||
      row.canonicalId.toLowerCase().includes(value) ||
      row.description.toLowerCase().includes(value);
    if (!matches) return false;
    if (actionFilter === 'enabled') return row.enabled;
    if (actionFilter === 'draft') return !row.enabled;
    return true;
  });
  const canonicalOnlyGroups = canonicalGroupRows.filter((row) => {
    if (row.adoption !== 'canonical') return false;
    const value = groupQuery.trim().toLowerCase();
    const matches =
      !value ||
      row.name.toLowerCase().includes(value) ||
      row.id.toLowerCase().includes(value) ||
      row.tools.some((tool) => tool.alias.toLowerCase().includes(value));
    if (!matches) return false;
    if (groupFilter === 'built') return Boolean(row.release);
    if (groupFilter === 'unbuilt') return !row.release;
    return true;
  });
  // User-owned rows first; product-managed system rows stay visible below.
  canonicalOnlyActions.sort((left, right) => Number(left.system) - Number(right.system));
  const visibleCount =
    tab === 'actions'
      ? filteredActions.length + canonicalOnlyActions.length
      : filteredGroups.length + canonicalOnlyGroups.length;
  const totalCount = tab === 'actions' ? actionCount : groupCount;

  return (
    <main aria-label="Actions Panel" className="flex h-full min-h-0 flex-col bg-bg-primary text-text-primary">
      {quickPending && <div role="status" className="shrink-0 border-b border-warning/30 p-3 text-sm">
        <p className="break-all">Original request {quickPending.requestKey}. Unknown results are query-only.</p>
        <Button disabled={quickBusy} onClick={async () => {
          setQuickBusy(true);
          try {
            const result = await canonicalJobs.lookup(quickPending.operation, quickPending.requestKey);
            if (['completed', 'rejected', 'cancelled'].includes(result.status)) {
              setQuickPending(null); await loadCanonical();
              if (result.status === 'completed') closeEditor();
            }
          } catch (cause) { notify(messageOf(cause), 'error'); }
          finally { setQuickBusy(false); }
        }}>Query original operation</Button>
      </div>}
      {targetAction && !targetAction.canonicalId ? (
        <LegacyRecordPanel action={targetAction} onClose={() => setCanonicalTarget(null)} onChanged={loadCanonical} />
      ) : targetGroup && !targetGroup.canonicalWritable ? (
        <LegacyRecordPanel group={targetGroup} onClose={() => setCanonicalTarget(null)} onChanged={loadCanonical} />
      ) : targetAction ? (
        <CanonicalActionPanel
          row={targetAction}
          onClose={() => setCanonicalTarget(null)}
          onChanged={loadCanonical}
          onEditForm={(draft) => {
            setCanonicalEdit({
              id: targetAction.canonicalId,
              revision: Number(targetAction.canonical?.revision ?? 1),
            });
            setCanonicalTarget(null);
            setTab('actions');
            setEditor(actionEditorState(draft, 'edit'));
            setEditorError('');
          }}
        />
      ) : targetGroup ? (
        <CanonicalGroupPanel
          row={targetGroup}
          catalog={canonicalCatalog}
          references={obj(canonicalPage?.references) as Record<string, JsonObject>}
          sessions={sessions}
          onChanged={async () => {
            await loadCanonical();
            await useActionRegistryStore.getState().ensureRegistry({ force: true });
          }}
          onInspect={() => undefined}
          onSelectAction={() => undefined}
        />
      ) : canonicalNew ? (
        <section aria-label="New advanced Action" className="min-h-0 overflow-auto p-4 sm:p-6">
          <h2 className="text-lg font-semibold">New advanced Action</h2>
          <p className="my-3 text-sm">
            The full definition supports multiple commands, environment and advanced fields. Saving creates a draft. Publish and execution are separate operations.
          </p>
          <CanonicalDefinitionEditor
            onDirtyChange={setCanonicalDirty}
            onChanged={async () => {
              await useActionRegistryStore.getState().refreshRegistry();
              closeEditor();
              notify('Canonical Action created. Manage it in the unified catalog.', 'info');
            }}
          />
          <Button className="mt-4" onClick={requestCloseEditor}>
            Back to catalog
          </Button>
        </section>
      ) : canonicalEditor ? (
        <MappedActionPanel
          key={canonicalEditor.legacyId}
          mapped={canonicalEditor}
          legacyAction={registry.actions.find((action) => action.id === canonicalEditor.legacyId)}
          existingNames={registry.actions.map((action) => action.name)}
          defaultDuplicateOpen={canonicalDuplicateIntent}
          onClose={requestCloseEditor}
          onChanged={() => syncCanonicalEditor(canonicalEditor.legacyId)}
          onOpenCopy={openCanonicalCopy}
          onRequestDelete={requestDeleteAction}
          error={editorError}
          onDirtyChange={setCanonicalDirty}
        />
      ) : editor ? (
        editor.kind === 'action' ? (
          <ActionEditorPage
            state={editor}
            groups={registry.groups}
            busy={mutationPending || quickBusy || !!quickPending}
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
            actions={canonicalCatalog.filter((row) => row.canonicalId && obj(obj(canonicalPage.references)[row.canonicalId]).digest).map((row) => ({
              ...emptyActionDraft(), id: row.canonicalId, name: row.name, description: row.description,
              enabled: true, execution: { kind: 'api' as const, method: 'GET' as const, path: '/api/sessions' },
            }))}
            busy={mutationPending || quickBusy || !!quickPending}
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
                size="sm"
                className="ml-auto max-sm:w-full max-sm:justify-center"
                onClick={newAdvancedAction}
              >
                <Braces size={13} /> New advanced Action
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                className="max-sm:w-full max-sm:justify-center"
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
                filteredActions.length === 0 && canonicalOnlyActions.length === 0 ? (
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
                      {canonicalOnlyActions.map((row) => (
                        <CanonicalActionRow
                          key={row.key}
                          row={row}
                          onOpen={() => setCanonicalTarget({ kind: 'action', key: row.key })}
                        />
                      ))}
                      {filteredActions.map((action) => (
                        <CanonicalActionRow key={action.id} row={canonicalCatalog.find((row) => row.legacyId === action.id)!} onOpen={() => openAction(action)} />
                      ))}
                    </ul>
                  </div>
                )
              ) : filteredGroups.length === 0 && canonicalOnlyGroups.length === 0 ? (
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
                    {canonicalOnlyGroups.map((row) => (
                      <CanonicalGroupRow
                        key={row.key}
                        row={row}
                        onOpen={() => setCanonicalTarget({ kind: 'group', key: row.key })}
                      />
                    ))}
                    {filteredGroups.map((group) => (
                      <CanonicalGroupRow key={group.id} row={canonicalGroupRows.find((row) => row.id === group.id)!} onOpen={() => openGroup(group)} />
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



function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

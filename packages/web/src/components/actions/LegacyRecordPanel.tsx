import { useMemo, useRef, useState } from 'react';
import { actionRequest } from '@/services/api';
import { useUIStore } from '@/stores/uiStore';
import { useActionRegistryStore } from '@/stores/actionRegistryStore';
import type { Action, ActionGroup, ActionRegistry } from '@/types/actions';
import { Button } from '@/components/ui/Button';
import { Badge, ConfirmDialog, Section } from './ActionsUi';
import { ActionEditorPage, GroupEditorPage } from './ActionGroupEditors';
import { MappedActionPanel } from './MappedActionPanel';
import { UseInSessionDialog } from './UseInSessionDialog';
import {
  actionEditorState,
  groupEditorState,
  isEditorDirty,
  sessionOperationError,
  type EditorState,
} from './actionsModel';
import { adoptionTone, type UnifiedActionRow, type UnifiedGroupRow } from './unifiedModel';

type RegistryWithItem = ActionRegistry & { item?: Action | ActionGroup };

const messageOf = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

/**
 * The legacy writer surface for one record.
 *
 * Adopting a record into the canonical catalog does not delete its original
 * editor: preserved records keep blocking with their reason, mapped records
 * keep the P6 duplicate/lifecycle/delete panel, and legacy-only Groups keep
 * their build/unbuild/bind flow. Every request keeps the original registry
 * CAS revision so a concurrent refresh can never overwrite a newer record.
 */
export function LegacyRecordPanel({
  action,
  group,
  onClose,
  onChanged,
}: {
  action?: UnifiedActionRow;
  group?: UnifiedGroupRow;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const registry = useActionRegistryStore((state) => state.registry);
  const mutationPending = useActionRegistryStore((state) => state.mutationPending);
  const showToast = useUIStore((state) => state.showToast);
  const [editor, setEditor] = useState<EditorState | null>(() => seedEditor(action, group));
  const [error, setError] = useState('');
  const [discard, setDiscard] = useState(false);
  const [binding, setBinding] = useState(false);
  const busyRef = useRef(false);

  const notify = (message: string, type: 'info' | 'error' | 'warning' = 'info') =>
    showToast(message, type);
  const legacyAction = action?.legacy;
  const legacyGroup = group?.legacy;
  const liveGroup = useMemo(
    () => (group ? (registry.groups.find((item) => item.id === group.id) ?? null) : null),
    [group, registry.groups],
  );

  function seedEditor(
    actionRow: UnifiedActionRow | undefined,
    groupRow: UnifiedGroupRow | undefined,
  ): EditorState | null {
    if (actionRow?.legacy) return actionEditorState(actionRow.legacy, 'edit');
    if (groupRow?.legacy)
      return groupEditorState(
        { id: groupRow.legacy.id, name: groupRow.legacy.name, action_ids: [...groupRow.legacy.action_ids] },
        'edit',
      );
    return null;
  }

  function requestClose() {
    if (editor && isEditorDirty(editor)) setDiscard(true);
    else onClose();
  }

  async function mutate<T extends ActionRegistry>(
    work: (expectedRevision: number) => Promise<T>,
    done: (result: T) => void | Promise<void>,
  ) {
    if (busyRef.current) return;
    busyRef.current = true;
    setError('');
    try {
      const result = await useActionRegistryStore.getState().runMutation(work);
      await done(result);
      await onChanged();
    } catch (caught) {
      const message = messageOf(caught);
      setError(message);
      notify(message, 'error');
      if (/revision|changed|conflict/i.test(message)) {
        void useActionRegistryStore.getState().ensureRegistry({ force: true });
      }
    } finally {
      busyRef.current = false;
    }
  }

  async function saveAction() {
    if (!editor || editor.kind !== 'action') return;
    const { draft, schemaError } = editor;
    if (editor.executionError || sessionOperationError(draft)) {
      setError(editor.executionError || sessionOperationError(draft));
      return;
    }
    if (!draft.name.trim() || !draft.description.trim() || schemaError) {
      setError('Name, description and a valid input schema are required.');
      return;
    }
    await mutate(
      (expectedRevision) =>
        editor.mode === 'new'
          ? actionRequest<RegistryWithItem>('actions/actions', 'POST', {
              item: withoutId(draft),
              expectedRevision,
            })
          : actionRequest<RegistryWithItem>(
              `actions/actions/${encodeURIComponent(draft.id)}`,
              'PUT',
              { item: draft, expectedRevision },
            ),
      (result) => {
        const item = result.item as Action | undefined;
        notify(editor.mode === 'new' ? `Action created${item ? ` · ${item.id}` : ''}` : 'Action saved');
        onClose();
      },
    );
  }

  async function saveGroup() {
    if (!editor || editor.kind !== 'group') return;
    if (!editor.draft.name.trim()) {
      setError('A Group name is required.');
      return;
    }
    await mutate(
      (expectedRevision) => {
        const item =
          editor.mode === 'new'
            ? { name: editor.draft.name.trim(), action_ids: [...editor.draft.action_ids] }
            : {
                id: editor.draft.id,
                name: editor.draft.name.trim(),
                action_ids: [...editor.draft.action_ids],
              };
        return actionRequest<RegistryWithItem>(
          editor.mode === 'new'
            ? 'actions/groups'
            : `actions/groups/${encodeURIComponent(editor.draft.id)}`,
          editor.mode === 'new' ? 'POST' : 'PUT',
          { item, expectedRevision },
        );
      },
      (result) => {
        const item = result.item as ActionGroup | undefined;
        notify(editor.mode === 'new' ? `Group created${item ? ` · ${item.id}` : ''}` : 'Group saved');
        onClose();
      },
    );
  }

  async function buildGroup(id: string) {
    await mutate(
      (expectedRevision) =>
        actionRequest<ActionRegistry>(`actions/groups/${encodeURIComponent(id)}/build`, 'POST', {
          expectedRevision,
        }),
      () => notify('Group built · fixed release registered'),
    );
  }

  async function unbuildGroup(id: string) {
    await mutate(
      (expectedRevision) =>
        actionRequest<ActionRegistry>(`actions/groups/${encodeURIComponent(id)}/unbuild`, 'POST', {
          expectedRevision,
        }),
      () => notify('Group unbuilt · Session bindings removed'),
    );
  }

  async function deleteGroup(id: string) {
    await mutate(
      (expectedRevision) =>
        actionRequest<ActionRegistry>(
          `actions/groups/${encodeURIComponent(id)}?expectedRevision=${expectedRevision}`,
          'DELETE',
        ),
      () => {
        notify('Group deleted');
        onClose();
      },
    );
  }

  async function deleteAction(action_: Action) {
    await mutate(
      (expectedRevision) =>
        actionRequest<ActionRegistry>(
          `actions/actions/${encodeURIComponent(action_.id)}?expectedRevision=${expectedRevision}`,
          'DELETE',
        ),
      () => {
        notify('Action deleted');
        onClose();
      },
    );
  }

  if (!editor) {
    return (
      <Section
        title={action?.name ?? group?.name ?? 'Legacy record'}
        description="该记录只存在于统一目录，旧编辑器没有可写的兼容来源。"
        actions={
          <Button size="sm" onClick={onClose}>
            返回
          </Button>
        }
      >
        <p className="text-xs text-text-secondary">
          请在统一目录条目中编辑定义、版本与发布。旧记录状态：
          <Badge tone={adoptionTone(action?.adoption ?? group?.adoption ?? 'canonical')}>
            {action?.adoption ?? group?.adoption ?? 'canonical'}
          </Badge>
        </p>
      </Section>
    );
  }

  if (action?.preservedReason) {
    return (
      <Section
        title={`${action.name} · 已保留`}
        description="该 Action 的旧定义无法被旧编辑器无损表达，因此写入被明确阻断，原字节与来源保持不变。"
        actions={
          <Button size="sm" onClick={onClose}>
            返回
          </Button>
        }
      >
        <p role="status" className="break-words rounded border border-warning/35 bg-warning/10 p-2 text-xs text-warning">
          保留原因：{action.preservedReason}
        </p>
        <p className="mt-2 text-xs text-text-secondary">
          该记录保留原执行描述与来源，不在旧编辑器中覆盖；请在统一目录中查看它的 canonical 版本与引用。
        </p>
      </Section>
    );
  }

  if (action?.mapped) {
    return (
      <MappedActionPanel
        key={action.mapped.legacyId}
        mapped={action.mapped}
        legacyAction={legacyAction}
        existingNames={registry.actions.map((item) => item.name)}
        onClose={requestClose}
        onChanged={onChanged}
        onOpenCopy={() => {
          notify('Canonical copy created; manage it in the unified catalog.', 'info');
          onClose();
        }}
        onRequestDelete={(record) => void deleteAction(record)}
        error={error}
      />
    );
  }

  return (
    <>
      {editor.kind === 'action' ? (
        <ActionEditorPage
          state={editor}
          groups={registry.groups}
          busy={mutationPending}
          error={error}
          onChange={(next) => {
            setEditor(next);
            setError('');
          }}
          onSave={() => void saveAction()}
          onCancel={requestClose}
          onDelete={() => void deleteAction(editor.draft)}
        />
      ) : (
        <GroupEditorPage
          state={editor}
          liveGroup={liveGroup}
          actions={registry.actions}
          busy={mutationPending}
          error={error}
          onChange={(next) => {
            setEditor(next);
            setError('');
          }}
          onSave={() => void saveGroup()}
          onCancel={requestClose}
          onDelete={() => liveGroup && void deleteGroup(liveGroup.id)}
          onBuild={() => liveGroup && void buildGroup(liveGroup.id)}
          onUnbuild={() => liveGroup && void unbuildGroup(liveGroup.id)}
          onUseInSession={() => setBinding(true)}
        />
      )}
      {discard && (
        <ConfirmDialog
          title="Discard unsaved changes?"
          message="Your draft has unsaved changes. Leaving now discards them and returns to the list."
          confirmLabel="Discard draft"
          onCancel={() => setDiscard(false)}
          onConfirm={() => {
            setDiscard(false);
            notify('Draft discarded');
            onClose();
          }}
        />
      )}
      {binding && legacyGroup && (
        <UseInSessionDialog group={legacyGroup} onClose={() => setBinding(false)} />
      )}
    </>
  );
}

function withoutId(action: Action): Omit<Action, 'id'> {
  const { id: _ignored, ...item } = action;
  void _ignored;
  return item;
}

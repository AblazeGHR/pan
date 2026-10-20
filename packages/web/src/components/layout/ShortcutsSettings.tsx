import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import {
  bindingFromEvent,
  detectShortcutPlatform,
  findBindingConflicts,
  formatBinding,
  isReservedBinding,
  resolveBinding,
  platformBinding,
} from '@/shortcuts/bindings';
import { useShortcutActions, type ShortcutAction, type ShortcutGroup } from '@/shortcuts/registry';
import { setShortcutRecording } from '@/shortcuts/useGlobalShortcuts';

const GROUP_ORDER: ShortcutGroup[] = ['General', 'Composer', 'Panels', 'Queue', 'Session'];
const GROUP_LABELS: Record<ShortcutGroup, string> = {
  General: 'General',
  Composer: 'Message composer',
  Panels: 'Panels — generated from the live view/plugin registry',
  Queue: 'Queue',
  Session: 'Selected Session',
};

const FIXED_SHORTCUTS = [
  {
    label: 'Save file',
    binding: 'Mod+S',
    scope: 'Only while the Monaco editor is focused.',
  },
  {
    label: 'Paste',
    binding: 'Mod+V',
    scope: 'Only inside the terminal (xterm); the browser paste action is preserved.',
  },
  {
    label: 'Close the top overlay',
    binding: 'Escape',
    scope: 'Dialog/menu-local; it is not a configurable app action.',
  },
] as const;

function actionBinding(
  action: ShortcutAction,
  bindings: Record<string, string | null>,
): string | null {
  return resolveBinding(action.id, action.defaultBinding, bindings);
}

export function ShortcutsSettings() {
  const platform = useMemo(() => detectShortcutPlatform(), []);
  const actions = useShortcutActions();
  const bindings = useAppSettingsStore((state) => state.shortcuts);
  const setShortcutBinding = useAppSettingsStore((state) => state.setShortcutBinding);
  const resetShortcutBinding = useAppSettingsStore((state) => state.resetShortcutBinding);
  const resetAllShortcuts = useAppSettingsStore((state) => state.resetAllShortcuts);
  const pluginError = usePluginPanelStore((state) => state.error);
  const [recordingId, setRecordingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feedbackActionId, setFeedbackActionId] = useState<string | null>(null);
  const feedbackRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (error) feedbackRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [error]);
  const [saveStatus, setSaveStatus] = useState('');
  const [saving, setSaving] = useState(false);
  const save = useCallback((write: Promise<void>, actionId: string | null = null) => {
    setFeedbackActionId(actionId);
    setSaving(true);
    setSaveStatus('Saving…');
    void write.then(() => setSaveStatus('Saved — active immediately.')).catch((cause: unknown) => {
      setSaveStatus('');
      setError(`Could not save shortcut: ${cause instanceof Error ? cause.message : String(cause)}. Please retry.`);
    }).finally(() => setSaving(false));
  }, []);

  // Re-read the live catalog whenever Settings opens. New panels appear and
  // removed panels stop producing actions; a failed refresh keeps the last
  // good built-in/plugin list and never crashes the page.
  useEffect(() => {
    void usePluginPanelStore.getState().load(true);
  }, []);

  const conflicts = useMemo(() => findBindingConflicts(actions, bindings), [actions, bindings]);
  const conflictingIds = useMemo(() => {
    const ids = new Set<string>();
    for (const actionIds of conflicts.values()) {
      for (const id of actionIds) ids.add(id);
    }
    return ids;
  }, [conflicts]);

  useEffect(() => {
    if (!recordingId) {
      setShortcutRecording(false);
      return;
    }
    setShortcutRecording(true);
    const handler = (event: KeyboardEvent) => {
      // Composition belongs to the IME; prevent it reaching business listeners.
      if (event.isComposing || event.keyCode === 229) {
        event.stopImmediatePropagation();
        return;
      }
      // Cancel capture and let native Tab/Shift+Tab move focus.
      if (event.key === 'Tab' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        setRecordingId(null);
        setError(null);
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.repeat) return;
      if (event.key === 'Escape' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        setRecordingId(null);
        setError(null);
        return;
      }
      const binding = bindingFromEvent(event, platform);
      if (!binding) {
        if (!['Control', 'Meta', 'Alt', 'Shift', 'AltGraph'].includes(event.key)) {
          setError('Use a modifier combination or a function key (F1–F24). Escape cancels; Tab moves focus.');
        }
        return;
      }
      if (isReservedBinding(binding, recordingId)) {
        setError(`${formatBinding(binding, platform)} is reserved by the browser or the editor/terminal.`);
        setRecordingId(null);
        return;
      }
      const action = actions.find((candidate) => candidate.id === recordingId);
      const conflict = actions.find(
        (candidate) =>
          candidate.id !== recordingId &&
          platformBinding(actionBinding(candidate, bindings) ?? '') === binding,
      );
      if (conflict) {
        setError(
          `${formatBinding(binding, platform)} is already assigned to “${conflict.label}”. Clear it first.`,
        );
        setRecordingId(null);
        return;
      }
      if (!action) {
        setRecordingId(null);
        return;
      }
      save(setShortcutBinding(action.id, binding), action.id);
      setError(null);
      setRecordingId(null);
    };
    window.addEventListener('keydown', handler, true);
    return () => {
      window.removeEventListener('keydown', handler, true);
      setShortcutRecording(false);
    };
  }, [actions, bindings, platform, recordingId, setShortcutBinding, save]);

  const renderRow = (action: ShortcutAction) => {
    const binding = actionBinding(action, bindings);
    const display = formatBinding(binding, platform);
    const isRecording = recordingId === action.id;
    const conflict = binding ? conflicts.get(platformBinding(binding)) : undefined;
    const hasOverride = Object.prototype.hasOwnProperty.call(bindings, action.id);
    const isDefault = binding === action.defaultBinding && !hasOverride;
    return (
      <div
        key={action.id}
        data-shortcut-action={action.id}
        className="px-3 py-2.5"
      >
        <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-text-primary">{action.label}</span>
              {conflict ? (
                <span
                  data-testid="shortcut-conflict"
                  className="rounded bg-danger/10 px-1.5 py-0.5 text-[10px] font-medium text-danger"
                >
                  Conflict
                </span>
              ) : null}
            </div>
            <p className="mt-0.5 text-[11px] leading-relaxed text-text-tertiary">
              {action.description}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
            <kbd
              data-shortcut-binding={action.id}
              className={`min-w-[5.5rem] rounded border px-2 py-1 text-center font-mono text-[11px] ${
                isRecording
                  ? 'border-accent bg-accent/10 text-accent'
                  : conflictingIds.has(action.id)
                    ? 'border-danger text-danger'
                    : 'border-border-default bg-bg-tertiary text-text-secondary'
              }`}
              aria-live={isRecording ? 'polite' : undefined}
            >
              {isRecording ? 'Press keys…' : display}
            </kbd>
            <button
              type="button"
              data-shortcut-record={action.id}
              aria-label={`Record shortcut for ${action.label}`}
              aria-pressed={isRecording}
              aria-describedby={feedbackActionId === action.id ? `shortcut-feedback-${action.id}` : undefined}
              disabled={saving}
              onClick={() => {
                setError(null);
                setSaveStatus('');
                setFeedbackActionId(action.id);
                setRecordingId(isRecording ? null : action.id);
              }}
              className="rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] text-text-secondary hover:bg-bg-hover hover:text-text-primary"
            >
              {isRecording ? 'Cancel' : 'Record'}
            </button>
            <button
              type="button"
              data-shortcut-clear={action.id}
              aria-label={`Clear shortcut for ${action.label}`}
              disabled={saving || binding === null}
              onClick={() => {
                setError(null);
                setRecordingId(null);
                save(setShortcutBinding(action.id, null), action.id);
              }}
              className="rounded border border-border-default px-2 py-1 text-[11px] text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
            >
              Clear
            </button>
            <button
              type="button"
              data-shortcut-reset={action.id}
              aria-label={`Restore default shortcut for ${action.label}`}
              disabled={saving || isDefault}
              onClick={() => {
                setError(null);
                setRecordingId(null);
                save(resetShortcutBinding(action.id), action.id);
              }}
              className="rounded border border-border-default px-2 py-1 text-[11px] text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
            >
              Default
            </button>
          </div>
        </div>
        {feedbackActionId === action.id && (
          <p
            ref={feedbackRef}
            id={`shortcut-feedback-${action.id}`}
            role={error ? 'alert' : 'status'}
            data-testid={error ? 'shortcut-recorder-error' : 'shortcut-save-status'}
            className={`mt-1.5 text-xs ${error ? 'text-danger' : 'text-text-secondary'}`}
          >
            {error ?? (isRecording ? 'Press a combination or function key. Escape cancels; Tab moves focus.' : saveStatus)}
          </p>
        )}
        {conflict ? (
          <p className="mt-1.5 text-[11px] text-danger">
            Also assigned to{' '}
            {conflict
              .filter((id) => id !== action.id)
              .map((id) => actions.find((candidate) => candidate.id === id)?.label ?? id)
              .join(', ')}
            . Conflicting shortcuts are inactive until resolved.
          </p>
        ) : null}
      </div>
    );
  };

  return (
    <div data-testid="shortcuts-settings" className="space-y-6">
      <section>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">
              Keyboard shortcuts
            </h3>
            <p className="mt-1 text-[11px] leading-relaxed text-text-tertiary">
              Choose Record, then press a modifier combination, function key, or composer Enter.
              Escape cancels; Tab cancels and moves focus. Saved in settings. Display follows this platform:{' '}
              <strong>{platform === 'mac' ? 'Cmd (Meta)' : 'Ctrl'}</strong>. Shortcuts are
              Composer actions run only in the message input. Other app actions ignore editable
              fields except explicitly scoped queue/Session actions. IME, repeat and dialogs suppress actions.
            </p>
          </div>
          <button
            type="button"
            data-testid="shortcuts-restore-all"
            disabled={saving}
            onClick={() => {
              setError(null);
              setRecordingId(null);
              save(resetAllShortcuts());
            }}
            className="shrink-0 rounded border border-border-default bg-bg-tertiary px-2.5 py-1.5 text-[11px] text-text-secondary hover:bg-bg-hover hover:text-text-primary"
          >
            Restore all defaults
          </button>
        </div>
        {!feedbackActionId && <p role="status" className="mt-2 text-xs text-text-secondary">{saveStatus}</p>}
        {error && !feedbackActionId ? (
          <p
            role="alert"
            data-testid="shortcut-recorder-error"
            className="mt-2 rounded border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-[11px] text-danger"
          >
            {error}
          </p>
        ) : null}
        {pluginError ? (
          <p className="mt-2 text-[11px] text-text-tertiary">
            Plugin panel catalog could not be refreshed ({pluginError}). Built-in views remain
            configurable; reconnect or reopen Settings to retry.
          </p>
        ) : null}
      </section>

      {GROUP_ORDER.map((group) => {
        const groupActions = actions.filter((action) => action.group === group);
        if (groupActions.length === 0) return null;
        return (
          <section key={group} data-shortcut-group={group}>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-tertiary">
              {GROUP_LABELS[group]}
            </h3>
            <div className="divide-y divide-border-muted rounded-md border border-border-muted bg-bg-primary">
              {groupActions.map(renderRow)}
            </div>
            {group === 'Panels' ? (
              <p className="mt-1.5 text-[11px] leading-relaxed text-text-tertiary">
                Panel actions are derived from the actual navigation registry. Their ids use
                pluginId/panelId (never an array index), so adding or removing a panel never moves
                a saved binding to another panel. New panels start unbound.
              </p>
            ) : null}
          </section>
        );
      })}

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-tertiary">
          Fixed, context-scoped shortcuts
        </h3>
        <div className="divide-y divide-border-muted rounded-md border border-border-muted bg-bg-primary">
          {FIXED_SHORTCUTS.map((item) => (
            <div key={item.label} className="flex flex-wrap items-center gap-2 px-3 py-2">
              <span className="min-w-0 flex-1 text-xs text-text-primary">{item.label}</span>
              <kbd className="rounded border border-border-default bg-bg-tertiary px-2 py-1 font-mono text-[11px] text-text-secondary">
                {formatBinding(item.binding, platform)}
              </kbd>
              <span className="basis-full text-[11px] text-text-tertiary md:basis-auto">
                {item.scope}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-text-tertiary">
          These stay owned by the editor, terminal and focused overlay; they are intentionally not
          remappable so their built-in behavior is preserved.
        </p>
      </section>
    </div>
  );
}

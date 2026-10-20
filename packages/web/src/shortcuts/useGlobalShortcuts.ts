import { useEffect, useMemo } from 'react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import {
  bindingFromEvent,
  detectShortcutPlatform,
  resolveBinding,
  type ShortcutPlatform,
} from './bindings';
import { getShortcutActions } from './registry';

let recordingShortcut = false;

/** While a recorder is capturing keys, every business shortcut is disabled. */
export function setShortcutRecording(active: boolean): void {
  recordingShortcut = active;
}

export function isShortcutRecording(): boolean {
  return recordingShortcut;
}

export function isEditableShortcutTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target instanceof HTMLElement && target.isContentEditable) return true;
  return !!target.closest(
    'input,textarea,select,[contenteditable]:not([contenteditable="false"]),.monaco-editor,.xterm,[data-shortcut-ignore]',
  );
}

/** The chat composer is an explicit scope for Session/queue actions. */
export function isComposerShortcutTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return !!target.closest('.composer-editor,[data-testid="rich-text-composer"]');
}

export function hasOpenModal(): boolean {
  return typeof document !== 'undefined' &&
    document.querySelector('[role="dialog"][aria-modal="true"]') !== null;
}

/**
 * Shared guard for real keydowns; exported for focused unit coverage.
 * `allowInComposer` is granted only by actions that explicitly own the chat
 * composer scope (queue/session actions). All other editable targets — inputs,
 * textareas, Monaco, terminal, other contenteditable — remain excluded.
 */
export function shouldIgnoreShortcutEvent(
  event: KeyboardEvent,
  _platform: ShortcutPlatform = detectShortcutPlatform(),
  allowInComposer = false,
): boolean {
  if (recordingShortcut) return true;
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return true;
  if (event.repeat) return true;
  if (isEditableShortcutTarget(event.target)) {
    return !(allowInComposer && isComposerShortcutTarget(event.target));
  }
  if (hasOpenModal()) return true;
  return false;
}

/**
 * Mount exactly one document-level shortcut listener for the app shell.
 *
 * Keeping this in the shell avoids the old pattern where separate components
 * registered overlapping document listeners and could double-fire an action.
 * The action list is resolved on every keydown so plugin-panel registration
 * and removal take effect without re-binding the listener.
 */
export function useGlobalShortcuts(navigate: (to: string) => void): void {
  const platform = useMemo(() => detectShortcutPlatform(), []);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const binding = bindingFromEvent(event, platform);
      if (!binding) return;
      const actions = getShortcutActions();
      const bindings = useAppSettingsStore.getState().shortcuts;
      const owners = actions.filter(
        (action) =>
          resolveBinding(action.id, action.defaultBinding, bindings) === binding,
      );
      // A persisted conflict is left visible in Settings and is not executed
      // ambiguously. Normal recording refuses to create this state.
      if (owners.length !== 1) return;
      const action = owners[0]!;
      if (shouldIgnoreShortcutEvent(event, platform, action.allowInComposer === true)) return;
      event.preventDefault();
      event.stopPropagation();
      action.run({ navigate });
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [navigate, platform]);
}

import { useMemo } from 'react';
import {
  getViewEntriesSnapshot,
  useViewEntries,
  type ViewEntry,
} from '@/components/layout/viewEntries';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { AgentQueueItem } from '@/types';
import { runSessionToggleShortcut } from '@/utils/sessionQuickActions';
import {
  hasChatScrollToBottomHandler,
  requestChatScrollToBottom,
} from './chatScrollBridge';
import { requestLockedComposerEnqueue } from './composerBridge';
import { hasQueueSelectedEditHandler, requestQueueSelectedEdit } from './queueBridge';

export type ShortcutGroup = 'General' | 'Panels' | 'Queue' | 'Session' | 'Composer';

export interface ShortcutContext {
  navigate: (to: string) => void;
}

export interface ShortcutAction {
  /** Stable action id; persisted bindings are keyed by this value. */
  id: string;
  label: string;
  description: string;
  group: ShortcutGroup;
  /** Default binding; `null` means new/high-impact actions start unbound. */
  defaultBinding: string | null;
  /**
   * Queue/Session actions explicitly operate on the selected Session and must
   * remain usable while the chat composer is focused. Other inputs, Monaco and
   * the terminal still ignore every action.
   */
  allowInComposer?: boolean;
  composerOnly?: boolean;
  /**
   * Runtime gate: a disabled action neither runs nor consumes the key. The
   * dispatcher checks it before `preventDefault`, so scoped actions (for
   * example the queue panel) can stay bound without swallowing the same chord
   * when their surface is not available.
   */
  isEnabled?: () => boolean;
  run: (context: ShortcutContext) => void;
}

const PANEL_DEFAULT_BINDINGS: Readonly<Record<string, string>> = {
  'builtin:chat': 'Mod+1',
  'builtin:editor': 'Mod+2',
};

function selectedSessionId(): string | null {
  const store = useSessionStore.getState();
  const id = store.currentSessionId;
  if (!id) return null;
  return store.sessions.some((session) => session.id === id) ? id : null;
}

/** Rows the panel treats as selectable: exactly the still-queued projection. */
function queuedRows(sessionId: string): AgentQueueItem[] {
  return (useQueueStore.getState().queues[sessionId] ?? []).filter(
    (item) => item.meta?.dispatchState === 'queued',
  );
}

/**
 * Scope of the queue-panel shortcuts: the panel must be expanded and the
 * selected Session must still have queued rows. Native-control Sessions keep
 * their panel always visible but leave `panelOpen` false, so Alt+Arrow stays
 * available to the embedded CLI terminal there.
 */
function queuePanelScope(): { sessionId: string; items: AgentQueueItem[] } | null {
  const store = useQueueStore.getState();
  if (!store.panelOpen) return null;
  const sessionId = selectedSessionId();
  if (!sessionId) return null;
  const items = queuedRows(sessionId);
  return items.length > 0 ? { sessionId, items } : null;
}

function selectedQueueIndex(sessionId: string, items: AgentQueueItem[]): number {
  const selected = useQueueStore.getState().queueSelection[sessionId] ?? null;
  return selected ? items.findIndex((item) => item.id === selected) : -1;
}

function selectedQueueRow(): { sessionId: string; item: AgentQueueItem } | null {
  const scope = queuePanelScope();
  if (!scope) return null;
  const index = selectedQueueIndex(scope.sessionId, scope.items);
  if (index < 0) return null;
  return { sessionId: scope.sessionId, item: scope.items[index]! };
}

/**
 * Move the highlight by one row without wrapping. With nothing selected,
 * Down starts at the first row and Up at the last one; the boundaries clamp,
 * so a held chord can never jump to the other end or reorder the queue.
 */
function moveQueueSelection(delta: -1 | 1): void {
  const scope = queuePanelScope();
  if (!scope) return;
  const index = selectedQueueIndex(scope.sessionId, scope.items);
  const next = index < 0
    ? (delta > 0 ? 0 : scope.items.length - 1)
    : Math.min(scope.items.length - 1, Math.max(0, index + delta));
  useQueueStore.getState().setQueueSelection(scope.sessionId, scope.items[next]!.id);
}

function canSelectQueueRow(): boolean {
  return queuePanelScope() !== null;
}

function canEditSelectedQueueRow(): boolean {
  const target = selectedQueueRow();
  if (!target) return false;
  if (!hasQueueSelectedEditHandler()) return false;
  const store = useQueueStore.getState();
  if (store.edits[target.sessionId]) return false;
  const session = useSessionStore.getState().sessions.find(
    (candidate) => candidate.id === target.sessionId,
  );
  return session?.readonlySession !== true;
}

function canToggleSelectedQueueLock(): boolean {
  const target = selectedQueueRow();
  if (!target) return false;
  const store = useQueueStore.getState();
  return !store.queueLockUpdating[target.sessionId] && !store.agentReportsPauseUpdating[target.sessionId];
}

const GENERAL_ACTIONS: ShortcutAction[] = [
  {
    id: 'app.toggleSidebar',
    label: 'Toggle sidebar',
    description: 'Collapse or expand the left sidebar.',
    group: 'General',
    defaultBinding: 'Mod+B',
    run: () => useUIStore.getState().toggleSidebar(),
  },
  {
    id: 'app.commandPalette',
    label: 'Open command palette',
    description: 'Open or close the command palette. Escape closes it.',
    group: 'General',
    defaultBinding: 'Mod+K',
    run: () => {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new Event('pan:shortcut:command-palette'));
      }
    },
  },
  {
    id: 'app.historySearch',
    label: 'Search current Session history',
    description: 'Opens Session history search when it is enabled in Appearance.',
    group: 'General',
    defaultBinding: 'Mod+F',
    run: () => {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new Event('pan:shortcut:history-search'));
      }
    },
  },
  {
    id: 'app.globalHistorySearch',
    label: 'Search all Session history',
    description: 'Opens global Session history search when it is enabled in Appearance.',
    group: 'General',
    defaultBinding: 'Mod+G',
    run: () => {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new Event('pan:shortcut:global-history-search'));
      }
    },
  },
  {
    id: 'chat.scrollToBottom',
    label: 'Scroll chat to bottom',
    description:
      'Jumps the visible chat to its newest message with the same recovery the scroll-to-bottom button runs, and resumes following new output. Inactive when no chat list is on screen, and ignored in inputs, the editor, the terminal and while recording a shortcut.',
    group: 'General',
    defaultBinding: 'Mod+End',
    isEnabled: () => {
      const sessionId = selectedSessionId();
      return sessionId !== null && hasChatScrollToBottomHandler(sessionId);
    },
    run: () => {
      const sessionId = selectedSessionId();
      if (!sessionId) return;
      requestChatScrollToBottom(sessionId);
    },
  },
];

const COMPOSER_ACTIONS: ShortcutAction[] = [
  { id: 'composer.send', label: 'Send message', description: 'Send using the current composer mode (including locked queue mode).', defaultBinding: 'Enter' },
  { id: 'composer.steer', label: 'Steer current task', description: 'Use the existing Steer button when available. Starts unbound; all Steer gates still apply.', defaultBinding: null },
  { id: 'composer.newline', label: 'Insert newline', description: 'Replace the current selection with a newline and keep focus in the composer.', defaultBinding: 'Shift+Enter' },
].map((action) => ({ ...action, group: 'Composer', composerOnly: true, run: () => {} }));

const QUEUE_ACTIONS: ShortcutAction[] = [
  {
    id: 'queue.selectPrevious',
    label: 'Select previous queued message',
    description:
      'Moves the queue-panel highlight up one row without moving or sending it. Only active while the queue panel is open with queued messages; selection clamps at the first row and never wraps.',
    group: 'Queue',
    defaultBinding: 'Alt+ArrowUp',
    allowInComposer: true,
    isEnabled: canSelectQueueRow,
    run: () => moveQueueSelection(-1),
  },
  {
    id: 'queue.selectNext',
    label: 'Select next queued message',
    description:
      'Moves the queue-panel highlight down one row without moving or sending it. Only active while the queue panel is open with queued messages; selection clamps at the last row and never wraps.',
    group: 'Queue',
    defaultBinding: 'Alt+ArrowDown',
    allowInComposer: true,
    isEnabled: canSelectQueueRow,
    run: () => moveQueueSelection(1),
  },
  {
    id: 'queue.editSelected',
    label: 'Edit selected queued message',
    description:
      'Opens the existing edit flow for the highlighted queue row and fills its body back into the composer. Agent/system confirmation and the edit lease still apply. Only active while the queue panel is open with a selected row; otherwise it does nothing.',
    group: 'Queue',
    defaultBinding: 'Alt+Enter',
    allowInComposer: true,
    isEnabled: canEditSelectedQueueRow,
    run: () => {
      if (!canEditSelectedQueueRow()) return;
      const target = selectedQueueRow();
      if (!target) return;
      // The panel owns the confirmation, lease and composer focus hand-off.
      requestQueueSelectedEdit(target.sessionId, target.item.id);
    },
  },
  {
    id: 'queue.toggleSelectedLock',
    label: 'Lock or unlock selected queued message',
    description:
      'Toggles the lock on the highlighted queue row through the existing per-item lock action. Only active while the queue panel is open with a selected row; nothing happens without a selection or while a lock update is running.',
    group: 'Queue',
    defaultBinding: 'Alt+L',
    allowInComposer: true,
    isEnabled: canToggleSelectedQueueLock,
    run: () => {
      if (!canToggleSelectedQueueLock()) return;
      const target = selectedQueueRow();
      if (!target) return;
      void useQueueStore.getState().setQueueItemLocked(
        target.sessionId,
        target.item.id,
        target.item.meta?.locked !== true,
      );
    },
  },
  {
    id: 'queue.togglePanel',
    label: 'Expand or collapse the queue',
    description: 'Toggles the selected Session’s send-queue panel.',
    group: 'Queue',
    defaultBinding: null,
    allowInComposer: true,
    run: () => {
      if (!selectedSessionId()) return;
      useQueueStore.getState().togglePanel();
    },
  },
  {
    id: 'queue.enqueueLocked',
    label: 'Enqueue draft as locked message',
    description:
      'Adds the current composer text, attachment occurrences and URL nodes to the queue with locked=true, using the normal send transaction. The server locks the item before it can be dispatched. The draft is restored if enqueue is rejected.',
    group: 'Queue',
    defaultBinding: null,
    allowInComposer: true,
    run: () => {
      const sessionId = selectedSessionId();
      if (!sessionId) return;
      if (!requestLockedComposerEnqueue(sessionId)) {
        useUIStore.getState().showToast(
          'Open the chat composer for the selected Session first',
          'error',
        );
      }
    },
  },
  {
    id: 'queue.lockAll',
    label: 'Lock all queued messages',
    description:
      'Locks every message that is still queued (the existing “Lock all” action). It does not pause the queue, lock future messages, or stop the Worker.',
    group: 'Queue',
    defaultBinding: null,
    allowInComposer: true,
    run: () => {
      const sessionId = selectedSessionId();
      if (!sessionId) return;
      const store = useQueueStore.getState();
      if (store.queueLockUpdating[sessionId] || store.agentReportsPauseUpdating[sessionId]) {
        return;
      }
      // The server locks every row that is still queued in one durable
      // revision. It never pauses the queue and never stops the Worker.
      void store.setQueueItemsLocked(sessionId, true);
    },
  },
  {
    id: 'queue.pauseAll',
    label: 'Pause everything',
    description:
      'Pauses queue dispatch for the selected Session, including future messages (the existing “Pause everything” action). It does not lock queued items or stop the Worker.',
    group: 'Queue',
    defaultBinding: null,
    allowInComposer: true,
    run: () => {
      const sessionId = selectedSessionId();
      if (!sessionId) return;
      const store = useQueueStore.getState();
      if (!store.queuePauseLoaded[sessionId] || store.queuePauseUpdating[sessionId]) return;
      void store.setQueuePaused(sessionId, !(store.queuePaused[sessionId] ?? false));
    },
  },
];

const SESSION_ACTIONS: ShortcutAction[] = [
  {
    id: 'session.toggleStopReport',
    label: 'Toggle Stop report',
    description:
      'Stops or restores the direct manager’s completion-report subscription for the selected Session, using the card’s Stop report semantics. Disabled without a direct manager, exactly like the card.',
    group: 'Session',
    defaultBinding: null,
    allowInComposer: true,
    run: () => {
      const sessionId = selectedSessionId();
      if (!sessionId) return;
      void runSessionToggleShortcut(sessionId, 'stopReport');
    },
  },
  {
    id: 'session.toggleNotification',
    label: 'Toggle notification bell',
    description:
      'Turns all message-bridge notifications on or off for the selected Session, using the card’s Notification semantics. Readonly Sessions and unknown bridge state are disabled, exactly like the card.',
    group: 'Session',
    defaultBinding: null,
    allowInComposer: true,
    run: () => {
      const sessionId = selectedSessionId();
      if (!sessionId) return;
      void runSessionToggleShortcut(sessionId, 'notification');
    },
  },
];

/** Panel actions are generated from the live registry; positions are never identities. */
export function buildPanelShortcutActions(entries: readonly ViewEntry[]): ShortcutAction[] {
  return entries.map((entry) => ({
    id: `panel:${entry.id}`,
    label: `Go to ${entry.label}`,
    description: entry.pluginId && entry.panelId
      ? `Open plugin panel ${entry.pluginId}/${entry.panelId}.`
      : `Open the ${entry.label} view.`,
    group: 'Panels' as const,
    defaultBinding: PANEL_DEFAULT_BINDINGS[entry.id] ?? null,
    run: (context: ShortcutContext) => context.navigate(entry.to),
  }));
}

export function buildShortcutActions(entries: readonly ViewEntry[]): ShortcutAction[] {
  return [
    ...GENERAL_ACTIONS,
    ...COMPOSER_ACTIONS,
    ...buildPanelShortcutActions(entries),
    ...QUEUE_ACTIONS,
    ...SESSION_ACTIONS,
  ];
}

/** Non-reactive action list for the single global keydown dispatcher. */
export function getShortcutActions(): ShortcutAction[] {
  return buildShortcutActions(getViewEntriesSnapshot());
}

/** Reactive action list for the settings UI. */
export function useShortcutActions(): ShortcutAction[] {
  const entries = useViewEntries();
  return useMemo(() => buildShortcutActions(entries), [entries]);
}

/** Static metadata for tests/documentation; panel actions are appended by callers. */
export const STATIC_SHORTCUT_ACTIONS: readonly ShortcutAction[] = [
  ...GENERAL_ACTIONS,
  ...COMPOSER_ACTIONS,
  ...QUEUE_ACTIONS,
  ...SESSION_ACTIONS,
];

import { useMemo } from 'react';
import {
  getViewEntriesSnapshot,
  useViewEntries,
  type ViewEntry,
} from '@/components/layout/viewEntries';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { runSessionToggleShortcut } from '@/utils/sessionQuickActions';
import { requestLockedComposerEnqueue } from './composerBridge';

export type ShortcutGroup = 'General' | 'Panels' | 'Queue' | 'Session';

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
];

const QUEUE_ACTIONS: ShortcutAction[] = [
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
  ...QUEUE_ACTIONS,
  ...SESSION_ACTIONS,
];

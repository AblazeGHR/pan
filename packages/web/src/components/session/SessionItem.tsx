import { memo, useEffect, useMemo, useRef, useState } from 'react';
import type { Session } from '@/types';
import { WorkerDot } from '@/components/worker/WorkerDot';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { setReportsToManager, setSessionMsgBridge, setSessionReadonly } from '@/services/api';
import type { DropZone } from './sessionDrag';
import { MessageSquare, Folder, Monitor, Settings, ChevronDown, ChevronRight, Eye, EyeOff, Pin, Bell, Lock, Unlock } from 'lucide-react';
import './sessionQuickActions.css';

type QuickActionKey = 'pin' | 'readonly' | 'stopReport' | 'notification';

const quickActionLabels: Record<QuickActionKey, string> = {
  pin: 'Pin',
  readonly: 'Readonly',
  stopReport: 'Stop report',
  notification: 'Notification',
};

function quickActionIcon(key: QuickActionKey, isOn: boolean) {
  if (key === 'readonly') return isOn ? Lock : Unlock;
  if (key === 'pin') return Pin;
  if (key === 'stopReport') return null;
  return Bell;
}

function ReportUpIcon({ stopped }: { stopped: boolean }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false">
      <circle cx="10" cy="8" r="4" />
      <path d="M4 20c0-4 2.7-6 6-6" />
      <path d="M17 20V10" />
      <path d="m13 14 4-4 4 4" />
      {stopped ? <path d="m4 4 16 16" /> : null}
    </svg>
  );
}

function quickActionState(session: Session, key: QuickActionKey): boolean {
  if (key === 'pin') return session.pinned === true;
  if (key === 'readonly') return session.readonlySession === true;
  if (key === 'stopReport') return session.reportsToManager === false && !!session.managedBy;
  return session.msgBridgeEnabled === true;
}

function quickActionDisabled(session: Session, key: QuickActionKey): boolean {
  if (key === 'readonly') return !session.managedBy || typeof session.readonlySession !== 'boolean';
  if (key === 'stopReport') return !session.managedBy || typeof session.reportsToManager !== 'boolean';
  if (key === 'notification') return typeof session.msgBridgeEnabled !== 'boolean';
  return false;
}

function quickActionDescription(session: Session, key: QuickActionKey, isOn: boolean): string {
  if (key === 'readonly' && !session.managedBy) return 'Readonly unavailable: this Session has no direct manager';
  if (key === 'stopReport' && !session.managedBy) return 'Stop report unavailable: this Session has no direct manager';
  if (key === 'stopReport' && typeof session.reportsToManager !== 'boolean') return 'Stop report unavailable: manager subscription is loading or unavailable';
  if (key === 'notification' && typeof session.msgBridgeEnabled !== 'boolean') return 'Notification state is loading or unavailable';
  if (key === 'stopReport') {
    return isOn
      ? 'Completion reports to the direct parent are stopped; click to restore the parent subscription'
      : 'The direct parent receives completion reports; click to stop the parent subscription';
  }
  if (key === 'notification') {
    return isOn
      ? 'QQ, system, or browser msgBridge is on; click to turn all three off'
      : 'msgBridge is off; click to turn on system notifications only';
  }
  if (key === 'readonly') return isOn ? 'Readonly on; click to allow manager actions' : 'Readonly off; click to block manager actions';
  return isOn ? 'Pinned; click to unpin' : 'Not pinned; click to pin';
}

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  isSelected?: boolean;
  multiSelectMode?: boolean;
  /** Select-mode only: this card is currently hidden from the normal list. */
  isHidden?: boolean;
  /** Select-mode only: eye button callback (id returned by the component). */
  onToggleHidden?: (id: string) => void;
  /** Select-mode only: checkbox toggle (select/deselect) callback. Kept
   *  separate from onSelect so the checkbox toggles selection while a click
   *  on the card body still opens the session. */
  onToggleSelect?: (id: string) => void;
  /** Show a collapse/expand chevron (used for manager groups with children). */
  expandable?: boolean;
  expanded?: boolean;
  onToggleChildren?: (e: React.MouseEvent) => void;
  /** id 由组件内部回传，父级可传稳定引用（配合 React.memo 避免无关卡片重渲染）。 */
  onSelect?: (id: string) => void;
  onMenu?: (e: React.MouseEvent, id: string) => void;
  /** Enable the "∷" drag handle next to the status dot (flat list only). */
  dragEnabled?: boolean;
  /** Pointer-down on the drag handle (start a drag). */
  onDragHandlePointerDown?: (e: React.PointerEvent, id: string) => void;
  /** Drag feedback: this card is the one being dragged (stays in place). */
  isDragSource?: boolean;
  /** Drag feedback: pointer is over this card's CENTER band (→ mock manage). */
  isCenterTarget?: boolean;
  /** Drag feedback: pointer is over this card's edge band (→ insert here). */
  insertZone?: DropZone | null;
}

function shortWorkdir(workdir?: string): string {
  if (!workdir) return '';
  const parts = workdir.replace(/\\/g, '/').split('/');
  parts.pop();
  const lastTwo = parts.slice(-2);
  if (lastTwo.length < parts.length) {
    return `…/${lastTwo.join('/')}`;
  }
  return `/${lastTwo.join('/')}`;
}

/** Strip common markdown syntax so a one-line preview reads as plain text. */
function stripMarkdown(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ') // fenced code blocks
    .replace(/`([^`]*)`/g, '$1') // inline code
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1') // images
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // links
    .replace(/^#{1,6}\s*/gm, '') // headings
    .replace(/^>\s?/gm, '') // blockquotes
    .replace(/\*\*([^*]+)\*\*/g, '$1') // bold
    .replace(/\*([^*]+)\*/g, '$1') // italic
    .replace(/__([^_]+)__/g, '$1') // bold (underscore)
    .replace(/_([^_]+)_/g, '$1') // italic (underscore)
    .replace(/~~([^~]+)~~/g, '$1') // strikethrough
    .replace(/^\s*[-*+]\s+/gm, '') // unordered list markers
    .replace(/^\s*\d+\.\s+/gm, '') // ordered list markers
    .replace(/\|/g, ' ') // table pipes
    .replace(/\s+/g, ' ')
    .trim();
}

export const SessionItem = memo(function SessionItem({
  session,
  isActive,
  isSelected = false,
  multiSelectMode = false,
  isHidden = false,
  onToggleHidden,
  onToggleSelect,
  expandable = false,
  expanded = true,
  onToggleChildren,
  onSelect,
  onMenu,
  dragEnabled = false,
  onDragHandlePointerDown,
  isDragSource = false,
  isCenterTarget = false,
  insertZone = null,
}: SessionItemProps) {
  const isPending = session.id.startsWith('__pending_');
  const [busyAction, setBusyAction] = useState<QuickActionKey | null>(null);
  const [syncUnavailable, setSyncUnavailable] = useState(false);
  const busyRef = useRef(false);
  // A failed refresh leaves the displayed toggle value uncertain. A later
  // Session snapshot (or a remount) is required before another write.
  useEffect(() => { setSyncUnavailable(false); }, [session]);
  // Preview comes from the summary endpoint's lastMessage (the list carries no
  // history now); fall back to the last local history message when present.
  const messages = session.history || [];
  const lastContent = messages.length > 0 ? messages[messages.length - 1]?.content : undefined;
  // stripMarkdown + 截断的成本不低，而卡片会因 isActive / isSelected / 拖拽反馈
  // 等无关 props 变化重渲染。按来源文本缓存：内容不变则跳过重复计算。
  const preview = useMemo(() => {
    const source = session.lastMessage || lastContent;
    if (!source) return null;
    const text = stripMarkdown(source);
    if (!text) return null;
    return text.length > 50 ? `${text.slice(0, 50)}...` : text;
  }, [session.lastMessage, lastContent]);
  // A cold summary intentionally uses null/undefined for an unknown total.
  // Only a loaded local message list can provide a conservative fallback;
  // never turn an unloaded empty list into a misleading zero.
  const messageCount = typeof session.historyTotal === 'number'
    ? session.historyTotal
    : messages.length > 0
      ? messages.length
      : '—';
  const credit = session.totalUsage?.credit ?? null;
  // 未读 done 徽标：0 时完全不渲染（无空圆、无 0、不占位）；多位数值收窄为
  // “99+”胶囊，随内容增宽而不挤压卡片布局。计数与 ack 均以整型收敛。
  const unreadDoneCount = typeof session.unreadDoneCount === 'number'
      && Number.isFinite(session.unreadDoneCount)
    ? Math.max(0, Math.floor(session.unreadDoneCount))
    : 0;
  // Workspace membership badge: shown only in the unscoped "all" view (inside
  // a workspace tab the scope is already known, and the chip just costs width).
  const workspaceId = session.workspaceIds?.[0] ?? null;
  const workspaceName = useWorkspaceStore((s) =>
    workspaceId ? s.workspaces.find((w) => w.id === workspaceId)?.name ?? null : null,
  );
  const showWorkspaceBadge = useUIStore((s) => s.activeWorkspaceId === 'all') && !!workspaceName;

  const handleClick = () => {
    if (isPending) return;
    onSelect?.(session.id);
  };

  const toggleQuickAction = async (key: QuickActionKey) => {
    if (isPending || busyRef.current) return;
    const store = useSessionStore.getState();
    const current = store.sessions.find((item) => item.id === session.id);
    if (!current || quickActionDisabled(current, key)) return;
    const managerAtStart = current.managedBy;
    busyRef.current = true;
    setBusyAction(key);
    try {
      if (key === 'pin') {
        await store.setSessionPinned(current.id, !current.pinned);
      } else if (key === 'readonly' && managerAtStart) {
        await setSessionReadonly(managerAtStart, current.id, !current.readonlySession, current.readonlySession);
      } else if (key === 'stopReport' && managerAtStart) {
        await setReportsToManager(current.id, managerAtStart, !current.reportsToManager);
      } else if (key === 'notification') {
        await setSessionMsgBridge(current.id, !current.msgBridgeEnabled);
      }
      if (key !== 'pin') {
        try {
          await useSessionStore.getState().loadSessions({ throwOnError: true });
        } catch {
          setSyncUnavailable(true);
          useUIStore.getState().showToast('Saved, but the Session list could not refresh', 'error');
        }
      }
    } catch (error) {
      // No optimistic state is retained on failure. Re-read server state in
      // case another control changed the same Session while this request ran.
      try {
        await useSessionStore.getState().loadSessions({ throwOnError: true });
      } catch {
        setSyncUnavailable(true);
      }
      useUIStore.getState().showToast(
        error instanceof Error ? error.message : `${quickActionLabels[key]} update failed`,
        'error',
      );
    } finally {
      busyRef.current = false;
      setBusyAction(null);
    }
  };

  return (
    <div
      data-session-card-id={session.id}
      onClick={handleClick}
      className={`relative flex items-center gap-2 px-3 py-2 cursor-pointer border-b border-border-default border-l-[3px] transition-colors ${
        isActive
          ? 'bg-bg-tertiary border-l-accent'
          : 'bg-bg-secondary hover:bg-bg-tertiary/60 border-l-text-tertiary/50'
      } ${isPending ? 'opacity-50' : ''} ${isHidden ? 'opacity-50' : ''} ${
        isDragSource ? 'opacity-60 outline-2 outline-offset-[-2px] outline-dashed outline-accent/70' : ''
      } ${isCenterTarget ? 'ring-2 ring-accent z-[1]' : ''}`}
    >
      {/* Drag feedback: insert line at the top edge (= boundary above this card). */}
      {insertZone === 'before' && (
        <>
          <span
            aria-hidden
            data-insert-line="before"
            className="pointer-events-none absolute top-0 left-0 right-0 h-[6px] bg-accent rounded-b z-10 shadow-[0_0_10px_2px_rgba(96,165,250,0.6)]"
          />
          <span
            aria-hidden
            data-insert-band="before"
            className="pointer-events-none absolute top-0 left-0 right-0 h-[30%] bg-accent/10 z-0"
          />
        </>
      )}
      {/* Drag feedback: insert line at the bottom edge (= boundary below). */}
      {insertZone === 'after' && (
        <>
          <span
            aria-hidden
            data-insert-line="after"
            className="pointer-events-none absolute bottom-0 left-0 right-0 h-[6px] bg-accent rounded-t z-10 shadow-[0_0_10px_2px_rgba(96,165,250,0.6)]"
          />
          <span
            aria-hidden
            data-insert-band="after"
            className="pointer-events-none absolute bottom-0 left-0 right-0 h-[30%] bg-accent/10 z-0"
          />
        </>
      )}
      {/* Drag feedback: center-band tint (generous "manage" hit zone visual). */}
      {isCenterTarget && (
        <span
          aria-hidden
          data-drag-center
          className="pointer-events-none absolute inset-x-0 top-[30%] bottom-[30%] bg-accent/10 z-0"
        />
      )}

      {multiSelectMode ? (
        // Selection hit zone: a label wrapping the checkbox. Generous padding
        // (-my-2 stretches it across the full card height) makes the small
        // native checkbox easy to tap on mobile; the negative margins cancel
        // the card's own padding so the layout is unchanged. stopPropagation
        // keeps the click from reaching the card (which opens the session);
        // the label itself forwards the click onto the input, so tapping
        // anywhere in the zone toggles selection exactly once.
        <label
          data-testid="select-checkbox-zone"
          onClick={(e) => e.stopPropagation()}
          className="shrink-0 flex items-center p-2 -my-2 -ml-1 cursor-pointer"
        >
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect?.(session.id)}
            aria-label={`Select ${session.name || 'session'}`}
            className="accent-accent"
          />
        </label>
      ) : null}

      {/* Merged drag gutter: the status indicator AND the drag zone share one
          narrow column (full card height). The whole column — indicator
          included — responds to dragging; a dot-matrix texture fills the
          column around the centered indicator as a visual affordance.
          Clicking without moving still selects the session (drag threshold). */}
      {dragEnabled && !isPending && !multiSelectMode ? (
        <span
          data-testid="drag-handle"
          role="button"
          aria-label={`Drag ${session.name}`}
          title={session.workerStatus ?? 'offline'}
          onPointerDown={(e) => onDragHandlePointerDown?.(e, session.id)}
          className="drag-gutter relative z-[5] shrink-0 flex items-center justify-center w-5 self-stretch -my-2 -ml-1 -mr-1 cursor-grab active:cursor-grabbing select-none touch-none hover:bg-bg-hover/60 rounded-l-sm"
        >
          <span aria-hidden="true" className="drag-matrix pointer-events-none absolute inset-x-0 top-[10px] bottom-[10px] rounded-sm" />
          <span className="relative z-[1] flex items-center">
            <WorkerDot status={session.workerStatus} legalState={session.lastLegalWorkerState} />
          </span>
        </span>
      ) : (
        <WorkerDot status={session.workerStatus} legalState={session.lastLegalWorkerState} />
      )}

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-text-primary truncate font-medium">
            {session.name || 'Untitled'}
          </span>
          {session.pinned && multiSelectMode && (
            <Pin
              data-testid="session-pinned-indicator"
              aria-label="Pinned"
              size={11}
              className="shrink-0 text-accent"
            />
          )}
          {session.adapter && multiSelectMode && (
            <span data-testid="session-adapter-badge" className="max-md:hidden text-[10px] text-text-tertiary bg-bg-tertiary border border-border-default rounded px-1 py-px shrink-0">
              {session.adapter}
            </span>
          )}
          {showWorkspaceBadge && (
            <span
              data-testid="session-workspace-badge"
              className="max-md:hidden flex items-center gap-0.5 text-[10px] text-accent bg-accent/10 border border-accent/25 rounded px-1 py-px shrink-0"
              title={`工作区：${workspaceName}`}
            >
              <Folder size={9} />
              <span className="max-w-[72px] truncate">{workspaceName}</span>
            </span>
          )}
        </div>

        {preview && (
          <div className="text-xs text-text-tertiary truncate mt-0.5 leading-tight">
            {preview}
          </div>
        )}

        <div className="flex items-center gap-2 mt-1 text-xs text-text-secondary">
          <span className="flex items-center gap-0.5">
            <MessageSquare size={10} />
            {messageCount}
          </span>
          {session.model && (
            <span className="flex items-center gap-0.5 truncate">
              <Monitor size={10} />
              {session.model}
            </span>
          )}
          {session.workdir && (
            <span className="flex items-center gap-0.5 truncate text-text-tertiary" title={session.workdir}>
              <Folder size={10} />
              {shortWorkdir(session.workdir)}
            </span>
          )}
          {credit !== null && (
            <span className="shrink-0 text-text-tertiary">
              {credit.toFixed(2)} cr
            </span>
          )}
        </div>
      </div>

      {multiSelectMode && !isPending && onToggleHidden ? (
        // Select-mode hide/show (eye) button: toggles the session's hidden
        // state for the normal list. stopPropagation keeps it from toggling
        // the card's selection checkbox/handlers.
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggleHidden(session.id);
          }}
          className="shrink-0 flex items-center p-2 -my-2 -ml-1 text-text-tertiary hover:text-text-primary rounded transition-colors"
          title={isHidden ? 'Show session' : 'Hide session'}
        >
          {isHidden ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
      ) : null}

      {!multiSelectMode && !isPending ? (
        <div className="session-quick-action-rail" data-testid="session-quick-action-rail">
          <div className="session-quick-action-group" role="group" aria-label="Session quick actions">
            {(['pin', 'readonly', 'stopReport', 'notification'] as const).map((key) => {
              const isOn = quickActionState(session, key);
              const unavailable = syncUnavailable || quickActionDisabled(session, key);
              const description = syncUnavailable
                ? 'Session state could not refresh; reload the Session list before retrying'
                : quickActionDescription(session, key, isOn);
              const ActionIcon = quickActionIcon(key, isOn);
              return (
                <button
                  key={key}
                  type="button"
                  className="session-quick-action-button"
                  data-quick-action={key}
                  data-quick-state={isOn ? 'on' : 'off'}
                  data-testid={key === 'pin' && isOn ? 'session-pinned-indicator' : undefined}
                  aria-label={`${quickActionLabels[key]}: ${isOn ? 'on' : 'off'}. ${description}`}
                  aria-pressed={isOn}
                  aria-disabled={unavailable || busyAction !== null}
                  aria-busy={busyAction === key}
                  title={busyAction === key ? 'Updating…' : description}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (!unavailable && busyAction === null) void toggleQuickAction(key);
                  }}
                >
                  {key === 'stopReport' ? (
                    <ReportUpIcon stopped={isOn} />
                  ) : ActionIcon ? (
                    <ActionIcon size={12} strokeWidth={2} aria-hidden="true" />
                  ) : null}
                </button>
              );
            })}
          </div>

          {(session.adapter || unreadDoneCount > 0) && (
            <span className="session-quick-action-adapter-row">
              {unreadDoneCount > 0 && (
                // 未读 done 徽标（adapter 标签左侧）。单击只拦截冒泡、不选中
                // 卡片也不清零：否则第一次 click 会经“选择即读”把徽标清零并
                // 卸载，第二次 click 失去目标，双击永远无法成立。清零只在
                // 双击（或选择会话）时发生；事件拦截同时防止卡片选中。
                <span
                  className="session-unread-done-badge"
                  data-testid="session-unread-done-badge"
                  role="img"
                  aria-label={`${unreadDoneCount} unread done. Double-click to mark as read.`}
                  title={`${unreadDoneCount} unread done · 双击标记已读`}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => e.stopPropagation()}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    if (isPending) return;
                    void useSessionStore.getState().ackSessionUnread(session.id, unreadDoneCount);
                  }}
                >
                  {unreadDoneCount > 99 ? '99+' : unreadDoneCount}
                </span>
              )}
              {session.adapter && (
                <span
                  className="session-quick-action-adapter"
                  data-testid="session-adapter-badge"
                  role="img"
                  aria-label={`Adapter ${session.adapter}`}
                  title={`Adapter: ${session.adapter}`}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => e.stopPropagation()}
                >
                  {session.adapter}
                </span>
              )}
            </span>
          )}

          {expandable && (
            <button
              type="button"
              data-testid="session-group-toggle"
              className="session-quick-action-control session-quick-action-chevron"
              title={expanded ? 'Collapse group' : 'Expand group'}
              aria-label={expanded ? 'Collapse group' : 'Expand group'}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onToggleChildren?.(e);
              }}
            >
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          )}
          <button
            type="button"
            data-testid="session-actions-menu"
            className="session-quick-action-control session-quick-action-settings"
            title="Session actions"
            aria-label="Session actions"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onMenu?.(e, session.id);
            }}
          >
            <Settings size={14} />
          </button>
        </div>
      ) : (
        /* The manager chevron remains available during multi-select. */
        !isPending && expandable && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleChildren?.(e);
            }}
            className="shrink-0 p-1 text-text-tertiary hover:text-text-primary rounded transition-colors"
            title={expanded ? 'Collapse group' : 'Expand group'}
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        )
      )}
    </div>
  );
});

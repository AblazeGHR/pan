import { memo } from 'react';
import type { Session } from '@/types';
import { WorkerDot } from '@/components/worker/WorkerDot';
import type { DropZone } from './sessionDrag';
import { MessageSquare, Folder, Monitor, Settings, ChevronDown, ChevronRight, Eye, EyeOff } from 'lucide-react';

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  isSelected?: boolean;
  multiSelectMode?: boolean;
  /** Select-mode only: this card is currently hidden from the normal list. */
  isHidden?: boolean;
  /** Select-mode only: eye button callback (id returned by the component). */
  onToggleHidden?: (id: string) => void;
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
  // Preview comes from the summary endpoint's lastMessage (the list carries no
  // history now); fall back to the last local history message when present.
  const messages = session.history || [];
  const lastMsg = messages.length > 0 ? messages[messages.length - 1] : null;
  const previewText = session.lastMessage
    ? stripMarkdown(session.lastMessage)
    : lastMsg
      ? stripMarkdown(lastMsg.content)
      : '';
  const preview = previewText
    ? previewText.length > 50
      ? previewText.slice(0, 50) + '...'
      : previewText
    : null;
  const credit = session.totalUsage?.credit ?? null;

  const handleClick = () => {
    if (isPending) return;
    onSelect?.(session.id);
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
        <input
          type="checkbox"
          checked={isSelected}
          onChange={(e) => {
            e.stopPropagation();
            onSelect?.(session.id);
          }}
          className="shrink-0 accent-accent"
        />
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
            <WorkerDot status={session.workerStatus} />
          </span>
        </span>
      ) : (
        <WorkerDot status={session.workerStatus} />
      )}

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-text-primary truncate font-medium">
            {session.name || 'Untitled'}
          </span>
          {session.adapter && (
            <span className="text-[10px] text-text-tertiary bg-bg-tertiary border border-border-default rounded px-1 py-px shrink-0">
              {session.adapter}
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
            {session.historyTotal ?? messages.length}
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
          className="shrink-0 p-1 text-text-tertiary hover:text-text-primary rounded transition-colors"
          title={isHidden ? 'Show session' : 'Hide session'}
        >
          {isHidden ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
      ) : null}

      {!multiSelectMode && !isPending && expandable && (
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
      )}

      {!multiSelectMode && !isPending && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onMenu?.(e, session.id);
          }}
          className="shrink-0 p-1 text-text-tertiary hover:text-text-primary rounded transition-colors"
          title="Session actions"
        >
          <Settings size={14} />
        </button>
      )}
    </div>
  );
});

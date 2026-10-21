import { useEffect, useRef, useState } from 'react';
import { Check, Copy, ExternalLink } from 'lucide-react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { copyText } from '@/utils/clipboard';
import { isValidSessionRef, openSessionFromNotification } from '@/utils/session-deeplink';
import { hasSessionOrigin, storedSourceSessionId } from '@/utils/queueSourceSession';
import type { AgentQueueItem } from '@/types';

/**
 * Structured origin of one queued message, shown inside the full-text preview.
 *
 * Identity rules:
 *  - The origin comes ONLY from the server-provided `meta.sourceSessionId`
 *    (see `@/utils/queueSourceSession`), so a message can never claim an origin
 *    it was not stored with.
 *  - Every other source (user/qq/wechat) has no agent origin and renders
 *    nothing — a wrong origin is worse than no origin.
 *  - The Session name is resolved from the already-loaded Session store. This
 *    is a pure read: no polling, no per-open refetch, no full list load.
 *  - A missing / unresolved origin is reported honestly in English and the
 *    known ID stays copyable.
 */
const SOURCE_ID_MISSING = 'No source session ID was recorded for this message.';
const SOURCE_NOT_LOADED = 'This session is not in the loaded session list yet. It may have been deleted, or it may not be accessible to you.';
const SOURCE_NAME_UNAVAILABLE = 'Unavailable';
const UNTITLED = 'Untitled session';

/** Feedback wording for the jump, so a failure from this entry point stays English. */
const SOURCE_JUMP_MESSAGES = {
  missing: 'The source session no longer exists, so its chat could not be opened.',
  listUnavailable: 'The session list is unavailable right now, so the source session could not be opened.',
  workspaceUnavailable: 'The workspace list is unavailable right now, so the source session could not be opened.',
  routeFailed: 'Could not return to the chat view.',
};

const COPY_BUTTON = 'shrink-0 rounded p-1 text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-30';
const OPEN_BUTTON = 'inline-flex items-center gap-1.5 self-start rounded-md border border-border-default bg-bg-tertiary px-2.5 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50';

type CopyField = 'name' | 'id';

/**
 * Renders the origin block for a queue item, or nothing when the item has no
 * agent origin. Reports without a stored origin render nothing (their context
 * is unchanged); an agent message without an origin renders the honest
 * "not recorded" note instead of a guessed identity.
 */
export function QueueSourceSession({ item, onClose }: { item: AgentQueueItem; onClose?: () => void }) {
  if (!hasSessionOrigin(item)) return null;
  const sourceSessionId = storedSourceSessionId(item);
  if (!sourceSessionId && item.kind === 'report') return null;
  // Keyed remount: a different queue item never inherits copy feedback or a
  // pending clipboard result from the previous one.
  return <SourceSessionIdentity key={item.id} itemId={item.id} sourceSessionId={sourceSessionId} onClose={onClose} />;
}

function SourceSessionIdentity({ itemId, sourceSessionId, onClose }: {
  itemId: string;
  sourceSessionId: string | null;
  onClose?: () => void;
}) {
  const showToast = useUIStore((state) => state.showToast);
  const session = useSessionStore((state) =>
    sourceSessionId ? state.sessions.find((candidate) => candidate.id === sourceSessionId) : undefined,
  );
  const [copied, setCopied] = useState<CopyField | null>(null);
  const copySeq = useRef(0);

  useEffect(() => {
    // The slot may be reused for another message: drop pending feedback so a
    // late clipboard result can never surface under a different preview.
    copySeq.current += 1;
    setCopied(null);
  }, [itemId, sourceSessionId]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(null), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copyValue = (field: CopyField, value: string) => {
    const seq = ++copySeq.current;
    void copyText(value)
      .then(() => {
        if (seq !== copySeq.current) return;
        setCopied(field);
      })
      .catch(() => {
        if (seq !== copySeq.current) return;
        showToast('Copy failed', 'error');
      });
  };

  const openChat = () => {
    if (!sourceSessionId) return;
    if (!isValidSessionRef(sourceSessionId)) {
      showToast('The recorded source session ID cannot be opened.', 'warning');
      return;
    }
    // Close the preview first, then reuse the existing session routing so the
    // jump switches the workspace scope and the selected session together.
    onClose?.();
    void openSessionFromNotification(sourceSessionId, SOURCE_JUMP_MESSAGES);
  };

  const displayName = session ? (session.name?.trim() || UNTITLED) : null;
  const nameValue = displayName ?? SOURCE_NAME_UNAVAILABLE;
  const copiedLabel = copied === 'name'
    ? 'Copied source session name'
    : copied === 'id'
      ? 'Copied source session ID'
      : '';

  return (
    <div className="flex min-w-0 flex-col gap-1 rounded border border-border-muted bg-bg-tertiary/40 p-2">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-text-tertiary">Source session</span>
      {sourceSessionId ? (
        <>
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="w-9 shrink-0 text-[11px] text-text-tertiary">Name</span>
            <button
              type="button"
              onClick={() => { if (displayName) copyValue('name', displayName); }}
              disabled={!displayName}
              title={displayName ? `Copy ${displayName}` : SOURCE_NAME_UNAVAILABLE}
              className="min-w-0 flex-1 truncate rounded text-left text-xs text-text-primary transition-colors hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-default disabled:text-text-tertiary disabled:hover:text-text-tertiary"
            >
              {nameValue}
            </button>
            <button
              type="button"
              onClick={() => { if (displayName) copyValue('name', displayName); }}
              disabled={!displayName}
              aria-label="Copy source session name"
              title="Copy name"
              className={COPY_BUTTON}
            >
              {copied === 'name' ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
            </button>
          </div>
          <div className="flex min-w-0 items-start gap-1.5">
            <span className="w-9 shrink-0 pt-px text-[11px] text-text-tertiary">ID</span>
            <button
              type="button"
              onClick={() => copyValue('id', sourceSessionId)}
              title={`Copy ${sourceSessionId}`}
              className="min-w-0 flex-1 break-all rounded text-left font-mono text-[11px] leading-tight text-text-secondary transition-colors hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {sourceSessionId}
            </button>
            <button
              type="button"
              onClick={() => copyValue('id', sourceSessionId)}
              aria-label="Copy source session ID"
              title="Copy ID"
              className={COPY_BUTTON}
            >
              {copied === 'id' ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
            </button>
          </div>
          {!displayName && <p className="text-[11px] leading-snug text-text-tertiary">{SOURCE_NOT_LOADED}</p>}
          <span role="status" aria-live="polite" className="min-h-[14px] text-[11px] leading-tight text-accent">{copiedLabel}</span>
          <button type="button" onClick={openChat} className={OPEN_BUTTON} title="Open the source session chat">
            <ExternalLink size={12} aria-hidden="true" />
            Open source chat
          </button>
        </>
      ) : (
        <p className="text-[11px] leading-snug text-text-tertiary">{SOURCE_ID_MISSING}</p>
      )}
    </div>
  );
}

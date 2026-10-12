import { useLayoutEffect, useRef, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

interface RenameSessionModalProps {
  session: Session | null;
  onClose: () => void;
}

export function RenameSessionModal({ session, onClose }: RenameSessionModalProps) {
  const rename = useSessionStore((state) => state.rename);
  const showToast = useUIStore((state) => state.showToast);
  const sessionId = session?.id ?? null;
  const [draft, setDraft] = useState<{ sessionId: string | null; name: string }>(() => ({
    sessionId,
    name: session?.name || '',
  }));
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Re-seed the draft synchronously when the target session id changes (React's
  // "adjusting state when a prop changes" pattern — the correction lands in the
  // same render, so a stale draft never paints). Keyed on the id, never on the
  // session object identity: while the dialog is open, store refreshes (WS
  // stream previews, session.updated, loadSessions merges) replace the session
  // object with a new identity for the SAME id, and those must not overwrite
  // what the user is typing. Closing (id -> null) clears the draft so it cannot
  // leak into the next session's dialog.
  if (draft.sessionId !== sessionId) {
    setDraft({ sessionId, name: session?.name || '' });
    setSubmitting(false);
  }

  // Focus + select-all once per opened session. The Modal portals its content
  // synchronously in the same commit, so the layout effect sees the ref
  // attached; unlike a requestAnimationFrame callback this also runs in a
  // hidden tab and cannot be preempted by a refresh landing between commit and
  // frame. Re-running only on id change keeps the user's caret and selection
  // untouched while they edit.
  useLayoutEffect(() => {
    if (!sessionId) return;
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    input.select();
  }, [sessionId]);

  if (!session) return null;

  const submit = async () => {
    const trimmed = draft.name.trim();
    if (!trimmed) {
      showToast('Session name cannot be empty', 'error');
      inputRef.current?.focus();
      return;
    }
    setSubmitting(true);
    try {
      await rename(session.id, trimmed);
      showToast('Session renamed');
      onClose();
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Rename failed', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open title="Rename Session" onClose={onClose} size="sm">
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-4">
        <label className="block text-sm text-text-primary" htmlFor="rename-session-input">Session name</label>
        <input
          ref={inputRef}
          id="rename-session-input"
          value={draft.name}
          onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              // Modal also listens on document; prevent the same keypress from
              // closing the dialog a second time through that global handler.
              event.stopPropagation();
              onClose();
            }
          }}
          disabled={submitting}
          autoComplete="off"
          className="w-full rounded border border-border-default bg-bg-primary px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
        />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={submitting}>{submitting ? 'Saving…' : 'Rename'}</Button>
        </div>
      </form>
    </Modal>
  );
}

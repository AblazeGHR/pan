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

interface EditState {
  sessionId: string | null;
  name: string;
  /**
   * Edit-instance generation. Increments whenever the dialog starts editing a
   * new target (open, close, or switch to another session) so an awaited
   * callback from a previous edit can be recognised and discarded — keying on
   * the session id alone cannot tell "reopened the same session" apart.
   */
  generation: number;
}

export function RenameSessionModal({ session, onClose }: RenameSessionModalProps) {
  const rename = useSessionStore((state) => state.rename);
  const showToast = useUIStore((state) => state.showToast);
  const sessionId = session?.id ?? null;
  const [edit, setEdit] = useState<EditState>(() => ({
    sessionId,
    name: session?.name || '',
    generation: 0,
  }));
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  /** Generation whose rename request is currently in flight (null = none). */
  const inFlightRef = useRef<number | null>(null);
  /** Latest committed generation, read by the awaited callbacks below. */
  const generationRef = useRef(0);

  // Re-seed the draft synchronously when the target session id changes (React's
  // "adjusting state when a prop changes" pattern — the correction lands in the
  // same render, so a stale draft never paints). Keyed on the id, never on the
  // session object identity: while the dialog is open, store refreshes (WS
  // stream previews, session.updated, loadSessions merges) replace the session
  // object with a new identity for the SAME id, and those must not overwrite
  // what the user is typing. Closing (id -> null) clears the draft so it cannot
  // leak into the next session's dialog.
  if (edit.sessionId !== sessionId) {
    setEdit({ sessionId, name: session?.name || '', generation: edit.generation + 1 });
    setSubmitting(false);
  }

  // Mirror the committed generation for the awaited callbacks. Instance changes
  // always originate from user interaction, so this has run before a previous
  // request's continuation can observe the new generation.
  useLayoutEffect(() => {
    generationRef.current = edit.generation;
  }, [edit.generation]);

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
    // Synchronous re-entry guard. `disabled` only lands on the next render, so
    // two Enter presses (or Enter plus implicit form submission) in the same
    // event batch would otherwise start a duplicate request.
    if (inFlightRef.current === edit.generation) return;
    const trimmed = edit.name.trim();
    if (!trimmed) {
      showToast('Session name cannot be empty', 'error');
      inputRef.current?.focus();
      return;
    }
    const generation = edit.generation;
    inFlightRef.current = generation;
    setSubmitting(true);
    try {
      await rename(session.id, trimmed);
      // The rename itself persisted, but if this edit instance is gone the
      // dialog now belongs to a newer one (another session, or the same one
      // reopened): closing it or toasting here would attribute the result to
      // the wrong edit.
      if (generationRef.current !== generation) return;
      showToast('Session renamed');
      onClose();
    } catch (error) {
      if (generationRef.current !== generation) return;
      showToast(error instanceof Error ? error.message : 'Rename failed', 'error');
    } finally {
      if (inFlightRef.current === generation) inFlightRef.current = null;
      // Never release the guard of a newer edit whose own request is in flight.
      if (generationRef.current === generation) setSubmitting(false);
    }
  };

  return (
    <Modal open title="Rename Session" onClose={onClose} size="sm">
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-4">
        <label className="block text-sm text-text-primary" htmlFor="rename-session-input">Session name</label>
        <input
          ref={inputRef}
          id="rename-session-input"
          value={edit.name}
          onChange={(event) => setEdit((current) => ({ ...current, name: event.target.value }))}
          onKeyDown={(event) => {
            // IME convention: keydown while a composition is active belongs to
            // the candidate window (Enter confirms it, Escape dismisses it) and
            // must not reach the dialog. 229 is the legacy keyCode browsers use
            // for the key that ends a composition.
            if (event.nativeEvent.isComposing || event.keyCode === 229) return;
            if (event.key === 'Escape') {
              // Modal also listens on document; prevent the same keypress from
              // closing the dialog a second time through that global handler.
              event.stopPropagation();
              onClose();
            }
            if (event.key === 'Enter') {
              // Submit explicitly rather than relying on implicit form
              // submission, whose IME/Enter behaviour differs per engine. The
              // synchronous guard in submit() makes the double path harmless.
              event.preventDefault();
              void submit();
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

import { useEffect, useRef, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { handoffSession } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

interface Props {
  session: Session | null;
  onClose: () => void;
}

// Unmount on close and key by identity: each opening owns its draft and request.
export function HandoffSessionModal({ session, onClose }: Props) {
  return session ? <HandoffForm key={session.id} session={session} onClose={onClose} /> : null;
}

function HandoffForm({ session, onClose }: { session: Session; onClose: () => void }) {
  const [brief, setBrief] = useState('');
  const [override, setOverride] = useState(false);
  const [original, setOriginal] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(true);
  const submitting = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  const submit = async () => {
    if (!brief.trim() || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      await handoffSession(session.id, {
        handoffPrompt: brief,
        copySettings: true,
        overrideOriginalPrompt: override,
        ...(override ? { originalPrompt: original } : {}),
      });
    } catch (e) {
      if (active.current) {
        setError(e instanceof Error ? e.message : 'Handoff failed');
        setBusy(false);
        submitting.current = false;
      }
      return;
    }
    if (!active.current) return;
    onClose();
    // A committed handoff must never become a retryable form after refresh fails.
    try {
      await useSessionStore.getState().loadSessions({ throwOnError: true });
    } catch {
      useUIStore.getState().showToast('Handoff completed, but the session list could not be refreshed', 'error');
    }
  };

  return (
    <Modal open onClose={onClose} title={`Session Handoff · ${session.name}`} size="lg">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <p className="text-sm text-text-secondary">
          Create a new CLI session that takes over the relationships and settings, then archives the source session. Only this brief is carried over; previous briefs and chat history are not.
        </p>
        <label className="block text-sm">
          Handoff brief (required)
          <textarea
            required
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            disabled={busy}
            className="mt-1 w-full rounded border border-border-default bg-bg-primary p-2"
            rows={6}
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={override}
            onChange={(e) => setOverride(e.target.checked)}
            disabled={busy}
          />
          Override original prompt (originalPrompt)
        </label>
        <p className="text-sm text-text-secondary">
          By default, the source session's original prompt is inherited. When enabled, the text below is used; an empty value clears the original prompt. The source session is unchanged.
        </p>
        <label className="block text-sm">
          New original prompt
          <textarea
            value={original}
            onChange={(e) => setOriginal(e.target.value)}
            disabled={!override || busy}
            className="mt-1 w-full rounded border border-border-default bg-bg-primary p-2 disabled:opacity-50"
            rows={5}
          />
        </label>
        <p className="text-sm text-text-secondary">
          The new session system prompt combines this handoff brief with the selected original prompt.
        </p>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || !brief.trim()}
            className="rounded bg-accent px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {busy ? 'Handing off…' : 'Confirm handoff'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

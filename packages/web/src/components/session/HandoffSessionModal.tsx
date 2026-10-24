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
  const [target, setTarget] = useState('');
  const sessions = useSessionStore((s) => s.sessions);
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
        ...(target ? { targetSessionId: target } : { copySettings: true }),
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
          Source A: {session.name} ({session.id}). A is archived and managed by successor B. Select whether to create B or use an existing session.
        </p>
        <label className="block text-sm">
          Successor B
          <select value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy}
            className="mt-1 w-full rounded border border-border-default bg-bg-primary p-2">
            <option value="">Create a new session</option>
            {sessions.filter((s) => s.id !== session.id).map((s) => (
              <option key={s.id} value={s.id}>{s.name} ({s.id})</option>
            ))}
          </select>
        </label>
        <p className="text-sm text-text-secondary">
          {target
            ? 'Existing B keeps its name, history, CLI identity, settings and effective system prompt. Relationships are merged; the brief is queued as ordinary input, respecting Pause and locks. Queued does not mean read. Different managers, ancestors and different Workspaces are rejected.'
            : 'New B copies source settings and starts with a new CLI context. Previous briefs and chat history are not copied.'}
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
          By default, B's original prompt metadata is replaced with A's original prompt. When enabled, the text below is used; an empty value clears the metadata. A is unchanged. {target && 'This metadata is never injected into the existing target CLI, including later restart/resume.'}
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
          {target ? 'Existing B: actual system prompt stays exactly as before. The brief is one separate input message.' : 'New B: system prompt combines this handoff brief with the selected original prompt.'}
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

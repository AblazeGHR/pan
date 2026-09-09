import { useEffect, useRef, useState } from 'react';
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
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!session) return;
    setName(session.name || '');
    setSubmitting(false);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  }, [session]);

  if (!session) return null;

  const submit = async () => {
    const trimmed = name.trim();
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
          value={name}
          onChange={(event) => setName(event.target.value)}
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

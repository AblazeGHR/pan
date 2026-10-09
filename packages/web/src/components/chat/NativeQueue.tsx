import { useState } from 'react';
import { SendQueuePanel } from './SendQueuePanel';
import { useQueueStore } from '@/stores/queueStore';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';

/** Reuses the durable queue and its edit leases; never writes into the TUI. */
export function NativeQueue({ sessionId }: { sessionId: string }) {
  const edit = useQueueStore((s) => s.edits[sessionId]);
  const adding = useQueueStore((s) => s.lockedComposerModes[sessionId] ?? false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const close = () => {
    if (edit) useQueueStore.getState().cancelEdit();
    else useQueueStore.getState().setLockedComposerMode(sessionId, false);
  };
  const save = async () => {
    if (edit) { useQueueStore.getState().saveEdit(); return; }
    setSaving(true);
    try {
      if (await useQueueStore.getState().enqueue(draft, undefined, sessionId, undefined, { locked: true })) {
        setDraft(''); close();
      }
    } finally { setSaving(false); }
  };
  return <div className="flex-none border-t border-border-default" data-testid="native-queue">
    <SendQueuePanel nativeMode />
    <Modal open={!!edit || adding} onClose={close} title={edit ? 'Edit queued message' : 'Add queued message'}>
      <textarea aria-label="Queued message" className="w-full min-h-32 bg-bg-primary text-text-primary p-2 rounded border border-border-default"
        value={edit?.text ?? draft} disabled={!!edit?.acquiring || !!edit?.saving || saving}
        onChange={(event) => edit ? useQueueStore.getState().updateEditDraft(event.target.value) : setDraft(event.target.value)} />
      {edit?.error && <p role="alert">{edit.error}</p>}
      <div className="flex justify-end gap-2 mt-2">
        <Button onClick={close} disabled={saving || !!edit?.saving}>Cancel</Button>
        <Button onClick={() => void save()} disabled={saving || !!edit?.acquiring || !!edit?.saving || !!edit?.error || !(edit?.text ?? draft).trim()}>Save to queue</Button>
      </div>
    </Modal>
  </div>;
}

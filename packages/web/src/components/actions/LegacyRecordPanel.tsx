import { useState } from 'react';
import { actionRequest } from '@/services/api';
import { useActionRegistryStore } from '@/stores/actionRegistryStore';
import { Button } from '@/components/ui/Button';
import { Badge } from './ActionsUi';
import type { UnifiedActionRow, UnifiedGroupRow } from './unifiedModel';

export function LegacyRecordPanel({ action, group, onClose, onChanged }: {
  action?: UnifiedActionRow; group?: UnifiedGroupRow; onClose: () => void; onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const registry = useActionRegistryStore((state) => state.registry);
  const blocked = (group?.legacy?.blockers?.length ?? 0) > 0;
  async function unbuild() {
    if (!group || busy || blocked) return;
    setBusy(true); setError('');
    try {
      await actionRequest(`actions/groups/${encodeURIComponent(group.id)}/unbuild`, 'POST', {expectedRevision: registry.revision});
      await useActionRegistryStore.getState().refreshRegistry();
      await onChanged(); onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Cleanup unconfirmed'); }
    finally { setBusy(false); }
  }
  return <section className="space-y-3 rounded border border-border-default p-4" aria-label="Preserved source record">
    <Badge tone="warning">Invalid / Retired</Badge>
    {action?.preservedReason && <p role="note">{action.preservedReason}</p>}
    <p className="text-sm text-text-secondary">Legacy definitions are read-only and cannot execute. Create a new Action or Group to continue.</p>
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(action?.legacy ?? group?.legacy, null, 2)}</pre>
    {error && <p role="alert">{error}</p>}
    {group?.legacy?.build && <Button disabled={busy || blocked} title={blocked ? 'Unbuild blocked by running Sessions' : 'Unregister safely; preserve source files'} onClick={() => void unbuild()}>Unbuild retired Group</Button>}
    <Button onClick={onClose}>Back to catalog</Button>
  </section>;
}

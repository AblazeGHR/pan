import { useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { definitelyRejected, useOperationIdentity } from '@/components/actions/useOperationIdentity';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import type { CanonicalObject, CanonicalReceipt } from '@/types/canonicalJobs';

export function CanonicalJobEnable({ item, onChanged }: { item: CanonicalObject; onChanged: () => void | Promise<void> }) {
  const [pending, setPending] = useOperationIdentity(`job-enable:${item.id}`);
  const [reviewing, setReviewing] = useState(false); const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const lock = useRef(false);
  const settle = async (receipt: CanonicalReceipt) => {
    if (receipt.status === 'completed') { setPending(null); await onChanged(); }
    else if (['rejected', 'cancelled'].includes(receipt.status)) { setPending(null); setError(receipt.error?.message ?? `Operation ${receipt.status}.`); }
    else setError(`Operation ${receipt.status}; query the original request.`);
  };
  const execute = async (query: boolean) => {
    if (lock.current || (query ? !pending : !!pending)) return;
    const operation = item.state === 'enabled' ? 'pause' : item.state === 'paused' ? 'resume' : 'enable';
    const identity = pending ?? { operation, requestKey: crypto.randomUUID() };
    if (!query) setPending(identity);
    lock.current = true; setBusy(true); setError('');
    try { await settle(query ? await canonicalJobs.lookup(identity.operation, identity.requestKey) : await canonicalJobs.controlDefinition('jobs', item, identity.operation, identity.requestKey)); }
    catch (e) { if (definitelyRejected(e)) setPending(null); setError(canonicalError(e)); }
    finally { lock.current = false; setBusy(false); }
  };
  return <span className="inline-flex flex-wrap items-center gap-2">{pending ? <Button disabled={busy} onClick={() => void execute(true)}>Query original operation</Button> : item.state === 'enabled' || item.state === 'paused' ? <Button disabled={busy} onClick={() => void execute(false)}>{item.state === 'enabled' ? 'Pause' : 'Resume'}</Button> : !reviewing ? <Button disabled={busy} onClick={() => setReviewing(true)}>Enable Action Job</Button> : <span role="region" aria-label="Review before enabling" className="flex flex-wrap items-center gap-2"><label><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} /> I reviewed this definition, Actions, parameters and source references.</label><Button disabled={busy || !reviewed} onClick={() => void execute(false)}>Confirm enable</Button><Button disabled={busy} onClick={() => { setReviewing(false); setReviewed(false); }}>Cancel enable review</Button></span>}{error && <span role="alert">{error}</span>}</span>;
}

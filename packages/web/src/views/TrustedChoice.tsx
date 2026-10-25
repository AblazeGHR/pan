import { ShieldCheck } from 'lucide-react';
import type { InteractionCard, TrustedBinding } from './lockerModel';

export function TrustedChoice({
  choice,
  binding,
}: {
  choice: NonNullable<InteractionCard['definition']['choices']>[number];
  binding?: TrustedBinding;
}) {
  return (
    <section className="locker-trusted-choice" aria-label={`Trusted execution ${choice.id}`}>
      <ShieldCheck size={15} />
      <div>
        <strong>{binding ? 'On submit' : 'Execution unavailable'}</strong>
        {binding ? (
          <>
            <p>{binding.summary}</p>
            <p>Target: {binding.targetIds.join(', ')}</p>
            <details>
              <summary>Operation details</summary>
              <p>Action: {binding.actionId}</p>
              <code>{binding.actionDigest}</code>
            </details>
          </>
        ) : (
          <p>This choice is preserved for context but cannot execute without a trusted binding.</p>
        )}
      </div>
    </section>
  );
}

import { useRef, useState } from 'react';
import {
  lockerParent,
  lockerRequest,
  LockerRequestError,
  type LockerItem,
  type UserCard,
} from './lockerModel';

export function AttachedReminder({
  item,
  hasChild,
  created,
}: {
  item: LockerItem;
  hasChild: boolean;
  created: (card: UserCard) => void;
}) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState('');
  const [kind, setKind] = useState<'question' | 'task'>('question');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Keep the exact request after an uncertain response so retry cannot duplicate a card.
  const intent = useRef<Record<string, unknown> | null>(null);
  const submitted = useRef(false);
  if (hasChild) return null;
  return (
    <div className="locker-attach-action">
      <button type="button" onClick={() => setOpen(!open)}>
        Attach a reminder card
      </button>
      {open && (
        <form
          aria-label="Attach a reminder card"
          onSubmit={async (event) => {
            event.preventDefault();
            if (busy || submitted.current || hasChild) return;
            submitted.current = true;
            setBusy(true);
            setError('');
            const payload = intent.current ?? {
              ownerSessionId: item.ownerSessionId,
              parent: lockerParent(item),
              requestKey: crypto.randomUUID(),
              kind,
              body,
              attachments: [],
            };
            intent.current = payload;
            try {
              const result = await lockerRequest<{ card: UserCard }>(
                '/api/reminding-cards',
                'POST',
                payload,
              );
              created(result.card);
              intent.current = null;
              setBody('');
              setOpen(false);
            } catch (value) {
              if (value instanceof LockerRequestError && value.status >= 400 && value.status < 500)
                intent.current = null;
              setError(String(value));
            } finally {
              submitted.current = false;
              setBusy(false);
            }
          }}
        >
          <label>
            Reminder type{' '}
            <select
              value={kind}
              disabled={busy || !!intent.current}
              onChange={(event) => setKind(event.target.value as 'question' | 'task')}
            >
              <option value="question">Question</option>
              <option value="task">Task</option>
            </select>
          </label>
          <label>
            Reminder body
            <textarea
              required
              value={body}
              maxLength={100000}
              disabled={busy || !!intent.current}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
          <button type="submit" disabled={busy || !body.trim()}>
            {busy ? 'Creating…' : intent.current ? 'Retry creation' : 'Create'}
          </button>
          {error && (
            <p role="alert" className="locker-error">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

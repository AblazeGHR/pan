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
  created,
}: {
  item: LockerItem;
  created: (card: UserCard) => void;
}) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState('');
  const [kind, setKind] = useState<'question' | 'task'>('question');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Keep the exact request after an uncertain response so retry cannot duplicate a card.
  const intent = useRef<Record<string, unknown> | null>(null);
  return (
    <div className="locker-attach-action">
      <button type="button" onClick={() => setOpen(!open)}>
        创建提醒卡
      </button>
      {open && (
        <form
          aria-label="Create attached reminding card"
          onSubmit={async (event) => {
            event.preventDefault();
            if (busy) return;
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
              setBusy(false);
            }
          }}
        >
          <label>
            提醒类型{' '}
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
            提醒内容
            <textarea
              required
              value={body}
              maxLength={100000}
              disabled={busy || !!intent.current}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
          <button type="submit" disabled={busy || !body.trim()}>
            {busy ? 'Creating…' : intent.current ? 'Retry creation' : 'Create reminding card'}
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

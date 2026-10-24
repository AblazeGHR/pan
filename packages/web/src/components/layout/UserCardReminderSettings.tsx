import { useEffect, useState } from 'react';
import { lockerRequest } from '@/views/lockerModel';

export function UserCardReminderSettings() {
  const [minutes, setMinutes] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void lockerRequest<{ minutes: number }>(
      '/api/user-cards/settings',
      'GET',
      undefined,
      controller.signal,
    )
      .then((result) => setMinutes(result.minutes))
      .catch((value) => {
        if (!controller.signal.aborted) setError(String(value));
      });
    return () => controller.abort();
  }, []);
  return (
    <section className="rounded-md border border-border-muted bg-bg-primary p-3 space-y-2">
      <label className="flex items-center justify-between gap-3 text-xs text-text-secondary">
        User card reminder interval (minutes)
        <input
          className="w-20 shrink-0 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
          aria-label="User card reminder interval (minutes)"
          type="number"
          min={1}
          max={1440}
          step={1}
          disabled={minutes === null || busy}
          key={minutes}
          defaultValue={minutes ?? ''}
          onBlur={(event) => {
            const next = Number(event.target.value);
            if (!Number.isInteger(next) || next < 1 || next > 1440) {
              event.target.value = String(minutes ?? '');
              setError('Enter an integer from 1 to 1440.');
              return;
            }
            if (next === minutes) return;
            setBusy(true);
            setError('');
            void lockerRequest<{ minutes: number }>('/api/user-cards/settings', 'PUT', {
              minutes: next,
            })
              .then((result) => setMinutes(result.minutes))
              .catch((value) => setError(String(value)))
              .finally(() => setBusy(false));
          }}
        />
      </label>
      <p className="text-[11px] leading-relaxed text-text-tertiary">Default: 10 minutes. Notifications enter the owning Session queue.</p>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

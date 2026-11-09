import { useState } from 'react';
import { automationLogin, automationLogout, useAutomationAuth } from '@/stores/automationStore';
import { Button } from '@/components/ui/Button';
import { Field, inputClass } from '@/components/jobs/ScheduleListEditor';

/** Reuses the shared automation Cookie or Bearer session. */
export function JobUserAuthorization({ purpose = 'jobs' }: { purpose?: 'jobs' | 'locker' }) {
  const token = useAutomationAuth((s) => s.token || s.authenticated);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <section
      aria-label={purpose === 'locker' ? 'Locker user authorization' : 'Job user authorization'}
      className="flex flex-col gap-3 rounded-xl border border-border-default bg-bg-secondary px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="min-w-0">
        <p className="flex items-center gap-2 font-medium">
          <span
            aria-hidden
            className={`size-1.5 rounded-full ${token ? 'bg-success' : 'bg-warning'}`}
          />
          {token ? 'Authorized' : 'Sign in required'}
        </p>
        <p className="mt-1 max-w-3xl text-xs leading-5 text-text-secondary">
          {purpose === 'locker'
            ? 'Locker uses automation/user.key and the shared login session. Login accesses this profile’s Locker messages and cards.'
            : 'User confirmation uses automation/user.key and the shared login session. Enabled Jobs keep their granted capability after logout.'}
        </p>
      </div>
      <div className="shrink-0">
        {token ? (
          <Button
            size="sm"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void automationLogout()
                .catch(() => setError('Server logout unconfirmed; local token cleared'))
                .finally(() => setBusy(false));
            }}
          >
            Log out
          </Button>
        ) : (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const secret = key;
              setKey('');
              setBusy(true);
              setError('');
              void automationLogin(secret)
                .catch((failure: unknown) =>
                  setError(failure instanceof Error ? failure.message : 'Login failed'),
                )
                .finally(() => setBusy(false));
            }}
          >
            <Field label="Local user key">
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                required
              />
            </Field>
            <Button type="submit" size="sm" variant="primary" disabled={busy}>
              Log in
            </Button>
          </form>
        )}
      </div>
      {error && (
        <p role="alert" className="break-words text-danger sm:order-last sm:w-full">
          {error}
        </p>
      )}
    </section>
  );
}

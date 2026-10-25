import { useState } from 'react';
import { automationLogin, automationLogout, useAutomationAuth } from '@/stores/automationStore';
import { Button } from '@/components/ui/Button';
import { Field, inputClass } from '@/components/jobs/ScheduleListEditor';

/** Reuses the shared automation Cookie or Bearer session. */
export function JobUserAuthorization({ purpose = 'jobs' }: { purpose?: 'jobs' | 'locker' }) {
  const token = useAutomationAuth((s) => s.token || s.authenticated);
  const [key, setKey] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  return <section aria-label={purpose === 'locker' ? 'Locker user authorization' : 'Job user authorization'} className="rounded border border-border-default p-3 text-xs space-y-2">
    <p>{purpose === 'locker'
      ? 'Locker uses automation/user.key and the shared login session. Login accesses this profile’s Locker messages and cards.'
      : 'User confirmation uses automation/user.key and the shared login session. Enabled Jobs keep their granted capability after logout.'}</p>
    {token ? <Button disabled={busy} onClick={() => { setBusy(true); void automationLogout().catch(() => setError('Server logout unconfirmed; local token cleared')).finally(() => setBusy(false)); }}>Log out</Button> : <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
      e.preventDefault(); const secret=key;setKey('');setBusy(true);setError('');
      void automationLogin(secret).catch((failure: unknown) => setError(failure instanceof Error ? failure.message : 'Login failed')).finally(() => setBusy(false));
    }}><Field label="Local user key"><input className={inputClass} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} required /></Field><Button type="submit" disabled={busy}>Log in</Button></form>}
    {error && <p role="alert" className="text-danger">{error}</p>}
  </section>;
}

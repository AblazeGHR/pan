import { useEffect, useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/Button';
import { Field, inputClass } from '@/components/jobs/ScheduleListEditor';
import { automationLogin, useAutomationAuth } from '@/stores/automationStore';

type Status = { configured: boolean; revision: string };

export function AuthorizationSettings() {
  const token = useAutomationAuth((state) => state.token);
  const clear = useAutomationAuth((state) => state.clear);
  const [status, setStatus] = useState<Status | null>(null);
  const [key, setKey] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!token) { setStatus(null); return; }
    let cancelled = false;
    void fetch('/api/automation/authorization', { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok) throw new Error('Authorization status unavailable');
        return response.json() as Promise<Status>;
      })
      .then((value) => { if (!cancelled) setStatus(value); })
      .catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Authorization status unavailable'); });
    return () => { cancelled = true; };
  }, [token]);

  const login = async () => {
    const secret = key; setKey(''); setError(''); setBusy(true);
    try { await automationLogin(secret); setMessage('Logged in for this browser session.'); }
    catch (reason: unknown) { setError(reason instanceof Error ? reason.message : 'Login failed'); }
    finally { setBusy(false); }
  };

  const change = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setMessage('');
    if (newPassword !== confirmNewPassword) { setError('New password confirmation does not match.'); return; }
    setBusy(true);
    try {
      const response = await fetch('/api/automation/authorization', {
        method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ currentPassword, newPassword, confirmNewPassword, expectedRevision: status?.revision }),
      });
      const body = await response.json().catch(() => ({})) as { detail?: string };
      if (!response.ok) throw new Error(body.detail || `Password change failed (${response.status})`);
      setCurrentPassword(''); setNewPassword(''); setConfirmNewPassword(''); setMessage('Password changed. Please log in again with the new password.');
      clear(); setStatus(null);
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : 'Password change failed'); }
    finally { setBusy(false); }
  };

  return <div className="space-y-4">
    <section className="rounded border border-border-default p-3 text-xs space-y-2">
      <h3 className="font-semibold text-text-primary">Local Authorization</h3>
      <p className="text-text-secondary">The local user key is stored in the profile as <code>automation/user.key</code>. Bearer sessions stay in memory and are revoked after a password change.</p>
      <p role="status" className="text-text-secondary">Login status: {token ? 'Logged in' : 'Logged out'} · key configured: {status?.configured ? 'Yes' : token ? 'Unknown' : 'Log in to check'}</p>
      {!token && <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); void login(); }}><Field label="Current local password"><input className={inputClass} type="password" autoComplete="current-password" value={key} onChange={(event) => setKey(event.target.value)} required /></Field><Button type="submit" disabled={busy}>Log in</Button></form>}
    </section>
    {token && <form className="rounded border border-border-default p-3 space-y-3" onSubmit={(event) => void change(event)}>
      <h3 className="font-semibold text-text-primary">Change password</h3>
      <p className="text-[11px] text-text-secondary">Use 16-128 printable ASCII characters and at least 3 of lowercase, uppercase, digit, and symbol.</p>
      <Field label="Current password"><input className={inputClass} type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></Field>
      <Field label="New password"><input className={inputClass} type="password" autoComplete="new-password" minLength={16} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></Field>
      <Field label="Confirm new password"><input className={inputClass} type="password" autoComplete="new-password" minLength={16} value={confirmNewPassword} onChange={(event) => setConfirmNewPassword(event.target.value)} required /></Field>
      <Button type="submit" disabled={busy || !status}>{busy ? 'Saving…' : 'Change password'}</Button>
    </form>}
    {message && <p role="status" className="text-success text-xs">{message}</p>}
    {error && <p role="alert" className="text-danger text-xs">{error}</p>}
  </div>;
}

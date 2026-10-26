import { useEffect, useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/Button';
import { Field, inputClass } from '@/components/jobs/ScheduleListEditor';
import { automationLogin, automationLogout, automationRequest, useAutomationAuth } from '@/stores/automationStore';

const PERMISSIONS = {
  trigger: ['read', 'create', 'updateConfig', 'lifecycle', 'delete'],
  action: ['read', 'create', 'updateConfig', 'lifecycle', 'delete', 'manageSystemActions'],
  jobTemplate: ['read', 'create', 'updateConfig', 'use', 'delete'],
  job: ['read', 'create', 'updateConfig', 'lifecycle', 'execute', 'delete'],
} as const;
type PermissionKind = keyof typeof PERMISSIONS;
type Permissions = Record<PermissionKind, Record<string, boolean>>;
type Status = { configured: boolean; revision: string; authorizationRevision: number; permissions: Permissions; sessionDays: number };

export function AuthorizationSettings() {
  const token = useAutomationAuth((state) => state.token || state.authenticated);
  const clear = useAutomationAuth((state) => state.clear);
  const [status, setStatus] = useState<Status | null>(null);
  const [key, setKey] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [permissions, setPermissions] = useState<Permissions | null>(null);
  const [permissionRevision, setPermissionRevision] = useState(0);
  const [sessionDays, setSessionDays] = useState(0);

  useEffect(() => {
    if (!token) { setStatus(null); return; }
    let cancelled = false;
    void automationRequest<Status>('automation/authorization')
      .then((value) => { if (!cancelled) { setStatus(value); setPermissions(value.permissions); setPermissionRevision(value.authorizationRevision); setSessionDays(value.sessionDays ?? 0); } })
      .catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Authorization status unavailable'); });
    return () => { cancelled = true; };
  }, [token]);

  const login = async () => {
    const secret = key; setKey(''); setError(''); setBusy(true);
    try { await automationLogin(secret); setMessage('Logged in for this browser session.'); }
    catch (reason: unknown) { setError(reason instanceof Error ? reason.message : 'Login failed'); }
    finally { setBusy(false); }
  };

  const savePermissions = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const body = await automationRequest<{ revision: number; permissions: Permissions }>('automation/authorization/permissions', 'PUT', { permissions, expectedRevision: permissionRevision });
      if (body.permissions) setPermissions(body.permissions);
      if (typeof body.revision === 'number') setPermissionRevision(body.revision);
      setMessage('Permission matrix saved and active for new operations.');
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : 'Permission save failed'); }
    finally { setBusy(false); }
  };

  const change = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setMessage('');
    if (newPassword !== confirmNewPassword) { setError('New password confirmation does not match.'); return; }
    setBusy(true);
    try {
      await automationRequest('automation/authorization', 'PUT', { currentPassword, newPassword, confirmNewPassword, expectedRevision: status?.revision });
      clear(); setStatus(null);
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : 'Password change failed'); }
    finally { setBusy(false); }
  };

  return <div className="space-y-4">
    <section className="rounded border border-border-default p-3 text-xs space-y-2">
      <h3 className="font-semibold text-text-primary">Local Authorization</h3>
      <p className="text-text-secondary">The local user key is stored in the profile as <code>automation/user.key</code>. Sessions are restored by an HttpOnly cookie and all sessions are revoked after a password change.</p>
      <p role="status" className="text-text-secondary">Login status: {token ? 'Logged in' : 'Logged out'} · key configured: {status?.configured ? 'Yes' : token ? 'Unknown' : 'Log in to check'}</p>
      {token && <Button disabled={busy} onClick={() => void automationLogout().catch(() => setError('Server logout could not be confirmed.'))}>Log out</Button>}
      {!token && <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); void login(); }}><Field label="Current local password"><input className={inputClass} type="password" autoComplete="current-password" value={key} onChange={(event) => setKey(event.target.value)} required /></Field><Button type="submit" disabled={busy}>Log in</Button></form>}
    </section>
    {token && <section className="rounded border border-border-default p-3 space-y-3">
      <Field label="Long login duration"><select className={inputClass} value={sessionDays} disabled={busy} onChange={async (event) => {
        const days = Number(event.target.value); setBusy(true); setError(''); setMessage('');
        try {
          const saved = await automationRequest<{ sessionDays: number }>('automation/settings', 'PUT', { sessionDays: days });
          setSessionDays(saved.sessionDays); useAutomationAuth.setState({ sessionDays: saved.sessionDays });
          setMessage('Login duration saved for this profile.');
        } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : 'Login duration save failed'); }
        finally { setBusy(false); }
      }}>{[0, 7, 30, 90].map((days) => <option key={days} value={days}>{days} days</option>)}</select></Field>
      <p className="text-xs text-text-secondary">0 = long login disabled: the cookie expires when the browser closes; server sessions expire after 12 hours. Other values use sliding expiration and apply to all sessions.</p>
    </section>}
    {token && <form className="rounded border border-border-default p-3 space-y-3" onSubmit={(event) => void change(event)}>
      <h3 className="font-semibold text-text-primary">Change password</h3>
      <p className="text-[11px] text-text-secondary">Use 16-128 printable ASCII characters and at least 3 of lowercase, uppercase, digit, and symbol.</p>
      <Field label="Current password"><input className={inputClass} type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></Field>
      <Field label="New password"><input className={inputClass} type="password" autoComplete="new-password" minLength={16} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></Field>
      <Field label="Confirm new password"><input className={inputClass} type="password" autoComplete="new-password" minLength={16} value={confirmNewPassword} onChange={(event) => setConfirmNewPassword(event.target.value)} required /></Field>
      <Button type="submit" disabled={busy || !status}>{busy ? 'Saving…' : 'Change password'}</Button>
    </form>}
    {token && permissions && <form className="rounded border border-border-default p-3 space-y-3" onSubmit={(event) => void savePermissions(event)}>
      <h3 className="font-semibold text-text-primary">Profile-wide Agent permissions</h3>
      <p className="text-[11px] text-text-secondary">Unchecked capabilities deny delegated Agent operations. User Bearer management remains available. Saves use revision CAS and apply immediately to trusted MCP calls and new delegated effects.</p>
      <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr><th className="text-left">Object</th>{Array.from(new Set(Object.values(PERMISSIONS).flat())).map((cap) => <th key={cap} className="px-2 text-left">{cap}</th>)}</tr></thead><tbody>{(Object.keys(PERMISSIONS) as PermissionKind[]).map((kind) => <tr key={kind}><th className="py-1 text-left">{kind}</th>{Array.from(new Set(Object.values(PERMISSIONS).flat())).map((cap) => <td key={cap} className="px-2">{(PERMISSIONS[kind] as readonly string[]).includes(cap) && <input aria-label={`${kind}.${cap}`} type="checkbox" checked={Boolean(permissions[kind][cap])} onChange={(event) => setPermissions({ ...permissions, [kind]: { ...permissions[kind], [cap]: event.target.checked } })} />}</td>)}</tr>)}</tbody></table></div>
      <Button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save permissions'}</Button>
    </form>}
    {message && <p role="status" className="text-success text-xs">{message}</p>}
    {error && <p role="alert" className="text-danger text-xs">{error}</p>}
  </div>;
}

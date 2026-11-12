import { settingsText, settingsVisible } from '@/components/layout/settingsCatalog';
import { useEffect, useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/Button';
import { Field, inputClass } from '@/components/jobs/ScheduleListEditor';
import { automationLogin, automationLogout, automationRequest, useAutomationAuth } from '@/stores/automationStore';

import { SETTING_PERMISSIONS as PERMISSIONS } from './settingsDescriptions';
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
    if (!newPassword) { setError('New password must not be empty.'); return; }
    if (newPassword !== newPassword.trim()) { setError('New password must not start or end with whitespace.'); return; }
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
      <h3 className="font-semibold text-text-primary" data-settings-id="local-authorization">{settingsText("local-authorization")}</h3>
      <p className="text-text-secondary" data-settings-id="authorization-settings.1">{settingsText("authorization-settings.1")}<code data-settings-id="authorization-settings.2">{settingsText("authorization-settings.2")}</code>{settingsText("authorization-settings.3")}</p>
      <p role="status" className="text-text-secondary" data-settings-id="authorization-settings.4">{settingsText("authorization-settings.4")}{token ? 'Logged in' : 'Logged out'} {settingsText("authorization-settings.5")}{status?.configured ? 'Yes' : token ? 'Unknown' : 'Log in to check'}</p>
      {settingsVisible('authorized', { authorizationLoggedIn: Boolean(token) }) && <Button disabled={busy} onClick={() => void automationLogout().catch(() => setError('Server logout could not be confirmed.'))} data-settings-id="authorization-settings.6">{settingsText("authorization-settings.6")}</Button>}
      {settingsVisible('anonymous', { authorizationLoggedIn: Boolean(token) }) && <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); void login(); }}><Field label={settingsText("authorization-settings.7")} settingId="authorization-settings.7"><input className={inputClass} type="password" autoComplete="current-password" value={key} onChange={(event) => setKey(event.target.value)} required /></Field><Button type="submit" disabled={busy} data-settings-id="authorization-settings.8">{settingsText("authorization-settings.8")}</Button></form>}
    </section>
    {settingsVisible('authorized', { authorizationLoggedIn: Boolean(token) }) && <section className="rounded border border-border-default p-3 space-y-3">
      <Field label={settingsText("authorization-settings.9")} settingId="authorization-settings.9"><select className={inputClass} value={sessionDays} disabled={busy} onChange={async (event) => {
        const days = Number(event.target.value); setBusy(true); setError(''); setMessage('');
        try {
          const saved = await automationRequest<{ sessionDays: number }>('automation/settings', 'PUT', { sessionDays: days });
          setSessionDays(saved.sessionDays); useAutomationAuth.setState({ sessionDays: saved.sessionDays });
          setMessage('Login duration saved for this profile.');
        } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : 'Login duration save failed'); }
        finally { setBusy(false); }
      }}>{[0, 7, 30, 90].map((days) => <option key={days} value={days} data-settings-id="authorization-settings.10">{days} {settingsText("authorization-settings.10")}</option>)}</select></Field>
      <p className="text-xs text-text-secondary" data-settings-id="authorization-settings.11">{settingsText("authorization-settings.11")}</p>
    </section>}
    {settingsVisible('authorized', { authorizationLoggedIn: Boolean(token) }) && <form className="rounded border border-border-default p-3 space-y-3" onSubmit={(event) => void change(event)}>
      <h3 className="font-semibold text-text-primary" data-settings-id="change-password">{settingsText("change-password")}</h3>
      <p className="text-[11px] text-text-secondary" data-settings-id="authorization-settings.12">{settingsText("authorization-settings.12")}</p>
      <Field label={settingsText("authorization-settings.13")} settingId="authorization-settings.13"><input className={inputClass} type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></Field>
      <Field label={settingsText("authorization-settings.14")} settingId="authorization-settings.14"><input className={inputClass} type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></Field>
      <Field label={settingsText("authorization-settings.15")} settingId="authorization-settings.15"><input className={inputClass} type="password" autoComplete="new-password" value={confirmNewPassword} onChange={(event) => setConfirmNewPassword(event.target.value)} required /></Field>
      <Button type="submit" disabled={busy || !status}>{busy ? 'Saving…' : 'Change password'}</Button>
    </form>}
    {settingsVisible('authorized', { authorizationLoggedIn: Boolean(token) }) && permissions && <form className="rounded border border-border-default p-3 space-y-3" onSubmit={(event) => void savePermissions(event)}>
      <h3 className="font-semibold text-text-primary" data-settings-id="agent-permissions">{settingsText("agent-permissions")}</h3>
      <p className="text-[11px] text-text-secondary" data-settings-id="authorization-settings.16">{settingsText("authorization-settings.16")}</p>
      <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr><th className="text-left" data-settings-id="authorization-settings.17">{settingsText("authorization-settings.17")}</th>{Array.from(new Set(Object.values(PERMISSIONS).flat())).map((cap) => <th key={cap} className="px-2 text-left">{cap}</th>)}</tr></thead><tbody>{(Object.keys(PERMISSIONS) as PermissionKind[]).map((kind) => <tr key={kind}><th className="py-1 text-left">{kind}</th>{Array.from(new Set(Object.values(PERMISSIONS).flat())).map((cap) => <td key={cap} className="px-2">{(PERMISSIONS[kind] as readonly string[]).includes(cap) && <input data-settings-id={`agent-permissions.${kind}.${cap}`} aria-label={settingsText(`agent-permissions.${kind}.${cap}`)} type="checkbox" checked={Boolean(permissions[kind][cap])} onChange={(event) => setPermissions({ ...permissions, [kind]: { ...permissions[kind], [cap]: event.target.checked } })} />}</td>)}</tr>)}</tbody></table></div>
      <Button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save permissions'}</Button>
    </form>}
    {message && <p role="status" className="text-success text-xs">{message}</p>}
    {error && <p role="alert" className="text-danger text-xs">{error}</p>}
  </div>;
}

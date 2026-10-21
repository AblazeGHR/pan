import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { automationLogin, automationLogout, useAutomationAuth, useAutomationStore } from '@/stores/automationStore';
import { cardClass, fieldClass, JsonDetail } from '@/components/automation/fields';
import { TriggerEditor } from '@/components/automation/TriggerEditor';
import { PauseTemplate } from '@/components/automation/PauseTemplate';
import { RunList } from '@/components/automation/RunList';
import type { Trigger } from '@/types/automation';
import { useActionRegistryStore } from '@/stores/actionRegistryStore';

function Login() {
  const token = useAutomationAuth((s) => s.token);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <section aria-label="Automation login" className={cardClass}><h2 className="mb-2 font-medium">User authorization</h2><p className="mb-3 text-sm text-text-secondary">Local key: automation/user.key beside the Session data directory. Login lasts up to 12 hours in memory. Refresh requires login again. Token expiry does not stop enabled Triggers.</p>
    {error && <p role="alert" className="mb-2 text-sm text-danger">{error}</p>}
    {token ? <Button disabled={busy} onClick={() => { setBusy(true); setError(''); void automationLogout().catch(() => setError('Server logout could not be confirmed. Local credentials cleared.')).finally(() => setBusy(false)); }}>Log out</Button> : <form className="flex min-w-0 flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); const secret = key; setKey(''); setBusy(true); setError(''); void automationLogin(secret).catch((e: unknown) => setError(e instanceof Error ? e.message : 'Login failed')).finally(() => setBusy(false)); }}><label className="min-w-0 flex-1 basis-48 text-sm">Local user key<input type="password" required autoComplete="off" className={fieldClass} value={key} onChange={(e) => setKey(e.target.value)} /></label><Button type="submit" disabled={busy}>Log in</Button></form>}
  </section>;
}

export default function AutomationView() {
  const { data: snapshot, loading, error, refresh, updatedAt } = useAutomationStore();
  const registry = useActionRegistryStore((s) => s.registry);
  const registryError = useActionRegistryStore((s) => s.refreshError);
  const data = snapshot ? { ...snapshot, actions: registry } : null;
  const [tab, setTab] = useState<'Triggers' | 'Runs' | 'Diagnostics'>('Triggers');
  const [editor, setEditor] = useState<{ key: number; initial: Trigger | null } | null>(null);
  const [template, setTemplate] = useState(false);
  const leaveGuard = useRef<(() => boolean) | null>(null);
  const editorSequence = useRef(0);
  const [editorBusy, setEditorBusy] = useState(false);
  function openEditor(initial: Trigger | null) {
    if (editor && !leaveGuard.current?.()) return;
    setEditor({ key: ++editorSequence.current, initial });
  }
  useEffect(() => { void refresh(); }, [refresh]);
  return <section aria-label="Automation management" className="h-full min-w-0 overflow-y-auto p-3 pt-12 text-text-primary sm:p-6"><div className="mx-auto max-w-5xl space-y-5">
    <header className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold">Automation</h1><p className="mt-1 text-sm text-text-secondary">Events → Triggers → Actions</p></div><div className="flex flex-wrap gap-2"><Link className="rounded border border-border-default p-2 text-sm" to="/actions">Manage Actions</Link><Button disabled={loading} onClick={() => void refresh(tab !== 'Runs')}>{tab === 'Runs' ? 'Refresh Runs' : 'Refresh'}</Button></div></header>
    {registryError && <p role="alert" className="text-sm text-danger">Action refresh failed: {registryError}. Cached Actions are retained.</p>}
    <p role="status" className="text-xs text-text-secondary">{loading ? 'Refreshing snapshot…' : updatedAt ? `Snapshot fetched ${new Date(updatedAt).toLocaleString()}. Manual refresh; no live updates.` : 'No snapshot loaded.'}</p>
    {error && <p role="alert" className="text-sm text-danger">Refresh failed: {error}. The last successful snapshot is retained.</p>}
    <Login />
    <nav aria-label="Automation sections" className="flex flex-wrap gap-2">{(['Triggers', 'Runs', 'Diagnostics'] as const).map((name) => <Button key={name} aria-pressed={tab === name} variant={tab === name ? 'primary' : 'secondary'} onClick={() => setTab(name)}>{name}</Button>)}</nav>
    {data && <>
      {<section hidden={tab !== 'Triggers'} aria-label="Triggers" className="min-w-0 space-y-4"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold">Triggers</h2><div className="flex flex-wrap gap-2"><Button onClick={() => setTemplate(true)}>Pause Everything template</Button><Button disabled={editorBusy} variant="primary" onClick={() => openEditor(null)}>New Trigger</Button></div></div>
        {template && <PauseTemplate onClose={() => setTemplate(false)} />}
        {editor && <TriggerEditor key={editor.key} initial={editor.initial} data={data} leaveGuard={leaveGuard} onBusyChange={setEditorBusy} onClose={() => setEditor(null)} />}
        {!Object.keys(data.automation.triggers).length && <p className={cardClass}>No Triggers yet. Create a disabled draft, choose Events and reference an Action.</p>}
        <div className="grid min-w-0 gap-3 sm:grid-cols-2">{Object.values(data.automation.triggers).map((trigger) => { const dependency = data.automation.dependencies[trigger.id]; const action = data.actions.actions.find((a) => a.id === trigger.actionId); return <article key={trigger.id} className={`${cardClass} space-y-3`}>
          <h3 className="break-words font-semibold">{trigger.name}</h3><p className="text-sm">{trigger.enabled ? 'Enabled' : 'Disabled'} · Serial · Revision {trigger.revision}</p><p className="break-all text-xs text-text-secondary">{trigger.id} · Created by {trigger.createdBy.type}{trigger.createdBy.id ? `: ${trigger.createdBy.id}` : ''}</p><p className="break-words text-sm">{trigger.description}</p>
          <p className="break-all text-sm">Events (OR): {trigger.eventTypes.join(', ')}</p><p className="break-all text-sm">Action: {action?.name ?? trigger.actionId} · {action?.enabled ? 'Enabled' : action ? 'Disabled' : 'Missing'}</p><p className="break-all text-sm">Dependencies: {dependency?.available ? 'Available' : 'Unavailable'}{dependency?.unavailable.length ? ` · ${dependency.unavailable.join(', ')}` : ''}</p>
          <Button disabled={editorBusy} onClick={() => openEditor(trigger)}>Edit {trigger.name}</Button>
        </article>; })}</div>
      </section>}
      {tab === 'Runs' && <RunList snapshot={data.automation} />}
      {tab === 'Diagnostics' && <section aria-label="Diagnostics" className="space-y-4"><h2 className="text-lg font-semibold">Dependencies and diagnostics</h2><p className="text-sm text-text-secondary">Pre-persistence diagnostics are process-local and may disappear on restart. Runs retain persisted errors. Event delivery is not a durable replay log.</p><JsonDetail label="Runtime diagnostics" value={data.automation.diagnostics} /><JsonDetail label="Event definitions and availability" value={data.automation.definitions} /><JsonDetail label="Trigger dependencies" value={data.automation.dependencies} /><JsonDetail label="Runtime limits" value={data.automation.limits} /></section>}
    </>}
  </div></section>;
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { actionRequest } from '@/services/api';
import { Button } from '@/components/ui/Button';
import { PluginSelection } from '@/components/session/PluginSelection';
import type { McpServerInfo, PluginBinding } from '@/types';
import type { Action, ActionGroup, ActionRegistry, InputSchema } from '@/types/actions';

const emptyAction = (): Action => ({ id: '', name: '', description: '', enabled: false, effect: 'read', output_format: 'json',
  input_schema: { type: 'object', properties: {}, required: [], additionalProperties: false },
  execution: { kind: 'api', method: 'GET', path: '/api/sessions' } });
type SessionConfig = { id: string; name: string; pluginBindings: PluginBinding[]; pluginSelectionRevision: number; pluginApplyStatus?: string; error?: string };
type GroupDraft = Pick<ActionGroup, 'id' | 'name' | 'action_ids'>;
type Tab = 'Actions' | 'New Action' | 'Groups';
const fieldClass = 'mt-1 w-full min-w-0 rounded-md border border-border-default bg-bg-primary px-3 py-2 text-sm focus:border-accent focus:outline-none';
const cardClass = 'min-w-0 rounded-xl border border-border-default bg-bg-secondary p-4 sm:p-6';

export default function ActionsView() {
  const [tab, setTab] = useState<Tab>('Actions');
  const [registry, setRegistry] = useState<ActionRegistry>({ revision: 0, actions: [], groups: [] });
  const [action, setAction] = useState<Action | null>(null);
  const [group, setGroup] = useState<GroupDraft | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sessions, setSessions] = useState<{ id: string; name: string }[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [session, setSession] = useState<SessionConfig | null>(null);
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const [bindingGroupId, setBindingGroupId] = useState('');
  const currentSession = useRef(sessionId);
  currentSession.current = sessionId;

  const reload = useCallback(async () => {
    const [sources, catalog, list] = await Promise.all([
      actionRequest<ActionRegistry>('actions'), actionRequest<{ servers: McpServerInfo[] }>('mcp/servers'),
      actionRequest<{ sessions: { id: string; name: string }[] }>('sessions'),
    ]);
    setRegistry(sources); setServers(catalog.servers); setSessions(list.sessions);
  }, []);
  useEffect(() => { void reload().catch((e: Error) => setError(e.message)).finally(() => setLoading(false)); }, [reload]);
  useEffect(() => {
    setSession(null);
    if (!sessionId) return;
    const controller = new AbortController();
    void actionRequest<SessionConfig>(`sessions/${encodeURIComponent(sessionId)}`, 'GET', undefined, controller.signal)
      .then((value) => { if (!controller.signal.aborted) { if (value.error) throw new Error(value.error); setSession(value); } })
      .catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [sessionId]);

  async function perform(operation: () => Promise<void>) {
    setBusy(true); setError('');
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function mutate(path: string, method: string, item?: unknown) {
    const selected = sessionId;
    const data = await actionRequest<ActionRegistry & { item?: Action | ActionGroup }>(path, method,
      { ...(item ? { item } : {}), expectedRevision: registry.revision });
    setRegistry(data);
    // Commit the returned identity before secondary refreshes, so a failed
    // catalog read cannot turn a successful create into another create on retry.
    if (data.item && 'execution' in data.item) { setAction(data.item); setTab('Actions'); }
    else if (data.item) setGroup({ id: data.item.id, name: data.item.name, action_ids: data.item.action_ids });
    const catalog = await actionRequest<{ servers: McpServerInfo[] }>('mcp/servers');
    setServers(catalog.servers);
    if (selected) {
      const updated = await actionRequest<SessionConfig>(`sessions/${encodeURIComponent(selected)}`);
      if (currentSession.current === selected) setSession(updated);
    }
    return data;
  }
  const selectedGroup = registry.groups.find((item) => item.id === group?.id);
  const bindingGroup = registry.groups.find((item) => item.id === bindingGroupId);
  function newAction() { setAction(emptyAction()); setTab('New Action'); }
  function chooseGroup(item: ActionGroup) { setGroup({ id: item.id, name: item.name, action_ids: [...item.action_ids] }); }

  return <main className="h-full overflow-auto bg-bg-primary px-4 py-6 text-text-primary sm:px-8" aria-label="Actions Panel">
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4 pl-7 md:pl-0">
        <div><h1 className="text-2xl font-semibold tracking-tight">Actions & Groups</h1>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-secondary">Define an Action once. Share it across Groups, then build a Group to use it in a Session.</p></div>
        <Button disabled={busy || loading} onClick={() => void perform(reload)}>Refresh</Button>
      </header>
      <nav aria-label="Actions navigation" className="flex gap-1 rounded-lg bg-bg-secondary p-1" role="tablist">
        {(['Actions', 'New Action', 'Groups'] as Tab[]).map((name) => <button key={name} role="tab" aria-selected={tab === name}
          className={`min-w-0 flex-1 rounded-md px-3 py-2 text-sm font-medium transition-colors ${tab === name ? 'bg-bg-tertiary text-text-primary shadow-sm' : 'text-text-secondary hover:text-text-primary'}`}
          onClick={() => { if (name === 'New Action') newAction(); else setTab(name); }}>{name}</button>)}
      </nav>
      {error && <div role="alert" className="whitespace-pre-wrap break-words rounded-lg border border-danger/40 bg-danger/10 p-4 text-sm">{error}</div>}
      {loading ? <p role="status" className={cardClass}>Loading Actions and Groups…</p> : <>
        {tab === 'Actions' && <section aria-label="Actions" className="space-y-4">
          <div className="flex items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">Actions</h2><p className="text-sm text-text-secondary">Reusable definitions · {registry.actions.length} total</p></div><Button variant="primary" disabled={busy} onClick={newAction}>New Action</Button></div>
          {!registry.actions.length && <div className={cardClass}><h3 className="font-medium">No Actions yet</h3><p className="mt-2 text-sm text-text-secondary">Create an internal API or command Action, then add it to a Group.</p></div>}
          <div className="grid gap-3 sm:grid-cols-2">{registry.actions.map((item) => <article key={item.id} className={cardClass}>
            <div className="flex items-start justify-between gap-3"><h3 className="min-w-0 break-words font-semibold">{item.name}</h3><span className="shrink-0 rounded bg-bg-tertiary px-2 py-1 text-xs">{item.enabled ? 'Enabled' : 'Draft'}</span></div>
            <p className="my-3 break-words text-sm text-text-secondary">{item.description}</p>
            <p className="mb-4 text-xs text-text-tertiary">{item.execution.kind === 'api' ? 'Internal API' : 'Command'} · {item.effect.replaceAll('_', ' ')} · {registry.groups.filter((g) => g.action_ids.includes(item.id)).length} Groups</p>
            <Button disabled={busy} aria-label={`Edit ${item.name}`} onClick={() => setAction(structuredClone(item))}>Edit</Button>
          </article>)}</div>
          {action?.id && <div className={cardClass}><div className="mb-5 flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">Edit Action</h2><Button variant="ghost" disabled={busy} onClick={() => setAction(null)}>Close editor</Button></div>
            <ActionEditor key={action.id} action={action} busy={busy} onChange={setAction} onSave={(value) => perform(async () => { const saved = await mutate(`actions/actions/${encodeURIComponent(value.id)}`, 'PUT', value); setAction(saved.actions.find((a) => a.id === value.id) ?? value); })}
              onDelete={() => void perform(async () => { await mutate(`actions/actions/${action.id}?expectedRevision=${registry.revision}`, 'DELETE'); setAction(null); })} /></div>}
        </section>}
        {tab === 'New Action' && action && <section aria-label="New Action" className={cardClass}><h2 className="text-lg font-semibold">New Action</h2>
          <p className="mb-6 mt-1 text-sm text-text-secondary">Choose what it does. A stable ID is assigned automatically when you save.</p>
          <ActionEditor key="new" action={action} busy={busy} onChange={setAction} onSave={(value) => perform(async () => {
            const { id: _id, ...item } = value; void _id;
            await mutate('actions/actions', 'POST', item);
          })} /></section>}
        {tab === 'Groups' && <section aria-label="Groups" className="space-y-4">
          <div className="flex items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">Groups</h2><p className="text-sm text-text-secondary">Select Actions, build a plugin, choose a Session.</p></div><Button variant="primary" disabled={busy} onClick={() => setGroup({ id: '', name: '', action_ids: [] })}>New Group</Button></div>
          {!registry.groups.length && <div className={cardClass}><h3 className="font-medium">No Groups yet</h3><p className="mt-2 text-sm text-text-secondary">Group your shared Actions to make them available as a Session plugin.</p></div>}
          <div className="grid gap-3 sm:grid-cols-2">{registry.groups.map((item) => <article key={item.id} className={cardClass}>
            <div className="flex items-start justify-between gap-3"><h3 className="min-w-0 break-words font-semibold">{item.name}</h3><span className="shrink-0 rounded bg-bg-tertiary px-2 py-1 text-xs">{item.build ? 'Registered' : 'Unbuilt'}</span></div>
            <p className="my-3 text-sm text-text-secondary">{item.action_ids.length} Actions · {item.sessions.length} Sessions</p>
            <div className="flex flex-wrap gap-2"><Button disabled={busy} aria-label={`Edit ${item.name}`} onClick={() => chooseGroup(item)}>Edit</Button>
              <Button disabled={busy || !item.build} onClick={() => { setBindingGroupId(item.id); setSessionId(''); setSession(null); }}>Use in Session</Button></div>
            {!item.build && <p className="mt-3 text-xs text-text-tertiary">Build this Group before choosing a Session.</p>}
          </article>)}</div>
          {group && <div className={cardClass}><div className="mb-4 flex items-center justify-between gap-2"><h2 className="text-lg font-semibold">{group.id ? 'Edit Group' : 'New Group'}</h2><Button variant="ghost" disabled={busy} onClick={() => setGroup(null)}>Close editor</Button></div>
            <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void perform(async () => {
              if (group.id) await mutate(`actions/groups/${group.id}`, 'PUT', group);
              else await mutate('actions/groups', 'POST', { name: group.name, action_ids: group.action_ids });
            }); }}><fieldset disabled={busy} className="min-w-0 space-y-4">
              {group.id && <Identity id={group.id} />}
              <label className="block text-sm">Group name<input required className={fieldClass} value={group.name} onChange={(e) => setGroup({ ...group, name: e.target.value })} /></label>
              <div role="group" aria-label="Group Actions"><h3 className="mb-2 text-sm font-medium">Shared Actions</h3>
                {!registry.actions.length && <p className="text-sm text-text-secondary">Create an Action in New Action first.</p>}
                {registry.actions.map((item) => <label key={item.id} className="flex items-center gap-3 rounded-lg border border-border-default p-3 text-sm"><input type="checkbox" checked={group.action_ids.includes(item.id)}
                  onChange={(e) => setGroup({ ...group, action_ids: e.target.checked ? [...group.action_ids, item.id] : group.action_ids.filter((id) => id !== item.id) })} /><span className="min-w-0 break-words">{item.name}<span className="ml-2 text-xs text-text-tertiary">{item.enabled ? 'Enabled' : 'Draft'}</span></span></label>)}</div>
              <Button type="submit" variant="primary">Save Group</Button>
            </fieldset></form>
            {selectedGroup && <div className="mt-5 space-y-4 border-t border-border-default pt-5">
              <p className="text-sm text-text-secondary">Build approves the current execution definitions. Running Workers keep their loaded release until restart.</p>
              <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={() => void perform(() => mutate(`actions/groups/${group.id}/build`, 'POST').then(() => undefined))}>Build Group</Button>
                <Button disabled={busy || !selectedGroup.build} onClick={() => void perform(() => mutate(`actions/groups/${group.id}/unbuild`, 'POST').then(() => undefined))}>Unbuild Group</Button>
                <Button variant="danger" disabled={busy || !!selectedGroup.build} onClick={() => void perform(async () => { await mutate(`actions/groups/${group.id}?expectedRevision=${registry.revision}`, 'DELETE'); setGroup(null); if (bindingGroupId === group.id) setBindingGroupId(''); })}>Delete Group</Button></div>
              <div className="space-y-2 break-all text-xs text-text-secondary"><p>Status: {selectedGroup.build ? 'Registered' : 'Unbuilt'}</p>
                {selectedGroup.build && <><p>Plugin: {selectedGroup.build.plugin_id}</p><p>Version: {selectedGroup.build.version}</p><p>Exposed Actions: {selectedGroup.build.action_ids.join(', ')}</p></>}
                {selectedGroup.blockers.length > 0 && <p className="text-danger">Unbuild blocked by running Sessions: {selectedGroup.blockers.map((s) => `${s.name} (${s.id})`).join(', ')}</p>}
                {selectedGroup.sessions.map((s) => <p key={s.id}>{s.name} ({s.id}): {s.enabled ? s.mode : 'Disabled'} · Loaded version: {s.loadedVersion ?? 'Next start'}</p>)}
              </div>
            </div>}
          </div>}
          {bindingGroup?.build && <section aria-label="Use in Session" className={cardClass}>
            <h2 className="text-lg font-semibold">Use in Session</h2><p className="my-2 break-words text-sm text-text-secondary">Choose a Session for {bindingGroup.name}, then enable the plugin and select its mode. Running Workers apply connection changes after becoming idle.</p>
            {!sessions.length ? <p role="status" className="rounded-lg bg-bg-tertiary p-3 text-sm">No Sessions available. Create a Session from the sidebar, then Refresh here.</p> : <label className="block text-sm">Session<select aria-label="Session" className={fieldClass} value={sessionId} disabled={busy} onChange={(e) => { setSession(null); setError(''); setSessionId(e.target.value); }}><option value="">Select a Session</option>
              {sessions.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.id})</option>)}</select></label>}
            {!sessionId && sessions.length > 0 && <p className="mt-3 text-xs text-text-tertiary">Select a Session to load its plugin settings.</p>}
            {sessionId && !session && !error && <p role="status" className="mt-3 text-sm">Loading Session settings…</p>}
            {session && session.id === sessionId && <div className="mt-4"><PluginSelection servers={servers.filter((s) => s.name === bindingGroup.build?.plugin_id)} bindings={session.pluginBindings ?? []} disabled={busy}
              onChange={(value) => { const selected = session.id; void perform(async () => {
                const result = await actionRequest<SessionConfig>(`sessions/${encodeURIComponent(selected)}`, 'PATCH', { pluginBindings: [...session.pluginBindings.filter((b) => b.plugin_id !== value.plugin_id), value], expectedPluginRevision: session.pluginSelectionRevision });
                if (result.error) throw new Error(result.error);
                if (currentSession.current === selected) setSession(result);
                await reload();
              }); }} /></div>}
            {session?.pluginApplyStatus && <p role="status" className="mt-2 text-sm">Apply status: {session.pluginApplyStatus}</p>}
          </section>}
        </section>}
      </>}
    </div>
  </main>;
}

function Identity({ id }: { id: string }) {
  const [message, setMessage] = useState('');
  async function copyId() {
    try { await navigator.clipboard.writeText(id); setMessage('ID copied'); }
    catch { setMessage('Copy unavailable. Select the ID to copy it.'); }
  }
  return <details className="rounded-lg bg-bg-tertiary p-3 text-xs text-text-secondary"><summary className="cursor-pointer">Details</summary>
    <div className="mt-2 flex flex-wrap items-center gap-2"><span>ID: <code className="break-all select-all">{id}</code></span><Button type="button" size="sm" onClick={() => void copyId()}>Copy ID</Button>{message && <span role="status">{message}</span>}</div></details>;
}

function ActionEditor({ action, busy, onChange, onSave, onDelete }: {
  action: Action; busy: boolean; onChange: (value: Action) => void; onSave: (value: Action) => Promise<void>; onDelete?: () => void;
}) {
  const [schemaText, setSchemaText] = useState(JSON.stringify(action.input_schema, null, 2));
  const [schemaError, setSchemaError] = useState('');
  function updateSchema(properties: InputSchema['properties'], required = action.input_schema.required ?? []) {
    const schema: InputSchema = { ...action.input_schema, properties, required };
    onChange({ ...action, input_schema: schema }); setSchemaText(JSON.stringify(schema, null, 2)); setSchemaError('');
  }
  return <form onSubmit={(event) => { event.preventDefault(); try {
    const schema: unknown = JSON.parse(schemaText);
    if (!schema || typeof schema !== 'object' || Array.isArray(schema)) throw new Error('Schema must be a JSON object');
    setSchemaError(''); void onSave({ ...action, input_schema: schema as InputSchema });
  } catch (e) { setSchemaError(e instanceof Error ? e.message : String(e)); } }}>
    <fieldset disabled={busy} className="min-w-0 space-y-6">
      {action.id && <Identity id={action.id} />}
      <div className="grid gap-4 sm:grid-cols-2"><label className="text-sm">Name<input required className={fieldClass} value={action.name} onChange={(e) => onChange({ ...action, name: e.target.value })} /></label>
        <label className="text-sm">Description<input required className={fieldClass} value={action.description} onChange={(e) => onChange({ ...action, description: e.target.value })} /></label></div>
      <div className="flex flex-wrap items-center gap-5"><label className="text-sm"><input type="checkbox" checked={action.enabled} onChange={(e) => onChange({ ...action, enabled: e.target.checked })} /> Enable for build</label>
        <label className="min-w-40 text-sm">Effect<select className={fieldClass} value={action.effect} onChange={(e) => onChange({ ...action, effect: e.target.value as Action['effect'] })}>
          <option value="read">Read</option><option value="write_local">Write local</option><option value="write_external">Write external</option><option value="high_risk">High risk (build blocked)</option></select></label></div>
      <section className="space-y-3 border-t border-border-default pt-5"><h3 className="font-medium">Input fields</h3><p className="text-xs text-text-secondary">Add scalar fields here. Use the JSON schema editor for nested fields, defaults and constraints.</p>
        {Object.entries(action.input_schema.properties ?? {}).map(([name, schema]) => <div key={name} className="flex flex-wrap items-center gap-3 rounded-lg bg-bg-primary p-3 text-sm">
          <span className="min-w-0 break-all font-mono">{name}</span><select className="rounded border border-border-default bg-bg-primary p-1" aria-label={`${name} type`} value={schema.type ?? ''} onChange={(e) => updateSchema({ ...action.input_schema.properties, [name]: { ...schema, type: e.target.value } })}>
            <option value="" disabled>Custom schema</option>{['string', 'integer', 'number', 'boolean', 'object', 'array'].map((type) => <option key={type}>{type}</option>)}</select>
          <label><input type="checkbox" checked={action.input_schema.required?.includes(name) ?? false} onChange={(e) => updateSchema(action.input_schema.properties,
            e.target.checked ? [...(action.input_schema.required ?? []), name] : action.input_schema.required?.filter((n) => n !== name))} /> Required</label>
          <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${name}`} onClick={() => updateSchema(Object.fromEntries(Object.entries(action.input_schema.properties ?? {}).filter(([key]) => key !== name)), action.input_schema.required?.filter((n) => n !== name))}>Remove</Button>
        </div>)}
        <AddField onAdd={(name) => { if (name in (action.input_schema.properties ?? {})) { setSchemaError('Input field already exists'); return; }
          updateSchema({ ...action.input_schema.properties, [name]: { type: 'string' } }, [...(action.input_schema.required ?? []), name]); }} />
        <details className="rounded-lg border border-border-default p-3 text-sm"><summary className="cursor-pointer">Advanced JSON schema</summary><p className="my-2 text-xs text-text-secondary">This JSON replaces the field settings when saved.</p>
          <label className="block">Input schema<textarea className={`${fieldClass} font-mono`} rows={10} value={schemaText} onChange={(e) => setSchemaText(e.target.value)} /></label></details>
        {schemaError && <p role="alert" className="text-sm text-danger">{schemaError}</p>}
      </section>
      <section className="space-y-4 border-t border-border-default pt-5"><h3 className="font-medium">Execution</h3>
        <label className="block text-sm">Execution<select className={fieldClass} value={action.execution.kind} onChange={(e) => onChange({ ...action, execution: e.target.value === 'api'
          ? { kind: 'api', method: 'GET', path: '/api/sessions' } : { kind: 'command', executable: '', args: [], cwd: '', timeout_sec: 30 } })}><option value="api">Internal API</option><option value="command">Command</option>{action.execution.kind === 'session_operation' && <option value="session_operation">Session Operation</option>}</select></label>
        {action.execution.kind === 'api' ? <label className="block text-sm">API route<select className={fieldClass} value={action.execution.path} onChange={(e) => onChange({ ...action, execution: { kind: 'api', method: 'GET', path: e.target.value } })}>
          {['/api/sessions', '/api/models', '/api/workspaces'].map((path) => <option key={path}>{path}</option>)}</select></label> : action.execution.kind === 'session_operation' ? <div className="min-w-0 text-sm"><p>Pause Everything · Session Operation (automation only)</p><pre className="whitespace-pre-wrap break-all">{JSON.stringify(action.execution.selector, null, 2)}</pre><p>Group build does not support Session Operation. Create another target configuration in Automation.</p></div> : <>
          <div className="grid gap-4 sm:grid-cols-2"><label className="text-sm">Executable<input required className={fieldClass} value={action.execution.executable} onChange={(e) => { if (action.execution.kind === 'command') onChange({ ...action, execution: { ...action.execution, executable: e.target.value } }); }} /></label>
            <label className="text-sm">Working directory<input required className={fieldClass} value={action.execution.cwd} onChange={(e) => { if (action.execution.kind === 'command') onChange({ ...action, execution: { ...action.execution, cwd: e.target.value } }); }} /></label></div>
          <label className="block text-sm">Arguments (one per line)<textarea className={`${fieldClass} font-mono`} rows={4} value={action.execution.args.join('\n')} onChange={(e) => { if (action.execution.kind === 'command') onChange({ ...action, execution: { ...action.execution, args: e.target.value ? e.target.value.split('\n') : [] } }); }} /></label>
          <p className="text-xs text-text-secondary">Use {'{field}'} for one complete argument. Input is never interpreted by a shell. The executable must handle its arguments safely.</p>
          <label className="block text-sm">Timeout (seconds)<input type="number" min={1} max={30} required className={fieldClass} value={action.execution.timeout_sec ?? 30} onChange={(e) => { if (action.execution.kind === 'command') onChange({ ...action, execution: { ...action.execution, timeout_sec: Number(e.target.value) } }); }} /></label>
        </>}
        <label className="block text-sm">Output<select className={fieldClass} value={action.output_format} onChange={(e) => onChange({ ...action, output_format: e.target.value as Action['output_format'] })}><option value="json">JSON</option><option value="text">Text</option></select></label>
      </section>
      <div className="flex flex-wrap gap-3 border-t border-border-default pt-5"><Button type="submit" variant="primary">Save Action</Button>{onDelete && <Button type="button" variant="danger" onClick={onDelete}>Delete Action</Button>}</div>
    </fieldset>
  </form>;
}

function AddField({ onAdd }: { onAdd: (name: string) => void }) {
  const [name, setName] = useState('');
  return <div className="flex flex-wrap gap-2"><input aria-label="New input field" className={`${fieldClass} flex-1 basis-40`} value={name} onChange={(e) => setName(e.target.value)} placeholder="Field name" />
    <Button type="button" disabled={!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)} onClick={() => { onAdd(name); setName(''); }}>Add field</Button></div>;
}

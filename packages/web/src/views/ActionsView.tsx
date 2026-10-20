import { useCallback, useEffect, useRef, useState } from 'react';
import { actionRequest } from '@/services/api';
import { PluginSelection } from '@/components/session/PluginSelection';
import type { McpServerInfo, PluginBinding } from '@/types';
import type { Action, ActionGroup, ActionRegistry, InputSchema } from '@/types/actions';

const emptyAction = (): Action => ({ id: '', name: '', description: '', enabled: false, effect: 'read', output_format: 'json',
  input_schema: { type: 'object', properties: {}, required: [], additionalProperties: false },
  execution: { kind: 'api', method: 'GET', path: '/api/sessions' } });
type SessionConfig = { id: string; name: string; pluginBindings: PluginBinding[]; pluginSelectionRevision: number; pluginApplyStatus?: string; error?: string };
const fieldClass = 'w-full rounded border border-border-default bg-bg-primary p-2 text-sm';

export default function ActionsView() {
  const [registry, setRegistry] = useState<ActionRegistry>({ revision: 0, actions: [], groups: [] });
  const [action, setAction] = useState<Action>(emptyAction);
  const [group, setGroup] = useState({ id: '', name: '', action_ids: [] as string[] });
  const [editingAction, setEditingAction] = useState(false);
  const [editingGroup, setEditingGroup] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<{ id: string; name: string }[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [session, setSession] = useState<SessionConfig | null>(null);
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const currentSession = useRef(sessionId);
  currentSession.current = sessionId;

  const reload = useCallback(async () => {
    const [sources, catalog, list] = await Promise.all([
      actionRequest<ActionRegistry>('actions'), actionRequest<{ servers: McpServerInfo[] }>('mcp/servers'),
      actionRequest<{ sessions: { id: string; name: string }[] }>('sessions'),
    ]);
    setRegistry(sources); setServers(catalog.servers); setSessions(list.sessions);
  }, []);
  useEffect(() => { void reload().catch((e: Error) => setError(e.message)); }, [reload]);
  useEffect(() => {
    setSession(null);
    if (!sessionId) return;
    const controller = new AbortController();
    void actionRequest<SessionConfig>(`sessions/${encodeURIComponent(sessionId)}`, 'GET', undefined, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setSession(value); })
      .catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [sessionId]);

  async function perform(operation: () => Promise<void>) {
    setBusy(true); setError('');
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function mutate(path: string, method: string, item?: unknown) {
    const data = await actionRequest<ActionRegistry>(path, method, { ...(item ? { item } : {}), expectedRevision: registry.revision });
    setRegistry(data);
    const catalog = await actionRequest<{ servers: McpServerInfo[] }>('mcp/servers');
    setServers(catalog.servers);
    if (sessionId) setSession(await actionRequest<SessionConfig>(`sessions/${encodeURIComponent(sessionId)}`));
  }
  function updateSchema(properties: InputSchema['properties'], required = action.input_schema.required ?? []) {
    setAction({ ...action, input_schema: { type: 'object', properties, required, additionalProperties: false } });
  }
  function chooseGroup(item: ActionGroup) {
    setEditingGroup(true); setGroup({ id: item.id, name: item.name, action_ids: [...item.action_ids] });
  }

  return <main className="h-full overflow-auto p-4 text-text-primary" aria-label="Actions Panel">
    <div className="mx-auto flex max-w-5xl flex-col gap-5">
      <header className="flex flex-wrap items-center gap-3 pl-7 md:pl-0"><h1 className="text-xl font-semibold">Actions & Groups</h1>
        <button disabled={busy} onClick={() => void perform(reload)}>Refresh</button></header>
      <p className="text-sm text-text-secondary">Create reusable Actions, select them in Groups, then build a Group as an MCP plugin. Build approves the exact execution definition. Running Workers keep their loaded version until restart.</p>
      {error && <div role="alert" className="whitespace-pre-wrap rounded border border-red-500 p-3">{error}</div>}
      <fieldset disabled={busy} className="grid min-w-0 gap-5 lg:grid-cols-2">
        <section className="min-w-0 rounded border border-border-default p-3">
          <h2 className="mb-2 text-lg">Actions</h2>
          <div className="mb-3 flex flex-wrap gap-2">{registry.actions.map((item) => <button key={item.id} className="rounded border border-border-default px-2 py-1"
            onClick={() => { setAction(structuredClone(item)); setEditingAction(true); }}>{item.name} · {item.enabled ? 'Enabled' : 'Draft'}</button>)}
            <button onClick={() => { setAction(emptyAction()); setEditingAction(false); }}>New Action</button></div>
          <form className="flex flex-col gap-2" onSubmit={(event) => { event.preventDefault(); void perform(async () => {
            await mutate(`actions/actions/${encodeURIComponent(action.id)}`, 'PUT', action); setEditingAction(true);
          }); }}>
            <label>ID<input required pattern="[a-z][a-z0-9_]{0,31}" disabled={editingAction} className={fieldClass} value={action.id} onChange={(e) => setAction({ ...action, id: e.target.value })} /></label>
            <label>Name<input required className={fieldClass} value={action.name} onChange={(e) => setAction({ ...action, name: e.target.value })} /></label>
            <label>Description<input required className={fieldClass} value={action.description} onChange={(e) => setAction({ ...action, description: e.target.value })} /></label>
            <label><input type="checkbox" checked={action.enabled} onChange={(e) => setAction({ ...action, enabled: e.target.checked })} /> Enable for build</label>
            <label>Effect<select className={fieldClass} value={action.effect} onChange={(e) => setAction({ ...action, effect: e.target.value as Action['effect'] })}>
              <option value="read">Read</option><option value="write_local">Write local</option><option value="write_external">Write external</option><option value="high_risk">High risk (build blocked)</option></select></label>
            <h3>Input fields</h3>
            {Object.entries(action.input_schema.properties ?? {}).map(([name, schema]) => <div key={name} className="flex flex-wrap items-center gap-2">
              <span>{name}</span><select aria-label={`${name} type`} value={schema.type} onChange={(e) => updateSchema({ ...action.input_schema.properties, [name]: { ...schema, type: e.target.value } })}>
                {['string', 'integer', 'number', 'boolean'].map((type) => <option key={type}>{type}</option>)}</select>
              <label><input type="checkbox" checked={action.input_schema.required?.includes(name) ?? false} onChange={(e) => updateSchema(action.input_schema.properties,
                e.target.checked ? [...(action.input_schema.required ?? []), name] : action.input_schema.required?.filter((n) => n !== name))} /> Required</label>
              <button type="button" aria-label={`Remove ${name}`} onClick={() => updateSchema(Object.fromEntries(Object.entries(action.input_schema.properties).filter(([key]) => key !== name)), action.input_schema.required?.filter((n) => n !== name))}>Remove</button>
            </div>)}
            <AddField onAdd={(name) => { if (name in (action.input_schema.properties ?? {})) { setError('Input field already exists'); return; }
              updateSchema({ ...action.input_schema.properties, [name]: { type: 'string' } }, [...(action.input_schema.required ?? []), name]); }} />
            <label>Execution<select className={fieldClass} value={action.execution.kind} onChange={(e) => setAction({ ...action, execution: e.target.value === 'api'
              ? { kind: 'api', method: 'GET', path: '/api/sessions' } : { kind: 'command', executable: '', args: [], cwd: '', timeout_sec: 30 } })}><option value="api">Internal API</option><option value="command">Command</option></select></label>
            {action.execution.kind === 'api' ? <label>API route<select className={fieldClass} value={action.execution.path} onChange={(e) => setAction({ ...action, execution: { kind: 'api', method: 'GET', path: e.target.value } })}>
              {['/api/sessions', '/api/models', '/api/workspaces'].map((path) => <option key={path}>{path}</option>)}</select></label> : <>
              <label>Executable<input required className={fieldClass} value={action.execution.executable} onChange={(e) => { if (action.execution.kind === 'command') setAction({ ...action, execution: { ...action.execution, executable: e.target.value } }); }} /></label>
              <label>Working directory<input required className={fieldClass} value={action.execution.cwd} onChange={(e) => { if (action.execution.kind === 'command') setAction({ ...action, execution: { ...action.execution, cwd: e.target.value } }); }} /></label>
              <label>Arguments (one per line)<textarea className={fieldClass} rows={4} value={action.execution.args.join('\n')} onChange={(e) => { if (action.execution.kind === 'command') setAction({ ...action, execution: { ...action.execution, args: e.target.value ? e.target.value.split('\n') : [] } }); }} /></label>
              <p className="text-xs">Use {'{field}'} for one complete argument. Input is never interpreted by a shell. The executable must handle its arguments safely.</p>
            </>}
            <label>Output<select className={fieldClass} value={action.output_format} onChange={(e) => setAction({ ...action, output_format: e.target.value as Action['output_format'] })}><option value="json">JSON</option><option value="text">Text</option></select></label>
            <button type="submit" className="rounded bg-accent px-3 py-2 text-white">Save Action</button>
            {editingAction && <button type="button" onClick={() => void perform(async () => {
              setRegistry(await actionRequest<ActionRegistry>(`actions/actions/${action.id}?expectedRevision=${registry.revision}`, 'DELETE'));
              setAction(emptyAction()); setEditingAction(false);
            })}>Delete Action</button>}
          </form>
        </section>
        <section className="min-w-0 rounded border border-border-default p-3">
          <h2 className="mb-2 text-lg">Groups</h2>
          <div className="mb-3 flex flex-wrap gap-2">{registry.groups.map((item) => <button key={item.id} onClick={() => chooseGroup(item)} className="rounded border border-border-default px-2 py-1">{item.name} · {item.build ? 'Registered' : 'Unbuilt'}</button>)}
            <button onClick={() => { setEditingGroup(false); setGroup({ id: '', name: '', action_ids: [] }); }}>New Group</button></div>
          <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void perform(async () => { await mutate(`actions/groups/${encodeURIComponent(group.id)}`, 'PUT', group); setEditingGroup(true); }); }}>
            <label>Group ID<input required pattern="[a-z][a-z0-9_]{0,31}" disabled={editingGroup} className={fieldClass} value={group.id} onChange={(e) => setGroup({ ...group, id: e.target.value })} /></label>
            <label>Group name<input required className={fieldClass} value={group.name} onChange={(e) => setGroup({ ...group, name: e.target.value })} /></label>
            <div role="group" aria-label="Group Actions">{registry.actions.map((item) => <label key={item.id} className="block"><input type="checkbox" checked={group.action_ids.includes(item.id)}
              onChange={(e) => setGroup({ ...group, action_ids: e.target.checked ? [...group.action_ids, item.id] : group.action_ids.filter((id) => id !== item.id) })} /> {item.name}</label>)}</div>
            <button type="submit" className="rounded bg-accent px-3 py-2 text-white">Save Group</button>
          </form>
          {editingGroup && <div className="mt-3 flex flex-wrap gap-3">
            <button onClick={() => void perform(() => mutate(`actions/groups/${group.id}/build`, 'POST'))}>Build Group</button>
            <button disabled={!registry.groups.find((g) => g.id === group.id)?.build} onClick={() => void perform(() => mutate(`actions/groups/${group.id}/unbuild`, 'POST'))}>Unbuild Group</button>
            <button onClick={() => void perform(async () => { setRegistry(await actionRequest<ActionRegistry>(`actions/groups/${group.id}?expectedRevision=${registry.revision}`, 'DELETE')); setEditingGroup(false); setGroup({ id: '', name: '', action_ids: [] }); })}>Delete Group</button>
          </div>}
          {registry.groups.filter((item) => item.id === group.id).map((item) => <div key={item.id} className="mt-4 break-words text-sm">
            <p>Status: {item.build ? 'Registered' : 'Unbuilt'}</p>
            {item.build && <><p>Plugin: {item.build.plugin_id}</p><p>Version: {item.build.version}</p><p>Exposed Actions: {item.build.action_ids.join(', ')}</p></>}
            {item.blockers.length > 0 && <p>Unbuild blocked by running Sessions: {item.blockers.map((s) => `${s.name} (${s.id})`).join(', ')}</p>}
            {item.sessions.map((s) => <p key={s.id}>{s.name} ({s.id}): {s.enabled ? s.mode : 'Disabled'} · Loaded version: {s.loadedVersion ?? 'Next start'}</p>)}
          </div>)}
        </section>
      </fieldset>
      <section className="rounded border border-border-default p-3">
        <h2 className="text-lg">Session plugins</h2>
        <p className="my-2 text-sm">Uses the shared plugin selection and Worker apply policy. Running Workers apply connection changes after becoming idle.</p>
        <label>Session<select aria-label="Session" className={fieldClass} value={sessionId} disabled={busy} onChange={(e) => { setSession(null); setError(''); setSessionId(e.target.value); }}><option value="">Select a Session</option>
          {sessions.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.id})</option>)}</select></label>
        {session && session.id === sessionId && <PluginSelection servers={servers} bindings={session.pluginBindings ?? []} disabled={busy}
          onChange={(binding) => { const selected = session.id; void perform(async () => {
            const result = await actionRequest<SessionConfig>(`sessions/${selected}`, 'PATCH', { pluginBindings: [...session.pluginBindings.filter((b) => b.plugin_id !== binding.plugin_id), binding], expectedPluginRevision: session.pluginSelectionRevision });
            if (result.error) throw new Error(result.error);
            if (currentSession.current === selected) setSession(result);
            await reload();
          }); }} />}
        {session?.pluginApplyStatus && <p role="status">Apply status: {session.pluginApplyStatus}</p>}
      </section>
    </div>
  </main>;
}

function AddField({ onAdd }: { onAdd: (name: string) => void }) {
  const [name, setName] = useState('');
  return <div className="flex gap-2"><input aria-label="New input field" className={fieldClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="Field name" />
    <button type="button" disabled={!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)} onClick={() => { onAdd(name); setName(''); }}>Add field</button></div>;
}

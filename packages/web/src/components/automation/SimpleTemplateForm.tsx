import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import type { JsonObject } from '@/types/canonicalJobs';
import { fieldClass } from './fields';

/** A complete passive product Template. Shell normalization belongs to the host. */
export function SimpleTemplateForm({ kind, busy, onSubmit, onCancel }: {
  kind: 'assign' | 'shell'; busy: boolean;
  onSubmit: (definition: JsonObject, form: JsonObject) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [text, setText] = useState('');
  const [command, setCommand] = useState('');
  const [cwd, setCwd] = useState('');
  const [hideWindow, setHideWindow] = useState(true);
  const [runtime, setRuntime] = useState(false);
  const [fixed, setFixed] = useState<{ ref: JsonObject; completion: string }>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setFixed(undefined);
    setError('');
    const id = kind === 'assign' && runtime ? 'product_assign_runtime_v1' :
      kind === 'assign' ? 'product_assign_v2' : 'product_process_logs_v1';
    void canonicalJobs.actions().then((page) => {
      if (!active) return;
      const action = page.items.find((item) => item.id === id);
      const ref = page.references?.[id];
      if (!action || action.system !== true || action.state !== 'published' || !action.enabled || !ref)
        throw new Error('Required product Action is unavailable. No Template was created.');
      setFixed({ ref, completion: String(action.defaultCompletion) });
    }).catch((failure: unknown) => { if (active) setError(canonicalError(failure)); });
    return () => { active = false; };
  }, [kind, runtime]);
  return <form aria-label={`Simple ${kind} Template`} className="space-y-3 rounded border border-border-default p-3"
    onSubmit={(event) => {
      event.preventDefault();
      if (!fixed || busy) return;
      const definition = { name, jobDefinition: { name, steps: [{ stepId: 'effect', actionRef: fixed.ref,
        arguments: {}, completion: { contract: fixed.completion } }], retention: { policyRef: 'default' } },
        composition: { creators: [], triggers: [] } };
      onSubmit(definition, { kind, fields: kind === 'assign' ? { sessionId, text, runtime } : { command, cwd, hideWindow } });
    }}>
    <h3>{kind === 'assign' ? 'Assign Template' : 'Shell Template'}</h3>
    <p>保存完整的被动模板。实例化默认只创建 disabled Job，不立即运行。</p>
    {error && <p role="alert">{error}</p>}
    <fieldset disabled={busy} className="space-y-3">
      <label className="block">Template name<input required className={fieldClass} value={name} onChange={(e) => setName(e.target.value)} /></label>
      {kind === 'assign' ? <>
        <label><input type="checkbox" checked={runtime} onChange={(e) => setRuntime(e.target.checked)} />Use Delivery Manager runtime target/text before queue acceptance</label>
        <label className="block">Session ID<input required className={fieldClass} value={sessionId} onChange={(e) => setSessionId(e.target.value)} /></label>
        <label className="block">Text<textarea required className={fieldClass} value={text} onChange={(e) => setText(e.target.value)} /></label>
      </> : <>
        <label className="block">Command<textarea required className={fieldClass} value={command} onChange={(e) => setCommand(e.target.value)} /></label>
        <label className="block">Working directory<input required className={fieldClass} value={cwd} onChange={(e) => setCwd(e.target.value)} /></label>
        <label><input type="checkbox" checked={hideWindow} onChange={(e) => setHideWindow(e.target.checked)} />Hide window</label>
      </>}
      <Button type="submit" disabled={!fixed || busy}>Save passive Template</Button>
      <Button type="button" disabled={busy} onClick={onCancel}>Cancel simple Template</Button>
    </fieldset>
  </form>;
}

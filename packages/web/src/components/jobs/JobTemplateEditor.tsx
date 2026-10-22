import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Field, inputClass } from './ScheduleListEditor';
import { UnifiedJobForm } from './UnifiedJobForm';
import { deleteCompleteJobTemplate, fetchCompleteJobTemplates, instantiateCompleteJobTemplate, saveCompleteJobTemplate } from '@/services/api';
import type { CompleteJobTemplate } from '@/services/api';
import type { Action } from '@/types/actions';
import type { Job, UnifiedJobConfig } from '@/types/jobs';

/** Complete Templates use the same editor/validator as Job instances. */
export function JobTemplateEditor({ actions, submitting, onCreate, onInstantiated, onDirtyChange }: {
  actions: Action[]; submitting: boolean; onCreate: (config: UnifiedJobConfig) => Promise<void>;
  onInstantiated: (job: Job) => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [templates, setTemplates] = useState<CompleteJobTemplate[]>([]);
  const [selected, setSelected] = useState('');
  const [mode, setMode] = useState<'job' | 'template'>('job');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(false);
  const key = useRef(crypto.randomUUID());
  useEffect(() => { let active = true;
    void fetchCompleteJobTemplates().then((items) => { if (active) setTemplates(items); })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : 'Templates unavailable'); });
    return () => { active = false; };
  }, []);
  const template = templates.find((item) => item.id === selected);
  const save = async (config: UnifiedJobConfig) => {
    if (mode === 'job') { await onCreate(config); return; }
    const item = await saveCompleteJobTemplate({ ...(template ? { id: template.id } : {}), name: config.name, description: config.description, enabled: config.enabled, config }, template?.revision ?? 0);
    setTemplates((items) => [...items.filter((old) => old.id !== item.id), item]); setSelected(item.id); setDraft(false);
  };
  const instantiate = async () => {
    if (!template) return;
    setBusy(true); setError('');
    try { onInstantiated(await instantiateCompleteJobTemplate(template, key.current)); }
    catch (e) { setError(e instanceof Error ? e.message : 'Unconfirmed result. Reuse this request; do not create a new key.'); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!template || !window.confirm(`Delete Template ${template.name} revision ${template.revision}? Existing Jobs keep their copied configuration.`)) return;
    setBusy(true); setError('');
    try { await deleteCompleteJobTemplate(template); setTemplates((items) => items.filter((item) => item.id !== template.id)); setSelected(''); setDraft(false); }
    catch (e) { setError(e instanceof Error ? e.message : 'Template deletion failed; reload revision'); }
    finally { setBusy(false); }
  };
  return <div className="flex min-w-0 flex-col gap-3">
    <Field label="Create"><select aria-label="Create" className={inputClass} value={mode} onChange={(e) => {
      if (draft && !window.confirm('Discard unsaved configuration and switch editor mode?')) return;
      setDraft(false); setMode(e.target.value as 'job' | 'template');
    }}><option value="job">Job instance</option><option value="template">Complete Job Template</option></select></Field>
    <Field label="Complete Template"><select aria-label="Complete Template" className={inputClass} value={selected} onChange={(e) => {
      if (draft && !window.confirm('Discard unsaved changes and load the selected complete configuration?')) return;
      setSelected(e.target.value); setDraft(false); key.current = crypto.randomUUID();
    }}><option value="">New configuration</option>{templates.map((item) => <option key={item.id} value={item.id}>{item.name} · revision {item.revision}{item.enabled ? '' : ' · disabled'}</option>)}</select></Field>
    {template && <><p className="text-xs text-text-secondary">Instantiate copies revision {template.revision} unchanged; Action stays a live reference. Editor changes are never applied to instantiate.</p><Button disabled={busy || submitting || !template.enabled || draft} onClick={() => void instantiate()}>Instantiate fixed Template</Button>{mode === 'template' && <Button disabled={busy || submitting || draft} onClick={() => void remove()}>Delete complete Template</Button>}</>}
    {error && <p role="alert" className="text-xs text-danger break-words">{error}</p>}
    <div onChange={() => setDraft(true)}><UnifiedJobForm key={`${selected}:${template?.revision ?? 0}:${mode}`} initial={template?.config} expectedRevision={mode === 'template' ? template?.revision : undefined} actions={actions} submitting={submitting || busy} onSubmit={save} onDirtyChange={onDirtyChange} submitLabel={mode === 'template' ? 'Save complete Template' : 'Save unified Job'} /></div>
  </div>;
}

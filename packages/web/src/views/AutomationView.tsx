import { Link, useSearchParams } from 'react-router-dom';
import { useEffect, useRef, useState } from 'react';
import { useAutomationAuth } from '@/stores/automationStore';
import { useAutomationWorkspace } from '@/stores/automationWorkspace';
import { JobUserAuthorization } from '@/components/automation/JobUserAuthorization';
import { DraftBoundary } from '@/components/automation/DraftBoundary';
import { EventRegister } from '@/components/automation/EventRegister';
import { CanonicalCollection } from './CanonicalJobsView';
import { PauseTemplate } from '@/components/automation/PauseTemplate';
import { useActionRegistryStore } from '@/stores/actionRegistryStore';
import { Button } from '@/components/ui/Button';
import { JsonDetail } from '@/components/automation/fields';

function AutomationContent() {
  const authorization = useAutomationAuth((s) => s.token || s.authenticated);
  const workspace = useAutomationWorkspace();
  const [params, setParams] = useSearchParams();
  const sections = ['Job', 'Trigger', 'Event Register'] as const;
  const keys = ['job', 'trigger', 'events'];
  const index = Math.max(0, keys.indexOf(params.get('tab') ?? 'job'));
  const focusId = params.get('focus') ?? undefined;
  const tabs = useRef<HTMLDivElement>(null);
  const [template, setTemplate] = useState(false);
  const jump = (section: typeof sections[number], id = '') => setParams({ tab: keys[sections.indexOf(section)]!, ...(id ? { focus: id } : {}) });
  useEffect(() => { if (focusId) document.getElementById(focusId)?.scrollIntoView?.({ block: 'center' }); }, [focusId, index, workspace.loading]);
  return <section aria-label="Automation management" className="h-full min-w-0 overflow-y-auto p-3 pt-12 text-text-primary sm:p-6"><div className="mx-auto max-w-6xl space-y-5">
    <header className="flex flex-wrap justify-between gap-3"><div><h1 className="text-2xl font-semibold">Automation</h1><p>Jobs, event subscriptions and reusable time sources</p></div><div className="flex gap-3"><Link to="/automation/canonical">Canonical Jobs</Link><Link to="/actions">Manage Actions</Link></div></header>
    <JobUserAuthorization />
    <Button disabled={workspace.loading} onClick={() => void workspace.refresh()}>Refresh</Button>
    {!authorization && <p>Log in to manage Jobs and Scheduler sources.</p>}
    {workspace.error && <p role="alert">{workspace.error}. The previous workspace snapshot is retained.</p>}
    <div ref={tabs} role="tablist" aria-label="Automation subpages" className="grid grid-cols-3 gap-1">{sections.map((name, i) => <button key={name} role="tab" aria-selected={index === i} tabIndex={index === i ? 0 : -1} onClick={() => jump(name)} onKeyDown={(e) => { const next = e.key === 'ArrowRight' ? (i + 1) % 3 : e.key === 'ArrowLeft' ? (i + 2) % 3 : e.key === 'Home' ? 0 : e.key === 'End' ? 2 : -1; if (next >= 0) { e.preventDefault(); jump(sections[next]!); (tabs.current?.children[next] as HTMLButtonElement)?.focus(); } }}>{name}</button>)}</div>
    <section hidden={index !== 1}><button onClick={() => { void useActionRegistryStore.getState().ensureRegistry(); setTemplate(true); }}>Pause Everything template</button>{template && <PauseTemplate onClose={() => setTemplate(false)} />}</section>
    {authorization && <div key={String(authorization)}>
      <section hidden={index !== 0}><CanonicalCollection kind="jobs" focusId={focusId} refreshToken={workspace.jobs} onChanged={workspace.refresh} /></section>
      <section hidden={index !== 1}><CanonicalCollection kind="triggers" reviewSourceRefs sources={workspace.sources} jobs={workspace.jobs} refreshToken={workspace.triggers} focusId={focusId} onChanged={workspace.refresh} /></section>
      <section hidden={index !== 2}><EventRegister sources={workspace.sources} providers={workspace.providers} references={workspace.references} definitions={workspace.definitions} refresh={workspace.refresh} focusId={focusId} jump={jump} /></section>
      <details><summary>Runtime diagnostics</summary><JsonDetail label="Runtime diagnostics" value={workspace.diagnostics} /></details>
    </div>}
  </div></section>;
}
export default function AutomationView() { return <DraftBoundary><AutomationContent /></DraftBoundary>; }

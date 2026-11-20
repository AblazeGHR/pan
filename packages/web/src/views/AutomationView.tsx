import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
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
import { AutomationNavigation, Notice, PageHeader, PageShell } from '@/components/automation/display';

const tabs = { Trigger: 'trigger', 'Event Register': 'events' } as const;

function AutomationContent() {
  const authorization = useAutomationAuth((s) => s.token || s.authenticated);
  const workspace = useAutomationWorkspace();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const showEvents = params.get('tab') === 'events';
  const focusId = params.get('focus') ?? undefined;
  const [template, setTemplate] = useState(false);
  function jump(section: keyof typeof tabs | 'Job', id: string) {
    if (section === 'Job') {
      navigate({ pathname: '/jobs', search: new URLSearchParams({ focus: id }).toString() });
      return;
    }
    setParams({ tab: tabs[section], focus: id });
  }
  useEffect(() => {
    if (focusId) document.getElementById(focusId)?.scrollIntoView?.({ block: 'center' });
  }, [focusId, showEvents, workspace.loading]);
  return (
    <PageShell label="Automation management">
      <div className="space-y-3 sm:space-y-5">
        <PageHeader
          eyebrow="Pan / Automation"
          title={showEvents ? 'Event Register' : 'Automation'}
          description={showEvents ? "Manage reusable Scheduler sources and inspect available events." : "Start Jobs when matching events arrive."}
          actions={<AutomationNavigation current={showEvents ? 'events' : 'trigger'} />}
        />
        <JobUserAuthorization compact />
        {(showEvents || workspace.error || workspace.loading) && (
          <div className="flex flex-wrap items-center justify-end gap-3">
            {workspace.loading && (
              <span role="status" className="text-xs text-text-secondary">
                Loading workspace...
              </span>
            )}
            <Button
              type="button"
              size="sm"
              disabled={workspace.loading}
              onClick={() => void workspace.refresh()}
            >
              Refresh
            </Button>
          </div>
        )}
        {!authorization && (
          <Notice>
            <p>Log in to manage Jobs and Scheduler sources.</p>
          </Notice>
        )}
        {workspace.error && (
          <Notice tone="danger" role="alert">
            <p className="break-words">
              {workspace.error}. The previous workspace snapshot is retained.
            </p>
          </Notice>
        )}
        {authorization && (
          <div key={String(authorization)} className="space-y-5">
            <section hidden={showEvents}>
              <CanonicalCollection
                kind="triggers"
                reviewSourceRefs
                sources={workspace.sources}
                jobs={workspace.jobs}
                refreshToken={workspace.triggers}
                focusId={focusId}
                onChanged={workspace.refresh}
              />
            </section>
            <section hidden={!showEvents}>
              <EventRegister
                sources={workspace.sources}
                providers={workspace.providers}
                references={workspace.references}
                definitions={workspace.definitions}
                refresh={workspace.refresh}
                focusId={focusId}
                jump={jump}
              />
            </section>
            <div className="rounded-xl border border-border-default bg-bg-secondary px-4 py-3">
              <JsonDetail label="Runtime diagnostics" value={workspace.diagnostics} />
            </div>
          </div>
        )}
        <details hidden={showEvents} className="rounded-xl border border-border-default bg-bg-secondary px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium">Bulk actions</summary>
          <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <p className="mt-0.5 text-sm text-text-secondary">
                Create a reusable Action template for pausing Sessions.
              </p>
            </div>
            <Button
              type="button"
              onClick={() => {
                void useActionRegistryStore.getState().ensureRegistry();
                setTemplate(true);
              }}
            >
              Pause Everything template
            </Button>
          </div>
          {template && <PauseTemplate onClose={() => setTemplate(false)} />}
        </details>
      </div>
    </PageShell>
  );
}
export default function AutomationView() {
  const [params] = useSearchParams();
  const tab = params.get('tab');
  if (tab !== 'trigger' && tab !== 'events') {
    const destination = new URLSearchParams(params);
    destination.delete('tab');
    return <Navigate replace to={{ pathname: '/jobs', search: destination.toString() }} />;
  }
  return (
    <DraftBoundary>
      <AutomationContent />
    </DraftBoundary>
  );
}

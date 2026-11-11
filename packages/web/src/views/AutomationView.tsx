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
import { HeaderLink, Notice, PageHeader, PageShell } from '@/components/automation/display';

const sections = ['Job', 'Trigger', 'Event Register'] as const;
const keys = ['job', 'trigger', 'events'];

function AutomationContent() {
  const authorization = useAutomationAuth((s) => s.token || s.authenticated);
  const workspace = useAutomationWorkspace();
  const [params, setParams] = useSearchParams();
  const index = Math.max(0, keys.indexOf(params.get('tab') ?? 'job'));
  const focusId = params.get('focus') ?? undefined;
  const tabs = useRef<HTMLDivElement>(null);
  const [template, setTemplate] = useState(false);
  const jump = (section: (typeof sections)[number], id = '') =>
    setParams({ tab: keys[sections.indexOf(section)]!, ...(id ? { focus: id } : {}) });
  useEffect(() => {
    if (focusId) document.getElementById(focusId)?.scrollIntoView?.({ block: 'center' });
  }, [focusId, index, workspace.loading]);
  return (
    <PageShell label="Automation management">
      <div className="space-y-5">
        <PageHeader
          eyebrow="Pan / Automation"
          title="Automation"
          description="Jobs, event subscriptions and reusable time sources"
          actions={
            <>
              <HeaderLink to="/jobs">Jobs</HeaderLink>
              <HeaderLink to="/actions">Manage Actions</HeaderLink>
            </>
          }
        />
        <JobUserAuthorization />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div
            ref={tabs}
            role="tablist"
            aria-label="Automation subpages"
            className="grid w-full grid-cols-3 gap-1 rounded-xl border border-border-default bg-bg-secondary p-1 sm:max-w-[28rem]"
          >
            {sections.map((name, i) => (
              <button
                key={name}
                role="tab"
                aria-selected={index === i}
                tabIndex={index === i ? 0 : -1}
                className={`whitespace-nowrap rounded-lg px-2 py-1.5 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 sm:text-sm ${
                  index === i
                    ? 'bg-bg-tertiary text-text-primary shadow-card'
                    : 'text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary'
                }`}
                onClick={() => jump(name)}
                onKeyDown={(e) => {
                  const next =
                    e.key === 'ArrowRight'
                      ? (i + 1) % 3
                      : e.key === 'ArrowLeft'
                        ? (i + 2) % 3
                        : e.key === 'Home'
                          ? 0
                          : e.key === 'End'
                            ? 2
                            : -1;
                  if (next >= 0) {
                    e.preventDefault();
                    jump(sections[next]!);
                    (tabs.current?.children[next] as HTMLButtonElement)?.focus();
                  }
                }}
              >
                {name}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3 sm:ml-auto">
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
        </div>
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
        <section hidden={index !== 1} className="space-y-4">
          <div className="flex flex-col gap-3 rounded-xl border border-border-default bg-bg-secondary px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold">Bulk actions</h2>
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
        </section>
        {authorization && (
          <div key={String(authorization)} className="space-y-5">
            <section hidden={index !== 0}>
              <div className="rounded border border-border-default bg-bg-secondary p-4"><p className="text-sm text-text-secondary">Manage Jobs and their Runs in the Jobs directory.</p><Link className="mt-2 inline-block text-accent" to="/jobs">Open Jobs</Link></div>
            </section>
            <section hidden={index !== 1}>
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
            <section hidden={index !== 2}>
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
        {!authorization && (
          <p className="text-sm text-text-secondary">
            <Link className="text-accent hover:text-accent-hover" to="/jobs">
              Open Jobs
            </Link>{' '}
            to review the definitions that are visible to this profile.
          </p>
        )}
      </div>
    </PageShell>
  );
}
export default function AutomationView() {
  return (
    <DraftBoundary>
      <AutomationContent />
    </DraftBoundary>
  );
}

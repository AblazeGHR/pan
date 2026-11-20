import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

const tones = {
  success: 'border-success/40 bg-success/10 text-success',
  warning: 'border-warning/40 bg-warning/10 text-warning',
  danger: 'border-danger/40 bg-danger/10 text-danger',
  accent: 'border-accent/40 bg-accent/10 text-accent',
  neutral: 'border-border-default bg-bg-tertiary text-text-secondary',
} as const;

/** Visual tone for a persisted state. Text is always the raw state value. */
export function stateTone(state: string): keyof typeof tones {
  switch (state) {
    case 'enabled':
    case 'published':
    case 'completed':
    case 'succeeded':
    case 'delivered':
    case 'available':
      return 'success';
    case 'draft':
    case 'paused':
    case 'scheduled':
    case 'pending':
      return 'warning';
    case 'running':
    case 'starting':
    case 'accepted':
      return 'accent';
    case 'failed':
    case 'timed_out':
    case 'uncertain':
    case 'unknown':
    case 'partial':
    case 'rejected':
    case 'undeliverable':
      return 'danger';
    default:
      return 'neutral';
  }
}

export function StateBadge({ state, className = '' }: { state: string; className?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-5 ${tones[stateTone(state)]} ${className}`}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {state}
    </span>
  );
}

export function PageShell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section
      aria-label={label}
      className="h-full min-h-0 overflow-y-auto bg-bg-primary text-text-primary [&_button]:min-h-9 max-sm:[&_button]:min-h-11"
    >
      <div className="mx-auto w-full max-w-6xl px-4 pb-20 pt-14 sm:px-6 lg:px-8">{children}</div>
    </section>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-4 border-b border-border-muted pb-5 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-text-tertiary">
          {eyebrow}
        </p>
        <h1 className="mt-1 break-words text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-text-secondary">{description}</p>
      </div>
      {actions && (
        <div className="min-w-0">
          {actions}
        </div>
      )}
    </header>
  );
}

export function HeaderLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex min-h-9 items-center rounded-lg border border-border-default bg-bg-secondary px-3 text-sm font-medium text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
    >
      {children}
    </Link>
  );
}

const automationPages = [
  { id: 'jobs', to: '/jobs', label: 'Jobs' },
  { id: 'trigger', to: '/automation?tab=trigger', label: 'Trigger' },
  { id: 'events', to: '/automation?tab=events', label: 'Event Register' },
  { id: 'actions', to: '/actions', label: 'Actions & Groups' },
] as const;

export function AutomationNavigation({ current, onLeave }: {
  current: (typeof automationPages)[number]['id'];
  onLeave?: () => boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1" aria-label="Automation pages" role="navigation">
      {automationPages.map(({ id, to, label }) => (
        <Link
          key={id}
          to={to}
          onClick={(event) => {
            if (id === current || onLeave?.() === false) event.preventDefault();
          }}
          aria-current={current === id ? 'page' : undefined}
          className={`inline-flex min-h-11 items-center rounded-md px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 sm:min-h-9 ${current === id ? 'bg-accent/10 text-accent' : 'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'}`}
        >
          {label}
        </Link>
      ))}
    </div>
  );
}

export function Notice({
  tone = 'neutral',
  children,
  role,
  label,
}: {
  tone?: 'neutral' | 'warning' | 'danger' | 'accent';
  children: ReactNode;
  role?: 'alert' | 'status' | 'region';
  label?: string;
}) {
  const style = {
    neutral: 'border-border-default bg-bg-secondary text-text-secondary',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    danger: 'border-danger/40 bg-danger/10 text-danger',
    accent: 'border-accent/40 bg-accent/10 text-accent',
  }[tone];
  return (
    <div role={role} aria-label={label} className={`rounded-xl border px-4 py-3 text-sm ${style}`}>
      {children}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-border-default bg-bg-secondary/40 px-5 py-10 text-center">
      <p className="text-sm font-medium text-text-primary">{title}</p>
      {hint && <p className="mt-1 text-sm text-text-secondary">{hint}</p>}
    </div>
  );
}

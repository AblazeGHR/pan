import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

/**
 * Shared shell for the independent Remote capabilities. Each capability keeps
 * its own heading, one-line purpose and live status summary, and can be
 * collapsed from the keyboard without unmounting its body: hiding the body
 * keeps drafts, avoids re-running load effects, and never issues a request.
 */
export function RemoteSettingsSection({
  id,
  title,
  description,
  status,
  testId,
  children,
}: {
  id: string;
  title: string;
  description: ReactNode;
  status?: ReactNode;
  testId?: string;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(true);
  const bodyId = `${id}-body`;
  const titleId = `${id}-title`;
  return (
    <section
      aria-labelledby={titleId}
      data-testid={testId}
      className="min-w-0 overflow-hidden rounded-md border border-border-muted bg-bg-primary"
    >
      <h3 className="m-0">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => setExpanded((current) => !current)}
          className="flex w-full items-start justify-between gap-3 px-3 py-2.5 text-left transition-colors hover:bg-bg-hover focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
        >
          <span className="min-w-0 space-y-1">
            <span id={titleId} className="block text-xs font-semibold text-text-primary">
              {title}
            </span>
            <span className="block text-[11px] leading-relaxed text-text-secondary">
              {description}
            </span>
            {status && <span className="block">{status}</span>}
          </span>
          <ChevronDown
            aria-hidden
            className={`mt-0.5 h-3.5 w-3.5 shrink-0 text-text-tertiary transition-transform ${
              expanded ? 'rotate-180' : ''
            }`}
          />
        </button>
      </h3>
      <div
        id={bodyId}
        hidden={!expanded}
        className="space-y-3 border-t border-border-muted px-3 py-3"
      >
        {children}
      </div>
    </section>
  );
}

/** Status line shared by every Remote section: dot plus preserved wording. */
export function RemoteStatus({
  tone = 'idle',
  children,
}: {
  tone?: 'ok' | 'off' | 'idle';
  children: ReactNode;
}) {
  const dot = tone === 'ok' ? 'bg-success' : tone === 'off' ? 'bg-danger' : 'bg-text-tertiary';
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-text-tertiary">
      <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      <span className="min-w-0 break-words">{children}</span>
    </span>
  );
}

/**
 * Label + control column. Keeps labels above their inputs and lets narrow
 * layouts shrink instead of overflowing on long hosts/paths.
 */
export function RemoteField({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="block text-xs text-text-secondary">
        {label}
      </label>
      {children}
    </div>
  );
}

/** Boolean row shared by the Remote panels; toggles the draft only. */
export function RemoteSwitchRow({
  label,
  hint,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-3 rounded border border-border-muted px-2 py-1.5 text-left transition-colors hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-60"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">{label}</span>
        {hint && (
          <span className="mt-0.5 block text-[10px] leading-relaxed text-text-tertiary">
            {hint}
          </span>
        )}
      </span>
      <span
        aria-hidden
        className={`relative inline-flex h-[18px] w-8 shrink-0 rounded-full transition-colors ${
          checked ? 'bg-accent' : 'bg-bg-hover'
        }`}
      >
        <span
          className={`absolute left-[2px] top-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-[14px]' : 'translate-x-0'
          }`}
        />
      </span>
    </button>
  );
}

/** Single control style for text/number inputs and selects in Remote pages. */
export const remoteControlClass =
  'mt-1 block min-w-0 rounded border border-border-default bg-bg-primary px-2 py-1 text-xs text-text-primary disabled:opacity-60';

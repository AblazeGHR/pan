import type { ReactNode } from 'react';

export type FreshnessState =
  | 'loading'
  | 'cached'
  | 'stale'
  | 'refreshing'
  | 'updated'
  | 'error'
  | 'unknown';

interface FreshnessStatusProps {
  state: FreshnessState;
  updatedAt?: string | number | null;
  source?: string | null;
  error?: string | null;
  onRetry?: () => void;
  children?: ReactNode;
}

function formatUpdatedAt(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

const STATE_LABELS: Record<FreshnessState, string> = {
  loading: 'loading',
  cached: 'cached',
  stale: 'stale',
  refreshing: 'refreshing',
  updated: 'updated',
  error: 'error',
  unknown: 'unknown',
};

/** Small, explicit provenance line shared by metadata and usage views. */
export function FreshnessStatus({
  state,
  updatedAt,
  source,
  error,
  onRetry,
  children,
}: FreshnessStatusProps) {
  const formatted = formatUpdatedAt(updatedAt);
  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-text-tertiary"
      data-freshness={state}
      role={state === 'error' ? 'status' : undefined}
    >
      <span className="font-medium">{STATE_LABELS[state]}</span>
      {source && <span>source: {source}</span>}
      {formatted && <span>updated: {formatted}</span>}
      {children}
      {error && <span className="text-danger">{error}</span>}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded border border-border-default px-1.5 py-0.5 text-[11px] text-text-secondary hover:bg-bg-tertiary hover:text-text-primary"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function FreshnessSkeleton({ label = 'Loading' }: { label?: string }) {
  return (
    <div
      aria-label={label}
      className="h-4 w-32 animate-pulse rounded bg-bg-tertiary"
      data-freshness-skeleton="true"
    />
  );
}


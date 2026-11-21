import { useId } from 'react';
import { Badge, IconTile } from './ActionsUi';
import { Button } from '@/components/ui/Button';
import { Braces, Layers, TerminalSquare } from 'lucide-react';
import type { UnifiedActionRow, UnifiedGroupRow } from './unifiedModel';
import { obj, text } from './unifiedModel';

const EFFECT_LABELS: Record<string, string> = {
  read: 'Read',
  write_local: 'Write local',
  write_external: 'Write external',
  high_risk: 'High risk',
};

const OPERATION_LABELS: Record<string, string> = {
  'process.command': 'Command',
  'process.shell': 'Shell',
  'api.get': 'Internal API',
  'session.pause-everything': 'Session Operation',
  'template.instantiate': 'Create Job',
};

/**
 * Canonical card rows show stable identity and source alongside the display name.
 */
export function CanonicalActionRow({
  row,
  onOpen,
}: {
  row: UnifiedActionRow;
  onOpen: () => void;
}) {
  const operation = text(row.executionOperation);
  const owner = obj(row.canonical?.owner);
  const identityLabel = useId();
  const identity = [text(owner.type), text(owner.id), row.canonicalId || row.legacyId].filter(Boolean).join(' · ');
  return (
    <li className="cursor-pointer rounded-lg border border-border-default bg-bg-secondary/35" onClick={event => {
      if (event.target instanceof Element && !event.target.closest('button')) onOpen();
    }}>
      <div className="flex flex-wrap items-center gap-2 action-row px-3 py-2.5 transition-colors hover:bg-bg-tertiary/35 focus-within:bg-bg-tertiary/35">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <IconTile tone={operation === 'api.get' ? 'accent' : 'neutral'}>
            {operation === 'api.get' ? <Braces size={14} /> : <TerminalSquare size={14} />}
          </IconTile>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={onOpen}
                aria-label={`Inspect ${row.name}`}
                aria-describedby={identityLabel}
                className="truncate text-left text-sm font-medium text-text-primary transition-colors hover:text-accent"
                title={`Inspect ${row.name}`}
              >
                {row.name}
              </button>
              {row.system && <Badge tone="neutral">System</Badge>}
            </div>
            <p className="mt-0.5 line-clamp-1 text-xs text-text-secondary" title={row.description}>
              {row.description}
            </p>
            <p id={identityLabel} className="break-all text-xs text-text-secondary">{identity}</p>
            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-text-tertiary">
              <span className={row.enabled ? 'text-success' : 'text-text-tertiary'}>
                {!row.canonicalId ? 'Invalid / Retired' : row.enabled ? 'Enabled' : 'Draft'}
              </span>
              <span>{OPERATION_LABELS[operation] ?? operation}</span>
              <span>{EFFECT_LABELS[row.effectClass] ?? row.effectClass}</span>
              <span>{row.versionCount} versions</span>
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-end gap-1">
          <Button size="sm" onClick={onOpen}>
            {row.canonicalId ? (row.system ? 'View versions' : 'Versions and publication') : 'Inspect history'}
          </Button>
        </div>
      </div>
    </li>
  );
}

export function CanonicalGroupRow({
  row,
  onOpen,
}: {
  row: UnifiedGroupRow;
  onOpen: () => void;
}) {
  return (
    <li className="cursor-pointer rounded-lg border border-border-default bg-bg-secondary/35" onClick={event => {
      if (event.target instanceof Element && !event.target.closest('button')) onOpen();
    }}>
      <div className="flex flex-wrap items-center gap-2 group-row px-3 py-2.5 transition-colors hover:bg-bg-tertiary/35 focus-within:bg-bg-tertiary/35">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <IconTile tone={row.release ? 'accent' : 'neutral'}>
            <Layers size={14} />
          </IconTile>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={onOpen}
                aria-label={`Inspect ${row.name}`}
                className="truncate text-left text-sm font-medium text-text-primary transition-colors hover:text-accent"
                title={`Inspect ${row.name}`}
              >
                {row.name}
              </button>
            </div>
            <p className="mt-0.5 line-clamp-1 text-xs text-text-secondary">
              {row.tools.length} fixed tools
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-text-tertiary">
              <span>{!row.canonicalWritable ? 'Retired' : row.release ? 'Published' : 'Unbuilt'}</span>
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-end gap-1">
          <Button size="sm" onClick={onOpen}>
            Fixed versions and releases
          </Button>
        </div>
      </div>
    </li>
  );
}

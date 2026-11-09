import type { UnifiedActionRow, UnifiedGroupRow } from './unifiedModel';
import { Badge } from './ActionsUi';
import { Button } from '@/components/ui/Button';
import { Layers } from 'lucide-react';

/**
 * Incremental canonical block inside the designed directory: canonical-only
 * records (created here, not yet projected through a legacy alias) stay
 * reachable and open the canonical version/release detail.
 */
export function CanonicalDirectorySection({
  tab,
  actions,
  groups,
  onOpenAction,
  onOpenGroup,
}: {
  tab: 'actions' | 'groups';
  actions: UnifiedActionRow[];
  groups: UnifiedGroupRow[];
  onOpenAction: (row: UnifiedActionRow) => void;
  onOpenGroup: (row: UnifiedGroupRow) => void;
}) {
  const canonicalActions = actions.filter((row) => row.adoption === 'canonical' && !row.system);
  const canonicalGroups = groups.filter((row) => row.adoption === 'canonical');
  const items = tab === 'actions' ? canonicalActions : canonicalGroups;
  if (items.length === 0) return null;
  return (
    <section
      aria-label="Canonical directory"
      className="mb-3 overflow-hidden rounded-lg border border-border-default bg-bg-secondary/20"
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-border-default px-3 py-2">
        <Layers size={14} className="text-text-tertiary" />
        <h2 className="text-xs font-semibold text-text-primary">Catalog entry</h2>
        <span className="text-[11px] text-text-tertiary">
          {tab === 'actions'
            ? `${canonicalActions.length}  Actions with versions and publication`
            : `${canonicalGroups.length}  Groups with fixed versions and releases`}
        </span>
      </header>
      <ul className="divide-y divide-border-muted">
        {tab === 'actions'
          ? canonicalActions.map((row) => (
              <li key={row.key} className="flex min-w-0 flex-wrap items-center gap-2 px-3 py-2">
                <span className="min-w-0 break-all text-sm">{row.name}</span>
                <Badge tone={row.enabled ? 'success' : 'muted'} dot>
                  {row.enabled ? 'Enabled' : 'Draft'}
                </Badge>
                <span className="text-[11px] text-text-tertiary">
                  {row.versionCount}  versions / v{row.latestRevision ?? '—'}
                </span>
                <span className="ml-auto">
                  <Button size="sm" onClick={() => onOpenAction(row)}>
                    Versions and publication
                  </Button>
                </span>
              </li>
            ))
          : canonicalGroups.map((row) => (
              <li key={row.key} className="flex min-w-0 flex-wrap items-center gap-2 px-3 py-2">
                <span className="min-w-0 break-all text-sm">{row.name}</span>
                <Badge tone={row.release ? 'success' : 'muted'} dot>
                  {row.release ? 'Published release' : 'Unbuilt'}
                </Badge>
                <span className="text-[11px] text-text-tertiary">
                  {row.tools.length}  fixed tools
                </span>
                <span className="ml-auto">
                  <Button size="sm" onClick={() => onOpenGroup(row)}>
                    Fixed versions and releases
                  </Button>
                </span>
              </li>
            ))}
      </ul>
    </section>
  );
}

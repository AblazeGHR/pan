import { Badge, IconTile } from './ActionsUi';
import { Button } from '@/components/ui/Button';
import { Braces, Layers, TerminalSquare } from 'lucide-react';
import { shortVersion } from './actionsModel';
import type { UnifiedActionRow, UnifiedGroupRow } from './unifiedModel';
import { text } from './unifiedModel';

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
 * Canonical-only rows rendered inside the designed table (same grid, same
 * columns). They are the same logical objects as the legacy projection, so a
 * merged row is never shown twice: only rows without a legacy identity land
 * here.
 */
export function CanonicalActionRow({
  row,
  onOpen,
}: {
  row: UnifiedActionRow;
  onOpen: () => void;
}) {
  const operation = text(row.executionOperation);
  return (
    <li className="border-b border-border-muted last:border-0">
      <div className="action-grid action-row px-3 py-2.5 transition-colors hover:bg-bg-tertiary/35 focus-within:bg-bg-tertiary/35">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <IconTile tone={operation === 'api.get' ? 'accent' : 'neutral'}>
            {operation === 'api.get' ? <Braces size={14} /> : <TerminalSquare size={14} />}
          </IconTile>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={onOpen}
                aria-label={`查看 ${row.name}`}
                className="truncate text-left text-sm font-medium text-text-primary transition-colors hover:text-accent"
                title={`查看 ${row.name}`}
              >
                {row.name}
              </button>
              {row.system && <Badge tone="neutral">System</Badge>}
            </div>
            <p className="mt-0.5 line-clamp-1 text-xs text-text-secondary" title={row.description}>
              {row.description}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] text-text-tertiary md:hidden">
              <span className={row.enabled ? 'text-success' : 'text-text-tertiary'}>
                {row.enabled ? 'Enabled' : 'Draft'}
              </span>
              <span>{OPERATION_LABELS[operation] ?? operation}</span>
              <span>{EFFECT_LABELS[row.effectClass] ?? row.effectClass}</span>
              <span>{row.versionCount} versions</span>
            </div>
          </div>
        </div>
        <div className="action-col-type min-w-0">
          <p className="text-xs text-text-secondary">{OPERATION_LABELS[operation] ?? (operation || '—')}</p>
          <p className="truncate font-mono text-[10px] text-text-tertiary" title={row.canonicalId}>
            {row.canonicalId}
          </p>
        </div>
        <div className="action-col-effect">
          <Badge tone={row.effectClass === 'high_risk' ? 'danger' : row.effectClass === 'write_external' ? 'warning' : 'neutral'}>
            {EFFECT_LABELS[row.effectClass] ?? row.effectClass ?? '—'}
          </Badge>
        </div>
        <div className="action-col-status">
          <Badge tone={row.enabled ? 'success' : 'muted'} dot>
            {row.enabled ? 'Enabled' : 'Draft'}
          </Badge>
        </div>
        <div className="action-col-usage text-xs text-text-secondary">
          {row.usedByGroupIds.length
            ? `${row.usedByGroupIds.length} ${row.usedByGroupIds.length === 1 ? 'Group' : 'Groups'}`
            : '—'}
        </div>
        <div className="flex shrink-0 items-center justify-end gap-1">
          <Button size="sm" onClick={onOpen}>
            版本与发布
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
    <li className="border-b border-border-muted last:border-0">
      <div className="group-grid group-row px-3 py-2.5 transition-colors hover:bg-bg-tertiary/35 focus-within:bg-bg-tertiary/35">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <IconTile tone={row.release ? 'accent' : 'neutral'}>
            <Layers size={14} />
          </IconTile>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={onOpen}
                aria-label={`查看 ${row.name}`}
                className="truncate text-left text-sm font-medium text-text-primary transition-colors hover:text-accent"
                title={`查看 ${row.name}`}
              >
                {row.name}
              </button>
            </div>
            <p className="mt-0.5 line-clamp-1 text-xs text-text-secondary">
              {row.tools.length} 个固定工具
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] text-text-tertiary md:hidden">
              <span>{row.release ? '已发布' : '未构建'}</span>
              <span>{row.tools.length} 个固定工具</span>
            </div>
          </div>
        </div>
        <div className="group-col-release min-w-0" title={row.release || row.pluginId}>
          {row.release ? (
            <span className="font-mono text-[11px] text-text-secondary">{shortVersion(row.release)}</span>
          ) : (
            <span className="text-[11px] text-text-tertiary">未发布</span>
          )}
        </div>
        <div className="group-col-composition min-w-0 text-[11px] text-text-tertiary">
          {row.tools.map((tool) => `${tool.alias}@v${String(tool.actionRef.revision ?? '—')}`).join(' · ') || '—'}
        </div>
        <div className="group-col-status">
          <Badge tone={row.release ? 'success' : 'muted'} dot>
            {row.release ? 'Built' : 'Unbuilt'}
          </Badge>
        </div>
        <div className="flex shrink-0 items-center justify-end gap-1">
          <Button size="sm" onClick={onOpen}>
            固定版本与 release
          </Button>
        </div>
      </div>
    </li>
  );
}

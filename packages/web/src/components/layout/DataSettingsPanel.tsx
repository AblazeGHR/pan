import type { ReactNode } from 'react';
import type { ApiDataCatalogResponse, DataCatalogCategory } from '@/types';

interface DataSettingsPanelProps {
  catalog: ApiDataCatalogResponse | null;
  loading: boolean;
  error: string | null;
  /** Extension point for the future Jobs API retention control. */
  jobsRetentionSlot?: ReactNode;
}

function CategoryCard({
  category,
  jobsRetentionSlot,
}: {
  category: DataCatalogCategory;
  jobsRetentionSlot?: ReactNode;
}) {
  const protectedCategory = category.policyStatus === 'not_auto_cleanable';
  return (
    <section className="min-w-0 rounded-md border border-border-muted bg-bg-primary">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <h4 className="text-xs font-medium text-text-primary">{category.name}</h4>
          <p className="mt-1 text-[11px] leading-relaxed text-text-tertiary">
            {category.purpose}
          </p>
        </div>
        <span
          className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${
            protectedCategory
              ? 'border-border-muted text-text-tertiary'
              : 'border-accent/30 text-accent'
          }`}
        >
          {protectedCategory ? '不可自动清理' : '自动清理待策略确认'}
        </span>
      </div>
      <div className="min-w-0 border-t border-border-muted divide-y divide-border-muted">
        {category.paths.map((entry) => (
          <div key={`${entry.label}:${entry.path}`} className="min-w-0 px-3 py-2">
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-[10px] text-text-secondary">{entry.label}</span>
              {!entry.exists && (
                <span className="text-[10px] text-text-tertiary">尚未创建</span>
              )}
              {entry.overridden && (
                <span className="text-[10px] text-text-tertiary">{entry.source}</span>
              )}
              {entry.external && (
                <span className="rounded border border-border-muted px-1 text-[10px] text-text-tertiary">
                  外部路径
                </span>
              )}
            </div>
            <code className="mt-1 block min-w-0 break-all font-mono text-[10px] leading-relaxed text-text-tertiary">
              {entry.path}
            </code>
          </div>
        ))}
      </div>
      {category.note && (
        <p className="border-t border-border-muted px-3 py-2 text-[10px] leading-relaxed text-text-tertiary">
          {category.note}
        </p>
      )}
      {category.id === 'jobs-records' && jobsRetentionSlot && (
        <div id="jobs-retention-control-slot" className="border-t border-border-muted px-3 py-2">
          {jobsRetentionSlot}
        </div>
      )}
    </section>
  );
}

export function DataSettingsPanel({
  catalog,
  loading,
  error,
  jobsRetentionSlot,
}: DataSettingsPanelProps) {
  return (
    <div className="min-w-0 space-y-3" data-testid="data-settings-panel">
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">
          Data locations
        </h3>
        <p className="mt-1 text-[11px] leading-relaxed text-text-tertiary">
          这里只展示 Pan 登记的存储路径，不浏览目录内容，不读取凭据或统计全盘。此页目前不执行清理。
        </p>
      </section>

      {loading && (
        <p role="status" className="text-[11px] text-text-tertiary">
          正在读取存储路径…
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
          无法读取存储路径：{error}
        </p>
      )}
      {!loading && !error && catalog && (
        <>
          <div className="space-y-2">
            {catalog.categories.map((category) => (
              <CategoryCard
                key={category.id}
                category={category}
                jobsRetentionSlot={jobsRetentionSlot}
              />
            ))}
          </div>
          <p className="rounded-md border border-border-muted bg-bg-tertiary px-3 py-2 text-[10px] leading-relaxed text-text-tertiary">
            {catalog.notice}
          </p>
          <p className="rounded-md border border-border-muted px-3 py-2 text-[10px] leading-relaxed text-text-tertiary">
            {catalog.jobsRetention.message}
          </p>
        </>
      )}
    </div>
  );
}

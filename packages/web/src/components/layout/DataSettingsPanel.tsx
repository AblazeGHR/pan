import { Modal } from '@/components/ui/Modal';
import { scanWorkdirs, type WorkdirRetentionResult } from '@/services/api';
import { useState, type ReactNode } from 'react';
import type {
  ApiDataCatalogResponse,
  ApiDataRetentionResponse,
  DataCatalogCategory,
  DataRetentionPolicyId,
} from '@/types';

interface DataSettingsPanelProps {
  catalog: ApiDataCatalogResponse | null;
  loading: boolean;
  error: string | null;
  retention: ApiDataRetentionResponse | null;
  retentionDraft: ApiDataRetentionResponse['policies'] | null;
  retentionLoading: boolean;
  retentionError: string | null;
  retentionSaving: boolean;
  retentionSaveError: string | null;
  retentionDirty: boolean;
  onRetentionChange: (id: DataRetentionPolicyId, field: 'enabled' | 'days', value: boolean | number | null) => void;
  onSaveRetention: () => void;
  /** Insertion point for the reusable Jobs retention settings component. */
  jobsRetentionSlot?: ReactNode;
}

const POLICY_CARDS: Array<{
  id: DataRetentionPolicyId;
  label: string;
  description: string;
}> = [
  {
    id: 'workdirs',
    label: 'Orphan workdirs',
    description: 'Only direct directories under the default data/workdirs root, with no Session or Workspace references, are eligible. Session expiry always preserves workdirs.',
  },
  {
    id: 'sessions',
    label: 'Sessions 与 history',
    description: 'Delete only when Session, history, queue and task references are safe. Workdirs are retained separately.',
  },
  {
    id: 'attachments',
    label: '已登记上传附件',
    description: '独立于 Session 历史；过期上传文件可以使旧链接失效，清理 owner sidecar。',
  },
  {
    id: 'qq_history',
    label: '已保存的 QQ history',
    description: '按每条记录时间移除过期记录；格式或时间不确定时保留整个文件。',
  },
  {
    id: 'qq_media',
    label: 'QQ media',
    description: '按文件修改时间清理普通文件；引用可能失效，QQ inbox 永不自动清理。',
  },
  {
    id: 'pan_logs',
    label: 'Pan 主日志轮转文件',
    description: '清理 data/logs 中同名过期轮转普通文件；当前活动日志和外部日志路径保留。',
  },
];

function CategoryCard({
  category,
  jobsRetentionSlot,
}: {
  category: DataCatalogCategory;
  jobsRetentionSlot?: ReactNode;
}) {
  const protectedCategory = category.policyStatus === 'not_auto_cleanable';
  const policyLabel = protectedCategory
    ? '不可自动清理'
    : category.policyStatus === 'data_retention_policy'
      ? 'Data 策略（默认关闭）'
      : category.policyStatus === 'session_lifecycle_cleanup'
        ? '随 Session 生命周期清理'
        : '由 Jobs API 管理';
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
          {policyLabel}
        </span>
      </div>
      <div className="min-w-0 divide-y divide-border-muted border-t border-border-muted">
        {category.paths.map((entry) => (
          <div key={`${entry.label}:${entry.path}`} className="min-w-0 px-3 py-2">
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-[10px] text-text-secondary">{entry.label}</span>
              {!entry.exists && <span className="text-[10px] text-text-tertiary">尚未创建</span>}
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

function formatWhen(value?: string | null): string {
  if (!value) return '尚未扫描';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function RetentionControls({
  saved,
  draft,
  loading,
  error,
  saving,
  saveError,
  dirty,
  onChange,
  onSave,
}: {
  saved: ApiDataRetentionResponse | null;
  draft: ApiDataRetentionResponse['policies'] | null;
  loading: boolean;
  error: string | null;
  saving: boolean;
  saveError: string | null;
  dirty: boolean;
  onChange: DataSettingsPanelProps['onRetentionChange'];
  onSave: () => void;
}) {
  if (loading) {
    return <p role="status" className="text-[11px] text-text-tertiary">正在读取清理策略…</p>;
  }
  if (error || !draft || !saved) {
    return (
      <p role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
        无法读取清理策略：{error || '响应无效'}
      </p>
    );
  }

  return (
    <section className="min-w-0 space-y-2 rounded-md border border-border-muted bg-bg-secondary p-3">
      <div>
        <h3 className="text-xs font-semibold text-text-primary">自动清理策略</h3>
        <p className="mt-1 text-[10px] leading-relaxed text-text-tertiary">
          每类默认关闭且天数为空；空值表示不清理。符合期限的内容最多每日扫描一次；任何活动、归属、路径或时间不确定都会跳过并记录原因。
        </p>
      </div>
      <div className="space-y-2">
        {POLICY_CARDS.map((item) => {
          const policy = draft[item.id] ?? { enabled: false, days: null };
          const result = saved.lastScans[item.id];
          return (
            <section key={item.id} className="min-w-0 rounded border border-border-muted bg-bg-primary p-3">
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <h4 className="text-xs font-medium text-text-primary">{item.label}</h4>
                  <p className="mt-1 text-[10px] leading-relaxed text-text-tertiary">{item.description}</p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-label={item.id === 'workdirs' ? 'Orphan workdirs automatic cleanup' : `${item.label} 自动清理`}
                  aria-checked={policy.enabled}
                  onClick={() => onChange(item.id, 'enabled', !policy.enabled)}
                  className={`relative inline-flex h-[18px] w-8 shrink-0 rounded-full transition-colors ${policy.enabled ? 'bg-accent' : 'bg-bg-hover'}`}
                >
                  <span className={`absolute left-[2px] top-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform ${policy.enabled ? 'translate-x-[14px]' : 'translate-x-0'}`} />
                </button>
              </div>
              {item.id === 'workdirs' && <WorkdirCleanup days={policy.days} />}
              <label className="mt-2 flex max-w-full flex-wrap items-center gap-2 text-[10px] text-text-secondary">
                <span>{item.id === 'workdirs' ? 'Days without changes' : '保留天数'}</span>
                <input
                  type="number"
                  min={1}
                  max={36500}
                  step={1}
                  aria-label={item.id === 'workdirs' ? 'Orphan workdirs days without changes' : `${item.label} 保留天数`}
                  value={policy.days ?? ''}
                  placeholder={item.id === 'workdirs' ? 'Empty = keep' : '空＝不清理'}
                  onChange={(event) => {
                    const raw = event.target.value;
                    if (raw === '') {
                      onChange(item.id, 'days', null);
                      return;
                    }
                    const parsed = Number(raw);
                    if (Number.isFinite(parsed)) {
                      onChange(item.id, 'days', Math.max(1, Math.min(36500, Math.floor(parsed))));
                    }
                  }}
                  className="w-20 rounded border border-border-muted bg-bg-primary px-2 py-1 text-xs text-text-primary"
                />
              </label>
              <p className="mt-2 break-words text-[10px] leading-relaxed text-text-tertiary">
                {item.id === 'workdirs' ? `Last scan: ${result?.lastScanAt ?? 'Never'}; scanned ${result?.scanned ?? 0}, deleted ${result?.deleted ?? 0}, skipped ${result?.skipped ?? 0}` : <>最近扫描：{formatWhen(result?.lastScanAt)}；扫描 {result?.scanned ?? 0}，删除 {result?.deleted ?? 0}，跳过 {result?.skipped ?? 0}</>}
                {result?.skipReasons && Object.keys(result.skipReasons).length > 0
                  ? `（${Object.entries(result.skipReasons).map(([reason, count]) => `${reason}: ${count}`).join('；')}）`
                  : ''}
                {result?.error ? `; Error: ${result.error}` : ''}
              </p>
            </section>
          );
        })}
      </div>
      {saveError && (
        <p role="alert" className="text-[10px] text-danger">保存失败：{saveError}</p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[10px] text-text-tertiary">保存在 config.json 的 data_retention。</span>
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={onSave}
          className="rounded border border-border-muted px-3 py-1.5 text-[11px] text-text-primary hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
  {saving ? '保存中…' : '保存清理策略'}
        </button>
      </div>
    </section>
  );
}

export function DataSettingsPanel({
  catalog,
  loading,
  error,
  retention,
  retentionDraft,
  retentionLoading,
  retentionError,
  retentionSaving,
  retentionSaveError,
  retentionDirty,
  onRetentionChange,
  onSaveRetention,
  jobsRetentionSlot,
}: DataSettingsPanelProps) {
  return (
    <div className="min-w-0 space-y-3" data-testid="data-settings-panel">
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">
          Data locations
        </h3>
        <p className="mt-1 text-[11px] leading-relaxed text-text-tertiary">
          Registered Pan storage categories and paths. Orphan workdir candidates are scanned only when requested or when their automatic policy is enabled; other unregistered data directories are excluded.
        </p>
      </section>

      {loading && (
        <p role="status" className="text-[11px] text-text-tertiary">正在读取存储路径…</p>
      )}
      {error && (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
          无法读取存储路径：{error}
        </p>
      )}
      <RetentionControls
        saved={retention}
        draft={retentionDraft}
        loading={retentionLoading}
        error={retentionError}
        saving={retentionSaving}
        saveError={retentionSaveError}
        dirty={retentionDirty}
        onChange={onRetentionChange}
        onSave={onSaveRetention}
      />
      {!loading && !error && catalog && (
        <>
          <div className="space-y-2">
            {catalog.categories.map((category) => (
              <CategoryCard key={category.id} category={category} jobsRetentionSlot={jobsRetentionSlot} />
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

export function WorkdirCleanup({ days }: { days: number | null }) {
  const actionClass = "rounded border border-border-muted px-3 py-1.5 text-xs hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-50";
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<WorkdirRetentionResult | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<WorkdirRetentionResult['results']>([]);
  const valid = days !== null && Number.isInteger(days) && days >= 1 && days <= 36500;
  const load = async (remove = false) => {
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await scanWorkdirs(days, remove ? selected : undefined);
      if (remove) {
        setOutcomes(response.results);
        const deleted = new Set(response.results.filter(row => row.deleted).map(row => row.name));
        setResult(current => current && ({ ...current, candidates: current.candidates.filter(row => !deleted.has(row.name)) }));
        setSelected([]);
      } else {
        setResult(response);
        setSelected([]);
        setOutcomes([]);
      }
      if (response.error) setError(response.error);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <div className="mt-2 space-y-2 text-xs">
    <p className="rounded border border-amber-500/50 bg-amber-500/10 p-2 text-amber-500">
      The “no changes for N days” check may be inaccurate. Enable automatic cleanup with caution; manual cleanup is recommended.
    </p>
    <button type="button" className={actionClass} disabled={!valid} onClick={() => { setOpen(true); void load(); }}>Browse cleanup candidates</button>
    {!valid && <p>Set a day count from 1 to 36500 to browse candidates.</p>}
    <Modal open={open} onClose={() => { if (!busy) setOpen(false); }} title="Orphan workdir cleanup" size="lg">
      <div className="space-y-3 text-xs">
        <p>Directories with no changes for {days} days. Deleting a directory is permanent. Sessions are never deleted here. Every selected directory is checked again before deletion.</p>
        <button type="button" className={actionClass} disabled={busy} onClick={() => void load()}>Refresh</button>
        {busy && <p role="status">Scanning or deleting directories…</p>}
        {error && <p role="alert">Cleanup failed: {error}</p>}
        {result && <>
          <p>Scanned: {result.scanned}. Skipped: {result.skipped}. {Object.entries(result.skipReasons).map(([key, count]) => `${key}: ${count}`).join('; ')}</p>
          {result.candidates.length === 0 ? <p>No eligible workdirs found.</p> : <>
            <label><input type="checkbox" disabled={busy} checked={selected.length === result.candidates.length} onChange={e => setSelected(e.target.checked ? result.candidates.map(row => row.name) : [])} /> Select all</label>
            <div className="max-h-80 space-y-2 overflow-auto">
              {result.candidates.map(row => <label key={row.name} className="block break-all rounded border border-border-muted p-2">
                <input type="checkbox" disabled={busy} checked={selected.includes(row.name)} onChange={e => setSelected(current => e.target.checked ? [...current, row.name] : current.filter(name => name !== row.name))} /> {row.name}
                <span className="block">{row.path}</span>
                <span className="block">Last modified: {row.modifiedAt} · Entries: {row.entries}</span>
              </label>)}
            </div>
            <button type="button" className={`${actionClass} text-danger`} disabled={busy || !selected.length} onClick={() => void load(true)}>Delete selected ({selected.length})</button>
          </>}
        </>}
        {outcomes.map(row => <p key={row.name} role={row.deleted ? 'status' : 'alert'}>{row.name}: {row.deleted ? 'Deleted' : `Failed — ${row.reason}`}</p>)}
      </div>
    </Modal>
  </div>;
}

import { Link } from 'react-router-dom';
export function RetiredJobsRoute() {
  return <section aria-label="Jobs migrated" className="m-6 max-w-2xl rounded-lg border border-border-default bg-bg-secondary p-5 text-text-primary">
    <h1 className="text-xl font-semibold">Jobs 已迁移</h1>
    <p className="mt-2 text-sm text-text-secondary">旧 Jobs 页面已退役。请使用 Automation 中的 Canonical Jobs 管理入口。</p>
    <Link className="mt-4 inline-block rounded border border-border-default px-3 py-2 text-sm text-accent" to="/automation/canonical">打开 Canonical Jobs</Link>
  </section>;
}

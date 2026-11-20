import type { CanonicalObject, JsonObject } from '@/types/canonicalJobs';

export type CanonicalJobDetails = { job: CanonicalObject; actions: Record<string, JsonObject>; missing: string[] };
const obj = (value: unknown): JsonObject => value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
const display = (value: unknown) => value === undefined || value === null ? 'Not provided' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const literal = (value: unknown): unknown => { const binding = obj(value); return binding.from === 'literal' ? binding.value : value; };

/** Values come only from the fixed canonical definition and exact Action version. */
export function CanonicalExecutionDetails({ detail }: { detail: CanonicalJobDetails }) {
  const steps = Array.isArray(detail.job.steps) ? detail.job.steps as JsonObject[] : [];
  return <section aria-label="Canonical Job configuration" className="space-y-3">
    <h3 className="text-xs font-semibold uppercase tracking-wider text-text-tertiary">Action configuration</h3>
    {detail.missing.length > 0 && <p role="status" className="text-xs text-warning">Some fixed Action versions could not be loaded. Their references are preserved.</p>}
    {steps.map((step, index) => {
      const ref = obj(step.actionRef), key = `${String(ref.id)}:${String(ref.revision)}:${String(ref.digest)}`;
      const action = detail.actions[key], execution = obj(action?.execution), config = obj(execution.config), args = obj(step.arguments);
      const commands = Array.isArray(config.commands) ? config.commands as JsonObject[] : [];
      const argv = literal(args.argv) ?? config.argv ?? (commands.length ? 'See commands below' : undefined);
      const cwd = literal(args.cwd) ?? config.cwd;
      const env = literal(args.env) ?? literal(args.environment) ?? config.env ?? config.environment;
      const timeout = literal(args.timeout) ?? literal(args.timeoutSec) ?? literal(args.timeout_sec) ?? config.timeout ?? config.timeoutSec ?? config.timeout_sec;
      const window = literal(args.hideWindow) ?? literal(args.hide_window) ?? config.hideWindow ?? config.hide_window;
      return <div key={String(step.stepId ?? index)} className="space-y-2 rounded border border-border-default bg-bg-primary p-3 text-sm">
        <p className="font-medium">Step {index + 1} · {String(action?.name ?? ref.id ?? 'Unknown Action')}</p>
        <p className="text-text-secondary">Fixed Action · revision {String(ref.revision ?? 'unknown')}</p>
        <details className="text-text-secondary"><summary className="cursor-pointer">Technical details</summary><p className="break-all">ID: {String(ref.id ?? 'unknown')} · Digest: {String(ref.digest ?? 'unknown')}</p></details>
        {execution.operation != null && <p className="break-words text-text-secondary">{display(execution.operation)}</p>}
        <details className="rounded border border-border-muted p-2">
          <summary className="cursor-pointer font-medium">Execution parameters</summary>
          <dl className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[9rem_minmax(0,1fr)]">
            {Object.entries({ argv, 'Working directory': cwd, Environment: env, Window: window === undefined ? undefined : window === true ? 'Hidden' : window === false ? 'Visible' : window, Timeout: timeout }).map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-text-tertiary">{label}</dt>
                <dd className="min-w-0 whitespace-pre-wrap break-all font-mono">{display(value)}</dd>
              </div>
            ))}
          </dl>
        </details>
        {commands.map((command, i) => (
          <div key={i} className="space-y-1 rounded border border-border-muted p-2">
            <div className="flex flex-wrap items-center justify-between gap-1">
              <p className="font-medium">Command {i + 1}</p>
              <p className="text-text-secondary">Timeout: {display(command.timeout ?? command.timeoutSec ?? command.timeout_sec)}</p>
            </div>
            <details>
              <summary className="cursor-pointer text-text-secondary">Arguments and environment</summary>
              <pre className="mt-2 whitespace-pre-wrap break-all text-xs">{display(command.argv)}</pre>
              <p className="break-all">Environment: {display(command.env)}</p>
              <p className="break-all">Working directory: {display(command.cwd)}</p>
            </details>
          </div>
        ))}
        <details><summary>Full step and fixed Action definition</summary><pre className="overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({ step, action: action ?? null }, null, 2)}</pre></details>
      </div>;
    })}
    <details><summary>Full Job configuration</summary><pre className="overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(detail.job, null, 2)}</pre></details>
  </section>;
}

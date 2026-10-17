import type { McpServerInfo, PluginBinding, PluginMode } from '@/types';

const MODE_LABELS: Record<PluginMode, string> = {
  standalone: '独立 MCP',
  pan_native: '并入 Pan MCP',
  gateway: '聚合发现 / 调用',
};

/** Shared presentation for creation and management; callers own persistence. */
export function PluginSelection({ servers, bindings, disabled = false, keepOne = false, onChange }: {
  servers: McpServerInfo[];
  bindings: PluginBinding[];
  disabled?: boolean;
  keepOne?: boolean;
  onChange: (binding: PluginBinding) => void;
}) {
  const enabledCount = bindings.filter((b) => b.enabled).length;
  return <div className="flex flex-col gap-1">
    {servers.map((server) => {
      const binding = bindings.find((b) => b.plugin_id === server.name || server.aliases?.includes(b.plugin_id))
        ?? { plugin_id: server.name, enabled: false, mode: server.defaultMode ?? 'standalone' };
      const modes = server.supportedModes ?? ['standalone'];
      const lockedLast = keepOne && binding.enabled && enabledCount <= 1;
      const change = (patch: Partial<PluginBinding>) => onChange({ ...binding, plugin_id: server.name, ...patch });
      return <div key={server.name} className="flex flex-wrap items-center gap-2 rounded px-2.5 py-1.5 hover:bg-bg-tertiary">
        <label className="flex min-w-0 flex-1 items-start gap-2">
          <input type="checkbox" checked={binding.enabled} disabled={disabled || lockedLast}
            title={lockedLast ? 'At least one MCP server must remain enabled' : undefined}
            className="mt-0.5 shrink-0 accent-accent"
            onChange={(event) => change({ enabled: event.target.checked })} />
          <span className="min-w-0">
            <span className="block text-sm text-text-primary">{server.name}</span>
            {server.command && <span className="block truncate font-mono text-[11px] text-text-tertiary">
              {server.command}{server.cwd ? ` · cwd: ${server.cwd}` : ''}
            </span>}
          </span>
        </label>
        <select aria-label={`${server.name} 启用方式`} value={binding.mode}
          disabled={disabled || modes.length === 1}
          onChange={(event) => change({ mode: event.target.value as PluginMode })}
          className="ml-auto max-w-full rounded border border-border-muted bg-bg-primary px-2 py-1 text-xs text-text-primary disabled:opacity-60">
          {(['standalone', 'pan_native', 'gateway'] as PluginMode[]).map((mode) =>
            <option key={mode} value={mode} disabled={!modes.includes(mode)}>
              {MODE_LABELS[mode]}{!modes.includes(mode) ? '（此插件不支持）' : ''}
            </option>)}
        </select>
      </div>;
    })}
  </div>;
}

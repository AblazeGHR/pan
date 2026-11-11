import {
  SETTINGS_CATEGORY_LABELS,
  SETTINGS_CATEGORY_ORDER,
  type SettingsCategory,
} from './settingsCategories';

/**
 * Conditions that also decide whether the real panel renders a group at all.
 * Search results must never advertise a group the current page cannot show.
 */
export interface SettingsSearchContext {
  /** Remote / Tunnel renders only when config.json enables the tunnel. */
  remoteTunnel: boolean;
  /** Password and permission editors render only for a logged-in session. */
  authorizationLoggedIn: boolean;
}

export interface SettingsSearchEntry {
  id: string;
  tab: SettingsCategory;
  /** Group heading inside the tab; also the jump target. */
  group: string;
  /** Short row summary shown under the group name. */
  detail: string;
  keywords: string[];
  /**
   * `group` scrolls to the matching heading after switching tabs, `tab` only
   * switches tabs (the group has no heading of its own).
   */
  jump?: 'group' | 'tab';
  available?: (context: SettingsSearchContext) => boolean;
}

/**
 * One entry per settings group, mirroring the groups the panels actually render.
 * `settingsSearch.guard.test.ts` fails when an entry stops matching a rendered
 * heading, so adding or renaming a group cannot silently drop out of search.
 */
export const SETTINGS_SEARCH_ENTRIES: readonly SettingsSearchEntry[] = [
  // Lifecycle
  {
    id: 'pan-service',
    tab: 'lifecycle',
    group: 'Pan service',
    detail: 'Restart the Pan main service and pick running-Session handling',
    keywords: ['restart', 'restart pan', 'main service', 'service', '重启', '服务'],
  },
  {
    id: 'session-policy',
    tab: 'lifecycle',
    group: 'Session policy',
    detail: 'Opening links for the Restart/Exit and startup recovery policies',
    keywords: ['session policy', 'exit strategy', 'startup preference', '退出策略', '启动恢复'],
  },
  {
    id: 'login-startup',
    tab: 'lifecycle',
    group: 'Login startup',
    detail: 'Start Pan automatically after OS login',
    keywords: ['login startup', 'start after login', '开机自启', '登录启动'],
  },
  {
    id: 'exit-pan-service',
    tab: 'lifecycle',
    group: 'Exit Pan service',
    detail: 'Stop this Pan instance without restarting it',
    keywords: ['exit', 'stop service', 'shutdown', '退出服务'],
  },

  // Behavior
  {
    id: 'session-error-bridge',
    tab: 'behavior',
    group: 'Session errors → msgBridge',
    detail: 'Bridge Session errors into msgBridge items',
    keywords: ['session error', 'msgbridge', 'error bridge', '错误桥接'],
  },
  {
    id: 'reminding-card',
    tab: 'behavior',
    group: 'Reminding card',
    detail: 'Reminding-card reminder interval in minutes',
    keywords: ['reminding card', 'reminder interval', 'minutes', '提醒间隔'],
    jump: 'tab',
  },
  {
    id: 'auto-report',
    tab: 'behavior',
    group: 'Auto report',
    detail: 'Automatic worker-kill reports and append mode',
    keywords: ['auto report', 'report worker', '自动汇报'],
  },
  {
    id: 'prompt-summary',
    tab: 'behavior',
    group: 'Prompt summary',
    detail: 'Prompt summary defaults for auto report',
    keywords: ['prompt summary', 'summary', '摘要'],
  },
  {
    id: 'session-lifecycle',
    tab: 'behavior',
    group: 'Session legal-state lifecycle',
    detail: 'Restart/Exit Session policy, wake message, startup preference',
    keywords: [
      'session lifecycle',
      'wake message',
      'startup preference',
      'exit strategy',
      'illegal',
      '唤醒',
    ],
  },
  {
    id: 'worker-config',
    tab: 'behavior',
    group: 'Worker configuration',
    detail: 'Worker timeout_sec / task_timeout_sec / idle_sec',
    keywords: ['worker', 'timeout', 'idle', 'worker config', '超时'],
  },
  {
    id: 'worker-stop-reports',
    tab: 'behavior',
    group: 'Worker stop reports',
    detail: 'Report errors when you stop a running worker',
    keywords: ['worker stop', 'kill report', 'stop worker', '停止 worker'],
  },
  {
    id: 'auto-force-send',
    tab: 'behavior',
    group: 'Message auto force send',
    detail: 'Auto force send for terminal notices, MA messages and reports',
    keywords: ['force send', 'auto force', 'terminal notice', 'completion report', '强制发送'],
  },
  {
    id: 'new-sessions',
    tab: 'behavior',
    group: 'New Sessions',
    detail: 'Place new Sessions in the current Workspace by default',
    keywords: ['new session', 'workspace default', '默认工作区'],
  },
  {
    id: 'confirmations',
    tab: 'behavior',
    group: 'Confirmations',
    detail: 'Queue edit and cross-workspace management prompts',
    keywords: ['confirm', 'confirmation', 'queue edit', 'cross workspace', '确认'],
  },
  {
    id: 'memory-advanced',
    tab: 'behavior',
    group: 'Memory advanced',
    detail: 'Reload memory config (memory.enabled)',
    keywords: ['memory', 'reload memory', '记忆'],
  },

  // Remote
  {
    id: 'remote-tunnel',
    tab: 'remote',
    group: 'Remote / Tunnel',
    detail: 'cloudflared tunnel status and restart',
    keywords: ['tunnel', 'cloudflared', 'remote', '隧道'],
    available: (context) => context.remoteTunnel,
  },
  {
    id: 'mcp-remote',
    tab: 'remote',
    group: 'MCP 远程连接',
    detail: 'MCP gateway port, hostname and access settings',
    keywords: ['mcp', 'remote mcp', 'gateway', '网关', '远程连接'],
  },
  {
    id: 'tailscale',
    tab: 'remote',
    group: 'Tailscale 备用访问',
    detail: 'Tailscale fallback protocol and port',
    keywords: ['tailscale', 'protocol', 'port', '备用访问'],
  },
  {
    id: 'lan',
    tab: 'remote',
    group: '普通局域网访问（LAN）',
    detail: 'LAN startup switch, listen host and port',
    keywords: ['lan', 'host', 'port', '局域网'],
  },

  // Appearance
  {
    id: 'panel-order',
    tab: 'appearance',
    group: 'Panel order',
    detail: 'Reorder navigation panels by drag or with the move buttons',
    keywords: ['panel order', 'reorder', 'panel', '顺序'],
  },
  {
    id: 'chat-view',
    tab: 'appearance',
    group: 'Chat view',
    detail: 'Chat view style and switcher visibility',
    keywords: ['chat view', 'tui', 'bubble', 'raw', 'switcher', '视图'],
  },
  {
    id: 'sidebar',
    tab: 'appearance',
    group: 'Sidebar',
    detail: 'Show the sidebar collapse handle',
    keywords: ['sidebar', 'collapse handle', '侧边栏', '折叠'],
  },
  {
    id: 'session-list',
    tab: 'appearance',
    group: 'Session list',
    detail: 'Show Group by and pick the default grouping',
    keywords: ['group by', 'session list', 'grouping', '分组'],
  },
  {
    id: 'message-visibility',
    tab: 'appearance',
    group: 'Message visibility',
    detail: 'Show meta-agent, task-agent and QQ message info',
    keywords: ['meta-agent', 'task-agent', 'qq messages', 'visibility', '可见'],
  },
  {
    id: 'message-grouping',
    tab: 'appearance',
    group: 'Message grouping',
    detail: 'Group consecutive tool and thinking blocks',
    keywords: ['group blocks', 'tool block', 'thinking', '合并'],
  },
  {
    id: 'scroll-position',
    tab: 'appearance',
    group: 'Scroll position',
    detail: 'Keep the reading position per Session',
    keywords: ['scroll', 'reading position', '滚动位置'],
  },
  {
    id: 'quick-location',
    tab: 'appearance',
    group: 'Quick location',
    detail: 'Show the message navigation rail',
    keywords: ['navigation rail', 'quick location', 'rail', '快速定位'],
  },
  {
    id: 'history-search',
    tab: 'appearance',
    group: 'History search',
    detail: 'Enable Session and global history search',
    keywords: ['history search', 'search messages', '历史搜索'],
  },
  {
    id: 'reset-ui-preferences',
    tab: 'appearance',
    group: 'Reset',
    detail: 'Reset the saved UI preferences',
    keywords: ['reset', 'restore defaults', '重置'],
  },

  // Notifications
  {
    id: 'completion-bell',
    tab: 'notifications',
    group: 'Completion bell defaults',
    detail: 'Completion delivery defaults and QQ contacts',
    keywords: ['completion bell', 'completion', 'bell', '完成提醒'],
  },
  {
    id: 'locker-new-unread',
    tab: 'notifications',
    group: 'Locker new unread',
    detail: 'Where Locker unread notifications go',
    keywords: ['locker', 'unread', '未读'],
  },
  {
    id: 'new-errors',
    tab: 'notifications',
    group: 'New errors',
    detail: 'Where new error notifications go',
    keywords: ['error', 'new error', '错误通知'],
  },
  {
    id: 'unread-recording',
    tab: 'notifications',
    group: 'Unread recording',
    detail: 'Unread recording scopes',
    keywords: ['unread', 'recording', '未读记录'],
  },
  {
    id: 'locker-desktop',
    tab: 'notifications',
    group: 'Locker',
    detail: 'Desktop system notifications for Locker messages',
    keywords: ['locker notification', 'desktop notification', '系统通知'],
  },
  {
    id: 'cli-adapter-warnings',
    tab: 'notifications',
    group: 'CLI adapter warnings',
    detail: 'Codex warnings via Toast',
    keywords: ['codex warning', 'toast', 'adapter warning', '警告'],
  },

  // Shortcuts
  {
    id: 'shortcuts-keyboard',
    tab: 'shortcuts',
    group: 'Keyboard shortcuts',
    detail: 'Record, clear or restore composer and panel shortcuts',
    keywords: ['shortcut', 'keyboard', 'keybinding', '快捷键'],
  },
  {
    id: 'shortcuts-general',
    tab: 'shortcuts',
    group: 'General',
    detail: 'General shortcut group',
    keywords: ['general', 'shortcut group', '通用'],
  },
  {
    id: 'shortcuts-composer',
    tab: 'shortcuts',
    group: 'Message composer',
    detail: 'Composer shortcut group',
    keywords: ['composer', 'message composer', '输入框'],
  },
  {
    id: 'shortcuts-panels',
    tab: 'shortcuts',
    group: 'Panels — generated from the live view/plugin registry',
    detail: 'Panel shortcut group',
    keywords: ['panels', 'panel shortcuts', '面板'],
  },
  {
    id: 'shortcuts-queue',
    tab: 'shortcuts',
    group: 'Queue',
    detail: 'Queue shortcut group',
    keywords: ['queue', '队列'],
  },
  {
    id: 'shortcuts-session',
    tab: 'shortcuts',
    group: 'Selected Session',
    detail: 'Selected Session shortcut group',
    keywords: ['session', 'selected session', '会话'],
  },
  {
    id: 'shortcuts-fixed',
    tab: 'shortcuts',
    group: 'Fixed, context-scoped shortcuts',
    detail: 'Editor, terminal and overlay shortcuts that cannot be remapped',
    keywords: ['fixed', 'context shortcut', 'editor', 'terminal', 'escape', 'monaco'],
  },

  // Adapters
  {
    id: 'codex-adapter',
    tab: 'adapters',
    group: 'Codex',
    detail: 'Terminal input popup, native passthrough and model whitelist',
    keywords: ['codex', 'model whitelist', 'terminal input popup', 'codex models'],
  },
  {
    id: 'adapter-reload',
    tab: 'adapters',
    group: 'Adapter reload',
    detail: 'Reload adapter model lists from config.json',
    keywords: ['reload adapters', 'adapter reload', 'model list', '模型列表'],
  },

  // Plugins
  {
    id: 'local-plugin-panels',
    tab: 'plugins',
    group: 'Local plugin panels',
    detail: 'Enable locally built plugin panels',
    keywords: ['plugin panel', 'local plugin', '插件面板'],
  },
  {
    id: 'qq-plugins',
    tab: 'plugins',
    group: 'QQ plugins',
    detail: 'QQ bot accounts and WebSocket URLs',
    keywords: ['qq', 'qq plugin', 'bot_uin', 'ws url', '机器人'],
  },
  {
    id: 'plugin-manifests',
    tab: 'plugins',
    group: 'Plugin manifests',
    detail: 'Reload the plugin_manifests list from config.json',
    keywords: ['plugin manifest', 'reload plugins', '插件清单'],
  },

  // Data
  {
    id: 'data-locations',
    tab: 'data',
    group: 'Data locations',
    detail: 'Registered Pan storage categories and paths',
    keywords: ['data location', 'paths', 'storage', '存储路径'],
  },
  {
    id: 'data-retention',
    tab: 'data',
    group: '自动清理策略',
    detail: 'Automatic cleanup policies per data category',
    keywords: ['retention', 'cleanup', 'data retention', '自动清理'],
  },
  {
    id: 'jobs-retention',
    tab: 'data',
    group: 'Automatic cleanup settings',
    detail: 'Job record and log retention rules',
    keywords: ['job retention', 'logs retention', 'job cleanup', '任务清理'],
  },

  // Authorization
  {
    id: 'local-authorization',
    tab: 'authorization',
    group: 'Local Authorization',
    detail: 'Local user key login and logout',
    keywords: ['authorization', 'login', 'local key', '登录', '密码', '授权'],
  },
  {
    id: 'change-password',
    tab: 'authorization',
    group: 'Change password',
    detail: 'Change the local password',
    keywords: ['password', 'change password', '密码'],
    available: (context) => context.authorizationLoggedIn,
  },
  {
    id: 'agent-permissions',
    tab: 'authorization',
    group: 'Profile-wide Agent permissions',
    detail: 'Capability matrix for delegated Agent operations',
    keywords: ['permission', 'permissions', 'capability', 'agent permission', '权限'],
    available: (context) => context.authorizationLoggedIn,
  },
];

/** Case- and whitespace-insensitive form used by both search and jump. */
export function normalizeSettingsSearchText(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, ' ');
}

function entryHaystack(entry: SettingsSearchEntry): string {
  return normalizeSettingsSearchText(
    [SETTINGS_CATEGORY_LABELS[entry.tab], entry.group, entry.detail, ...entry.keywords].join(' '),
  );
}

function scoreEntry(entry: SettingsSearchEntry, tokens: string[]): number | null {
  const haystack = entryHaystack(entry);
  const normalizedGroup = normalizeSettingsSearchText(entry.group);
  const normalizedDetail = normalizeSettingsSearchText(entry.detail);
  let score = 0;
  for (const token of tokens) {
    if (!haystack.includes(token)) return null;
    if (normalizedGroup.startsWith(token)) score += 4;
    else if (normalizedGroup.includes(token)) score += 3;
    else if (normalizedDetail.includes(token)) score += 2;
    else score += 1;
  }
  return score;
}

/**
 * Rank matching groups. Every whitespace-separated token must appear in the tab
 * label, group, detail or keywords, so extra words narrow instead of widen.
 */
export function searchSettings(
  entries: readonly SettingsSearchEntry[],
  query: string,
  context: SettingsSearchContext,
): SettingsSearchEntry[] {
  const tokens = normalizeSettingsSearchText(query).split(' ').filter(Boolean);
  if (tokens.length === 0) return [];

  return entries
    .map((entry, index) => ({ entry, index }))
    .filter(({ entry }) => (entry.available ? entry.available(context) : true))
    .map(({ entry, index }) => ({ entry, index, score: scoreEntry(entry, tokens) }))
    .filter(
      (hit): hit is { entry: SettingsSearchEntry; index: number; score: number } =>
        hit.score !== null,
    )
    .sort((left, right) => {
      if (right.score !== left.score) return right.score - left.score;
      const leftOrder = SETTINGS_CATEGORY_ORDER.indexOf(left.entry.tab) * 1000 + left.index;
      const rightOrder = SETTINGS_CATEGORY_ORDER.indexOf(right.entry.tab) * 1000 + right.index;
      return leftOrder - rightOrder;
    })
    .map((hit) => hit.entry);
}

/**
 * Find the rendered group heading a search hit should scroll to. Headings that
 * wrap extra copy (collapsible Remote sections) keep the group name as a prefix.
 */
export function findSettingsGroupHeading(root: HTMLElement, group: string): HTMLElement | null {
  const target = normalizeSettingsSearchText(group);
  if (!target) return null;
  const headings = Array.from(
    root.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, legend'),
  ).map((heading) => ({ heading, text: normalizeSettingsSearchText(heading.textContent ?? '') }));

  const exact = headings.find((candidate) => candidate.text === target);
  if (exact) return exact.heading;
  const prefixed = headings.find((candidate) => candidate.text.startsWith(target));
  return prefixed ? prefixed.heading : null;
}

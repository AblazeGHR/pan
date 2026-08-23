import type { Message } from '@/types';
import type { AppSettings } from '@/stores/appSettingsStore';

/** Meta-agent orchestration messages (`worker_send` auto-prepends this marker). */
const META_AGENT_PREFIX = '////by agent';
/** Task-agent completion reports. */
const TASK_AGENT_PREFIX = '@@@@by agent';
/** QQ-injected messages (inbox reminders / subscription pushes). */
const QQ_PREFIX = '@@@@by qq';

export type MessageVisibilitySettings = Pick<
  AppSettings,
  'showMetaAgent' | 'showTaskAgent' | 'showQQ'
>;

/**
 * Frontend-only display filter. Drops messages whose source-marker prefix is
 * hidden by a disabled toggle. The input array is never mutated — the store
 * keeps every message and toggling a switch back on restores them.
 */
export function filterVisibleMessages(
  messages: Message[],
  settings: MessageVisibilitySettings,
): Message[] {
  const { showMetaAgent, showTaskAgent, showQQ } = settings;
  return messages.filter((m) => {
    const content = m.content.trimStart();
    if (!showMetaAgent && content.startsWith(META_AGENT_PREFIX)) return false;
    if (!showTaskAgent && content.startsWith(TASK_AGENT_PREFIX)) return false;
    if (!showQQ && content.startsWith(QQ_PREFIX)) return false;
    return true;
  });
}

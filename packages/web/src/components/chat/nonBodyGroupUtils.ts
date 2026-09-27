import type { Message } from '@/types';
import { isValidMessageTs } from '@/utils/messageTimestamp';

export interface ChildGroup {
  role: 'tool' | 'thinking';
  items: Message[];
  latestTs?: string;
}

export function groupChildren(items: Message[]): ChildGroup[] {
  const groups: ChildGroup[] = [];
  let current: ChildGroup | null = null;

  for (const item of items) {
    if (item.role !== 'tool' && item.role !== 'thinking') continue;
    if (!current || current.role !== item.role) {
      current = { role: item.role, items: [] };
      groups.push(current);
    }
    current.items.push(item);
    if (item.ts && isValidMessageTs(item.ts)) current.latestTs = item.ts;
  }

  return groups;
}

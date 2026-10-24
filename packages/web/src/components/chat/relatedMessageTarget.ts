import { fetchSessionNavigation } from '@/services/api';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { filterVisibleMessages, getNavigationKind } from './messageFilter';

export class RelatedMessageHiddenError extends Error {}

/** Same compact canonical pages/classifier as the rail. No text search or
 * legacy identity preparation. Bound work and fail explicitly on gaps/change. */
export async function resolveRelatedMessageTarget(sessionId: string, messageId: string, signal?: AbortSignal) {
  let before = 0;
  let epoch: string | undefined;
  let total: number | undefined;
  for (let count = 0; count < 200; count++) {
    const page = await fetchSessionNavigation(sessionId, before, 500, signal);
    const end = before || page.total;
    if (page.start !== Math.max(0, end - 500) || page.history.length !== end - page.start)
      throw new Error('导航索引未覆盖相关消息。');
    if (count && (page.historyEpoch !== epoch || page.total !== total))
      throw new Error('历史已变更，请刷新 Locker。');
    epoch = page.historyEpoch; total = page.total;
    const index = page.history.findIndex((row) => row.messageId === messageId);
    if (index >= 0) {
      const row = page.history[index]!;
      if (getNavigationKind(row) !== 'user') throw new Error('相关消息不再是导航中的 user 目标。');
      if (!filterVisibleMessages([row], useAppSettingsStore.getState()).length)
        throw new RelatedMessageHiddenError('相关消息被聊天显示设置隐藏，请先启用对应消息类型。');
      return { offset: page.start + index, total: page.total, messageId };
    }
    if (!page.hasMore || page.start === 0) throw new Error('相关消息已被删除、替换或索引未覆盖。');
    if (before && page.start >= before) throw new Error('导航索引分页无法继续。');
    before = page.start;
  }
  throw new Error('相关消息超出本次导航索引加载上限（100000 行）。');
}

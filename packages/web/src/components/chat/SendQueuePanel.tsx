import { useEffect, useMemo } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import type { QueuedMessage, AgentQueueItem } from '@/types';
import {
  Pencil,
  ArrowUp,
  ArrowDown,
  Trash2,
  Check,
  X,
  ClipboardList,
  Loader2,
  Bot,
} from 'lucide-react';

// 稳定的空数组引用（避免 selector 每次返回新数组导致无限重渲染）
const EMPTY_QUEUE: QueuedMessage[] = [];
const EMPTY_AGENT_QUEUE: AgentQueueItem[] = [];

const ROW_BTN =
  'rounded p-1 text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-colors disabled:opacity-30 disabled:cursor-not-allowed';

/** agent 队列行的 kind 徽章（agent task / agent report / agent qq）。 */
const AGENT_BADGE =
  'shrink-0 rounded border border-border-default bg-bg-tertiary px-1 py-px text-[10px] leading-tight text-text-secondary whitespace-nowrap';

/**
 * 待发送队列面板：单行截断 + hover 操作（编辑/上移/下移/删除）、行内编辑、
 * 批量拼接发送勾选、清空按钮、空态。默认折叠，由 InputRow 的 ^ 按钮控制。
 */
export function SendQueuePanel() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const panelOpen = useQueueStore((s) => s.panelOpen);
  const queue = useQueueStore((s) =>
    currentSessionId ? s.queues[currentSessionId] : undefined,
  );
  const edit = useQueueStore((s) =>
    currentSessionId ? s.edits[currentSessionId] : null,
  );
  const batchSend = useQueueStore((s) =>
    currentSessionId ? !!s.batchSend[currentSessionId] : false,
  );
  const sendingId = useQueueStore((s) => s.sendingId);

  const startEdit = useQueueStore((s) => s.startEdit);
  const remove = useQueueStore((s) => s.remove);
  const move = useQueueStore((s) => s.move);
  const clear = useQueueStore((s) => s.clear);
  const toggleBatchSend = useQueueStore((s) => s.toggleBatchSend);
  const updateEditDraft = useQueueStore((s) => s.updateEditDraft);
  const saveEdit = useQueueStore((s) => s.saveEdit);
  const cancelEdit = useQueueStore((s) => s.cancelEdit);

  const agentQueue = useQueueStore((s) =>
    currentSessionId ? s.agentQueues[currentSessionId] : undefined,
  );
  const removeAgentItem = useQueueStore((s) => s.removeAgentItem);
  const moveAgentItem = useQueueStore((s) => s.moveAgentItem);

  const queueItems = queue ?? EMPTY_QUEUE;
  const agentItems = agentQueue ?? EMPTY_AGENT_QUEUE;

  // 挂载 / session 切换时从 localStorage 恢复该 session 的队列并尝试自动发送
  useEffect(() => {
    useQueueStore.getState().loadForSession(currentSessionId);
  }, [currentSessionId]);

  // 编辑中的条目按原位置插回显示（视觉顺序与队列一致），编辑框输入实时反映到 store
  const displayItems = useMemo(() => {
    if (!edit) return queueItems;
    const items = queueItems.slice();
    items.splice(Math.min(edit.index, items.length), 0, {
      id: edit.id,
      text: edit.text,
      createdAt: edit.createdAt,
      status: 'pending',
    });
    return items;
  }, [queueItems, edit]);

  const total = queueItems.length + (edit ? 1 : 0);
  const batchSending = sendingId === '__batch__';

  return (
    <div
      className={`grid transition-[grid-template-rows] duration-200 ease-out ${
        panelOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
      }`}
    >
      <div className="overflow-hidden">
        <div className="px-3 pt-2 pb-1">
          {/* Header */}
          <div className="flex items-center gap-2 pb-1.5">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-text-secondary">
              <ClipboardList size={14} />
              待发送
              <span className="rounded-full bg-bg-tertiary px-1.5 py-0.5 text-[10px] leading-none text-text-secondary">
                {total}
              </span>
              {sendingId && (
                <Loader2 size={12} className="animate-spin text-accent" />
              )}
            </span>
            <div className="flex-1" />
            <label className="inline-flex items-center gap-1.5 text-xs text-text-secondary cursor-pointer select-none hover:text-text-primary transition-colors">
              <input
                type="checkbox"
                checked={batchSend}
                onChange={toggleBatchSend}
                className="accent-accent h-3.5 w-3.5 rounded border-border-default"
                title="勾选后 worker 空闲时把全部消息拼接成一条发出"
              />
              拼接发送
            </label>
            {total > 0 && (
              <button
                onClick={clear}
                className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs text-text-tertiary hover:bg-danger/10 hover:text-danger transition-colors"
                title="清空发送队列"
              >
                <Trash2 size={12} />
                清空
              </button>
            )}
          </div>

          {/* List */}
          <div className="queue-list-scroll max-h-[35vh] overflow-y-auto rounded-md border border-border-muted bg-bg-secondary/60">
            {total === 0 ? (
              <div className="px-3 py-3 text-center text-xs text-text-tertiary">
                队列为空 — worker 忙时发送的消息会排队等待
              </div>
            ) : (
              <div className="p-1">
                {displayItems.map((item, index) => {
                  const isEditing = edit?.id === item.id;
                  const isHead = index === 0;
                  const isLast = index === displayItems.length - 1;
                  const isSending = sendingId === item.id;

                  return (
                    <div
                      key={item.id}
                      className={`queue-row-in group flex items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors ${
                        isEditing ? 'bg-bg-tertiary/60' : 'hover:bg-bg-hover'
                      }`}
                    >
                      {isEditing ? (
                        <>
                          <textarea
                            autoFocus
                            value={edit.text}
                            rows={2}
                            onChange={(e) => updateEditDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                saveEdit();
                              } else if (e.key === 'Escape') {
                                e.preventDefault();
                                cancelEdit();
                              }
                            }}
                            className="flex-1 resize-none rounded border border-accent/50 bg-bg-tertiary px-2 py-1 text-sm text-text-primary placeholder:text-text-tertiary focus:outline-none"
                            placeholder="编辑消息…"
                          />
                          <span className="flex shrink-0 items-center gap-0.5">
                            <button
                              className={ROW_BTN + ' text-success hover:bg-success/10'}
                              onClick={saveEdit}
                              title="保存 (Enter)"
                            >
                              <Check size={14} />
                            </button>
                            <button
                              className={ROW_BTN + ' text-danger hover:bg-danger/10'}
                              onClick={cancelEdit}
                              title="取消 (Esc)"
                            >
                              <X size={14} />
                            </button>
                          </span>
                        </>
                      ) : (
                        <>
                          <span
                            className="flex-1 min-w-0 truncate text-text-primary"
                            title={item.text}
                          >
                            {item.text}
                          </span>
                          <span className="flex shrink-0 items-center gap-0.5 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 max-md:opacity-100">
                            {isSending && (
                              <Loader2 size={12} className="animate-spin text-accent" />
                            )}
                            <button
                              className={ROW_BTN}
                              onClick={() => startEdit(item.id)}
                              title="编辑（先出队，避免被自动发送）"
                            >
                              <Pencil size={12} />
                            </button>
                            <button
                              className={ROW_BTN}
                              disabled={isHead}
                              onClick={() => move(item.id, -1)}
                              title="上移"
                            >
                              <ArrowUp size={12} />
                            </button>
                            <button
                              className={ROW_BTN}
                              disabled={isLast}
                              onClick={() => move(item.id, 1)}
                              title="下移"
                            >
                              <ArrowDown size={12} />
                            </button>
                            <button
                              className={ROW_BTN + ' text-danger hover:bg-danger/10'}
                              onClick={() => remove(item.id)}
                              title="删除（不确认）"
                            >
                              <Trash2 size={12} />
                            </button>
                          </span>
                        </>
                      )}
                    </div>
                  );
                })}
                {batchSending && (
                  <div className="px-2 py-1.5 text-xs text-accent">
                    正在拼接发送全部消息…
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Agent queue group（只读展示 + 移动/删除，无编辑/批量/清空）。
              agent 队列为空或尚未加载时不显示该组。 */}
          {agentItems.length > 0 && (
            <div className="mt-2">
              <div className="flex items-center gap-1.5 pb-1.5">
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-text-secondary">
                  <Bot size={14} />
                  Agent 队列
                  <span className="rounded-full bg-bg-tertiary px-1.5 py-0.5 text-[10px] leading-none text-text-secondary">
                    {agentItems.length}
                  </span>
                </span>
              </div>
              <div className="queue-list-scroll max-h-[25vh] overflow-y-auto rounded-md border border-border-muted bg-bg-secondary/60">
                <div className="p-1">
                  {agentItems.map((item, index) => (
                    <div
                      key={item.id}
                      className="queue-row-in group flex items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors hover:bg-bg-hover"
                    >
                      <span className={AGENT_BADGE}>agent {item.kind}</span>
                      <span
                        className="flex-1 min-w-0 truncate text-text-primary"
                        title={item.text}
                      >
                        {item.text}
                      </span>
                      <span className="flex shrink-0 items-center gap-0.5 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 max-md:opacity-100">
                        <button
                          className={ROW_BTN}
                          disabled={index === 0}
                          onClick={() => moveAgentItem(item.id, -1)}
                          title="上移"
                        >
                          <ArrowUp size={12} />
                        </button>
                        <button
                          className={ROW_BTN}
                          disabled={index === agentItems.length - 1}
                          onClick={() => moveAgentItem(item.id, 1)}
                          title="下移"
                        >
                          <ArrowDown size={12} />
                        </button>
                        <button
                          className={ROW_BTN + ' text-danger hover:bg-danger/10'}
                          onClick={() => removeAgentItem(item.id)}
                          title="删除（不确认）"
                        >
                          <Trash2 size={12} />
                        </button>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

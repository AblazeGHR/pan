import { useEffect, useMemo } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import type { AgentQueueItem } from '@/types';
import { Pencil, ArrowUp, ArrowDown, Trash2, Check, X, ClipboardList } from 'lucide-react';

const EMPTY: AgentQueueItem[] = [];
const BUTTON = 'rounded p-1 text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-colors disabled:opacity-30 disabled:cursor-not-allowed';

function label(item: AgentQueueItem): string {
  if (item.source === 'user') return '用户';
  if (item.source === 'agent') return 'Agent';
  if (item.source === 'qq') return 'QQ';
  return item.source || item.kind;
}

export function SendQueuePanel() {
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const open = useQueueStore((state) => state.panelOpen);
  const items = (useQueueStore((state) => sessionId ? state.queues[sessionId] : undefined) ?? EMPTY)
    .filter((item) => item.meta?.dispatchState === 'queued');
  const edit = useQueueStore((state) => sessionId ? state.edits[sessionId] : null);
  const load = useQueueStore((state) => state.loadForSession);
  const startEdit = useQueueStore((state) => state.startEdit);
  const updateDraft = useQueueStore((state) => state.updateEditDraft);
  const saveEdit = useQueueStore((state) => state.saveEdit);
  const cancelEdit = useQueueStore((state) => state.cancelEdit);
  const remove = useQueueStore((state) => state.removeAgentItem);
  const move = useQueueStore((state) => state.moveAgentItem);
  const clear = useQueueStore((state) => state.clear);

  useEffect(() => { load(sessionId); }, [load, sessionId]);

  const displayItems = useMemo(() => {
    if (!edit) return items;
    const copy = items.slice();
    const index = copy.findIndex((item) => item.id === edit.id);
    if (index >= 0) {
      copy[index] = { ...copy[index]!, text: edit.text };
    } else {
      copy.splice(Math.min(edit.index, copy.length), 0, {
        id: edit.id, queueItemId: edit.id, kind: 'task', source: 'user', text: edit.text,
        createdAt: edit.createdAt, meta: { dispatchState: 'queued' },
      });
    }
    return copy;
  }, [edit, items]);

  return (
    <div className={`grid transition-[grid-template-rows] duration-200 ease-out ${open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
      <div className="overflow-hidden">
        <div className="px-3 pt-2 pb-1">
          <div className="flex items-center gap-2 pb-1.5">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-text-secondary">
              <ClipboardList size={14} /> 服务端队列
              <span className="rounded-full bg-bg-tertiary px-1.5 py-0.5 text-[10px] leading-none">{displayItems.length}</span>
            </span>
            <div className="flex-1" />
            {items.some((item) => item.meta?.dispatchState === 'queued') && (
              <button onClick={clear} className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs text-text-tertiary hover:bg-danger/10 hover:text-danger" title="清空仍在队列中的消息">
                <Trash2 size={12} /> 清空
              </button>
            )}
          </div>
          <div className="queue-list-scroll max-h-[45vh] overflow-y-auto rounded-md border border-border-muted bg-bg-secondary/60">
            {displayItems.length === 0 ? (
              <div className="px-3 py-3 text-center text-xs text-text-tertiary">队列为空</div>
            ) : (
              <div className="p-1">
                {displayItems.map((item, index) => {
                  const editing = edit?.id === item.id;
                  const editable = item.source === 'user';
                  return (
                    <div key={item.id} className="queue-row-in group flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-bg-hover">
                      {editing ? (
                        <>
                          <textarea autoFocus rows={2} value={edit.text} onChange={(event) => updateDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); saveEdit(); } else if (event.key === 'Escape') { event.preventDefault(); cancelEdit(); } }} className="flex-1 resize-none rounded border border-accent/50 bg-bg-tertiary px-2 py-1 text-sm text-text-primary focus:outline-none" />
                          <button className={BUTTON} onClick={saveEdit} title="保存"><Check size={14} /></button>
                          <button className={BUTTON} onClick={cancelEdit} title="取消"><X size={14} /></button>
                        </>
                      ) : (
                        <>
                          <span className="shrink-0 rounded border border-border-default bg-bg-tertiary px-1 py-px text-[10px] leading-tight text-text-secondary">{label(item)} {item.kind}</span>
                          <span className="flex-1 min-w-0 truncate text-text-primary" title={item.text}>{item.text}</span>
                          <span className="flex shrink-0 items-center gap-0.5 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 max-md:opacity-100">
                            {editable && <button className={BUTTON} onClick={() => startEdit(item.id)} title="编辑"><Pencil size={12} /></button>}
                            <button className={BUTTON} disabled={index === 0} onClick={() => move(item.id, -1)} title="上移"><ArrowUp size={12} /></button>
                            <button className={BUTTON} disabled={index === displayItems.length - 1} onClick={() => move(item.id, 1)} title="下移"><ArrowDown size={12} /></button>
                            <button className={BUTTON + ' text-danger hover:bg-danger/10'} onClick={() => void remove(item.id)} title="删除"><Trash2 size={12} /></button>
                          </span>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

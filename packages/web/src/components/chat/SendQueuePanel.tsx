import { useEffect, useMemo, useState } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import { useUIStore } from '@/stores/uiStore';
import type { AgentQueueItem } from '@/types';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { copyText } from '@/utils/clipboard';
import { Pencil, ArrowUp, ArrowDown, Trash2, Check, ClipboardList, Copy, Pause, Play, Lock, Unlock, Plus } from 'lucide-react';

const EMPTY: AgentQueueItem[] = [];
const BUTTON = 'rounded p-1 text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-colors disabled:opacity-30 disabled:cursor-not-allowed';
const PREVIEW_PRIMARY = 'inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent/90 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent';
const PREVIEW_SECONDARY = 'inline-flex items-center gap-1.5 rounded-md border border-border-default bg-bg-tertiary px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent';
const REPORT_PAUSE_BUTTON = 'inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-2 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50';

function label(item: AgentQueueItem): string {
  if (item.source === 'user') return '用户';
  if (item.source === 'agent') return 'Agent';
  if (item.source === 'qq') return 'QQ';
  return item.source || item.kind;
}

/** Read-only full-text preview of a single queued message. Reuses the shared
 *  Modal (Escape / close button / click-outside) and the clipboard helper.
 *  编辑入口走既有 startEdit 流程（确认设置 + 编辑锁完整正文），预览文本
 *  （可能为截断预览）绝不会作为编辑正文传入主输入框。 */
function QueueMessagePreview({ item, canEdit, onEdit, onClose }: {
  item: AgentQueueItem;
  canEdit: boolean;
  onEdit: (item: AgentQueueItem) => void;
  onClose: () => void;
}) {
  const showToast = useUIStore((state) => state.showToast);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const handleCopy = () => {
    void copyText(item.text)
      .then(() => setCopied(true))
      .catch(() => showToast('复制失败', 'error'));
  };

  return (
    <Modal open onClose={onClose} title="消息预览" size="md">
      <div className="flex flex-col gap-3">
        <span className="self-start rounded border border-border-default bg-bg-tertiary px-1 py-px text-[10px] leading-tight text-text-secondary">
          {label(item)} {item.kind}
        </span>
        <div
          tabIndex={0}
          role="region"
          aria-label="消息全文"
          className="max-h-[50vh] overflow-y-auto whitespace-pre-wrap break-words rounded border border-border-muted bg-bg-tertiary/40 p-2 text-sm text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {item.text}
        </div>
        <div className="flex justify-end gap-2">
          {canEdit && (
            <button type="button" onClick={() => onEdit(item)} className={PREVIEW_PRIMARY}>
              <Pencil size={14} />
              编辑
            </button>
          )}
          <button type="button" onClick={handleCopy} className={PREVIEW_PRIMARY}>
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? '已复制' : '复制'}
          </button>
          <button type="button" onClick={onClose} className={PREVIEW_SECONDARY}>关闭</button>
        </div>
      </div>
    </Modal>
  );
}

export function SendQueuePanel() {
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const readonlySession = useSessionStore((state) => state.sessions.find((session) => session.id === state.currentSessionId)?.readonlySession === true);
  const open = useQueueStore((state) => state.panelOpen);
  const queuePaused = useQueueStore((state) => sessionId ? state.queuePaused[sessionId] ?? false : false);
  const queuePauseLoaded = useQueueStore((state) => sessionId ? state.queuePauseLoaded[sessionId] ?? false : false);
  const queuePauseUpdating = useQueueStore((state) => sessionId ? state.queuePauseUpdating[sessionId] ?? false : false);
  const setQueuePaused = useQueueStore((state) => state.setQueuePaused);
  const reportsPaused = useQueueStore((state) => sessionId ? state.agentReportsPaused[sessionId] ?? false : false);
  const reportPauseLoaded = useQueueStore((state) => sessionId ? state.agentReportsPauseLoaded[sessionId] ?? false : false);
  const reportPauseUpdating = useQueueStore((state) => sessionId ? state.agentReportsPauseUpdating[sessionId] ?? false : false);
  const lockUpdating = useQueueStore((state) => sessionId ? state.queueLockUpdating[sessionId] ?? false : false);
  const lockedComposerMode = useQueueStore((state) => sessionId ? state.lockedComposerModes[sessionId] ?? false : false);
  const items = (useQueueStore((state) => sessionId ? state.queues[sessionId] : undefined) ?? EMPTY)
    .filter((item) => item.meta?.dispatchState === 'queued');
  const edit = useQueueStore((state) => sessionId ? state.edits[sessionId] : null);
  const load = useQueueStore((state) => state.loadForSession);
  const setReportsPaused = useQueueStore((state) => state.setAgentReportsPaused);
  const setItemLocked = useQueueStore((state) => state.setQueueItemLocked);
  const setItemsLocked = useQueueStore((state) => state.setQueueItemsLocked);
  const setLockedComposerMode = useQueueStore((state) => state.setLockedComposerMode);
  const startEdit = useQueueStore((state) => state.startEdit);
  const remove = useQueueStore((state) => state.removeAgentItem);
  const move = useQueueStore((state) => state.moveQueueItem);
  const clear = useQueueStore((state) => state.clear);
  const [preview, setPreview] = useState<AgentQueueItem | null>(null);

  const [confirmation, setConfirmation] = useState<{ sessionId: string; itemId: string } | null>(null);
  const requestEdit = async (item: AgentQueueItem) => {
    if (!sessionId || useQueueStore.getState().edits[sessionId]) return;
    const requestedSession = sessionId;
    await useAppSettingsStore.getState().ensureSettingsLoaded();
    if (useSessionStore.getState().currentSessionId !== requestedSession) return;
    if (!(item.kind === 'task' && item.source === 'user') && useAppSettingsStore.getState().notifications.confirmAgentSystemQueueEdit) {
      setConfirmation({ sessionId: requestedSession, itemId: item.id });
    } else startEdit(item.id);
  };
  useEffect(() => { load(sessionId); setConfirmation(null); }, [load, sessionId]);

  const displayItems = useMemo(() => {
    if (!edit) return items;
    const copy = items.slice();
    const index = copy.findIndex((item) => item.id === edit.id);
    if (index >= 0) {
      copy[index] = { ...copy[index]!, text: edit.text };
    }
    return copy;
  }, [edit, items]);

  return (
    <div className={`grid transition-[grid-template-rows] duration-200 ease-out ${open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
      <div className="overflow-hidden bg-bg-secondary">
        <div className="px-3 pt-2 pb-1">
          <div className="flex flex-wrap items-center gap-2 pb-1.5">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-text-secondary">
              <ClipboardList size={14} /> 服务端队列
              <span className="rounded-full bg-bg-tertiary px-1.5 py-0.5 text-[10px] leading-none">{displayItems.length}</span>
            </span>
            <div className="flex-1" />
            <button type="button" className={`${REPORT_PAUSE_BUTTON} ${lockedComposerMode
              ? 'border-danger bg-danger/10 text-danger hover:bg-danger/15'
              : 'border-border-default text-text-secondary hover:bg-bg-hover'}`}
              disabled={!sessionId} onClick={() => { if (sessionId) setLockedComposerMode(sessionId, !lockedComposerMode); }}
              aria-label={lockedComposerMode ? 'Cancel locked message mode' : 'New locked message'}
              aria-pressed={lockedComposerMode}
              title={lockedComposerMode ? 'Cancel locked message mode' : 'New locked message'}>
              <Plus size={12} aria-hidden="true" /> {lockedComposerMode ? 'cancel locked msg' : 'new locked msg'}
            </button>
            <button type="button" className={REPORT_PAUSE_BUTTON + ' border-border-default text-text-secondary hover:bg-bg-hover'}
              disabled={!sessionId || items.length === 0 || lockUpdating || reportPauseUpdating}
              onClick={() => { if (sessionId) void setItemsLocked(sessionId, true); }}
              aria-label="Lock all queued messages" title="Lock all queued messages">
              <Lock size={12} aria-hidden="true" /> Lock all
            </button>
            <button type="button" className={REPORT_PAUSE_BUTTON + ' border-border-default text-text-secondary hover:bg-bg-hover'}
              disabled={!sessionId || items.length === 0 || lockUpdating || reportPauseUpdating}
              onClick={() => { if (sessionId) void setItemsLocked(sessionId, false); }}
              aria-label="Unlock all queued messages" title="Unlock all queued messages">
              <Unlock size={12} aria-hidden="true" /> Unlock all
            </button>
            <button
              type="button"
              className={`${REPORT_PAUSE_BUTTON} ${reportsPaused
                ? 'border-accent/40 bg-accent/10 text-accent hover:bg-accent/15'
                : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'}`}
              aria-label={reportsPaused ? 'Resume reports' : 'Pause reports'}
              aria-pressed={reportsPaused}
              title={reportsPaused ? 'Resume reports' : 'Pause reports'}
              disabled={!sessionId || !reportPauseLoaded || reportPauseUpdating || lockUpdating}
              onClick={() => { if (sessionId) void setReportsPaused(sessionId, !reportsPaused); }}
            >
              {reportsPaused ? <Play size={12} aria-hidden="true" /> : <Pause size={12} aria-hidden="true" />}
              {reportsPaused ? 'Resume reports' : 'Pause reports'}
            </button>
            <button type="button"
              className={`${REPORT_PAUSE_BUTTON} ${queuePaused
                ? 'border-danger bg-danger/10 text-danger hover:bg-danger/15'
                : 'border-border-default text-text-secondary hover:bg-bg-hover'}`}
              aria-label={queuePaused ? 'Resume everything' : 'Pause everything'}
              aria-pressed={queuePaused}
              title="暂停或恢复当前 Session 的全部队列；已交接任务继续执行"
              disabled={!sessionId || !queuePauseLoaded || queuePauseUpdating}
              onClick={() => { if (sessionId) void setQueuePaused(sessionId, !queuePaused); }}>
              {queuePaused ? <Play size={12} aria-hidden="true" /> : <Pause size={12} aria-hidden="true" />}
              {queuePaused ? 'Resume everything' : 'Pause everything'}
            </button>
            {items.some((item) => item.meta?.dispatchState === 'queued') && (
              <button onClick={clear} className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs text-text-tertiary hover:bg-danger/10 hover:text-danger" title="清空仍在队列中的消息">
                <Trash2 size={12} /> 清空
              </button>
            )}
          </div>
          <div className="queue-list-scroll max-h-[45dvh] overflow-y-auto rounded-md border border-border-muted bg-bg-secondary">
            {displayItems.length === 0 ? (
              <div className="px-3 py-3 text-center text-xs text-text-tertiary">队列为空</div>
            ) : (
              <div className="p-1">
                {displayItems.map((item, index) => {
                  const editable = item.meta?.dispatchState === 'queued';
                  const locked = item.meta?.locked === true;
                  return (
                    <div key={item.id} className="queue-row-in group flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-bg-hover">
                      <>
                          <span className="shrink-0 rounded border border-border-default bg-bg-tertiary px-1 py-px text-[10px] leading-tight text-text-secondary">{label(item)} {item.kind}</span>
                          <button
                            type="button"
                            onClick={() => setPreview(item)}
                            title={item.text}
                            aria-haspopup="dialog"
                            className="flex-1 min-w-0 truncate rounded text-left text-text-primary transition-colors hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                          >
                            {item.text}
                          </button>
                          <span className="flex shrink-0 items-center gap-0.5 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 max-md:opacity-100">
                            {editable && <button className={BUTTON} disabled={!!edit || readonlySession} onClick={() => void requestEdit(item)} title="编辑"><Pencil size={12} /></button>}
                            <button className={BUTTON} disabled={index === 0} onClick={() => move(item.id, -1)} title="上移"><ArrowUp size={12} /></button>
                            <button className={BUTTON} disabled={index === displayItems.length - 1} onClick={() => move(item.id, 1)} title="下移"><ArrowDown size={12} /></button>
                            <button className={BUTTON + ' text-danger hover:bg-danger/10'} onClick={() => void remove(item.id)} title="删除"><Trash2 size={12} /></button>
                          </span>
                      </>
                      <button type="button"
                        className={`${BUTTON} shrink-0 ${locked ? 'border border-danger text-danger hover:bg-danger/10' : 'border border-border-default'}`}
                        disabled={!sessionId || lockUpdating || reportPauseUpdating}
                        onClick={() => { if (sessionId) void setItemLocked(sessionId, item.id, !locked); }}
                        aria-label={locked ? `Unlock queued message ${index + 1}` : `Lock queued message ${index + 1}`}
                        aria-pressed={locked}
                        title={locked ? 'Unlock' : 'Lock'}>
                        {locked ? <Lock size={12} aria-hidden="true" /> : <Unlock size={12} aria-hidden="true" />}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
      <Modal open={!!confirmation && confirmation.sessionId === sessionId} onClose={() => setConfirmation(null)} title="确认修改" size="sm">
        <p className="text-sm text-text-primary">是否确认修改agent/系统消息？</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmation(null)}>取消</Button>
          <Button onClick={() => {
            if (confirmation && useSessionStore.getState().currentSessionId === confirmation.sessionId) startEdit(confirmation.itemId);
            setConfirmation(null);
          }}>确认</Button>
        </div>
      </Modal>
      {preview && (
        <QueueMessagePreview
          item={preview}
          canEdit={!!sessionId && !edit && !readonlySession && preview.meta?.dispatchState === 'queued'}
          onEdit={(item) => {
            // 先关阅读窗再进入既有编辑流程；lease/确认失败时 toast 可观察，
            // 队列原文仍在服务端与面板行中，不会丢失。
            setPreview(null);
            void requestEdit(item);
          }}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}


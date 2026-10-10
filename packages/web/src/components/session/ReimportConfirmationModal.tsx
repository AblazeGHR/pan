import { useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { ReimportError } from '@/services/api';
import { useReimportConfirmationStore } from '@/stores/reimportConfirmationStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

const reasons: Record<string, string> = {
  missing_identity: '缺少可验证的 Pan 消息身份',
  changed_native_item: '原生消息已修改',
  ambiguous_match: '重复正文或身份不一致，无法唯一匹配',
  missing_or_changed: '原生历史中缺失或正文已变化',
};

export function ReimportConfirmationModal() {
  const { pending, open, close } = useReimportConfirmationStore();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [mustRecheck, setMustRecheck] = useState(false);
  if (!pending) return null;
  const dismiss = () => { if (!busy) { close(); setError(''); setMustRecheck(false); } };
  const submit = async (force: boolean) => {
    setBusy(true);
    setError('');
    try {
      await useSessionStore.getState().reimport(pending.sessionId, pending.workspaceId,
        force ? pending.conflict.confirmationToken : undefined);
      useUIStore.getState().showToast('Session reimported');
      close();
      setMustRecheck(false);
    } catch (caught) {
      if (caught instanceof ReimportError && caught.conflict) {
        open({ ...pending, conflict: caught.conflict });
        setMustRecheck(false);
        setError(caught.conflict.stale ? '历史已变化。已重新检查，请查看更新后的摘要并再次确认。' : '请查看摘要并确认是否覆盖。');
      } else {
        setError(caught instanceof Error ? caught.message : 'Reimport failed');
        setMustRecheck(caught instanceof ReimportError && ['reimport_stale', 'readonly_session'].includes(caught.code ?? ''));
      }
    } finally { setBusy(false); }
  };
  return (
    <Modal open onClose={dismiss} title="Reimport：来源无法无损匹配">
      <p className="text-sm text-text-primary">{pending.sessionName}：{pending.conflict.count} 条 Pan 来源记录无法匹配。</p>
      <p className="mt-2 text-sm text-text-secondary">强行覆盖将以原生历史替换当前历史。能匹配的消息身份和来源会保留；无法匹配的 Pan 消息及其任务、来源、envelope 和附件信息将丢失。取消会保留当前历史。</p>
      <ul className="mt-3 space-y-2 text-sm text-text-primary">
        {pending.conflict.items.map((item, index) => (
          <li key={`${item.position}-${index}`} className="rounded border border-border-default p-2">
            <div>位置 {item.position} · {item.source} · {reasons[item.reason] ?? item.reason}</div>
            <p className="mt-1 break-words whitespace-pre-wrap text-text-secondary">{item.preview || '（空正文）'}</p>
          </li>
        ))}
      </ul>
      {pending.conflict.omittedCount > 0 && <p className="mt-2 text-sm">另有 {pending.conflict.omittedCount} 条未展示。</p>}
      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button disabled={busy} onClick={dismiss}>取消</Button>
        {mustRecheck
          ? <Button disabled={busy} onClick={() => void submit(false)}>重新检查</Button>
          : <Button variant="danger" disabled={busy} onClick={() => void submit(true)}>{busy ? '正在导入…' : '确认强行覆盖'}</Button>}
      </div>
    </Modal>
  );
}

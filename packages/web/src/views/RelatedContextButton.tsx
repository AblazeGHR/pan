import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CornerUpLeft } from 'lucide-react';
import { resolveRelatedMessageTarget } from '@/components/chat/relatedMessageTarget';
import { useRelatedMessageStore } from '@/stores/relatedMessageStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useNativeControlStore } from '@/stores/nativeControlStore';
import type { Locker } from './lockerModel';

export function RelatedContextButton({ ownerSessionId, relatedMessageId, source, hidden }: {
  ownerSessionId: string; relatedMessageId?: string | null; source?: Locker; hidden?: boolean;
}) {
  const navigate = useNavigate();
  const showMetaAgent = useAppSettingsStore((s) => s.showMetaAgent);
  const showTaskAgent = useAppSettingsStore((s) => s.showTaskAgent);
  const showQQ = useAppSettingsStore((s) => s.showQQ);
  const [reason, setReason] = useState('正在检查相关消息…');
  const [busy, setBusy] = useState(false);
  const opening = useRef<AbortController | null>(null);
  useEffect(() => () => opening.current?.abort(), [ownerSessionId, relatedMessageId]);
  useEffect(() => {
    if (!relatedMessageId || hidden) return;
    if (source?.available === false) { setReason('所属 Session 已删除或不可用。'); return; }
    const controller = new AbortController();
    setReason('正在检查相关消息…');
    void resolveRelatedMessageTarget(ownerSessionId, relatedMessageId, controller.signal)
      .then(() => { if (!controller.signal.aborted) setReason(''); })
      .catch((error) => { if (!controller.signal.aborted) setReason(String(error)); });
    return () => controller.abort();
  }, [ownerSessionId, relatedMessageId, source?.available, hidden, showMetaAgent, showTaskAgent, showQQ]);
  if (!relatedMessageId) return null;
  const open = async () => {
    opening.current?.abort();
    const controller = new AbortController();
    opening.current = controller;
    setBusy(true);
    try {
      await resolveRelatedMessageTarget(ownerSessionId, relatedMessageId, controller.signal);
      if (controller.signal.aborted) return;
      await useSessionStore.getState().selectSession(ownerSessionId, controller.signal);
      if (controller.signal.aborted) return;
      if (useSessionStore.getState().currentSessionId !== ownerSessionId) throw new Error('所属 Session 不可用。');
      useNativeControlStore.getState().showTerminal(ownerSessionId, false);
      useRelatedMessageStore.getState().open(ownerSessionId, relatedMessageId);
      navigate('/');
    } catch (error) { if (!controller.signal.aborted) setReason(String(error)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <div className="locker-related-context" title={reason || '跳转到相关上下文'}>
    <button type="button" aria-label="跳转到相关上下文" title={reason || '跳转到相关上下文'}
      disabled={!!reason || busy || source?.available === false} onClick={() => void open()}>
      <CornerUpLeft size={14} aria-hidden="true" /> 跳转到相关上下文
    </button>
    {reason && <small role="status">{reason}</small>}
  </div>;
}

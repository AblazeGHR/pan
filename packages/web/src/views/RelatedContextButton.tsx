import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CornerUpLeft } from 'lucide-react';
import { RelatedMessageHiddenError, resolveRelatedMessageTarget } from '@/components/chat/relatedMessageTarget';
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
  const visibilityKey = `${showMetaAgent}:${showTaskAgent}:${showQQ}`;
  const [failure, setFailure] = useState<{ reason: string; hiddenAt?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const opening = useRef<AbortController | null>(null);
  useEffect(() => {
    setFailure(null);
    setBusy(false);
    return () => { opening.current?.abort(); opening.current = null; };
  }, [ownerSessionId, relatedMessageId]);
  if (!relatedMessageId) return null;
  const unavailable = source?.available === false;
  const hiddenBySettings = failure?.hiddenAt === visibilityKey;
  const reason = unavailable ? '所属 Session 已删除或不可用。' : failure?.hiddenAt && !hiddenBySettings ? '' : failure?.reason ?? '';
  const open = async () => {
    if (unavailable || hidden || hiddenBySettings || opening.current) return;
    const controller = new AbortController();
    opening.current = controller;
    setBusy(true);
    setFailure(null);
    try {
      const target = await resolveRelatedMessageTarget(ownerSessionId, relatedMessageId, controller.signal);
      if (controller.signal.aborted) return;
      await useSessionStore.getState().selectSession(ownerSessionId, controller.signal);
      if (controller.signal.aborted) return;
      if (useSessionStore.getState().currentSessionId !== ownerSessionId) throw new Error('所属 Session 不可用。');
      useNativeControlStore.getState().showTerminal(ownerSessionId, false);
      useRelatedMessageStore.getState().open({ sessionId: ownerSessionId, ...target });
      navigate('/');
    } catch (error) {
      if (!controller.signal.aborted) setFailure({ reason: String(error),
        ...(error instanceof RelatedMessageHiddenError ? { hiddenAt: visibilityKey } : {}) });
    } finally {
      if (opening.current === controller) opening.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  return <div className="locker-related-context" title={reason || '跳转到相关上下文'}>
    <button type="button" aria-label="跳转到相关上下文" title={reason || '跳转到相关上下文'}
      disabled={unavailable || hidden || hiddenBySettings} aria-busy={busy} onClick={() => void open()}>
      <CornerUpLeft size={14} aria-hidden="true" /> 跳转到相关上下文
    </button>
    {reason && <small role="status">{reason}</small>}
  </div>;
}

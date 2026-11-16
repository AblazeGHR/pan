import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { EditorReference } from '@/stores/editorStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useRelatedMessageStore } from '@/stores/relatedMessageStore';
import { useNativeControlStore } from '@/stores/nativeControlStore';
import { useUIStore } from '@/stores/uiStore';
import { resolveRelatedMessageTarget } from '@/components/chat/relatedMessageTarget';

export function EditorReturnReference({ reference }: { reference: EditorReference }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); }, [reference]);
  const open = async () => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    const selected = useSessionStore.getState().currentSessionId;
    const unsubscribe = useSessionStore.subscribe((state) => {
      if (state.currentSessionId !== selected) controller.abort();
    });
    try {
      const target = await resolveRelatedMessageTarget(reference.sessionId, reference.messageId, controller.signal, false);
      if (controller.signal.aborted) return;
      // Editor tabs belong to the selected Session. Avoid switching its root
      // (and discarding drafts) if this tab's context is no longer selected.
      if (useSessionStore.getState().currentSessionId !== reference.sessionId)
        throw new Error('来源 Session 已变化，请重新打开引用链接。');
      useNativeControlStore.getState().showTerminal(reference.sessionId, false);
      useRelatedMessageStore.getState().open({ sessionId: reference.sessionId, ...target });
      navigate('/');
    } catch (error) {
      if (!controller.signal.aborted) useUIStore.getState().showToast(`返回引用位置失败：${String(error)}`, 'error');
    } finally {
      unsubscribe();
      if (request.current === controller) { request.current = null; setBusy(false); }
    }
  };
  return <button type="button" disabled={busy} onClick={() => void open()}
    className="shrink-0 px-2 text-xs text-text-secondary hover:text-text-primary bg-bg-secondary border-b border-border-default"
  >返回引用位置</button>;
}

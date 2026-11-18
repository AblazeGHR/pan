import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { EditorReference } from '@/stores/editorStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useRelatedMessageStore } from '@/stores/relatedMessageStore';
import { useNativeControlStore } from '@/stores/nativeControlStore';
import { useUIStore } from '@/stores/uiStore';
import { resolveRelatedMessageTarget } from '@/components/chat/relatedMessageTarget';

/** Return-to-source action for a tab opened from a Chat message. It renders in
 * the Editor file operation row, beside save/copy/open/reveal, so the file
 * actions stay together on both narrow and desktop layouts. */
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
    className="flex h-7 shrink-0 items-center rounded px-1.5 text-[11px] text-text-secondary hover:bg-bg-tertiary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
  >返回引用位置</button>;
}

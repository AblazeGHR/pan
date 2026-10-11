/**
 * Deep link between a completion notification (or URL) and a Session.
 *
 * The browser side already shows a Web Notification on `worker.result` when the
 * session's `browser` notification setting is on; the event carries the session
 * id at the top level. Clicking the notification must focus Pan and jump to
 * that session — and the same `?session=<id>` URL contract doubles as the cold
 * start entry (bookmark / future service-worker `openWindow` / external
 * helper), so a session can be addressed from outside the SPA.
 */
import { useCallback, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

export const SESSION_DEEPLINK_PARAM = 'session';

/**
 * Conservative allowlist for a session reference: real ids are `ses_<hex>`,
 * demo ids are slugs. Anything else is rejected before it can reach the store
 * or the URL, so a hand-crafted link can never smuggle arbitrary strings in.
 */
const SESSION_REF_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

export function isValidSessionRef(value: string | null | undefined): value is string {
  return typeof value === 'string' && SESSION_REF_PATTERN.test(value);
}

const MISSING_SESSION_MESSAGE = '通知对应的会话不存在或已被删除，无法跳转';

/**
 * Notification click handler: focus Pan and select the session.
 *
 * Returns false (after a toast) when the session is gone — deletion between
 * delivery and click degrades to a message instead of dead navigation.
 */
export function openSessionFromNotification(sessionId: string): boolean {
  if (!isValidSessionRef(sessionId)) return false;
  const store = useSessionStore.getState();
  if (!store.sessions.some((session) => session.id === sessionId)) {
    useUIStore.getState().showToast(MISSING_SESSION_MESSAGE, 'warning');
    return false;
  }
  try {
    // Same-origin windows may be focused from the notification click listener
    // (the spec encourages focus() from within the click event). Best effort:
    // a blocked focus must not break the jump.
    window.focus();
  } catch {
    // ignore
  }
  void store.selectSession(sessionId);
  // A click can arrive while another view (jobs/terminals/editor) is open;
  // bring chat back. Lazy: the router singleton already exists at runtime and
  // this keeps the util out of the router's static import cycle.
  void import('@/router')
    .then(({ router }) => {
      void router.navigate('/');
    })
    .catch(() => {});
  return true;
}

/**
 * Cold start: `?session=<id>` (e.g. `/?session=ses_...`) selects the session
 * once the session list has loaded, then removes the parameter. A missing
 * session degrades with a toast — and only after the first list load settled,
 * so an in-flight load is never mistaken for "deleted".
 */
export function useSessionDeepLinkParam(): void {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get(SESSION_DEEPLINK_PARAM);
  const sessions = useSessionStore((s) => s.sessions);
  const sessionsLoading = useSessionStore((s) => s.sessionsLoading);
  const loadSeq = useSessionStore((s) => s._loadSeq);

  const clearParam = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete(SESSION_DEEPLINK_PARAM);
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  useEffect(() => {
    if (!raw) return;
    if (!isValidSessionRef(raw)) {
      useUIStore.getState().showToast('会话链接无效', 'warning');
      clearParam();
      return;
    }
    if (sessions.some((session) => session.id === raw)) {
      void useSessionStore.getState().selectSession(raw);
      clearParam();
      return;
    }
    // The list may not have arrived yet; wait for the first load to settle
    // before declaring the session missing.
    if (loadSeq > 0 && !sessionsLoading) {
      useUIStore.getState().showToast(MISSING_SESSION_MESSAGE, 'warning');
      clearParam();
    }
  }, [raw, sessions, sessionsLoading, loadSeq, clearParam]);
}

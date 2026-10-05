import { useEditorStore } from '@/stores/editorStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { AttachmentLocation } from '@/types/attachment';

/**
 * Server-owned attachment ids only (att_…/upload_…). UI occurrence ids,
 * display names, and arbitrary hrefs are never treated as read permission.
 */
const ATTACHMENT_ID_RE =
  /\/api\/attachments\/(?:ref|editor)?\/?(att_[A-Za-z0-9]{32}|upload_[A-Za-z0-9]{32}(?:\.[A-Za-z0-9._-]{1,32})?)/;

export function editorAttachmentIdFromHref(href: string | undefined): string | undefined {
  return href ? ATTACHMENT_ID_RE.exec(href)?.[1] : undefined;
}

export interface ComposerEditorOpenTarget {
  /** Server resource id; never the local composer occurrence id. */
  serverAttachmentId?: string;
  href?: string;
  location?: AttachmentLocation;
}

export interface ComposerEditorOpenContext {
  /** Session captured at click time; the open is abandoned if it changes. */
  sessionId: string;
  workdir: string;
  navigate: (path: string) => void;
}

/**
 * Open a composer attachment with the existing Editor flow: resolve the real
 * file path through the server-owned attachment registry, keep the editor
 * root pointed at the Session workdir, then reuse openFile (which routes
 * image paths to the image preview itself). Local fields such as displayName
 * or a stale `path` never become read permission, so no path is guessed here.
 */
export async function openComposerAttachmentInEditor(
  target: ComposerEditorOpenTarget,
  context: ComposerEditorOpenContext,
): Promise<void> {
  const attachmentId = target.serverAttachmentId || editorAttachmentIdFromHref(target.href);
  if (!attachmentId) {
    useUIStore.getState().showToast('该附件没有可打开的文件路径', 'error');
    return;
  }
  try {
    const response = await fetch(
      `/api/attachments/editor/${encodeURIComponent(attachmentId)}`
      + `?session_id=${encodeURIComponent(context.sessionId)}`,
    );
    const metadata = await response.json() as { ok?: boolean; path?: unknown };
    if (!response.ok || metadata.ok === false || typeof metadata.path !== 'string') {
      throw new Error('附件引用已失效');
    }
    // The registry lookup is async: switching Sessions while it is in flight
    // must not repoint the editor root or open a tab under the wrong Session.
    if (useSessionStore.getState().currentSessionId !== context.sessionId) return;
    const editor = useEditorStore.getState();
    if (editor.sessionId !== context.sessionId || editor.workdir !== context.workdir) {
      // setRoot updates the editor identity synchronously; let its directory
      // read run alongside the file read (same pattern as message file links).
      void editor.setRoot(context.sessionId, context.workdir);
    }
    const location = target.location
      ? { ...target.location, path: metadata.path }
      : undefined;
    const opened = await useEditorStore.getState().openFile(metadata.path, location);
    if (opened) context.navigate('/editor');
  } catch (error) {
    useUIStore.getState().showToast(
      `打开文件失败：${error instanceof Error ? error.message : '附件引用已失效'}`,
      'error',
    );
  }
}

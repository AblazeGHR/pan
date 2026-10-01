import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { useSessionStore } from '@/stores/sessionStore';
import { fetchRewindJobStatus } from '@/services/api';
import type { Message, RewindScope, StreamEvent } from '@/types';

export const REWIND_STAGE_LABELS: Record<string, string> = {
  starting: '\u51c6\u5907\u4e2d',
  resuming: '\u6253\u5f00\u4f1a\u8bdd',
  'rewinding-files': '\u56de\u6eda\u6587\u4ef6',
  truncating: '\u622a\u65ad\u5bf9\u8bdd',
  completed: '\u5b8c\u6210',
  failed: '\u5931\u8d25',
};

const MODAL_STAGE_LABELS: Record<string, string> = {
  starting: '\u6b63\u5728\u542f\u52a8\u56de\u6eda\u2026',
  resuming: '\u6b63\u5728\u6062\u590d\u4f1a\u8bdd\u526f\u672c\u2026',
  'rewinding-files': '\u6b63\u5728\u56de\u6eda\u4ee3\u7801\u2026',
  truncating: '\u6b63\u5728\u622a\u65ad\u5bf9\u8bdd',
  completed: '\u5b8c\u6210',
  failed: '\u5931\u8d25',
};

const SCOPE_OPTIONS: Array<{ value: RewindScope; label: string; hint: string }> = [
  { value: 1, label: '对话 + 代码', hint: '对话与代码一起回滚到这条消息' },
  { value: 2, label: '仅对话', hint: '只回滚对话，文件保持当前状态' },
  { value: 3, label: '仅代码', hint: '只回滚代码，对话保持完整' },
];

/** Extract mutated file paths from one tool message body. Handles both the
 *  FileChange shape ("tool call: FileChange\nargs: {...}" / "FileChange({...})",
 *  mirrored from ToolGroup) and cbc's own mutating file tools (Write/Edit
 *  carry "file_path", which is what real cbc sessions actually contain.
 *  Scanning only FileChange made the affected-files list always empty for
 *  cbc sessions, so the modal claimed "no code changes" on every anchor. */
function extractFileChangePaths(content: string): string[] {
  if (!content) return [];
  let name = '';
  let argsText = '';
  const callMatch = content.match(/^tool call:\s*(.+?)(?:\r?\n|\r)args:\s*([\s\S]*)$/);
  if (callMatch) {
    name = callMatch[1]?.split('\n')[0]?.trim() || '';
    argsText = callMatch[2]?.trim() || '';
  } else {
    const modernMatch = content.match(/^([^(]+)\(([\s\S]*)\)$/);
    if (!modernMatch) return [];
    name = (modernMatch[1] || '').trim();
    argsText = modernMatch[2] || '';
  }
  if (!argsText) return [];
  const normalizedName = name.trim().toLowerCase();
  try {
    const args = JSON.parse(argsText) as Record<string, unknown>;
    if (!args || typeof args !== 'object') return [];
    if (normalizedName === 'filechange') {
      if (!Array.isArray(args.changes)) return [];
      return args.changes
        .map((change) => {
          if (!change || typeof change !== 'object') return '';
          const record = change as Record<string, unknown>;
          return typeof record.path === 'string' ? record.path : '';
        })
        .filter(Boolean);
    }
    if (['write', 'edit', 'multiedit', 'notebookedit'].includes(normalizedName)) {
      const path = args.file_path ?? args.filePath;
      return typeof path === 'string' && path ? [path] : [];
    }
    return [];
  } catch {
    return [];
  }
}

interface RewindConfirmModalProps {
  message: Message;
  onClose: () => void;
}

export function RewindConfirmModal({ message, onClose }: RewindConfirmModalProps) {
  const [scope, setScope] = useState<RewindScope>(1);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const activeRewinds = useSessionStore((s) => s.activeRewinds);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const rewindCurrentMessage = useSessionStore((s) => s.rewindCurrentMessage);
  const minimizeRewind = useSessionStore((s) => s.minimizeRewind);
  const dismissRewind = useSessionStore((s) => s.dismissRewind);
  const confirmRef = useRef<HTMLButtonElement>(null);
  // The rewind this modal is showing progress for. Keyed to the session the
  // confirm was started from so a concurrent job on ANOTHER session can never
  // hijack this popup.
  const activeRewind = activeRewinds.find((item) => item.sessionId === currentSessionId) ?? null;

  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  // Files changed after the anchor message = the code this rewind may
  // restore. Computed lazily on open (currentMessages scan per bubble render
  // would be wasted work).
  const affectedFiles = useMemo(() => {
    const messages = useSessionStore.getState().currentMessages;
    const index = messages.indexOf(message);
    if (index < 0) return [] as string[];
    const paths = new Set<string>();
    for (const item of messages.slice(index + 1)) {
      if (item.role !== 'tool') continue;
      for (const path of extractFileChangePaths(item.content)) paths.add(path);
    }
    return [...paths];
  }, [message]);

  const running = activeRewind?.status === 'running';
  const failed = activeRewind?.status === 'failed';

  // Polling fallback: while the job runs, refetch the persisted rewind
  // record every 2s so a dropped WS event can never leave the job without
  // progress. WS events remain the fast path; re-applying an already-seen
  // stage is idempotent in applyRewindProgress. Runs independently of
  // whether the popup is open ? closing it must not stop the fallback.
  const jobId = activeRewind?.jobId || '';
  const jobSessionId = activeRewind?.sessionId || '';
  useEffect(() => {
    if (!running || !jobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const record = await fetchRewindJobStatus(jobSessionId, jobId);
        if (cancelled) return;
        useSessionStore.getState().applyRewindProgress({
          type: 'session.rewind.progress',
          jobId,
          sessionId: jobSessionId,
          stage: record.stage || undefined,
          error: typeof record.error === 'string' ? record.error : record.error?.message,
          newSessionId: record.newSessionId || undefined,
        } as StreamEvent);
      } catch {
        // Best-effort fallback; the next tick retries.
      }
    };
    const timer = setInterval(() => void poll(), 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [running, jobId, jobSessionId]);

  const codeScopeUnavailable = scope !== 2 && affectedFiles.length === 0;

  const onConfirm = async () => {
    setSubmitError(null);
    try {
      await rewindCurrentMessage(message, scope);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : '\u64a4\u56de\u5931\u8d25');
    }
  };

  // 「后台运行」 closes the popup, keeping the job alive; this is not a cancel.
  // entry is removed, so WS events / polling continue and completion still
  // jumps and prefills.
  const handleRunInBackground = () => {
    if (activeRewind?.jobId) minimizeRewind(activeRewind.jobId);
    onClose();
  };

  const handleClose = () => {
    if (running) return;
    if (failed) dismissRewind(activeRewind?.jobId);
    onClose();
  };

  return (
    <Modal open onClose={handleClose} title="撤回确认" size="sm">
      <div className="space-y-3 text-sm text-text-secondary">
        {failed ? (
          <>
            <p className="text-danger">{activeRewind?.error || '撤回失败'}</p>
            <div className="flex flex-wrap justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" onClick={handleClose}>
                关闭
              </Button>
            </div>
          </>
        ) : running ? (
          <>
            <div className="flex items-center gap-2 py-4 text-text-primary">
              <Loader2 size={16} className="animate-spin" />
              <span>{MODAL_STAGE_LABELS[activeRewind?.stage || 'starting'] || '正在处理…'}</span>
            </div>
            <p className="text-xs text-text-tertiary">
              撤回正在后台运行，关闭此窗口不会取消任务；完成后可在输入框上方的状态条中查看进度。
            </p>
            <div className="flex flex-wrap justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" onClick={handleRunInBackground}>
                后台运行
              </Button>
            </div>
          </>
        ) : (
          <>
            <p>
              撤回会创建新的分支会话，原会话保持不变。请选择回滚范围：
            </p>
            <div className="space-y-1.5">
              {SCOPE_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className="flex cursor-pointer items-start gap-2 rounded border border-border-default px-2 py-1.5 hover:bg-bg-tertiary"
                >
                  <input
                    type="radio"
                    name="rewind-scope"
                    className="mt-0.5"
                    checked={scope === option.value}
                    onChange={() => setScope(option.value)}
                  />
                  <span>
                    <span className="block text-text-primary">{option.label}</span>
                    <span className="block text-xs text-text-tertiary">{option.hint}</span>
                  </span>
                </label>
              ))}
            </div>
            {affectedFiles.length > 0 ? (
              <div>
                <p className="mb-1 text-xs text-text-tertiary">
                  这条消息之后被修改的文件：
                </p>
                <div className="max-h-32 overflow-y-auto rounded border border-border-default bg-bg-tertiary px-2 py-1.5">
                  {affectedFiles.map((path) => (
                    <code key={path} className="block break-all text-xs text-text-primary">
                      {path}
                    </code>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-xs text-text-tertiary">这条消息之后未检测到文件变更。</p>
            )}
            {codeScopeUnavailable && (
              <p className="text-xs text-warning">
                该检查点没有代码变更，无法仅回滚代码；请改选「对话 + 代码」或「仅对话」。
              </p>
            )}
            <p className="text-xs text-text-tertiary">
              仅回滚 cbc 自己文件编辑工具造成的改动；bash 命令与手动编辑的文件不会回滚。
            </p>
            {submitError && <p className="text-xs text-danger">{submitError}</p>}
            <div className="flex flex-wrap justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" onClick={handleClose}>
                取消
              </Button>
              <Button
                ref={confirmRef}
                type="button"
                variant="primary"
                onClick={() => void onConfirm()}
              >
                确认撤回
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}


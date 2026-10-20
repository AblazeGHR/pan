import { useEffect, useRef, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { handoffSession } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

interface Props {
  session: Session | null;
  onClose: () => void;
}

// Unmount on close and key by identity: each opening owns its draft and request.
export function HandoffSessionModal({ session, onClose }: Props) {
  return session ? <HandoffForm key={session.id} session={session} onClose={onClose} /> : null;
}

function HandoffForm({ session, onClose }: { session: Session; onClose: () => void }) {
  const [brief, setBrief] = useState('');
  const [override, setOverride] = useState(false);
  const [original, setOriginal] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(true);
  const submitting = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  const submit = async () => {
    if (!brief.trim() || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      await handoffSession(session.id, {
        handoffPrompt: brief,
        copySettings: true,
        overrideOriginalPrompt: override,
        ...(override ? { originalPrompt: original } : {}),
      });
    } catch (e) {
      if (active.current) {
        setError(e instanceof Error ? e.message : '交接失败');
        setBusy(false);
        submitting.current = false;
      }
      return;
    }
    if (!active.current) return;
    onClose();
    // A committed handoff must never become a retryable form after refresh fails.
    try {
      await useSessionStore.getState().loadSessions({ throwOnError: true });
    } catch {
      useUIStore.getState().showToast('交接已完成，但会话列表刷新失败', 'error');
    }
  };

  return (
    <Modal open onClose={onClose} title={`替身交接 · ${session.name}`} size="lg">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <p className="text-sm text-text-secondary">
          创建全新 CLI 会话，接管关系与设置，源会话归档。仅携带本次简报，不继承旧简报或对话历史。
        </p>
        <label className="block text-sm">
          交接简报（必填）
          <textarea
            required
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            disabled={busy}
            className="mt-1 w-full rounded border border-border-default bg-bg-primary p-2"
            rows={6}
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={override}
            onChange={(e) => setOverride(e.target.checked)}
            disabled={busy}
          />
          覆写原始提示（originalPrompt）
        </label>
        <p className="text-sm text-text-secondary">
          默认继承源会话的原始提示。启用后使用下方文本；留空会清空原始提示。不会修改源会话提示。
        </p>
        <label className="block text-sm">
          新的原始提示
          <textarea
            value={original}
            onChange={(e) => setOriginal(e.target.value)}
            disabled={!override || busy}
            className="mt-1 w-full rounded border border-border-default bg-bg-primary p-2 disabled:opacity-50"
            rows={5}
          />
        </label>
        <p className="text-sm text-text-secondary">
          新会话的系统提示由本次交接简报与选定的原始提示组成。
        </p>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button
            type="submit"
            disabled={busy || !brief.trim()}
            className="rounded bg-accent px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {busy ? '交接中…' : '确认交接'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

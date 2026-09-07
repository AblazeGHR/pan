import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Copy, Download, MessageSquare } from 'lucide-react';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useCurrentSession } from '@/stores/sessionStore';
import { useEditorStore } from '@/stores/editorStore';
import { useUIStore } from '@/stores/uiStore';
import { copyText } from '@/utils/clipboard';

interface EditorFileTopBarProps {
  path: string;
}

export function EditorFileTopBar({ path }: EditorFileTopBarProps) {
  const { isMobile } = useMediaQuery();
  const currentSession = useCurrentSession();
  const downloadFile = useEditorStore((s) => s.downloadFile);
  const requestChatAttachment = useUIStore((s) => s.requestChatAttachment);
  const showToast = useUIStore((s) => s.showToast);
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const resetCopiedRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (resetCopiedRef.current) clearTimeout(resetCopiedRef.current);
  }, []);

  const handleCopy = async () => {
    try {
      await copyText(path);
      setCopied(true);
      showToast('完整路径已复制');
      if (resetCopiedRef.current) clearTimeout(resetCopiedRef.current);
      resetCopiedRef.current = setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
      showToast('复制路径失败', 'error');
    }
  };

  const handleAddToChat = () => {
    if (!currentSession?.id) {
      showToast('当前没有可用的 session', 'error');
      return;
    }
    requestChatAttachment(currentSession.id, path);
    showToast('文件已加入聊天附件');
    navigate('/');
  };

  return (
    <div
      data-testid="editor-file-topbar"
      className="flex min-h-8 items-center gap-2 border-b border-border-default bg-bg-primary px-2 py-1"
    >
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-secondary" title={path}>
        {path}
      </span>
      <button
        type="button"
        aria-label={copied ? '完整路径已复制' : '复制完整路径'}
        title={copied ? '完整路径已复制' : '复制完整路径'}
        onClick={() => void handleCopy()}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary"
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
      {isMobile && (
        <>
          <button
            type="button"
            aria-label="下载当前文件"
            title="下载当前文件"
            onClick={() => downloadFile(path)}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary"
          >
            <Download size={14} />
          </button>
          <button
            type="button"
            aria-label="加入聊天"
            title="加入聊天"
            onClick={handleAddToChat}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary"
          >
            <MessageSquare size={14} />
          </button>
        </>
      )}
    </div>
  );
}

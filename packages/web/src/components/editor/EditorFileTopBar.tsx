import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Copy, Download, MessageSquare } from 'lucide-react';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useCurrentSession } from '@/stores/sessionStore';
import { useEditorStore } from '@/stores/editorStore';
import { useUIStore } from '@/stores/uiStore';
import { copyText } from '@/utils/clipboard';

interface EditorFileTopBarProps {
  /** Session-relative path used by backend operations and attachment queue. */
  operationPath: string;
}

export function getDisplayPath(workdir: string | null | undefined, operationPath: string): string {
  if (!workdir) return operationPath;

  const separator = workdir.includes('\\') ? '\\' : '/';
  const normalizedWorkdir = workdir.replace(/[\\/]+$/, '');
  const normalizedPath = operationPath.replace(/[\\/]+/g, separator).replace(/^[\\/]+/, '');
  if (!normalizedWorkdir) {
    // Keep a POSIX (or bare Windows) root instead of dropping its separator.
    return `${separator}${normalizedPath}`;
  }
  return normalizedWorkdir ? `${normalizedWorkdir}${separator}${normalizedPath}` : normalizedPath;
}

export function EditorFileTopBar({ operationPath }: EditorFileTopBarProps) {
  const { isMobile } = useMediaQuery();
  const currentSession = useCurrentSession();
  const downloadFile = useEditorStore((s) => s.downloadFile);
  const requestChatAttachment = useUIStore((s) => s.requestChatAttachment);
  const showToast = useUIStore((s) => s.showToast);
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const resetCopiedRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const displayPath = getDisplayPath(currentSession?.workdir, operationPath);
  const copyKey = `${currentSession?.id ?? ''}\u0000${currentSession?.workdir ?? ''}\u0000${operationPath}`;
  const copyGenerationRef = useRef(0);
  const copyKeyRef = useRef(copyKey);
  // Invalidate pending copy callbacks during render so a promise resolving
  // before the dependency effect runs cannot update the next file.
  if (copyKeyRef.current !== copyKey) {
    copyKeyRef.current = copyKey;
    copyGenerationRef.current += 1;
  }
  const copyLabel = currentSession?.workdir ? '复制完整路径' : '复制文件路径';
  const copiedLabel = currentSession?.workdir ? '完整路径已复制' : '文件路径已复制';

  useEffect(() => {
    setCopied(false);
    if (resetCopiedRef.current) {
      clearTimeout(resetCopiedRef.current);
      resetCopiedRef.current = null;
    }
    return () => {
      copyGenerationRef.current += 1;
      if (resetCopiedRef.current) {
        clearTimeout(resetCopiedRef.current);
        resetCopiedRef.current = null;
      }
    };
  }, [copyKey]);

  const handleCopy = async () => {
    const copyGeneration = copyGenerationRef.current;
    const copyKeyAtStart = copyKey;
    try {
      await copyText(displayPath);
      if (copyGenerationRef.current !== copyGeneration || copyKeyRef.current !== copyKeyAtStart)
        return;
      setCopied(true);
      showToast(`${currentSession?.workdir ? '完整路径' : '文件路径'}已复制`);
      if (resetCopiedRef.current) clearTimeout(resetCopiedRef.current);
      resetCopiedRef.current = setTimeout(() => {
        if (copyGenerationRef.current !== copyGeneration || copyKeyRef.current !== copyKeyAtStart)
          return;
        setCopied(false);
        resetCopiedRef.current = null;
      }, 1600);
    } catch {
      if (copyGenerationRef.current !== copyGeneration || copyKeyRef.current !== copyKeyAtStart)
        return;
      setCopied(false);
      showToast('复制路径失败', 'error');
    }
  };

  const handleAddToChat = () => {
    if (!currentSession?.id) {
      showToast('当前没有可用的 session', 'error');
      return;
    }
    requestChatAttachment(currentSession.id, operationPath);
    showToast('文件已加入聊天附件');
    navigate('/');
  };

  return (
    <div
      data-testid="editor-file-topbar"
      className="flex min-h-8 items-center gap-2 border-b border-border-default bg-bg-primary px-2 py-1"
    >
      <span
        className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-secondary"
        title={displayPath}
      >
        {displayPath}
      </span>
      <button
        type="button"
        aria-label={copied ? copiedLabel : copyLabel}
        title={copied ? copiedLabel : copyLabel}
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
            onClick={() => downloadFile(operationPath)}
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

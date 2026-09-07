import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Copy, Download, MessageSquare, Save as SaveIcon } from 'lucide-react';
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

  // Pick the separator from the root syntax, then normalize every internal
  // separator in the workdir and operation path to that same style. In
  // particular, do not collapse the two leading separators of a UNC path.
  const driveRoot = /^([A-Za-z]:)([\\/])/.exec(workdir);
  const uncRoot = /^(\\\\|\/\/)/.exec(workdir);
  const separator = driveRoot?.[2] ?? uncRoot?.[1]?.[0] ?? (workdir.startsWith('/') ? '/' : workdir.includes('\\') ? '\\' : '/');
  const normalizedRest = (value: string) => value.replace(/[\\/]+/g, separator).replace(new RegExp(`${separator === '\\' ? '\\\\' : '\\/'}+$`), '');

  let normalizedWorkdir: string;
  if (driveRoot) {
    const rest = normalizedRest(workdir.slice(3));
    normalizedWorkdir = rest ? `${driveRoot[1]}${separator}${rest}` : `${driveRoot[1]}${separator}`;
  } else if (uncRoot) {
    const rest = normalizedRest(workdir.slice(2));
    normalizedWorkdir = `${separator}${separator}${rest}`;
  } else if (workdir.startsWith('/')) {
    const rest = normalizedRest(workdir.slice(1));
    normalizedWorkdir = rest ? `/${rest}` : '/';
  } else {
    normalizedWorkdir = normalizedRest(workdir);
  }

  const normalizedPath = operationPath.replace(/[\\/]+/g, separator).replace(/^[\\/]+/, '');
  if (!normalizedWorkdir || normalizedWorkdir === separator) return `${separator}${normalizedPath}`;
  if (normalizedWorkdir.endsWith(separator)) return `${normalizedWorkdir}${normalizedPath}`;
  return `${normalizedWorkdir}${separator}${normalizedPath}`;
}

export function EditorFileTopBar({ operationPath }: EditorFileTopBarProps) {
  const { isMobile } = useMediaQuery();
  const currentSession = useCurrentSession();
  const downloadFile = useEditorStore((s) => s.downloadFile);
  const requestSave = useEditorStore((s) => s.requestSave);
  const isDirty = useEditorStore((s) => s.dirty.has(operationPath));
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
        aria-label="保存文件"
        title={isDirty ? '保存文件' : '文件没有未保存修改'}
        disabled={!isDirty}
        onClick={() => requestSave(operationPath)}
        className="flex h-7 shrink-0 items-center gap-1 rounded px-1.5 text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
      >
        <SaveIcon size={14} />
        <span className="hidden sm:inline text-[11px]">保存</span>
      </button>
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

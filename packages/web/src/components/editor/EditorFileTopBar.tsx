import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Copy, Download, ExternalLink, FolderOpen, MessageSquare, Save as SaveIcon } from 'lucide-react';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useCurrentSession } from '@/stores/sessionStore';
import { useEditorStore } from '@/stores/editorStore';
import { useCapability, usePlatformStore } from '@/stores/platformStore';
import { useUIStore } from '@/stores/uiStore';
import { copyText } from '@/utils/clipboard';
import { openFileExternally, revealFileExternally } from '@/services/api';

interface EditorFileTopBarProps {
  /** Session-relative path used by backend operations and attachment queue. */
  operationPath: string;
  imagePreview?: boolean;
  previewFailed?: boolean;
  imageDisplayName?: string;
  imageDownloadHref?: string;
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

  // Markdown links may open a server-absolute file. Do not prepend the
  // current Session workdir to an already absolute operation path.
  // Editor roots (CWD/Workspace/Temp) contribute absolute paths, so a POSIX
  // absolute operation path must not be re-parented under the workdir.
  const operationIsUnc = /^(\\\\|\/\/)/.test(operationPath);
  const operationIsDriveAbsolute = /^[A-Za-z]:[\\/]/.test(operationPath);
  if (operationIsUnc) {
    return `${separator}${separator}${operationPath.slice(2).replace(/[\\/]+/g, separator)}`;
  }
  if (operationIsDriveAbsolute) {
    return operationPath.replace(/[\\/]+/g, separator);
  }
  if (operationPath.startsWith('/') && workdir.startsWith('/')) {
    // A POSIX-workdir Session browsing an absolute POSIX root (CWD/Workspace/
    // Temp): show the absolute path instead of re-parenting it under the
    // workdir. A bare leading '/' with a Windows workdir stays workdir-relative
    // (existing Markdown-link behavior).
    return operationPath.replace(/\\/g, '/');
  }
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

function revealUnavailableReason(platform: string | null): string {
  switch (platform) {
    case 'windows':
      return '当前环境没有可用的资源管理器';
    case 'darwin':
      return '当前环境没有可用的 Finder';
    case 'linux':
      return '当前环境没有可用的桌面文件管理器';
    default:
      return '当前平台不支持在系统资源管理器中定位';
  }
}

export function EditorFileTopBar({
  operationPath,
  imagePreview = false,
  previewFailed = false,
  imageDisplayName,
  imageDownloadHref,
}: EditorFileTopBarProps) {
  const { isMobile } = useMediaQuery();
  const currentSession = useCurrentSession();
  const downloadFile = useEditorStore((s) => s.downloadFile);
  const requestSave = useEditorStore((s) => s.requestSave);
  const isDirty = useEditorStore((s) => s.dirty.has(operationPath));
  const platform = usePlatformStore((s) => s.platform);
  const revealAvailable = useCapability('fileManager.reveal');
  const requestChatAttachment = useUIStore((s) => s.requestChatAttachment);
  const showToast = useUIStore((s) => s.showToast);
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const [openingExternal, setOpeningExternal] = useState(false);
  const [revealingExternal, setRevealingExternal] = useState(false);
  const resetCopiedRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const displayPath = imageDisplayName || getDisplayPath(currentSession?.workdir, operationPath);
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

  const handleDownload = () => {
    if (!imageDownloadHref) {
      downloadFile(operationPath);
      return;
    }
    const link = document.createElement('a');
    link.href = imageDownloadHref;
    link.download = '';
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  // Attachment previews use a virtual tab key ("attachment:<session>:<id>")
  // with no server filesystem path, so neither OS action can run. Dirty
  // buffers do have a path: these actions intentionally use the on-disk file
  // and never flush the buffer.
  const hasRealPath = Boolean(operationPath) && !operationPath.startsWith('attachment:');
  const externalActionDisabledReason = !currentSession?.id
    ? '当前没有可用的 session'
    : !hasRealPath
      ? '该文件没有可用的本地路径'
      : null;
  // The reveal action additionally needs a runtime desktop file manager. A
  // failed/absent capability probe stays available (Windows baseline) and the
  // server's 501 is surfaced as a toast.
  const revealDisabledReason =
    externalActionDisabledReason ??
    (revealAvailable
      ? null
      : `无法在系统资源管理器中定位：${revealUnavailableReason(platform)}`);
  // OS actions always hand the on-disk file to the platform. Unsaved edits are
  // never written implicitly; say so instead of hiding the action.
  const externalOpenTitle = isDirty
    ? '用系统默认方式打开磁盘上的版本（未保存的修改不会写入）'
    : '用系统默认方式打开';

  const handleOpenExternal = async () => {
    const sessionId = currentSession?.id;
    if (!sessionId || externalActionDisabledReason) return;
    setOpeningExternal(true);
    try {
      await openFileExternally(sessionId, operationPath);
      if (isDirty) showToast('已打开磁盘上的版本，未保存的修改未写入');
    } catch (error) {
      showToast(
        `打开失败：${error instanceof Error ? error.message : '未知错误'}`,
        'error',
      );
    } finally {
      setOpeningExternal(false);
    }
  };

  const handleRevealExternal = async () => {
    const sessionId = currentSession?.id;
    if (!sessionId || revealDisabledReason) return;
    setRevealingExternal(true);
    try {
      await revealFileExternally(sessionId, operationPath);
    } catch (error) {
      showToast(
        `定位失败：${error instanceof Error ? error.message : '未知错误'}`,
        'error',
      );
    } finally {
      setRevealingExternal(false);
    }
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
        disabled={!isDirty || previewFailed}
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
      <button
        type="button"
        aria-label="用系统默认方式打开"
        title={externalActionDisabledReason ?? externalOpenTitle}
        disabled={Boolean(externalActionDisabledReason) || openingExternal}
        onClick={() => void handleOpenExternal()}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
      >
        <ExternalLink size={14} />
      </button>
      <button
        type="button"
        aria-label="在系统资源管理器中定位"
        title={revealDisabledReason ?? '在系统资源管理器中定位'}
        disabled={Boolean(revealDisabledReason) || revealingExternal}
        onClick={() => void handleRevealExternal()}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
      >
        <FolderOpen size={14} />
      </button>
      {(isMobile || imagePreview || previewFailed) && (
        <>
          <button
            type="button"
            aria-label="下载当前文件"
            title="下载当前文件"
            onClick={handleDownload}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary"
          >
            <Download size={14} />
          </button>
          {!imageDownloadHref && <button
            type="button"
            aria-label="加入聊天"
            title="加入聊天"
            onClick={handleAddToChat}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary"
          >
            <MessageSquare size={14} />
          </button>}
        </>
      )}
    </div>
  );
}

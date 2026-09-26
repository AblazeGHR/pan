import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { DirectoryInput } from '@/components/session/DirectoryInput';
import { fetchDirectories } from '@/services/api';

interface AddDirectoryModalProps {
  open: boolean;
  mode: 'workspace' | 'temp';
  /** Workspace display name for the confirmation copy; omitted when unassigned. */
  workspaceName?: string;
  onClose: () => void;
  onSubmit: (path: string) => Promise<void> | void;
}

interface ResolvedDirectory {
  path: string;
  valid: boolean;
  loading: boolean;
  error: string | null;
}

const EMPTY_RESOLVED: ResolvedDirectory = { path: '', valid: false, loading: false, error: null };

/**
 * Add one existing absolute server directory as an Editor root.
 *
 * The server validates existence/is-dir on submit; this modal mirrors that over
 * the same `/api/directories` listing the New Session picker uses, and never
 * creates, uploads, or copies anything.
 */
export function AddDirectoryModal({
  open,
  mode,
  workspaceName,
  onClose,
  onSubmit,
}: AddDirectoryModalProps) {
  const [value, setValue] = useState('');
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [resolved, setResolved] = useState<ResolvedDirectory>(EMPTY_RESOLVED);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  // Reset the flow whenever the dialog is (re)opened.
  useEffect(() => {
    if (!open) return;
    setValue('');
    setSelectedPath(null);
    setResolved(EMPTY_RESOLVED);
    setSubmitting(false);
    setSubmitError(null);
  }, [open, mode]);

  const candidate = selectedPath ?? value.trim();

  useEffect(() => {
    const requestId = ++requestIdRef.current;
    if (!candidate) {
      setResolved(EMPTY_RESOLVED);
      return;
    }
    setResolved({ path: candidate, valid: false, loading: true, error: null });
    fetchDirectories(candidate)
      .then((result) => {
        if (requestId !== requestIdRef.current) return;
        setResolved({ path: result.current || candidate, valid: true, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (requestId !== requestIdRef.current) return;
        setResolved({
          path: candidate,
          valid: false,
          loading: false,
          error: error instanceof Error && error.message ? error.message : '目录不存在或不可用',
        });
      });
    return () => {
      if (requestId === requestIdRef.current) requestIdRef.current += 1;
    };
  }, [candidate]);

  const handleSubmit = async () => {
    if (!resolved.valid || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onSubmit(resolved.path);
      onClose();
    } catch (error) {
      setSubmitError(error instanceof Error && error.message ? error.message : '添加目录失败');
    } finally {
      setSubmitting(false);
    }
  };

  const title = mode === 'workspace' ? '为工作区添加目录' : '添加临时目录';

  return (
    <Modal open={open} onClose={onClose} title={title} size="md">
      <div className="space-y-3 text-sm text-text-secondary">
        <p className="text-xs text-text-tertiary" data-testid="add-directory-hint">
          {mode === 'workspace'
            ? `目录将保存到工作区${workspaceName ? `「${workspaceName}」` : ''}，该工作区的所有 Session 都会显示它。只记录路径，不复制或创建磁盘目录。`
            : '临时目录仅保存在当前浏览器内存中，刷新页面即消失，且对所有 Session 可见。只记录路径，不复制或创建磁盘目录。'}
        </p>

        <DirectoryInput
          value={value}
          onChange={(next) => {
            setValue(next);
            setSelectedPath(null);
          }}
          onSelect={(path) => {
            setValue(path);
            setSelectedPath(path);
          }}
          selectDirectories
          inputTestId="add-directory-input"
        />

        {resolved.loading && (
          <div className="flex items-center gap-2 text-xs text-text-tertiary">
            <Loader2 size={14} className="animate-spin" /> 校验中…
          </div>
        )}
        {!resolved.loading && resolved.valid && (
          <p className="text-xs text-text-tertiary" data-testid="add-directory-resolved">
            将添加：<span className="text-text-primary">{resolved.path}</span>
          </p>
        )}
        {!resolved.loading && !resolved.valid && resolved.error && (
          <p className="text-xs text-danger" data-testid="add-directory-error">
            无法使用该目录：{resolved.error}
          </p>
        )}
        {submitError && <p className="text-xs text-danger">{submitError}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            type="button"
            variant="primary"
            onClick={() => void handleSubmit()}
            disabled={!resolved.valid || submitting}
          >
            {submitting ? '添加中…' : '添加目录'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

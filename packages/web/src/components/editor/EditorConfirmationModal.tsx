import { useEffect, useRef } from 'react';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { useEditorStore } from '@/stores/editorStore';

export function EditorConfirmationModal() {
  const request = useEditorStore((state) => state.pendingConfirmation);
  const confirm = useEditorStore((state) => state.confirmPendingOperation);
  const cancel = useEditorStore((state) => state.cancelPendingOperation);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!request) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    confirmRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' || event.defaultPrevented) return;
      event.preventDefault();
      void confirm();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previousFocusRef.current?.focus();
      previousFocusRef.current = null;
    };
  }, [request, confirm]);

  if (!request) return null;

  const isDelete = request.kind === 'delete';
  const actionLabel = isDelete ? '删除文件' : '保存文件';
  return (
    <Modal
      open
      onClose={cancel}
      title={isDelete ? '确认删除文件' : '确认保存文件'}
      size="sm"
    >
      <div className="space-y-3 text-sm text-text-secondary">
        <p>
          {isDelete
            ? '确定要删除以下路径吗？此操作会从工作区删除文件或空目录。'
            : '确定要保存以下文件吗？当前编辑内容将写入磁盘。'}
        </p>
        <code className="block break-all rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary">
          {request.path}
        </code>
        <div className="flex flex-wrap justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={cancel}>
            取消
          </Button>
          <Button
            ref={confirmRef}
            type="button"
            variant={isDelete ? 'danger' : 'primary'}
            onClick={() => void confirm()}
          >
            {actionLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

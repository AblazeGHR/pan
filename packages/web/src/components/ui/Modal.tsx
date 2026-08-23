import { type ReactNode, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  className?: string;
  size?: 'sm' | 'md' | 'lg' | 'xl';
}

const sizeClasses: Record<string, string> = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
};

export function Modal({ open, onClose, title, children, className = '', size = 'md' }: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  // Render through a portal to <body>. The modals are opened from the sidebar,
  // whose mobile container uses `transform` (translateX) — a transformed
  // ancestor becomes the containing block for `position: fixed` descendants,
  // which would otherwise clamp the overlay to the sidebar width and squash
  // the content into a vertical line.
  return createPortal(
    <div
      ref={overlayRef}
      className="modal-overlay fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
    >
      <div
        className={`modal-card bg-bg-secondary border border-border-default rounded-lg shadow-xl w-full max-h-[85vh] flex flex-col overflow-hidden ${sizeClasses[size]} ${className}`}
      >
        {title && (
          <div className="flex items-center justify-between border-b border-border-default px-4 py-3">
            <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
            <button
              onClick={onClose}
              aria-label="Close"
              className="text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary p-1 rounded transition-colors"
            >
              <X size={14} />
            </button>
          </div>
        )}
        <div className="p-4 overflow-y-auto flex-1">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

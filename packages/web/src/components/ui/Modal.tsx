import { type ReactNode, useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  className?: string;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  /** Enable viewport-filling presentation on small screens for this caller. */
  mobileFullscreen?: boolean;
}

const sizeClasses: Record<string, string> = {
  sm: 'max-w-[24rem]',
  md: 'max-w-[32rem]',
  lg: 'max-w-[42rem]',
  xl: 'max-w-[56rem]',
};

export function Modal({
  open,
  onClose,
  title,
  children,
  className = '',
  size = 'md',
  mobileFullscreen = false,
}: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

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
      className={`modal-overlay fixed inset-0 z-40 flex items-center justify-center bg-black/50 ${mobileFullscreen ? 'modal-overlay--mobile-fullscreen p-0 md:p-4' : 'p-4'}`}
      onClick={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        className={`modal-card bg-bg-secondary border border-border-default rounded-lg shadow-xl w-full max-h-[85vh] flex flex-col overflow-hidden ${sizeClasses[size]} ${mobileFullscreen ? 'modal-card--mobile-fullscreen max-md:h-[100dvh] max-md:max-h-[100dvh] max-md:max-w-none max-md:rounded-none max-md:border-0' : ''} ${className}`}
      >
        {title && (
          <div className="flex items-center justify-between border-b border-border-default px-4 py-3">
            <h2 id={titleId} className="text-sm font-semibold text-text-primary">{title}</h2>
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

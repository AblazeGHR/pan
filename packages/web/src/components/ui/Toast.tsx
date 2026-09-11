import { useEffect, useState } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { CheckCircle, AlertCircle, X } from 'lucide-react';

const TOAST_DURATION = 3000;

export function ToastContainer() {
  const { toastQueue, dismissToast } = useUIStore();
  // Ids currently playing the exit animation — dismissed only after it ends
  const [exitingIds, setExitingIds] = useState<Set<string>>(new Set());

  // Auto-dismiss after TOAST_DURATION, preceded by the exit animation
  useEffect(() => {
    if (toastQueue.length === 0) return;
    const timers = toastQueue
      .filter((t) => !exitingIds.has(t.id))
      .map((t) =>
        setTimeout(() => {
          setExitingIds((prev) => {
            if (prev.has(t.id)) return prev;
            const next = new Set(prev);
            next.add(t.id);
            return next;
          });
        }, TOAST_DURATION),
      );
    return () => timers.forEach(clearTimeout);
  }, [toastQueue, exitingIds]);

  const startExit = (id: string) => {
    setExitingIds((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  };

  const copyAndDismiss = (id: string, message: string) => {
    startExit(id);
    void (async () => {
      try {
        await navigator.clipboard?.writeText(message);
      } catch {
        // Clipboard access is best-effort; dismissal must remain reliable.
      }
    })();
  };

  if (toastQueue.length === 0) return null;

  const iconFor = (type: string) => {
    if (type === 'error') return <AlertCircle size={16} />;
    return <CheckCircle size={16} />;
  };

  return (
    <div
      className="fixed z-50 flex flex-col gap-2 pointer-events-none"
      style={{
        bottom: `max(16px, var(--safe-bottom))`,
        right: `max(16px, var(--safe-right))`,
      }}
    >
      {toastQueue.map((toast) => (
        <div
          key={toast.id}
          className={`toast-enter rounded-lg px-4 py-2.5 shadow-panel pointer-events-auto flex items-center gap-2.5 transition-all ${
            exitingIds.has(toast.id) ? 'toast-exit' : ''
          } ${toast.type === 'error' ? 'bg-danger text-white' : 'bg-accent text-white'}`}
          role="alert"
          aria-live="polite"
          onAnimationEnd={() => {
            if (exitingIds.has(toast.id)) dismissToast(toast.id);
          }}
        >
          <button
            type="button"
            aria-label={`Copy toast message: ${toast.message}`}
            onClick={() => copyAndDismiss(toast.id, toast.message)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                copyAndDismiss(toast.id, toast.message);
              }
            }}
            className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
          >
            {iconFor(toast.type)}
            <span className="text-sm">{toast.message}</span>
          </button>
          <button
            type="button"
            aria-label="Dismiss toast"
            onClick={() => startExit(toast.id)}
            className="opacity-70 hover:opacity-100 transition-opacity shrink-0"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

import { useUIStore } from '@/stores/uiStore';
import { CheckCircle, AlertCircle, X } from 'lucide-react';

export function ToastContainer() {
  const { toastQueue, dismissToast } = useUIStore();

  if (toastQueue.length === 0) return null;

  const iconFor = (type: string) => {
    if (type === 'error') return <AlertCircle size={16} />;
    return <CheckCircle size={16} />;
  };

  return (
    <div className="fixed z-50 flex flex-col gap-2 pointer-events-none" style={{ bottom: `max(16px, var(--safe-bottom))`, right: `max(16px, var(--safe-right))` }}>
      {toastQueue.map((toast) => (
        <div
          key={toast.id}
          className={`toast-enter rounded-lg px-4 py-2.5 shadow-panel pointer-events-auto flex items-center gap-2.5 transition-all ${
            toast.type === 'error'
              ? 'bg-danger text-white'
              : 'bg-accent text-white'
          }`}
          role="alert"
        >
          {iconFor(toast.type)}
          <span className="text-sm flex-1">{toast.message}</span>
          <button
            onClick={() => dismissToast(toast.id)}
            className="opacity-70 hover:opacity-100 transition-opacity shrink-0"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

import { useUIStore } from '@/stores/uiStore';

export function ToastContainer() {
  const { toastQueue, dismissToast } = useUIStore();

  if (toastQueue.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toastQueue.map((toast) => (
        <div
          key={toast.id}
          className={`rounded-lg px-4 py-2 text-sm shadow-lg transition-all duration-300 ${
            toast.type === 'error'
              ? 'bg-danger text-white'
              : 'bg-accent text-white'
          }`}
          onClick={() => dismissToast(toast.id)}
          role="alert"
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
}

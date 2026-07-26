import { create } from 'zustand';
import type { ToastMessage } from '@/types';

interface UIStore {
  settingsOpen: boolean;
  toastQueue: ToastMessage[];
  bubbleViewEnabled: boolean;

  toggleSettings: () => void;
  closeSettings: () => void;
  showToast: (message: string, type?: ToastMessage['type']) => void;
  dismissToast: (id: string) => void;
  toggleBubbleView: () => void;
}

let toastCounter = 0;

export const useUIStore = create<UIStore>((set, get) => ({
  settingsOpen: false,
  toastQueue: [],
  bubbleViewEnabled: true,

  toggleSettings: () => {
    set((s) => ({ settingsOpen: !s.settingsOpen }));
  },

  closeSettings: () => {
    set({ settingsOpen: false });
  },

  showToast: (message, type = 'info') => {
    const id = `toast-${++toastCounter}`;
    set((s) => ({
      toastQueue: [...s.toastQueue, { id, message, type }],
    }));
    setTimeout(() => {
      get().dismissToast(id);
    }, 3000);
  },

  dismissToast: (id) => {
    set((s) => ({
      toastQueue: s.toastQueue.filter((t) => t.id !== id),
    }));
  },

  toggleBubbleView: () => {
    set((s) => ({ bubbleViewEnabled: !s.bubbleViewEnabled }));
  },
}));

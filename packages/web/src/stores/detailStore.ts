import { create } from 'zustand';

interface DetailTarget {
  type: 'thinking' | 'tool';
  content: string;
  title?: string;
}

interface DetailStore {
  detailTarget: DetailTarget | null;
  panelWidth: number;
  openDetail: (target: DetailTarget) => void;
  closeDetail: () => void;
  setPanelWidth: (w: number) => void;
}

function loadPanelWidth(): number {
  try {
    const saved = localStorage.getItem('pan:panelWidth');
    if (saved) {
      const n = parseInt(saved, 10);
      if (!isNaN(n) && n >= 320 && n <= 600) return n;
    }
  } catch {
    // ignore
  }
  return 460;
}

export const useDetailStore = create<DetailStore>((set) => ({
  detailTarget: null,
  panelWidth: loadPanelWidth(),

  openDetail: (target: DetailTarget) => {
    set({ detailTarget: target });
  },

  closeDetail: () => {
    set({ detailTarget: null });
  },

  setPanelWidth: (w: number) => {
    const clamped = Math.max(320, Math.min(600, Math.round(w)));
    set({ panelWidth: clamped });
    try {
      localStorage.setItem('pan:panelWidth', String(clamped));
    } catch {
      // ignore
    }
  },
}));

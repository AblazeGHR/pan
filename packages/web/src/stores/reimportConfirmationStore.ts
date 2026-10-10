import { create } from 'zustand';
import type { ReimportConflict } from '@/services/api';

export interface ReimportConfirmation {
  sessionId: string;
  sessionName: string;
  workspaceId: string | undefined;
  conflict: ReimportConflict;
}

export const useReimportConfirmationStore = create<{
  pending: ReimportConfirmation | null;
  open: (pending: ReimportConfirmation) => void;
  close: () => void;
}>((set) => ({
  pending: null,
  open: (pending) => set({ pending }),
  close: () => set({ pending: null }),
}));

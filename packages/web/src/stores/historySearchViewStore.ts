import { create } from 'zustand';

/** Transient view constraint only: never persisted into ordinary list settings. */
export const useHistorySearchViewStore = create<{
  matchingSessionIds: Set<string> | null;
  preparing: boolean;
  update: (ids: string[] | null, preparing?: boolean) => void;
}>((set) => ({
  matchingSessionIds: null,
  preparing: false,
  update: (ids, preparing = false) => set({
    matchingSessionIds: ids === null ? null : new Set(ids), preparing,
  }),
}));

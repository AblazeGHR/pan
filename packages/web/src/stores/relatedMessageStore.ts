import { create } from 'zustand';

export interface RelatedMessageTarget {
  sessionId: string;
  messageId: string;
  offset: number;
  total: number;
}

/** Ephemeral click-resolved handoff; shared jump checks loaded message identity. */
export const useRelatedMessageStore = create<{
  target: RelatedMessageTarget | null;
  open: (target: RelatedMessageTarget) => void;
}>((set) => ({ target: null, open: (target) => set({ target }) }));

import { create } from 'zustand';

/** Ephemeral cross-view handoff only; identity/offsets are rechecked in Chat. */
export const useRelatedMessageStore = create<{
  target: { sessionId: string; messageId: string } | null;
  open: (sessionId: string, messageId: string) => void;
}>((set) => ({ target: null, open: (sessionId, messageId) => set({ target: { sessionId, messageId } }) }));

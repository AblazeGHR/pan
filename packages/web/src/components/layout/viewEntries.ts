import { Code, ListChecks, MessageSquare, TerminalSquare } from 'lucide-react';

export type ViewEntry = {
  to: string;
  label: string;
  icon: typeof MessageSquare;
  /** Exact-match only (only `/` needs it among the current entries). */
  end?: boolean;
};

// All view entries belong here; every three entries form one page.
// Single source of truth shared by the expanded entry strip
// (ViewEntryGroups) and the collapsed desktop nav rail (ViewEntryRail),
// so both surfaces always cover exactly the same routes.
export const entries: ViewEntry[] = [
  { to: '/', label: 'Chat', icon: MessageSquare, end: true },
  { to: '/editor', label: 'Editor', icon: Code },
  { to: '/jobs', label: 'Jobs', icon: ListChecks },
  { to: '/terminals', label: 'Terminal', icon: TerminalSquare },
];

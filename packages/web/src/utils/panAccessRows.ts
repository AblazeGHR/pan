import type { PanAccess } from '@/types';

/** Single source for the Pan Access toggles (single-Session and bulk Manage). */
export const PAN_ACCESS_ROWS: {
  key: keyof PanAccess;
  label: string;
  hint: string;
  desc: string;
}[] = [
  {
    key: 'restrictToManaged',
    label: 'Restrict to managed',
    hint: 'restrict_to_managed',
    desc: 'Over MCP this agent may only operate on sessions it manages.',
  },
  {
    key: 'canClaimUnmanaged',
    label: 'Can claim unmanaged',
    hint: 'can_claim_unmanaged',
    desc: 'Over MCP this agent may claim sessions that have no manager yet.',
  },
  {
    key: 'autoClaimCreated',
    label: 'Auto-claim created',
    hint: 'auto_claim_created',
    desc: 'Over MCP sessions created by this agent are claimed automatically.',
  },
];

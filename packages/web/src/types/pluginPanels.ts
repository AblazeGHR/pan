export interface PluginPanel {
  id: string;
  pluginId: string;
  title: string;
  renderer: 'account-switcher/v1';
  path: string;
  actions: Record<string, { capability: string; effect: 'read' | 'write_external' }>;
}

export interface AccountQuota {
  status: string;
  available: boolean;
  queriedAt: number | null;
  message: string | null;
  tiers: { name: string; usedPercent: number; resetsAt: string | null }[];
  balances: { planName?: string; remaining?: number; used?: number; total?: number; unit?: string }[];
}

export interface AccountSnapshot {
  app: string;
  currentProviderId: string | null;
  observedAt: number;
  providers: { id: string; name: string; origin: string | null; isCurrent: boolean; quota: AccountQuota | null; notes?: string; category?: string }[];
  accounts: { id: string; login: string; isDefault: boolean; reauthRequired?: boolean }[];
  accountError: string | null;
}

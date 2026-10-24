export interface PluginPanel {
  id: string;
  pluginId: string;
  title: string;
  renderer: 'account-switcher/v1' | 'web-app/v1';
  path: string;
  /** Present for web-app/v1: the served build root used by the iframe host. */
  assetPath?: string;
  actions: Record<string, { capability: string; effect: 'read' | 'write_external' }>;
}

export interface LocalPluginPanel {
  pluginId: string;
  panelId: string;
  title: string;
  entry: string;
  assetPath: string | null;
  enabled: boolean;
  status: 'unbuilt' | 'building' | 'built' | 'failed';
  /** A successful artifact exists even when the latest build failed. */
  available: boolean;
  activeBuild: string | null;
  builtAt: number | null;
  startedAt: number | null;
  finishedAt: number | null;
  lastError: string | null;
  log: string;
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

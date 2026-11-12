import { capabilityAllowed, type CapabilityMap } from '@/utils/platformCapabilities';
import type { SettingsCategory } from './settingsCategories';
import { SETTING_DESCRIPTIONS } from './settingsDescriptions';

export interface SettingsSearchContext {
  remoteTunnel: boolean;
  authorizationLoggedIn: boolean;
  codexSession?: boolean;
  qqPlugins?: boolean;
  qqSelected?: boolean;
  snowLuma?: boolean;
  localPanels?: boolean;
  completionContacts?: boolean;
  notificationContacts?: boolean;
  capabilities?: CapabilityMap | null;
}

export type SettingsVisibility = 'authorized' | 'anonymous' | 'remoteTunnel' | 'codexSession' | 'qqPlugins' | 'qqSelected' | 'snowLuma' | 'localPanels' | 'completionContacts' | 'notificationContacts';
export interface SettingDescription {
  id: string;
  tab: SettingsCategory;
  label: string;
  hint?: string;
  aliases?: string[];
  anchor?: string;
  gate?: SettingsVisibility;
  capability?: string;
  kind?: string;
  source?: string;
  searchable?: boolean;
}

const byId = new Map(SETTING_DESCRIPTIONS.map((description) => [description.id, description]));
if (byId.size !== SETTING_DESCRIPTIONS.length) throw new Error('Duplicate setting description ID');

/** The UI and index use the original description, never a mirrored label list. */
export function settingsText(id: string): string {
  const description = byId.get(id);
  if (!description) throw new Error(`Unregistered setting description: ${id}`);
  return description.label;
}

export function settingsVisible(gate: SettingsVisibility, context: Partial<SettingsSearchContext>): boolean {
  if (gate === 'authorized') return context.authorizationLoggedIn === true;
  if (gate === 'anonymous') return context.authorizationLoggedIn === false;
  if (gate === 'remoteTunnel') return context.remoteTunnel === true;
  return context[gate] === true;
}

export function settingAvailable(description: SettingDescription, context: SettingsSearchContext): boolean {
  return (!description.gate || settingsVisible(description.gate, context))
    && (!description.capability || capabilityAllowed(context.capabilities ?? null, description.capability));
}

export { SETTING_DESCRIPTIONS };

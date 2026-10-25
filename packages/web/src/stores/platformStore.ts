import { create } from 'zustand';
import { fetchPlatformInfo } from '@/services/api';
import {
  capabilityAllowed,
  sanitizeCapabilities,
  type CapabilityMap,
  type PlatformCapability,
} from '@/utils/platformCapabilities';

interface PlatformState {
  /** Server platform name (`windows`/`darwin`/`linux`); null until probed. */
  platform: string | null;
  /**
   * Null means "unknown": every capability is treated as available. This is
   * the pre-`/api/platform` behavior and the only safe fallback — a failed
   * probe must never hide the whole UI.
   */
  capabilities: CapabilityMap | null;
  /** The probe finished (successfully or not); it is not retried per mount. */
  loaded: boolean;
  load: () => Promise<void>;
}

let inFlight: Promise<void> | null = null;

export const usePlatformStore = create<PlatformState>((set, get) => ({
  platform: null,
  capabilities: null,
  loaded: false,
  load: () => {
    if (get().loaded) return Promise.resolve();
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        const info = await fetchPlatformInfo();
        const platform = typeof info?.platform === 'string' && info.platform ? info.platform : null;
        set({ platform, capabilities: sanitizeCapabilities(info?.capabilities), loaded: true });
      } catch {
        // Endpoint missing (older server / A-stream still landing), offline, or
        // unauthorized: keep null capabilities so all entries stay available.
        set({ loaded: true });
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  },
}));

/** Reactive capability check; defaults to allowed while unknown. */
export function useCapability(capability: PlatformCapability | string): boolean {
  return usePlatformStore((state) => capabilityAllowed(state.capabilities, capability));
}

/** Non-reactive check for event handlers and module-level snapshots. */
export function capabilityAvailable(capability: PlatformCapability | string): boolean {
  return capabilityAllowed(usePlatformStore.getState().capabilities, capability);
}

/** Test-only reset so one test's probe result cannot leak into the next. */
export function resetPlatformStoreForTests(): void {
  inFlight = null;
  usePlatformStore.setState({ platform: null, capabilities: null, loaded: false });
}

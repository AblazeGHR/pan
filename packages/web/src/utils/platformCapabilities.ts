/**
 * Capability vocabulary shared with `GET /api/platform`.
 *
 * The backend owns the truth; this module only knows how the UI must react to
 * it. The single rule that matters: an unknown capability set never hides or
 * disables anything, so a dashboard talking to a server without the endpoint
 * (or to a temporarily failing one) behaves exactly like the Windows baseline
 * instead of rendering a blank or gutted page.
 */

export type PlatformCapability =
  | 'terminal.native'
  | 'terminal.detach'
  | 'rewind'
  | 'service.lifecycle';

export type CapabilityMap = Record<string, boolean>;

/**
 * `true` unless the server explicitly said `false`. A missing key means the
 * server did not advertise that feature, not that it is broken.
 */
export function capabilityAllowed(
  capabilities: CapabilityMap | null | undefined,
  capability: PlatformCapability | string,
): boolean {
  if (!capabilities) return true;
  return capabilities[capability] !== false;
}

/** Human-readable platform name for "not supported here" copy. */
export function platformLabel(platform: string | null | undefined): string {
  switch (platform) {
    case 'windows':
      return 'Windows';
    case 'darwin':
      return 'macOS';
    case 'linux':
      return 'Linux';
    default:
      return '当前平台';
  }
}

/** True once a probe answered with a platform name other than Windows. */
export function isNonWindowsPlatform(platform: string | null | undefined): boolean {
  return Boolean(platform) && platform !== 'windows';
}

/** Keep only well-formed booleans so a malformed body cannot disable the UI. */
export function sanitizeCapabilities(raw: unknown): CapabilityMap | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const result: CapabilityMap = {};
  let count = 0;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === 'boolean') {
      result[key] = value;
      count += 1;
    }
  }
  return count > 0 ? result : null;
}

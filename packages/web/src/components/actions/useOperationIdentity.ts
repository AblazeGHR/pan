import { useState } from 'react';
import { ApiRequestError } from '@/services/api';
/** Store only query identities, never arguments, output or bearer credentials. */
export function useOperationIdentity(scope: string) {
  // Server lookups enforce profile and principal. Cookie restoration and
  // relogin must not erase an unresolved query identity.
  const storageKey = `pan:action-operation:${scope}`;
  const [identity, setIdentity] = useState<{ operation: string; requestKey: string } | null>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null');
      return value && typeof value.operation === 'string' && typeof value.requestKey === 'string'
        ? value
        : null;
    } catch {
      return null;
    }
  });
  const set = (value: typeof identity) => {
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
    setIdentity(value);
  };
  return [identity, set] as const;
}
export const definitelyRejected = (e: unknown) =>
  e instanceof ApiRequestError &&
  e.status >= 400 &&
  e.status < 500 &&
  ![408, 429].includes(e.status);

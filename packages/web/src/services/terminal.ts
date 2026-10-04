export interface TerminalView {
  terminal_id: string;
  status: string;
  detached?: boolean;
  pid?: number | null;
  scope?: { workspace_id?: string | null; session_id?: string | null };
}

export async function terminalRequest<T>(path = '', body?: Record<string, unknown>): Promise<T> {
  const response = await fetch(`/api/terminals${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok || value.ok !== true) throw new Error(value.error?.code || `HTTP ${response.status}`);
  return value.result as T;
}

export function encodeInput(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function decodeOutput(text: string): Uint8Array {
  return Uint8Array.from(atob(text), (character) => character.charCodeAt(0));
}

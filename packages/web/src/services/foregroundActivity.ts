// A small scheduler signal, not React state. Optional speculative work must
// yield synchronously when a real API request starts, including body parsing.
let requests = 0;
let lastActivity = Date.now();
let version = 0;
const listeners = new Set<() => void>();
export function foregroundActivity() {
  return { requests, lastActivity, version };
}
export function noteForegroundActivity(): void {
  lastActivity = Date.now();
  version += 1;
  for (const listener of listeners) listener();
}
export function subscribeForegroundActivity(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export function beginForegroundRequest(): () => void {
  requests += 1;
  noteForegroundActivity();
  let finished = false;
  return () => {
    if (finished) return;
    finished = true;
    requests -= 1;
    noteForegroundActivity();
  };
}

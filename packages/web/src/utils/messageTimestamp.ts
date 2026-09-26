/** HH:MM; include the date for timestamps outside the local current day. */
export function formatMessageTs(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return '';

  const pad = (value: number) => String(value).padStart(2, '0');
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const now = new Date();
  if (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  ) {
    return time;
  }

  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return `${day} ${time}`;
}

export function isValidMessageTs(ts: unknown): ts is string {
  return typeof ts === 'string' && ts.trim().length > 0 && Number.isFinite(Date.parse(ts));
}

/** Last valid timestamp in display order; missing or invalid values are skipped. */
export function getLatestMessageTs(messages: readonly { ts?: string }[]): string | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const ts = messages[index]?.ts;
    if (ts && isValidMessageTs(ts)) return ts;
  }
  return undefined;
}

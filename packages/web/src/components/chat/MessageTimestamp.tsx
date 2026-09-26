import { memo, useEffect, useRef, useState } from 'react';
import { formatMessageTs } from '@/utils/messageTimestamp';

interface MessageTimestampProps {
  ts?: string;
  /** A one-render event token. A new token restarts the CSS animation. */
  flashKey?: string;
  onFlashConsumed?: (flashKey: string) => void;
  className?: string;
}

export const MessageTimestamp = memo(function MessageTimestamp({
  ts,
  flashKey,
  onFlashConsumed,
  className = '',
}: MessageTimestampProps) {
  const label = ts ? formatMessageTs(ts) : '';
  const [activeFlashKey, setActiveFlashKey] = useState<string | null>(null);
  const seenFlashKey = useRef<string | null>(null);

  useEffect(() => {
    if (!flashKey || seenFlashKey.current === flashKey) return;
    seenFlashKey.current = flashKey;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      onFlashConsumed?.(flashKey);
      return;
    }
    setActiveFlashKey(flashKey);
  }, [flashKey, onFlashConsumed]);

  if (!ts || !label) return null;

  return (
    <time
      key={activeFlashKey ?? 'idle'}
      dateTime={ts}
      className={`message-timestamp text-[11px] text-text-secondary select-none ${className} ${activeFlashKey ? 'message-timestamp-flash' : ''}`}
      onAnimationStart={() => {
        if (activeFlashKey) onFlashConsumed?.(activeFlashKey);
      }}
      onAnimationEnd={() => setActiveFlashKey(null)}
    >
      {label}
    </time>
  );
});

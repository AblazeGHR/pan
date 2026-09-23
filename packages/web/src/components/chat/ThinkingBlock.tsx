import { memo } from 'react';
import type { Message } from '@/types';
import { ThinkingGroup } from './ThinkingGroup';

interface ThinkingBlockProps {
  message: Message;
}

/** Single-block adapter retained for callers that render a thinking row directly. */
export const ThinkingBlock = memo(function ThinkingBlock({ message }: ThinkingBlockProps) {
  return <ThinkingGroup items={[message]} />;
});

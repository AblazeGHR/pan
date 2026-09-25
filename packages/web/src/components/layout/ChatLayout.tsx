import { TopBar } from './TopBar';
import type { ReactNode } from 'react';

interface ChatLayoutProps {
  children: ReactNode;
  topBarRightAction?: ReactNode;
}

export function ChatLayout({ children, topBarRightAction }: ChatLayoutProps) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <TopBar rightAction={topBarRightAction} />
      <div className="flex-1 min-h-0 overflow-hidden">
        {children}
      </div>
    </div>
  );
}

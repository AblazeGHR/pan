import { TopBar } from './TopBar';
import { SettingsPanel } from './SettingsPanel';
import type { ReactNode } from 'react';

interface ChatLayoutProps {
  children: ReactNode;
}

export function ChatLayout({ children }: ChatLayoutProps) {
  return (
    <div className="flex flex-col h-full">
      <TopBar />
      <div className="flex-1 min-h-0 overflow-hidden">
        {children}
      </div>
      <SettingsPanel />
    </div>
  );
}

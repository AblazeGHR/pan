import { X } from 'lucide-react';

interface PanelHeaderProps {
  title: string;
  subtitle?: string;
  onClose: () => void;
}

export function PanelHeader({ title, subtitle, onClose }: PanelHeaderProps) {
  return (
    <div className="h-[48px] border-b border-border-default flex items-center px-3 gap-3 flex-shrink-0">
      <div className="flex-1 min-w-0 flex items-center gap-2">
        <span className="font-mono text-sm font-bold text-text-secondary truncate">
          {title}
        </span>
        {subtitle && (
          <span className="text-xs text-text-tertiary truncate">{subtitle}</span>
        )}
      </div>
      <button
        onClick={onClose}
        className="text-text-tertiary hover:text-text-primary transition-colors flex-shrink-0"
        title="Close detail panel"
      >
        <X size={16} />
      </button>
    </div>
  );
}

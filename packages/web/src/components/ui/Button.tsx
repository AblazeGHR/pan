import { type ButtonHTMLAttributes } from 'react';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  size?: 'sm' | 'md';
}

const variantClasses: Record<string, string> = {
  primary:
    'bg-accent text-white hover:bg-accent-hover border-transparent',
  secondary:
    'bg-bg-tertiary text-text-primary hover:bg-bg-tertiary/80 border-border-default',
  danger:
    'bg-danger text-white hover:bg-danger/80 border-transparent',
  ghost:
    'bg-transparent text-text-secondary hover:text-text-primary hover:bg-bg-tertiary border-transparent',
};

const sizeClasses: Record<string, string> = {
  sm: 'px-2 py-1 text-xs',
  md: 'px-3 py-1.5 text-sm',
};

export function Button({
  variant = 'secondary',
  size = 'md',
  className = '',
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center gap-1 rounded border font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

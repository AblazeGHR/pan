import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Copy,
  Inbox,
  RotateCcw,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'muted';

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'border-border-default bg-bg-tertiary text-text-secondary',
  accent: 'border-accent/25 bg-accent/10 text-accent',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/35 bg-warning/10 text-warning',
  danger: 'border-danger/35 bg-danger/10 text-danger',
  muted: 'border-border-muted bg-bg-secondary text-text-tertiary',
};

export function Badge({
  tone = 'neutral',
  dot = false,
  children,
  title,
  className = '',
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        'inline-flex shrink-0 items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] font-medium leading-4',
        TONE_CLASSES[tone],
        className,
      )}
    >
      {dot && <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function IconTile({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'accent' | 'danger';
}) {
  return (
    <span
      aria-hidden="true"
      className={cx(
        'mt-px flex size-7 shrink-0 items-center justify-center rounded-md border',
        tone === 'accent' && 'border-accent/25 bg-accent/10 text-accent',
        tone === 'danger' && 'border-danger/25 bg-danger/10 text-danger',
        tone === 'neutral' && 'border-border-default bg-bg-tertiary/60 text-text-secondary',
      )}
    >
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-border-default bg-bg-tertiary px-1 py-0.5 font-mono text-[10px] text-text-tertiary">
      {children}
    </kbd>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'border-accent bg-accent' : 'border-border-default bg-bg-tertiary',
      )}
    >
      <span
        aria-hidden="true"
        className={cx(
          'inline-block size-3.5 rounded-full bg-white shadow-sm transition-transform',
          checked ? 'translate-x-[18px]' : 'translate-x-[3px]',
        )}
      />
    </button>
  );
}

export const inputClass =
  'w-full min-w-0 rounded-md border border-border-default bg-bg-primary px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-tertiary transition-colors hover:border-text-tertiary/50 focus:border-accent focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';

export function Field({
  label,
  hint,
  error,
  required = false,
  htmlFor,
  children,
  className = '',
}: {
  label: string;
  hint?: ReactNode;
  error?: string;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx('space-y-1.5', className)}>
      <label htmlFor={htmlFor} className="flex items-center gap-1 text-xs font-medium text-text-secondary">
        {label}
        {required && (
          <span aria-hidden="true" className="text-danger">
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p role="alert" className="text-[11px] text-danger">
          {error}
        </p>
      ) : hint ? (
        <p className="text-[11px] leading-relaxed text-text-tertiary">{hint}</p>
      ) : null}
    </div>
  );
}

export function Section({
  title,
  description,
  actions,
  children,
  className = '',
  bodyClassName = '',
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cx('overflow-hidden rounded-lg border border-border-default bg-bg-secondary/35', className)}>
      {title && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-border-default px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
            {description && <p className="mt-0.5 text-xs leading-relaxed text-text-tertiary">{description}</p>}
          </div>
          {actions && <div className="ml-auto flex max-w-full flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx('p-4', bodyClassName)}>{children}</div>
    </section>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled = false,
}: {
  value: T;
  options: Array<{ value: T; label: string; icon?: ReactNode; count?: number }>;
  onChange: (value: T) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex min-w-0 max-w-full flex-wrap rounded-md border border-border-default bg-bg-primary p-0.5"
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cx(
              'inline-flex min-w-0 items-center justify-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50',
              active
                ? 'bg-bg-tertiary text-text-primary shadow-sm'
                : 'text-text-secondary hover:text-text-primary',
            )}
          >
            {option.icon}
            <span className="truncate">{option.label}</span>
            {typeof option.count === 'number' && (
              <span className={cx('tabular text-[10px]', active ? 'text-text-secondary' : 'text-text-tertiary')}>
                {option.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function Select({
  value,
  onChange,
  children,
  label,
  disabled = false,
  className = '',
  id,
}: {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  label: string;
  disabled?: boolean;
  className?: string;
  id?: string;
}) {
  return (
    <div className={cx('relative', className)}>
      <select
        id={id}
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={cx(inputClass, 'appearance-none pr-7')}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        size={13}
        className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-text-tertiary"
      />
    </div>
  );
}

export function CopyButton({ value, label = 'Copy ID' }: { value: string; label?: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  useEffect(() => {
    if (state === 'idle') return;
    const timer = window.setTimeout(() => setState('idle'), 1800);
    return () => window.clearTimeout(timer);
  }, [state]);
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      title={state === 'failed' ? 'Clipboard unavailable' : label}
      onClick={() => {
        void navigator.clipboard
          ?.writeText(value)
          .then(() => setState('copied'))
          .catch(() => setState('failed'));
      }}
    >
      {state === 'copied' ? <Check size={12} /> : <Copy size={12} />}
      <span>{state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy'}</span>
    </Button>
  );
}

export function SkeletonList({ rows = 7, kind = 'action' }: { rows?: number; kind?: 'action' | 'group' }) {
  return (
    <div aria-busy="true" aria-label="Loading Actions and Groups">
      <div className="hidden border-b border-border-default px-3 py-2 sm:flex">
        <span className="skeleton block h-3 w-24" />
      </div>
      <ul>
        {Array.from({ length: rows }, (_, index) => (
          <li key={index} className="border-b border-border-muted px-3 py-3">
            <div className="flex items-start gap-3">
              <span className="skeleton block size-7 shrink-0 rounded-md" />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <span className={cx('skeleton block h-3.5', index % 3 === 0 ? 'w-52' : index % 3 === 1 ? 'w-36' : 'w-64')} />
                  <span className="skeleton block h-4 w-14 rounded" />
                </div>
                <span className={cx('skeleton block h-2.5', index % 2 === 0 ? 'w-[70%]' : 'w-[52%]')} />
                {kind === 'group' && <span className="skeleton block h-2.5 w-40" />}
              </div>
              <div className="hidden shrink-0 items-center gap-6 sm:flex">
                <span className="skeleton block h-3 w-20" />
                <span className="skeleton block h-3 w-16" />
                <span className="skeleton block h-7 w-20 rounded" />
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ListEmpty({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <span className="mb-4 flex size-10 items-center justify-center rounded-full border border-border-default bg-bg-tertiary text-text-tertiary">
        {icon ?? <Inbox size={18} />}
      </span>
      <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
      <p className="mt-1 max-w-[24rem] text-xs leading-relaxed text-text-secondary">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start gap-3 rounded-lg border border-danger/35 bg-danger/5 p-4"
    >
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-text-primary">Couldn’t load the Action registry</p>
        <p className="mt-0.5 break-words text-xs leading-relaxed text-text-secondary">{message}</p>
      </div>
      <Button type="button" size="sm" onClick={onRetry}>
        <RotateCcw size={12} /> Retry
      </Button>
    </div>
  );
}

export function StaleBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="status"
      className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-warning/35 bg-warning/10 px-3 py-2 text-xs text-warning"
    >
      <AlertTriangle size={14} className="shrink-0" />
      <span className="min-w-0 flex-1">
        Couldn’t refresh. Showing the last loaded registry. {message}
      </span>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 rounded border border-warning/40 px-2 py-0.5 text-[11px] font-medium hover:bg-warning/15"
      >
        Retry
      </button>
    </div>
  );
}

export function Modal({
  title,
  description,
  onClose,
  children,
  footer,
  width = 'max-w-2xl',
}: {
  title: ReactNode;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const first =
      panel?.querySelector<HTMLElement>('[data-autofocus]:not([disabled])') ??
      panel?.querySelector<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ??
      panel;
    first?.focus();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);
  function trap(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Tab') return;
    const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/55 p-0 sm:items-center sm:p-6">
      <button type="button" aria-label="Close dialog" className="absolute inset-0 cursor-default" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={trap}
        className={cx(
          'actions-modal-in relative flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-xl border border-border-default bg-bg-primary shadow-panel sm:rounded-xl',
          width,
        )}
      >
        <header className="flex items-start justify-between gap-3 border-b border-border-default px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <h2 id={titleId} className="text-base font-semibold text-text-primary">
              {title}
            </h2>
            {description && <div className="mt-0.5 text-xs leading-relaxed text-text-secondary">{description}</div>}
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded p-1 text-text-tertiary transition-colors hover:bg-bg-tertiary hover:text-text-primary"
          >
            <X size={16} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-4 sm:p-5">{children}</div>
        {footer && (
          <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-border-default bg-bg-secondary/40 px-4 py-3 sm:px-5">
            {footer}
          </footer>
        )}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  tone = 'danger',
  onCancel,
  onConfirm,
}: {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  tone?: 'danger' | 'primary';
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      width="max-w-[28rem]"
      footer={
        <>
          <Button type="button" size="sm" onClick={onCancel} data-autofocus>
            Keep editing
          </Button>
          <Button type="button" size="sm" variant={tone} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-sm leading-relaxed text-text-secondary">{message}</p>
    </Modal>
  );
}

export function IconButton({
  label,
  children,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cx(
        'inline-flex size-7 shrink-0 items-center justify-center rounded text-text-tertiary transition-colors hover:bg-bg-tertiary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, X, XCircle } from "lucide-react";
import { Link, type LinkProps } from "react-router";
import { useEffect, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

type Variant = "primary" | "secondary" | "danger" | "ghost";

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium whitespace-nowrap transition disabled:cursor-not-allowed disabled:opacity-50";
const BUTTON_VARIANTS: Record<Variant, string> = {
  primary: "bg-[var(--accent)] text-[var(--accent-fg)] hover:opacity-90 border-transparent",
  secondary: "bg-[var(--panel)] text-[var(--text)] border-[var(--border)] hover:bg-[var(--panel-2)]",
  danger: "bg-[var(--panel)] text-[var(--err)] border-[var(--border)] hover:bg-[var(--panel-2)]",
  ghost: "bg-transparent text-[var(--muted)] border-transparent hover:text-[var(--text)] hover:bg-[var(--panel-2)]",
};

/** A navigation link styled as a button (avoids nesting a button inside a link). */
export function LinkButton({ variant = "secondary", className, ...rest }: LinkProps & { variant?: Variant }) {
  return <Link className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], className)} {...rest} />;
}

export function Button({
  variant = "secondary",
  busy,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], className)}
      disabled={disabled || busy}
      {...rest}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-xl border border-[var(--border)] bg-[var(--panel)] shadow-[var(--shadow)]", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-[var(--muted)]">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2">{actions}</div>
    </div>
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-[var(--muted)]">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-[var(--err)]">{error}</span>}
    </label>
  );
}

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-sm placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputClass, props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(inputClass, props.className)} />;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "warn" | "err" | "accent"; children: ReactNode }) {
  const tones = {
    neutral: "text-[var(--muted)] border-[var(--border)]",
    ok: "text-[var(--ok)] border-[color-mix(in_srgb,var(--ok)_40%,transparent)]",
    warn: "text-[var(--warn)] border-[color-mix(in_srgb,var(--warn)_40%,transparent)]",
    err: "text-[var(--err)] border-[color-mix(in_srgb,var(--err)_40%,transparent)]",
    accent: "text-[var(--accent)] border-[color-mix(in_srgb,var(--accent)_40%,transparent)]",
  };
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap", tones[tone])}>
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "ok":
    case "pass":
    case "success":
    case "running":
    case "complete":
      return (
        <Badge tone="ok">
          <CheckCircle2 className="size-3.5" aria-hidden /> {status}
        </Badge>
      );
    case "warning":
    case "warn":
      return (
        <Badge tone="warn">
          <AlertTriangle className="size-3.5" aria-hidden /> {status}
        </Badge>
      );
    case "error":
    case "fail":
    case "failure":
    case "denied":
      return (
        <Badge tone="err">
          <XCircle className="size-3.5" aria-hidden /> {status}
        </Badge>
      );
    default:
      return (
        <Badge>
          <CircleDashed className="size-3.5" aria-hidden /> {status}
        </Badge>
      );
  }
}

export function ErrorBanner({ error, onDismiss }: { error: string | null; onDismiss?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="mb-4 flex items-start gap-2 rounded-md border border-[var(--err)] bg-[color-mix(in_srgb,var(--err)_8%,transparent)] px-3 py-2 text-sm text-[var(--err)]">
      <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span className="flex-1">{error}</span>
      {onDismiss && (
        <button onClick={onDismiss} aria-label="Dismiss">
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-[var(--muted)]">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      {icon && <div className="mb-3 text-[var(--muted)]">{icon}</div>}
      <div className="font-medium">{title}</div>
      {children && <div className="mt-2 max-w-md text-sm text-[var(--muted)]">{children}</div>}
    </div>
  );
}

export function Dialog({ open, title, onClose, children, wide }: { open: boolean; title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className={cx(
        "m-auto w-[calc(100%-2rem)] rounded-lg border border-[var(--border)] bg-[var(--panel)] p-0 text-[var(--text)] backdrop:bg-black/40",
        wide ? "max-w-3xl" : "max-w-lg",
      )}
    >
      {open && (
        <div>
          <header className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-sm font-semibold">{title}</h2>
            <button onClick={onClose} aria-label="Close" className="text-[var(--muted)] hover:text-[var(--text)]">
              <X className="size-4" />
            </button>
          </header>
          <div className="max-h-[75vh] overflow-y-auto p-4">{children}</div>
        </div>
      )}
    </dialog>
  );
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] bg-[var(--panel-2)] text-left text-[11px] tracking-wide text-[var(--muted)] uppercase">
            {head.map((h, i) => (
              <th key={i} className="px-3 py-2 font-semibold first:rounded-tl-md last:rounded-tr-md">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-md border border-[var(--border)] bg-[var(--panel-2)] p-3 font-mono text-xs leading-relaxed">
      {children}
    </pre>
  );
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}

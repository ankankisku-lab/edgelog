"use client";

import { ButtonHTMLAttributes, ReactNode } from "react";

export function Card({ title, subtitle, action, children, className = "" }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-surface p-4 sm:p-5 ${className}`}>
      {(title || action) && (
        <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
}) {
  const styles: Record<Variant, string> = {
    primary: "bg-accent text-white hover:opacity-90",
    secondary: "border border-line bg-surface text-ink hover:bg-surface-2",
    ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
    danger: "border border-line bg-surface text-loss hover:bg-crit-wash",
  };
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`}
    />
  );
}

export function Stat({ label, value, hint, tone }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${tone ?? "text-ink"}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  done: "bg-surface-2 text-ink-2",
  partial: "bg-warn-wash text-warn",
  queued: "bg-surface-2 text-ink-2",
  processing: "bg-surface-2 text-accent",
  rate_limited: "bg-warn-wash text-warn",
  error: "bg-crit-wash text-loss",
  manual: "bg-manual-wash text-manual",
};

const STATUS_LABEL: Record<string, string> = {
  done: "Extracted",
  partial: "Partly extracted",
  queued: "Queued",
  processing: "Processing…",
  rate_limited: "AI rate-limited",
  error: "Extraction error",
  manual: "Manual",
};

export function ProcessingBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status] ?? "bg-surface-2 text-ink-2"}`}>
      {(status === "processing" || status === "queued") && <Spinner small />}
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warn" | "crit" | "manual" | "good" }) {
  const t = {
    neutral: "bg-surface-2 text-ink-2",
    warn: "bg-warn-wash text-warn",
    crit: "bg-crit-wash text-loss",
    manual: "bg-manual-wash text-manual",
    good: "bg-surface-2 text-good",
  }[tone];
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${t}`}>{children}</span>;
}

export function Spinner({ small }: { small?: boolean }) {
  return (
    <span
      aria-hidden
      className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent ${small ? "h-3 w-3" : "h-5 w-5"}`}
    />
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <div className="mt-1 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-lg border border-line bg-crit-wash px-3 py-2 text-sm text-loss">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

export function Select({ value, onChange, options, placeholder, className = "", ...rest }: {
  value: string;
  onChange: (v: string) => void;
  options: (string | { value: string; label: string })[];
  placeholder?: string;
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <select
      {...rest}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`rounded-lg border border-line px-2 py-1.5 text-sm ${className}`}
    >
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => {
        const v = typeof o === "string" ? o : o.value;
        return (
          <option key={v} value={v}>
            {typeof o === "string" ? o : o.label}
          </option>
        );
      })}
    </select>
  );
}

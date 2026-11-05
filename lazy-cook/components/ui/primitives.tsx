import clsx from "clsx";
import Link from "next/link";
import type { ComponentProps } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<Variant, string> = {
  primary: "bg-primary text-primary-contrast hover:opacity-90",
  secondary: "bg-surface-2 text-text hover:bg-border",
  ghost: "bg-transparent text-text hover:bg-surface-2",
  danger: "bg-danger text-white hover:opacity-90",
};

export function buttonClass(variant: Variant = "primary", extra?: string) {
  return clsx(
    "inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-[12px] px-4 font-medium transition disabled:opacity-50",
    variants[variant],
    extra,
  );
}

export function Button({
  variant = "primary",
  className,
  ...props
}: ComponentProps<"button"> & { variant?: Variant }) {
  return <button className={buttonClass(variant, className)} {...props} />;
}

export function ButtonLink({
  variant = "primary",
  className,
  ...props
}: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link className={buttonClass(variant, className)} {...props} />;
}

export function Card({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={clsx("border-border bg-surface rounded-[16px] border p-4", className)}
      {...props}
    />
  );
}

export function Chip({
  active,
  className,
  ...props
}: ComponentProps<"button"> & { active?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={clsx(
        "inline-flex min-h-11 items-center gap-1 rounded-full border px-4 text-sm whitespace-nowrap transition",
        active
          ? "border-primary bg-primary text-primary-contrast"
          : "border-border bg-surface text-text",
        className,
      )}
      {...props}
    />
  );
}

export function Badge({
  tone = "muted",
  className,
  ...props
}: ComponentProps<"span"> & {
  tone?: "muted" | "accent" | "success" | "warning" | "danger" | "primary";
}) {
  const tones = {
    muted: "bg-surface-2 text-muted",
    accent: "bg-accent/20 text-text",
    success: "bg-success/15 text-success",
    warning: "bg-warning/15 text-warning",
    danger: "bg-danger/15 text-danger",
    primary: "bg-primary/10 text-primary",
  };
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} aria-hidden />;
}

export function EmptyState({
  emoji,
  text,
  action,
}: {
  emoji: string;
  text: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-4 px-6 py-12 text-center">
      <div className="text-5xl" aria-hidden>
        {emoji}
      </div>
      <p className="text-muted max-w-xs leading-7">{text}</p>
      {action}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-3 px-4 pt-5 pb-3">
      <div>
        <h1 className="text-2xl font-extrabold">{title}</h1>
        {subtitle && <p className="text-muted mt-1 text-sm">{subtitle}</p>}
      </div>
      {action}
    </header>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="text-muted text-xs">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "min-h-11 w-full rounded-[12px] border border-border bg-surface px-3 text-text placeholder:text-muted focus:border-primary focus:outline-none";

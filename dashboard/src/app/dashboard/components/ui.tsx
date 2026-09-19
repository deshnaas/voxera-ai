"use client";

// Small, dependency-free UI kit used by the redesigned pages.

import Link from "next/link";
import { useEffect, type ReactNode } from "react";
import { cn, initials } from "@/lib/format";

export function PageHeader({
  eyebrow, title, subtitle, actions,
}: {
  eyebrow?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1 className="mt-1 text-2xl font-extrabold tracking-tight sm:text-3xl">{title}</h1>
        {subtitle && <p className="text-muted mt-1.5 max-w-2xl text-sm">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Page({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <main className={cn("dashboard-page mx-auto w-full px-4 py-6 sm:px-6 lg:px-8", wide ? "max-w-[1500px]" : "max-w-7xl")}>
      {children}
    </main>
  );
}

export function SectionCard({
  title, subtitle, actions, children, className, padded = true,
}: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode;
  children: ReactNode; className?: string; padded?: boolean;
}) {
  return (
    <section className={cn("card", className)}>
      {(title || actions) && (
        <div className="flex items-start justify-between gap-3 border-b px-5 py-4" style={{ borderColor: "var(--dashboard-border)" }}>
          <div className="min-w-0">
            {title && <h2 className="text-base font-bold">{title}</h2>}
            {subtitle && <p className="text-muted mt-0.5 text-sm">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={padded ? "p-5" : ""}>{children}</div>
    </section>
  );
}

export function StatCard({
  label, value, hint, tone = "neutral", href, pulse,
}: {
  label: string; value: ReactNode; hint?: ReactNode;
  tone?: "neutral" | "critical" | "warning" | "success" | "info";
  href?: string; pulse?: boolean;
}) {
  const toneCls =
    tone === "critical" ? "callout-critical"
    : tone === "warning" ? "callout-warning"
    : tone === "success" ? "callout-success"
    : tone === "info" ? "callout-info" : "";
  const body = (
    <div className={cn("card stat card-hover h-full", toneCls && `${toneCls}`, pulse && "pulse-alert")}
         style={toneCls ? { borderWidth: 1 } : undefined}>
      <p className="eyebrow" style={{ color: "inherit", opacity: 0.8 }}>{label}</p>
      <p className="stat-value mt-2">{value}</p>
      {hint && <p className="mt-1 text-xs" style={{ opacity: 0.8 }}>{hint}</p>}
    </div>
  );
  return href ? <Link href={href} className="block h-full">{body}</Link> : body;
}

export function Badge({ children, tone }: { children: ReactNode; tone?: "critical" | "warning" | "success" | "info" | "brand" | "neutral" }) {
  return <span className={cn("badge", tone && tone !== "neutral" && `badge-${tone}`)}>{children}</span>;
}

export function Callout({ tone = "info", children, className }: { tone?: "critical" | "warning" | "success" | "info"; children: ReactNode; className?: string }) {
  return <div role={tone === "critical" ? "alert" : "status"} className={cn("callout", `callout-${tone}`, className)}>{children}</div>;
}

export function EmptyState({ icon = "○", title, hint, action }: { icon?: string; title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full text-xl"
           style={{ background: "var(--dashboard-surface-muted)", color: "var(--dashboard-faint)" }}>{icon}</div>
      <p className="font-semibold">{title}</p>
      {hint && <p className="text-muted mt-1 max-w-md text-sm">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
    </div>
  );
}

export function Avatar({ name, size = 40, tone }: { name: string | null | undefined; size?: number; tone?: "critical" }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full font-bold"
      style={{
        width: size, height: size, fontSize: size * 0.38,
        background: tone === "critical" ? "var(--critical)" : "var(--brand-soft)",
        color: tone === "critical" ? "#fff" : "var(--brand-ink)",
        border: "1px solid var(--dashboard-gold-border)",
      }}
      aria-hidden
    >
      {initials(name)}
    </span>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="text-faint mt-1 block text-xs">{hint}</span>}
    </label>
  );
}

export function InfoItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="eyebrow">{label}</p>
      <div className="mt-1 break-words text-sm font-medium">{value ?? "—"}</div>
    </div>
  );
}

export function Modal({
  open, onClose, title, children, footer, width = 640,
}: {
  open: boolean; onClose: () => void; title: string;
  children: ReactNode; footer?: ReactNode; width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[10000] flex items-end justify-center p-0 sm:items-center sm:p-6" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/55" onClick={onClose} />
      <div className="card relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-b-none sm:rounded-b-[14px]"
           style={{ maxWidth: width, boxShadow: "0 24px 60px rgba(0,0,0,0.35)" }}>
        <div className="flex items-center justify-between border-b px-5 py-4" style={{ borderColor: "var(--dashboard-border)" }}>
          <h2 className="text-lg font-bold">{title}</h2>
          <button type="button" onClick={onClose} className="btn btn-ghost btn-sm" aria-label="Close">✕</button>
        </div>
        <div className="overflow-y-auto p-5">{children}</div>
        {footer && (
          <div className="flex flex-wrap justify-end gap-2 border-t px-5 py-4" style={{ borderColor: "var(--dashboard-border)", background: "var(--dashboard-surface-muted)" }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export function Tabs<T extends string>({
  tabs, value, onChange,
}: {
  tabs: Array<{ id: T; label: ReactNode }>;
  value: T; onChange: (id: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className="tab" onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Chip({ active, onClick, children }: { active?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button" onClick={onClick} aria-pressed={active}
      className={cn("btn btn-sm", active && "btn-primary")}
      style={active ? undefined : { borderRadius: 999 }}
    >
      {children}
    </button>
  );
}
